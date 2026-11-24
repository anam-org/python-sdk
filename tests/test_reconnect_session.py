import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from anam import (
    AnamClient,
    AnamEvent,
    Message,
    MessageDeliveryFailure,
    MessageRole,
    TalkMessageStreamState,
)
from anam._api import CoreApiClient
from anam._heartbeat_monitor import HeartbeatMonitor
from anam._signalling import SignallingClient
from anam._streaming import StreamingClient
from anam.client import Session
from anam.errors import SessionError

from .fake_engine import SESSION_ID, FakeEngine, session_info_for, stays


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SignallingClient, "RECONNECT_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(SignallingClient, "STABLE_CONNECTION_SECONDS", 0.3)
    monkeypatch.setattr(SignallingClient, "WRITE_STALL_SECONDS", 0.3)
    monkeypatch.setattr(HeartbeatMonitor, "ACK_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(HeartbeatMonitor, "SUSPECT_AFTER_SECONDS", 0.15)
    monkeypatch.setattr(HeartbeatMonitor, "PROBE_COALESCE_SECONDS", 0.01)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[FakeEngine]:
    async with FakeEngine() as fake:
        fake.answer_offers = "aiortc"
        yield fake


@pytest_asyncio.fixture
async def anam(engine: FakeEngine, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AnamClient]:
    info = session_info_for(engine, heartbeat_interval_seconds=0.1, max_reconnection_attempts=2)
    monkeypatch.setattr(CoreApiClient, "start_session", AsyncMock(return_value=info))
    client = AnamClient(api_key="test-key", persona_id="test-persona")
    yield client
    await client.close()


def record(client: AnamClient, event: AnamEvent) -> list[tuple[Any, ...]]:
    seen: list[tuple[Any, ...]] = []
    client.add_listener(event, lambda *args: seen.append(args))
    return seen


def signalling(client: AnamClient) -> SignallingClient:
    assert client._streaming_client is not None
    assert client._streaming_client._signalling_client is not None
    return client._streaming_client._signalling_client


def heartbeats_on(engine: FakeEngine, index: int) -> int:
    return len(engine.received_on(index, "heartbeat"))


async def round_trip_on(engine: FakeEngine, index: int) -> None:
    await engine.wait_for(lambda: heartbeats_on(engine, index) >= 1)


async def drop_and_wait_for_gap(engine: FakeEngine, client: AnamClient) -> None:
    sc = signalling(client)
    engine.hold_handshake = asyncio.Event()
    await engine.drop("abort")
    await engine.wait_for(lambda: sc._ws is None)


async def peer_connected(engine: FakeEngine, client: AnamClient) -> None:
    assert client._streaming_client is not None
    pc = client._streaming_client._peer_connection
    assert pc is not None
    await engine.wait_for(lambda: pc.connectionState == "connected", timeout=5.0)


def end_gap(engine: FakeEngine) -> None:
    assert engine.hold_handshake is not None
    engine.hold_handshake.set()


async def exhaust_retries(engine: FakeEngine) -> None:
    engine.refuse_new = True
    await engine.drop("abort")


def all_tasks_done(sc: SignallingClient) -> bool:
    return all(task.done() for task in sc._owned_tasks())


@pytest.mark.asyncio
async def test_drop_keeps_peer_connection_and_sends_one_offer(engine: FakeEngine) -> None:
    sc = StreamingClient(session_info_for(engine, heartbeat_interval_seconds=0.1))
    try:
        await sc.connect(timeout=5.0)
        pc = sc._peer_connection
        assert pc is not None
        await engine.wait_for(lambda: pc.connectionState == "connected")

        await engine.drop("abort")
        await round_trip_on(engine, 1)

        assert sc._peer_connection is pc
        assert len(engine.received_actions("offer")) == 1
    finally:
        await sc.close()


@pytest.mark.asyncio
async def test_repeated_open_callback_keeps_peer_connection(engine: FakeEngine) -> None:
    sc = StreamingClient(session_info_for(engine))
    try:
        await sc.connect(timeout=5.0)
        pc = sc._peer_connection

        await sc._on_signalling_open()

        assert sc._peer_connection is pc
        assert len(engine.received_actions("offer")) == 1
    finally:
        await sc.close()


@pytest.mark.asyncio
async def test_streaming_connect_raises_when_signalling_ends(engine: FakeEngine) -> None:
    engine.answer_offers = "none"
    sc = StreamingClient(session_info_for(engine, max_reconnection_attempts=1))
    connecting = asyncio.create_task(sc.connect(timeout=30.0))
    await engine.wait_for(lambda: len(engine.received_actions("offer")) == 1)

    await exhaust_retries(engine)

    with pytest.raises(SessionError):
        await asyncio.wait_for(connecting, 3.0)
    assert sc._peer_connection is None


@pytest.mark.asyncio
async def test_negotiated_peer_survives_drop(engine: FakeEngine, anam: AnamClient) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    session = await anam.connect_async()
    assert anam._streaming_client is not None
    pc = anam._streaming_client._peer_connection
    assert pc is not None
    await engine.wait_for(lambda: pc.connectionState == "connected")
    frames = session.video_frames()
    await asyncio.wait_for(frames.__anext__(), 5.0)

    await engine.drop("abort")
    await round_trip_on(engine, 1)
    assert pc.connectionState == "connected"
    await asyncio.wait_for(frames.__anext__(), 5.0)
    assert closed == []

    reader = asyncio.create_task(drain(frames))
    await session.close()
    await asyncio.wait_for(reader, 2.0)
    assert [event[0] for event in closed] == ["normal"]


async def drain(frames: AsyncIterator[Any]) -> None:
    async for _ in frames:
        pass


@pytest.mark.asyncio
async def test_recovered_drop_emits_no_connection_closed(
    engine: FakeEngine, anam: AnamClient
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    session = await anam.connect_async()

    await engine.drop("abort")
    await round_trip_on(engine, 1)
    await engine.drop("clean")
    await round_trip_on(engine, 2)

    assert closed == []
    assert anam.is_streaming
    assert session.is_active


@pytest.mark.asyncio
async def test_retries_exhausted_emits_signalling_failure_once_and_tears_down(
    engine: FakeEngine, anam: AnamClient
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    session = await anam.connect_async()
    assert anam._streaming_client is not None
    pc = anam._streaming_client._peer_connection
    assert pc is not None

    await exhaust_retries(engine)
    await asyncio.wait_for(session.wait_until_closed(), 2.0)
    await engine.wait_for(lambda: pc.connectionState == "closed")

    assert [event[0] for event in closed] == ["signalling_failure"]
    assert session.is_active is False
    assert anam.is_streaming is False
    assert await stays(lambda: len(closed) == 1, 0.2)


@pytest.mark.asyncio
async def test_close_after_exhaustion_does_not_emit_again(
    engine: FakeEngine, anam: AnamClient
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    session = await anam.connect_async()

    await exhaust_retries(engine)
    await asyncio.wait_for(session.wait_until_closed(), 2.0)
    await session.close()

    assert [event[0] for event in closed] == ["signalling_failure"]


@pytest.mark.asyncio
async def test_close_inside_connection_closed_listener(
    engine: FakeEngine, anam: AnamClient
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    session = await anam.connect_async()
    sc = signalling(anam)

    async def close_session(code: str, reason: str | None) -> None:
        await session.close()

    anam.add_listener(AnamEvent.CONNECTION_CLOSED, close_session)
    await exhaust_retries(engine)

    await engine.wait_for(lambda: len(closed) == 1 and all_tasks_done(sc))
    assert anam._streaming_client is None
    assert await stays(lambda: len(closed) == 1, 0.2)


@pytest.mark.asyncio
async def test_self_removing_listener_does_not_skip_session(
    engine: FakeEngine, anam: AnamClient
) -> None:
    def remove_self(code: str, reason: str | None) -> None:
        anam.remove_listener(AnamEvent.CONNECTION_CLOSED, remove_self)

    anam.add_listener(AnamEvent.CONNECTION_CLOSED, remove_self)
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    session = await anam.connect_async()

    await exhaust_retries(engine)

    await asyncio.wait_for(session.wait_until_closed(), 2.0)
    await engine.wait_for(lambda: len(closed) == 1)


@pytest.mark.asyncio
async def test_endsession_then_clean_close_no_reconnect(
    engine: FakeEngine, anam: AnamClient
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    session = await anam.connect_async()

    await engine.send({"actionType": "endsession", "sessionId": SESSION_ID, "payload": "bye"})
    await engine.drop("clean")
    await asyncio.wait_for(session.wait_until_closed(), 2.0)

    assert [event[0] for event in closed] == ["server_closed"]
    assert await stays(lambda: engine.connect_attempts == 1 and len(closed) == 1, 0.3)


@pytest.mark.asyncio
async def test_terminal_during_connect_raises(engine: FakeEngine, anam: AnamClient) -> None:
    engine.answer_offers = "none"
    connecting = asyncio.create_task(anam.connect_async())
    await engine.wait_for(lambda: len(engine.received_actions("offer")) == 1)

    await exhaust_retries(engine)

    with pytest.raises(SessionError):
        await asyncio.wait_for(connecting, 3.0)
    assert anam.is_streaming is False
    assert anam._streaming_client is None


@pytest.mark.asyncio
async def test_stale_streaming_client_callback_ignored(
    engine: FakeEngine, anam: AnamClient
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    await anam.connect_async()
    old = anam._streaming_client
    assert old is not None and old._on_connection_closed is not None
    stale_callback = old._on_connection_closed
    await anam.close()

    session = await anam.connect_async()
    await stale_callback("webrtc_failure", None)

    assert session.is_active
    assert anam.is_streaming
    assert [event[0] for event in closed] == ["normal"]


@pytest.mark.asyncio
async def test_session_ready_replayed_emits_once(engine: FakeEngine, anam: AnamClient) -> None:
    ready = record(anam, AnamEvent.SESSION_READY)
    interrupted = record(anam, AnamEvent.TALK_STREAM_INTERRUPTED)
    await anam.connect_async()

    for _ in range(2):
        await engine.send({"actionType": "sessionready", "sessionId": SESSION_ID, "payload": {}})
    await engine.send(
        {
            "actionType": "talkinputstreaminterrupted",
            "sessionId": SESSION_ID,
            "payload": {"correlationId": "sync"},
        }
    )
    await engine.wait_for(lambda: interrupted == [("sync",)])

    assert ready == [()]


@pytest.mark.asyncio
async def test_talk_chunk_in_gap_fails_stream(engine: FakeEngine, anam: AnamClient) -> None:
    session = await anam.connect_async()
    stream = session.create_talk_stream()
    seen: list[tuple[MessageDeliveryFailure, TalkMessageStreamState]] = []

    def on_failed(failure: MessageDeliveryFailure) -> None:
        seen.append((failure, stream.state))

    anam.add_listener(AnamEvent.MESSAGE_DELIVERY_FAILED, on_failed)
    await drop_and_wait_for_gap(engine, anam)
    await stream.send("hello")

    assert stream.state is TalkMessageStreamState.DELIVERY_FAILED
    await engine.wait_for(lambda: len(seen) == 1)
    assert seen == [
        (
            MessageDeliveryFailure("talk_stream", stream.correlation_id),
            TalkMessageStreamState.DELIVERY_FAILED,
        )
    ]
    with pytest.raises(RuntimeError, match="not in an active state"):
        await stream.send("more")
    end_gap(engine)
    await round_trip_on(engine, 1)
    assert engine.received_actions("talkstream") == []
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["end", "end_of_speech"])
async def test_final_chunk_fails_after_end(
    engine: FakeEngine, anam: AnamClient, finish: str
) -> None:
    failed = record(anam, AnamEvent.MESSAGE_DELIVERY_FAILED)
    session = await anam.connect_async()
    engine.answer_heartbeats = False
    stream = session.create_talk_stream()

    if finish == "end":
        await stream.send("hello")
        await stream.end()
    else:
        await stream.send("hello", end_of_speech=True)
    assert stream.state is TalkMessageStreamState.ENDED
    await engine.wait_for(lambda: len(engine.received_actions("talkstream")) >= 1)
    await engine.drop("abort")

    await engine.wait_for(lambda: len(failed) == 1)
    assert failed == [(MessageDeliveryFailure("talk_stream", stream.correlation_id),)]
    assert stream.state is TalkMessageStreamState.DELIVERY_FAILED
    await round_trip_on(engine, 1)
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_webrtc_closed_and_signalling_failure_emit_once(
    engine: FakeEngine, anam: AnamClient
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    session = await anam.connect_async()
    sc = signalling(anam)

    await peer_connected(engine, anam)
    await engine.close_peer()
    await asyncio.wait_for(session.wait_until_closed(), 2.0)
    await exhaust_retries(engine)
    await engine.wait_for(lambda: sc.is_permanently_closed)

    assert await stays(lambda: [event[0] for event in closed] == ["webrtc_failure"], 0.2)


@pytest.mark.asyncio
async def test_endsession_racing_peer_close_reports_server_closed(
    engine: FakeEngine, anam: AnamClient
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    listener_started = asyncio.Event()
    release = asyncio.Event()

    async def slow_ready() -> None:
        listener_started.set()
        await release.wait()

    anam.add_listener(AnamEvent.SESSION_READY, slow_ready)
    session = await anam.connect_async()
    sc = signalling(anam)
    await engine.send({"actionType": "sessionready", "sessionId": SESSION_ID, "payload": {}})
    await asyncio.wait_for(listener_started.wait(), 2.0)

    await peer_connected(engine, anam)
    await engine.send({"actionType": "endsession", "sessionId": SESSION_ID, "payload": "bye"})
    await engine.close_peer()
    await asyncio.wait_for(session.wait_until_closed(), 2.0)
    assert sc.server_ended
    release.set()

    await engine.wait_for(lambda: all_tasks_done(sc))
    assert [event[0] for event in closed] == ["server_closed"]


@pytest.mark.asyncio
async def test_reconnect_inside_close_listener_keeps_new_session(
    engine: FakeEngine, anam: AnamClient
) -> None:
    sessions: list[Session] = []

    async def reconnect(code: str, reason: str | None) -> None:
        if code == "normal" and not sessions:
            sessions.append(await anam.connect_async())

    anam.add_listener(AnamEvent.CONNECTION_CLOSED, reconnect)
    first = await anam.connect_async()
    anam._message_history.append(Message(id="old", role=MessageRole.USER, content="hi"))

    await first.close()

    assert len(sessions) == 1
    assert first.is_active is False
    assert sessions[0].is_active
    assert anam.is_streaming
    assert anam.session_id == SESSION_ID
    assert anam.get_message_history() == []
    new_signalling = signalling(anam)
    assert not new_signalling.is_permanently_closed
    await engine.wait_for(lambda: heartbeats_on(engine, 1) >= 1)


@pytest.mark.asyncio
async def test_signalling_failure_ends_pending_frame_read(
    engine: FakeEngine, anam: AnamClient
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    session = await anam.connect_async()
    frames = session.video_frames()
    await asyncio.wait_for(frames.__anext__(), 5.0)
    reader = asyncio.create_task(drain(frames))

    await exhaust_retries(engine)

    await asyncio.wait_for(reader, 2.0)
    await engine.wait_for(lambda: len(closed) == 1)
    assert [event[0] for event in closed] == ["signalling_failure"]


@pytest.mark.asyncio
async def test_close_during_connect_stops_it(engine: FakeEngine, anam: AnamClient) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    engine.answer_offers = "none"
    connecting = asyncio.create_task(anam.connect_async())
    await engine.wait_for(lambda: len(engine.received_actions("offer")) == 1)

    await anam.close()

    with pytest.raises(SessionError):
        await asyncio.wait_for(connecting, 3.0)
    assert anam.is_streaming is False
    assert anam.session_id is None
    assert closed == []
