import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiortc import RTCPeerConnection

from anam import (
    AgentAudioInputConfig,
    AnamClient,
    AnamEvent,
    Message,
    MessageDeliveryFailure,
    MessageRole,
    TalkMessageStreamState,
)
from anam._api import CoreApiClient
from anam._heartbeat_monitor import HeartbeatMonitor
from anam._signalling import HeartbeatAcks, SignallingClient
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
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    engine.answer_offers = "none"
    connecting = asyncio.create_task(anam.connect_async())
    await engine.wait_for(lambda: len(engine.received_actions("offer")) == 1)

    await exhaust_retries(engine)

    with pytest.raises(SessionError):
        await asyncio.wait_for(connecting, 3.0)
    assert anam.is_streaming is False
    assert anam._streaming_client is None
    assert closed == []


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
    await engine.wait_for(lambda: sc.server_ended)
    await engine.close_peer()
    await asyncio.wait_for(session.wait_until_closed(), 2.0)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["create_offer", "send_offer"])
async def test_close_while_offer_is_built_raises_session_error(
    engine: FakeEngine, anam: AnamClient, monkeypatch: pytest.MonkeyPatch, gate: str
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    reached = asyncio.Event()
    release = asyncio.Event()
    target: Any = RTCPeerConnection if gate == "create_offer" else SignallingClient
    name = "createOffer" if gate == "create_offer" else "send_offer"
    original = getattr(target, name)

    async def gated(self: Any, *args: Any, **kwargs: Any) -> Any:
        reached.set()
        await release.wait()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(target, name, gated)
    connecting = asyncio.create_task(anam.connect_async())
    await asyncio.wait_for(reached.wait(), 2.0)
    sc = anam._streaming_client
    assert sc is not None

    closing = asyncio.create_task(anam.close())
    await engine.wait_for(lambda: sc._peer_connection is None)
    release.set()
    await asyncio.wait_for(closing, 2.0)

    with pytest.raises(SessionError):
        await asyncio.wait_for(connecting, 3.0)
    assert sc._peer_connection is None
    assert sc._signalling_client is None
    assert closed == []


@pytest.mark.asyncio
async def test_session_ready_replayed_after_reconnect_emits_once(
    engine: FakeEngine, anam: AnamClient
) -> None:
    ready = record(anam, AnamEvent.SESSION_READY)
    interrupted = record(anam, AnamEvent.TALK_STREAM_INTERRUPTED)
    await anam.connect_async()
    await engine.send({"actionType": "sessionready", "sessionId": SESSION_ID, "payload": {}})
    await engine.wait_for(lambda: ready == [()])

    await engine.drop("abort")
    await round_trip_on(engine, 1)
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


async def end_session(engine: FakeEngine, ending: str) -> str:
    if ending == "endsession":
        await engine.send({"actionType": "endsession", "sessionId": SESSION_ID, "payload": "bye"})
        return "server_closed"
    await exhaust_retries(engine)
    return "signalling_failure"


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["endsession", "retries"])
async def test_async_close_listeners_finish_with_context_manager(
    engine: FakeEngine, anam: AnamClient, ending: str
) -> None:
    finished: list[str] = []

    def listener(name: str) -> Callable[[str, str | None], Awaitable[None]]:
        async def on_closed(code: str, reason: str | None) -> None:
            await asyncio.sleep(0.05)
            finished.append(f"{name}:{code}")

        return on_closed

    anam.add_listener(AnamEvent.CONNECTION_CLOSED, listener("a"))
    anam.add_listener(AnamEvent.CONNECTION_CLOSED, listener("b"))
    started = asyncio.Event()

    async def run() -> None:
        async with anam.connect() as session:
            started.set()
            await session.wait_until_closed()

    runner = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 5.0)
    code = await end_session(engine, ending)

    await asyncio.wait_for(runner, 3.0)
    assert finished == [f"a:{code}", f"b:{code}"]


@pytest.mark.asyncio
async def test_listener_gathering_session_close_finishes(
    engine: FakeEngine, anam: AnamClient
) -> None:
    done: list[str] = []
    session = await anam.connect_async()

    async def on_closed(code: str, reason: str | None) -> None:
        await asyncio.gather(session.close())
        done.append(code)

    anam.add_listener(AnamEvent.CONNECTION_CLOSED, on_closed)
    await exhaust_retries(engine)

    await engine.wait_for(lambda: done == ["signalling_failure"])
    assert anam._streaming_client is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["endsession", "retries", "close"])
async def test_listener_joining_frame_reader_finishes(
    engine: FakeEngine, anam: AnamClient, ending: str
) -> None:
    session = await anam.connect_async()
    frames = session.video_frames()
    await asyncio.wait_for(frames.__anext__(), 5.0)
    reader = asyncio.create_task(drain(frames))
    joined: list[str] = []

    async def on_closed(code: str, reason: str | None) -> None:
        await reader
        joined.append(code)

    anam.add_listener(AnamEvent.CONNECTION_CLOSED, on_closed)
    if ending == "close":
        await asyncio.wait_for(anam.close(), 3.0)
        code = "normal"
    else:
        code = await end_session(engine, ending)

    await engine.wait_for(lambda: joined == [code], timeout=3.0)


@pytest.mark.asyncio
async def test_failure_handler_resending_does_not_recurse(
    engine: FakeEngine, anam: AnamClient
) -> None:
    session = await anam.connect_async()
    stream = session.create_talk_stream()
    seen: list[MessageDeliveryFailure] = []
    rejected: list[str] = []
    ticks = 0

    async def on_failed(failure: MessageDeliveryFailure) -> None:
        seen.append(failure)
        try:
            await stream.send("again")
        except RuntimeError:
            rejected.append(failure.correlation_id)

    async def tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    anam.add_listener(AnamEvent.MESSAGE_DELIVERY_FAILED, on_failed)
    ticker = asyncio.create_task(tick())
    try:
        await drop_and_wait_for_gap(engine, anam)
        before = ticks
        await stream.send("hello")
        await engine.wait_for(lambda: len(seen) == 1)

        assert ticks > before
        assert rejected == [stream.correlation_id]
        assert await stays(lambda: len(seen) == 1, 0.1)
    finally:
        ticker.cancel()
    end_gap(engine)
    await round_trip_on(engine, 1)
    assert engine.received_actions("talkstream") == []


@pytest.mark.asyncio
async def test_close_during_backoff_stops_reconnect_before_listeners(
    engine: FakeEngine, anam: AnamClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SignallingClient, "RECONNECT_BACKOFF_SECONDS", 0.5)
    await anam.connect_async()
    sc = signalling(anam)
    release = asyncio.Event()
    attempts_at_listener: list[int] = []

    async def on_closed(code: str, reason: str | None) -> None:
        attempts_at_listener.append(engine.connect_attempts)
        await release.wait()

    anam.add_listener(AnamEvent.CONNECTION_CLOSED, on_closed)
    await engine.drop("abort")
    await engine.wait_for(lambda: sc._ws is None)
    first = asyncio.create_task(anam.close())
    await engine.wait_for(lambda: attempts_at_listener == [1])
    second = asyncio.create_task(anam.close())

    assert sc.is_permanently_closed
    assert await stays(lambda: engine.connect_attempts == 1 and not second.done(), 0.7)
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 2.0)
    assert all_tasks_done(sc)
    assert anam._streaming_client is None


@pytest.mark.asyncio
async def test_failed_startup_leaves_replacement_session_alone(
    engine: FakeEngine, anam: AnamClient
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    engine.answer_offers = "none"
    first = asyncio.create_task(anam.connect_async())
    await engine.wait_for(lambda: len(engine.received_actions("offer")) == 1)
    first_client = anam._streaming_client
    assert first_client is not None

    engine.answer_offers = "aiortc"
    second = await anam.connect_async()
    await first_client.close()

    with pytest.raises(SessionError):
        await asyncio.wait_for(first, 3.0)
    assert second.is_active
    assert anam.is_streaming
    assert anam.session_id == SESSION_ID
    assert anam._streaming_client not in (None, first_client)
    assert closed == []


@pytest.mark.asyncio
async def test_failure_marks_every_stream_and_new_stream_reuses_id(
    engine: FakeEngine, anam: AnamClient
) -> None:
    failed = record(anam, AnamEvent.MESSAGE_DELIVERY_FAILED)
    session = await anam.connect_async()
    first = session.create_talk_stream("shared")
    second = session.create_talk_stream("shared")

    await drop_and_wait_for_gap(engine, anam)
    await first.send("hello")
    assert first.state is TalkMessageStreamState.DELIVERY_FAILED
    assert second.state is TalkMessageStreamState.DELIVERY_FAILED
    await engine.wait_for(lambda: len(failed) == 1)
    end_gap(engine)
    await round_trip_on(engine, 1)

    third = session.create_talk_stream("shared")
    await third.send("again")
    await engine.wait_for(lambda: len(engine.received_actions("talkstream")) == 1)
    assert third.state is TalkMessageStreamState.STREAMING
    assert await stays(lambda: len(failed) == 1, 0.1)


@pytest.mark.asyncio
async def test_buffered_chunk_reported_before_webrtc_failure(
    engine: FakeEngine, anam: AnamClient
) -> None:
    events: list[str] = []
    anam.add_listener(AnamEvent.MESSAGE_DELIVERY_FAILED, lambda failure: events.append("failed"))
    anam.add_listener(AnamEvent.CONNECTION_CLOSED, lambda code, reason: events.append(code))
    session = await anam.connect_async()
    sc = signalling(anam)
    await engine.send({"actionType": "sessionready", "sessionId": SESSION_ID, "payload": {}})
    await engine.wait_for(lambda: sc._heartbeat_acks is HeartbeatAcks.NO)
    await peer_connected(engine, anam)

    await drop_and_wait_for_gap(engine, anam)
    stream = session.create_talk_stream()
    await stream.send("hello")
    assert len(sc._send_buffer) == 1
    await engine.close_peer()

    await engine.wait_for(lambda: "webrtc_failure" in events)
    assert events == ["failed", "webrtc_failure"]
    assert stream.state is TalkMessageStreamState.DELIVERY_FAILED


@pytest.mark.asyncio
async def test_close_inside_terminal_failure_listener_still_reports_close(
    engine: FakeEngine, anam: AnamClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(HeartbeatMonitor, "ACK_TIMEOUT_SECONDS", 5.0)
    events: list[str] = []
    session = await anam.connect_async()
    engine.answer_heartbeats = False

    async def on_failed(failure: MessageDeliveryFailure) -> None:
        events.append("failed")
        await session.close()

    anam.add_listener(AnamEvent.MESSAGE_DELIVERY_FAILED, on_failed)
    anam.add_listener(AnamEvent.CONNECTION_CLOSED, lambda code, reason: events.append(code))
    stream = session.create_talk_stream()
    await stream.send("hello")
    await engine.wait_for(lambda: len(engine.received_actions("talkstream")) == 1)
    await engine.send({"actionType": "endsession", "sessionId": SESSION_ID, "payload": "bye"})

    await engine.wait_for(lambda: "server_closed" in events)
    assert await stays(lambda: events == ["failed", "server_closed"], 0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["retries", "endsession"])
async def test_pending_failures_reported_before_connection_closed(
    engine: FakeEngine, anam: AnamClient, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    monkeypatch.setattr(HeartbeatMonitor, "ACK_TIMEOUT_SECONDS", 5.0)
    events: list[str] = []
    anam.add_listener(
        AnamEvent.MESSAGE_DELIVERY_FAILED,
        lambda failure: events.append(f"failed:{failure.correlation_id}"),
    )
    anam.add_listener(AnamEvent.CONNECTION_CLOSED, lambda code, reason: events.append(code))
    session = await anam.connect_async()
    first = session.create_talk_stream("a")
    second = session.create_talk_stream("b")

    if ending == "retries":
        await engine.send({"actionType": "sessionready", "sessionId": SESSION_ID, "payload": {}})
        await engine.wait_for(lambda: signalling(anam)._heartbeat_acks is HeartbeatAcks.NO)
        await drop_and_wait_for_gap(engine, anam)
        await first.send("one")
        await first.send("two")
        await second.send("three")
        engine.refuse_new = True
        end_gap(engine)
        expected = ["failed:a", "failed:b", "signalling_failure"]
    else:
        engine.answer_heartbeats = False
        await first.send("one")
        await engine.wait_for(lambda: len(engine.received_actions("talkstream")) == 1)
        await engine.send({"actionType": "endsession", "sessionId": SESSION_ID, "payload": "bye"})
        expected = ["failed:a", "server_closed"]

    await engine.wait_for(lambda: expected[-1] in events)
    assert events == expected


@pytest.mark.asyncio
async def test_resend_on_new_stream_during_gap_yields_and_recovers(
    engine: FakeEngine, anam: AnamClient
) -> None:
    limit = 5000
    session = await anam.connect_async()
    ticks = 0
    ticks_at_failure: list[int] = []

    async def on_failed(failure: MessageDeliveryFailure) -> None:
        ticks_at_failure.append(ticks)
        if len(ticks_at_failure) < limit:
            await session.send_talk_stream("again")

    async def tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    anam.add_listener(AnamEvent.MESSAGE_DELIVERY_FAILED, on_failed)
    ticker = asyncio.create_task(tick())
    try:
        await drop_and_wait_for_gap(engine, anam)
        await session.send_talk_stream("hello")
        await engine.wait_for(lambda: len(ticks_at_failure) >= 3)
        end_gap(engine)

        await engine.wait_for(lambda: len(engine.received_actions("talkstream")) == 1)
    finally:
        ticker.cancel()
    failures = len(ticks_at_failure)
    assert failures < limit
    assert all(b > a for a, b in zip(ticks_at_failure, ticks_at_failure[1:]))
    assert await stays(lambda: len(ticks_at_failure) == failures, 0.2)


@pytest.mark.asyncio
async def test_listener_cancelling_worker_that_closes_finishes(
    engine: FakeEngine, anam: AnamClient
) -> None:
    session = await anam.connect_async()
    done: list[str] = []

    async def work() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            await session.close()

    worker = asyncio.create_task(work())

    async def on_closed(code: str, reason: str | None) -> None:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        done.append(code)

    anam.add_listener(AnamEvent.CONNECTION_CLOSED, on_closed)
    await engine.send({"actionType": "endsession", "sessionId": SESSION_ID, "payload": "bye"})

    await engine.wait_for(lambda: done == ["server_closed"], timeout=3.0)


@pytest.mark.asyncio
async def test_close_after_cancelled_close_waits_for_teardown(
    engine: FakeEngine, anam: AnamClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await anam.connect_async()
    streaming = anam._streaming_client
    assert streaming is not None
    entered = asyncio.Event()
    release = asyncio.Event()
    original = SignallingClient.close

    async def slow_close(self: SignallingClient) -> None:
        entered.set()
        await release.wait()
        await original(self)

    monkeypatch.setattr(SignallingClient, "close", slow_close)
    first = asyncio.create_task(anam.close())
    await asyncio.wait_for(entered.wait(), 2.0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(anam.close())
    assert await stays(lambda: not second.done(), 0.2)
    release.set()
    await asyncio.wait_for(second, 3.0)
    assert streaming._peer_connection is None
    assert anam._streaming_client is None


@pytest.mark.asyncio
async def test_listener_awaiting_cancelled_child_does_not_stop_close_event(
    engine: FakeEngine, anam: AnamClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(HeartbeatMonitor, "ACK_TIMEOUT_SECONDS", 5.0)
    events: list[str] = []

    async def await_cancelled_child() -> None:
        child = asyncio.create_task(asyncio.sleep(10))
        child.cancel()
        await child

    async def on_failed(failure: MessageDeliveryFailure) -> None:
        events.append("failed")
        await await_cancelled_child()

    async def on_closed(code: str, reason: str | None) -> None:
        events.append(f"first:{code}")
        await await_cancelled_child()

    anam.add_listener(AnamEvent.MESSAGE_DELIVERY_FAILED, on_failed)
    anam.add_listener(AnamEvent.CONNECTION_CLOSED, on_closed)
    anam.add_listener(AnamEvent.CONNECTION_CLOSED, lambda code, reason: events.append(code))
    session = await anam.connect_async()
    engine.answer_heartbeats = False
    await session.create_talk_stream().send("hello")
    await engine.wait_for(lambda: len(engine.received_actions("talkstream")) == 1)
    await engine.send({"actionType": "endsession", "sessionId": SESSION_ID, "payload": "bye"})

    await engine.wait_for(lambda: "server_closed" in events)
    assert events == ["failed", "first:server_closed", "server_closed"]
    assert not anam.is_streaming


@pytest.mark.asyncio
async def test_close_event_waits_for_failure_listeners_in_flight(
    engine: FakeEngine, anam: AnamClient
) -> None:
    events: list[str] = []
    release = asyncio.Event()

    async def first(failure: MessageDeliveryFailure) -> None:
        events.append("first:start")
        await release.wait()
        events.append("first:end")

    async def second(failure: MessageDeliveryFailure) -> None:
        events.append("second")

    anam.add_listener(AnamEvent.MESSAGE_DELIVERY_FAILED, first)
    anam.add_listener(AnamEvent.MESSAGE_DELIVERY_FAILED, second)
    anam.add_listener(AnamEvent.CONNECTION_CLOSED, lambda code, reason: events.append(code))
    session = await anam.connect_async()
    streaming = anam._streaming_client
    assert streaming is not None

    await drop_and_wait_for_gap(engine, anam)
    await session.create_talk_stream().send("hello")
    await engine.wait_for(lambda: events == ["first:start"])
    engine.refuse_new = True
    end_gap(engine)
    await engine.wait_for(lambda: streaming._peer_connection is None)

    assert await stays(lambda: events == ["first:start"], 0.1)
    release.set()
    await engine.wait_for(lambda: "signalling_failure" in events)
    assert events == ["first:start", "first:end", "second", "signalling_failure"]


@pytest.mark.asyncio
async def test_failure_listener_in_flight_can_close(engine: FakeEngine, anam: AnamClient) -> None:
    events: list[str] = []
    session = await anam.connect_async()

    async def on_failed(failure: MessageDeliveryFailure) -> None:
        events.append("failed")
        await session.close()
        events.append("closed")

    anam.add_listener(AnamEvent.MESSAGE_DELIVERY_FAILED, on_failed)
    anam.add_listener(AnamEvent.CONNECTION_CLOSED, lambda code, reason: events.append(code))
    await drop_and_wait_for_gap(engine, anam)
    await session.create_talk_stream().send("hello")

    await engine.wait_for(lambda: "normal" in events, timeout=3.0)
    assert events == ["failed", "closed", "normal"]


@pytest.mark.asyncio
async def test_watchdog_cap_ends_with_signalling_failure_before_listeners(
    engine: FakeEngine, anam: AnamClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(HeartbeatMonitor, "SUSPECT_AFTER_SECONDS", 10.0)
    events: list[str] = []
    session = await anam.connect_async()
    sc = signalling(anam)

    async def on_failed(failure: MessageDeliveryFailure) -> None:
        events.append("failed")
        await session.close()

    anam.add_listener(AnamEvent.MESSAGE_DELIVERY_FAILED, on_failed)
    anam.add_listener(AnamEvent.CONNECTION_CLOSED, lambda code, reason: events.append(code))
    await engine.send(
        {"actionType": "sessionready", "sessionId": SESSION_ID, "payload": {"sendsHeartbeatAcks": True}}
    )
    await engine.wait_for(lambda: sc._heartbeat_acks is HeartbeatAcks.YES)
    engine.answer_heartbeats = False
    await engine.wait_for(lambda: len(engine.connections) == 3 and sc._ws is not None)
    await session.create_talk_stream().send("hello")

    await engine.wait_for(lambda: len(events) == 2, timeout=5.0)
    assert events == ["failed", "signalling_failure"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["endsession", "close"])
async def test_talk_stream_rejects_sends_after_session_end(
    engine: FakeEngine, anam: AnamClient, ending: str
) -> None:
    closed = record(anam, AnamEvent.CONNECTION_CLOSED)
    failed = record(anam, AnamEvent.MESSAGE_DELIVERY_FAILED)
    session = await anam.connect_async()
    sc = signalling(anam)
    stream = session.create_talk_stream()
    await stream.send("one")
    await engine.wait_for(lambda: not sc._monitor.is_tracked(stream.correlation_id))
    if ending == "endsession":
        await engine.send({"actionType": "endsession", "sessionId": SESSION_ID, "payload": "bye"})
        await engine.wait_for(lambda: len(closed) == 1)
    else:
        await anam.close()

    with pytest.raises(RuntimeError):
        await stream.send("two")
    with pytest.raises(RuntimeError):
        await stream.end()
    assert stream.state is TalkMessageStreamState.STREAMING
    assert sc._dispatch_queue.empty()
    assert await stays(lambda: failed == [], 0.1)


def stall_after_write(monkeypatch: pytest.MonkeyPatch, action: str) -> tuple[asyncio.Event, asyncio.Event]:
    written = asyncio.Event()
    release = asyncio.Event()
    original = SignallingClient._write

    async def write(self: SignallingClient, ws: Any, message: dict[str, Any]) -> None:
        await original(self, ws, message)
        if message["actionType"] == action and not release.is_set():
            written.set()
            await release.wait()

    monkeypatch.setattr(SignallingClient, "_write", write)
    return written, release


async def cancel_after_write(
    call: Awaitable[None], written: asyncio.Event, release: asyncio.Event
) -> None:
    task = asyncio.ensure_future(call)
    await asyncio.wait_for(written.wait(), 2.0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", ["chunk", "end"])
async def test_cancel_after_audio_write_keeps_sequence(
    engine: FakeEngine, anam: AnamClient, monkeypatch: pytest.MonkeyPatch, cancelled: str
) -> None:
    session = await anam.connect_async()
    audio = session.create_agent_audio_input_stream(AgentAudioInputConfig())
    if cancelled == "chunk":
        written, release = stall_after_write(monkeypatch, "agentaudioinput")
        await cancel_after_write(audio.send_audio_chunk(b"\x00\x00"), written, release)
        expected = [0, 1]
    else:
        await audio.send_audio_chunk(b"\x00\x00")
        written, release = stall_after_write(monkeypatch, "agentaudioinputend")
        await cancel_after_write(audio.end_sequence(), written, release)
        expected = [0, 0]
    await audio.send_audio_chunk(b"\x00\x00")

    await engine.wait_for(lambda: len(engine.received_actions("agentaudioinput")) == 2)
    seqs = [m["payload"]["sequenceNumber"] for _, m in engine.received_actions("agentaudioinput")]
    assert seqs == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("end_of_speech", [False, True])
async def test_cancel_after_talk_write_keeps_stream_state(
    engine: FakeEngine, anam: AnamClient, monkeypatch: pytest.MonkeyPatch, end_of_speech: bool
) -> None:
    session = await anam.connect_async()
    stream = session.create_talk_stream()
    written, release = stall_after_write(monkeypatch, "talkstream")
    utterance = "68fd86b6-0b3a-4f42-bf92-5866cd84f8ac"
    await cancel_after_write(
        stream.send("one", end_of_speech=end_of_speech, utterance_id=utterance), written, release
    )

    if end_of_speech:
        assert stream.state is TalkMessageStreamState.ENDED
        with pytest.raises(RuntimeError):
            await stream.send("two")
        return
    assert stream.state is TalkMessageStreamState.STREAMING
    await stream.end()
    await engine.wait_for(lambda: len(engine.received_actions("talkstream")) == 2)
    payloads = [m["payload"] for _, m in engine.received_actions("talkstream")]
    assert [p["startOfSpeech"] for p in payloads] == [True, False]
    assert payloads[1]["utteranceId"] == utterance


@pytest.mark.asyncio
async def test_reused_id_after_refused_chunk_does_not_fail_again(
    engine: FakeEngine, anam: AnamClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(HeartbeatMonitor, "ACK_TIMEOUT_SECONDS", 5.0)
    failed = record(anam, AnamEvent.MESSAGE_DELIVERY_FAILED)
    session = await anam.connect_async()
    sc = signalling(anam)
    await engine.send(
        {"actionType": "sessionready", "sessionId": SESSION_ID, "payload": {"sendsHeartbeatAcks": True}}
    )
    await engine.wait_for(lambda: sc._heartbeat_acks is HeartbeatAcks.YES)
    engine.answer_heartbeats = False
    old = session.create_talk_stream()
    await old.send("one")
    await engine.wait_for(sc._monitor.is_suspect)
    await old.send("two")
    await engine.wait_for(lambda: len(failed) == 1)

    new = session.create_talk_stream(old.correlation_id)
    engine.answer_heartbeats = True
    await engine.drop("abort")
    await round_trip_on(engine, 1)

    assert new.state is TalkMessageStreamState.UNSTARTED
    assert await stays(lambda: len(failed) == 1, 0.2)


@pytest.mark.asyncio
async def test_stream_created_after_interruption_receipt_is_not_interrupted(
    engine: FakeEngine, anam: AnamClient
) -> None:
    blocked = asyncio.Event()
    release = asyncio.Event()

    async def on_ready() -> None:
        blocked.set()
        await release.wait()

    anam.add_listener(AnamEvent.SESSION_READY, on_ready)
    interrupted = record(anam, AnamEvent.TALK_STREAM_INTERRUPTED)
    session = await anam.connect_async()
    sc = signalling(anam)
    old = session.create_talk_stream()
    await old.send("one")
    await engine.send({"actionType": "sessionready", "sessionId": SESSION_ID, "payload": {}})
    await asyncio.wait_for(blocked.wait(), 2.0)
    await engine.send(
        {
            "actionType": "talkinputstreaminterrupted",
            "sessionId": SESSION_ID,
            "payload": {"correlationId": old.correlation_id},
        }
    )
    await engine.wait_for(lambda: old.correlation_id in sc._interrupted_cids)

    new = session.create_talk_stream(old.correlation_id)
    release.set()
    await engine.wait_for(lambda: len(interrupted) == 1)

    assert old.state is TalkMessageStreamState.INTERRUPTED
    assert new.state is TalkMessageStreamState.UNSTARTED
    await new.send("two")
    assert new.state is TalkMessageStreamState.STREAMING
