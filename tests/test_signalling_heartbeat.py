import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from unittest.mock import ANY, AsyncMock, Mock

import pytest
import pytest_asyncio

from anam._heartbeat_monitor import HeartbeatMonitor
from anam._signalling import HeartbeatAcks, SignallingClient
from anam.types import ConnectionClosedCode

from .fake_engine import SESSION_ID, FakeEngine, session_info_for, stays

ClientFactory = Callable[..., SignallingClient]
FailureCallback = Callable[[str], Awaitable[None]]


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
        yield fake


@pytest.fixture
def failures() -> list[str]:
    return []


@pytest_asyncio.fixture
async def make_client(engine: FakeEngine, failures: list[str]) -> AsyncIterator[ClientFactory]:
    created: list[SignallingClient] = []

    async def record_failure(correlation_id: str) -> None:
        failures.append(correlation_id)

    def factory(
        on_message: AsyncMock | None = None,
        on_end: Mock | None = None,
        on_failed: FailureCallback | None = None,
        **info: Any,
    ) -> SignallingClient:
        client = SignallingClient(
            session_info_for(engine, heartbeat_interval_seconds=0.1, **info),
            on_message=on_message or AsyncMock(),
            on_open=AsyncMock(),
            on_end=on_end or Mock(),
            on_talk_stream_delivery_failed=on_failed or record_failure,
        )
        created.append(client)
        return client

    yield factory
    for client in created:
        await client.close()


async def announce_acks(engine: FakeEngine, client: SignallingClient, acks: bool) -> None:
    payload: dict[str, Any] = {"sendsHeartbeatAcks": True} if acks else {}
    await engine.send({"actionType": "sessionready", "sessionId": SESSION_ID, "payload": payload})
    expected = HeartbeatAcks.YES if acks else HeartbeatAcks.NO
    await engine.wait_for(lambda: client._heartbeat_acks is expected)


async def talk(client: SignallingClient, correlation_id: str) -> None:
    await client.send_talk_stream_input("hello", correlation_id)


def talk_cids(engine: FakeEngine, index: int | None = None) -> list[str]:
    return [
        message["payload"]["correlationId"]
        for conn, message in engine.received_actions("talkstream")
        if index is None or conn == index
    ]


def heartbeat_seqs(engine: FakeEngine, index: int | None = None) -> list[int]:
    return [
        message["payload"]["seq"]
        for conn, message in engine.received_actions("heartbeat")
        if index is None or conn == index
    ]


def owned_tasks_done(client: SignallingClient) -> bool:
    return all(task.done() for task in client._owned_tasks())


async def round_trip_on(engine: FakeEngine, index: int) -> None:
    await engine.wait_for(lambda: len(heartbeat_seqs(engine, index)) >= 1)


async def drop_and_wait_for_gap(engine: FakeEngine, client: SignallingClient) -> None:
    engine.hold_handshake = asyncio.Event()
    await engine.drop("abort")
    await engine.wait_for(lambda: len(engine.connections) == 1 and client._ws is None)


def end_gap(engine: FakeEngine) -> None:
    assert engine.hold_handshake is not None
    engine.hold_handshake.set()


async def sync_dispatch(engine: FakeEngine, on_message: AsyncMock) -> None:
    await engine.send({"actionType": "sync", "payload": {}})
    await engine.wait_for(
        lambda: any(call.args[0].get("actionType") == "sync" for call in on_message.call_args_list)
    )


@pytest.mark.asyncio
async def test_heartbeats_carry_increasing_seq_across_reconnect(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    client = make_client()
    await client.connect()
    await engine.wait_for(lambda: len(heartbeat_seqs(engine, 0)) >= 2)

    await engine.drop("abort")
    await engine.wait_for(lambda: len(heartbeat_seqs(engine, 1)) >= 2)

    seqs = heartbeat_seqs(engine)
    assert seqs == sorted(set(seqs))
    assert max(heartbeat_seqs(engine, 0)) < min(heartbeat_seqs(engine, 1))


@pytest.mark.asyncio
async def test_chunk_confirmed_then_drop_reports_nothing(
    engine: FakeEngine, make_client: ClientFactory, failures: list[str]
) -> None:
    client = make_client()
    await client.connect()
    await announce_acks(engine, client, True)
    engine.hold_heartbeat_answers = asyncio.Event()

    await talk(client, "a")
    await engine.wait_for(lambda: talk_cids(engine, 0) == ["a"])
    assert client._monitor.is_tracked("a")
    engine.hold_heartbeat_answers.set()
    await engine.wait_for(lambda: not client._monitor.is_tracked("a"))
    await engine.drop("abort")
    await round_trip_on(engine, 1)

    assert await stays(lambda: failures == [], 0.1)


@pytest.mark.asyncio
async def test_ack_during_suspended_write_counts(
    engine: FakeEngine,
    make_client: ClientFactory,
    failures: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    await client.connect()
    await announce_acks(engine, client, True)
    ws = client._ws
    assert ws is not None
    original_send = ws.send
    release = asyncio.Event()
    gate_next_heartbeat = False

    async def gated_send(message: Any, **kwargs: Any) -> None:
        nonlocal gate_next_heartbeat
        await original_send(message, **kwargs)
        if gate_next_heartbeat and '"heartbeat"' in message and client._monitor.is_tracked("a"):
            gate_next_heartbeat = False
            await release.wait()

    monkeypatch.setattr(ws, "send", gated_send)
    gate_next_heartbeat = True
    await talk(client, "a")
    await engine.wait_for(lambda: not client._monitor.is_tracked("a"))
    assert client._write_started is not None
    release.set()

    assert await stays(lambda: failures == [] and len(engine.connections) == 1, 0.4)


@pytest.mark.asyncio
async def test_chunk_written_then_abnormal_drop_fails(
    engine: FakeEngine, make_client: ClientFactory, failures: list[str]
) -> None:
    engine.answer_heartbeats = False
    client = make_client()
    await client.connect()

    await talk(client, "a")
    await engine.wait_for(lambda: talk_cids(engine) == ["a"])
    await engine.drop("abort")
    await engine.wait_for(lambda: failures == ["a"])
    await round_trip_on(engine, 1)

    assert talk_cids(engine, 0) == ["a"]
    assert talk_cids(engine) == ["a"]
    assert await stays(lambda: failures == ["a"], 0.1)


@pytest.mark.asyncio
async def test_chunk_during_gap_fails_immediately(
    engine: FakeEngine, make_client: ClientFactory, failures: list[str]
) -> None:
    client = make_client()
    await client.connect()

    await drop_and_wait_for_gap(engine, client)
    await talk(client, "a")
    await engine.wait_for(lambda: failures == ["a"])
    end_gap(engine)
    await round_trip_on(engine, 1)

    assert talk_cids(engine) == []


@pytest.mark.asyncio
async def test_unanswered_heartbeat_replaces_socket(
    engine: FakeEngine, make_client: ClientFactory, failures: list[str]
) -> None:
    client = make_client()
    await client.connect()
    await announce_acks(engine, client, True)
    engine.answer_heartbeats = False

    await talk(client, "a")
    await engine.wait_for(
        lambda: len(engine.connections) == 2,
        timeout=HeartbeatMonitor.ACK_TIMEOUT_SECONDS + 0.5,
    )

    await engine.wait_for(lambda: failures == ["a"])
    assert await stays(lambda: failures == ["a"], 0.1)


@pytest.mark.asyncio
async def test_stalled_write_replaces_socket(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    client = make_client()
    await client.connect()
    ws = client._ws
    assert ws is not None

    ws.pause_writing()
    stalled = asyncio.create_task(client.send_agent_audio_input_end())
    await engine.wait_for(
        lambda: len(engine.connections) == 2,
        timeout=SignallingClient.WRITE_STALL_SECONDS + 0.3,
    )
    await asyncio.wait_for(stalled, 1.0)
    await round_trip_on(engine, 1)

    await client.send_agent_audio_input_end()
    await engine.wait_for(lambda: bool(engine.received_on(1, "agentaudioinputend")))


@pytest.mark.asyncio
async def test_unanswered_replacements_capped(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    on_end = Mock()
    client = make_client(on_end=on_end, max_reconnection_attempts=2)
    await client.connect()
    await announce_acks(engine, client, True)
    engine.answer_heartbeats = False

    await engine.wait_for(lambda: on_end.call_count == 1, timeout=5.0)
    on_end.assert_called_once_with(ConnectionClosedCode.SIGNALLING_FAILURE, ANY)

    assert len(engine.connections) == 3
    assert client.is_permanently_closed
    assert await stays(lambda: len(engine.connections) == 3, 0.2)
    await engine.wait_for(lambda: owned_tasks_done(client))


@pytest.mark.asyncio
async def test_slow_listener_does_not_replace_socket(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    listener_started = asyncio.Event()

    async def on_message(message: dict[str, Any]) -> None:
        if message.get("actionType") == "sessionready":
            listener_started.set()
            await asyncio.sleep(1.0)

    client = make_client(on_message=AsyncMock(side_effect=on_message))
    await client.connect()
    await engine.send(
        {
            "actionType": "sessionready",
            "sessionId": SESSION_ID,
            "payload": {"sendsHeartbeatAcks": True},
        }
    )
    await asyncio.wait_for(listener_started.wait(), 2.0)

    assert await stays(lambda: len(engine.connections) == 1, 1.2)


@pytest.mark.asyncio
async def test_ack_parsing_rejects_non_int_seq(
    engine: FakeEngine, make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(HeartbeatMonitor, "ACK_TIMEOUT_SECONDS", 5.0)
    engine.answer_heartbeats = False
    on_message = AsyncMock()
    client = make_client(on_message=on_message)
    await client.connect()
    await engine.wait_for(lambda: len(heartbeat_seqs(engine, 0)) >= 2)
    last = heartbeat_seqs(engine, 0)[-1]

    for payload in ({"seq": True}, {"seq": str(last)}, {"seq": float(last)}, {}, ""):
        await engine.send({"actionType": "heartbeat", "sessionId": SESSION_ID, "payload": payload})
    await sync_dispatch(engine, on_message)

    assert any(hb.seq <= last for hb in client._monitor._pending)
    forwarded = [call.args[0]["actionType"] for call in on_message.call_args_list]
    assert forwarded == ["sync"]

    await engine.send(
        {"actionType": "heartbeat", "sessionId": SESSION_ID, "payload": {"seq": last}}
    )
    await engine.wait_for(lambda: not any(hb.seq <= last for hb in client._monitor._pending))


@pytest.mark.asyncio
async def test_engine_without_acks_disables_tracking(
    engine: FakeEngine, make_client: ClientFactory, failures: list[str]
) -> None:
    client = make_client()
    await client.connect()
    await announce_acks(engine, client, False)

    await drop_and_wait_for_gap(engine, client)
    await talk(client, "a")
    end_gap(engine)
    await engine.wait_for(lambda: talk_cids(engine, 1) == ["a"])

    assert await stays(
        lambda: failures == [] and len(engine.connections) == 2,
        HeartbeatMonitor.ACK_TIMEOUT_SECONDS + 0.1,
    )


@pytest.mark.asyncio
async def test_interrupted_stream_not_reported(
    engine: FakeEngine,
    make_client: ClientFactory,
    failures: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(HeartbeatMonitor, "ACK_TIMEOUT_SECONDS", 5.0)
    engine.answer_heartbeats = False
    on_message = AsyncMock()
    client = make_client(on_message=on_message)
    await client.connect()

    await talk(client, "a")
    await engine.wait_for(lambda: talk_cids(engine) == ["a"])
    await engine.send(
        {
            "actionType": "talkinputstreaminterrupted",
            "sessionId": SESSION_ID,
            "payload": {"correlationId": "a"},
        }
    )
    await engine.wait_for(lambda: not client._monitor.is_tracked("a"))
    await engine.drop("abort")
    await round_trip_on(engine, 1)

    assert await stays(lambda: failures == [], 0.1)
    forwarded = [call.args[0]["actionType"] for call in on_message.call_args_list]
    assert "talkinputstreaminterrupted" in forwarded


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["gap", "drop"])
async def test_failure_callback_calls_close(
    engine: FakeEngine, make_client: ClientFactory, path: str
) -> None:
    holder: list[SignallingClient] = []
    reported: list[str] = []

    async def on_failed(correlation_id: str) -> None:
        reported.append(correlation_id)
        await holder[0].close()

    engine.answer_heartbeats = False
    client = make_client(on_failed=on_failed)
    holder.append(client)
    await client.connect()

    if path == "gap":
        await drop_and_wait_for_gap(engine, client)
        await asyncio.wait_for(talk(client, "a"), 2.0)
    else:
        await talk(client, "a")
        await engine.wait_for(lambda: talk_cids(engine) == ["a"])
        await engine.drop("abort")

    await engine.wait_for(lambda: reported == ["a"] and owned_tasks_done(client))
    assert client.is_permanently_closed


@pytest.mark.asyncio
async def test_stale_ack_after_reconnect_confirms_nothing(
    engine: FakeEngine, make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(HeartbeatMonitor, "ACK_TIMEOUT_SECONDS", 5.0)
    engine.answer_heartbeats = False
    on_message = AsyncMock()
    client = make_client(on_message=on_message)
    await client.connect()
    await engine.wait_for(lambda: len(heartbeat_seqs(engine, 0)) >= 1)
    stale = heartbeat_seqs(engine, 0)[-1]

    await engine.drop("abort")
    await round_trip_on(engine, 1)
    await talk(client, "a")
    await engine.wait_for(lambda: talk_cids(engine, 1) == ["a"])
    await engine.wait_for(
        lambda: any(hb.seq > stale for hb in client._monitor._pending)
        and not client._monitor.needs_probe()
    )
    client._unanswered_replacements = 1

    await engine.send(
        {"actionType": "heartbeat", "sessionId": SESSION_ID, "payload": {"seq": stale}}
    )
    await sync_dispatch(engine, on_message)
    assert client._monitor.is_tracked("a")
    assert client._unanswered_replacements == 1

    current = max(hb.seq for hb in client._monitor._pending)
    await engine.send(
        {"actionType": "heartbeat", "sessionId": SESSION_ID, "payload": {"seq": current}}
    )
    await engine.wait_for(lambda: not client._monitor.is_tracked("a"))
    assert client._unanswered_replacements == 0


@pytest.mark.asyncio
async def test_cancelled_chunk_write_reported_at_most_once(
    engine: FakeEngine, make_client: ClientFactory, failures: list[str]
) -> None:
    # pause_writing() only holds drain(); the bytes still reach the engine.
    engine.answer_heartbeats = False
    client = make_client()
    await client.connect()
    ws = client._ws
    assert ws is not None

    ws.pause_writing()
    sending = asyncio.create_task(talk(client, "a"))
    await engine.wait_for(
        lambda: client._monitor.is_tracked("a") and client._write_started is not None
    )
    sending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending

    await engine.wait_for(lambda: failures == ["a"], timeout=3.0)
    await round_trip_on(engine, 1)
    assert await stays(lambda: failures == ["a"], 0.2)


@pytest.mark.asyncio
async def test_send_for_failed_correlation_id_is_dropped(
    engine: FakeEngine, make_client: ClientFactory, failures: list[str]
) -> None:
    client = make_client()
    await client.connect()

    await drop_and_wait_for_gap(engine, client)
    await talk(client, "a")
    end_gap(engine)
    await round_trip_on(engine, 1)
    await talk(client, "a")
    await talk(client, "b")
    await engine.wait_for(lambda: talk_cids(engine, 1) == ["b"])

    assert await stays(lambda: failures == ["a"], 0.1)


@pytest.mark.asyncio
async def test_suspect_refusal_only_after_acks_confirmed(
    engine: FakeEngine,
    make_client: ClientFactory,
    failures: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(HeartbeatMonitor, "ACK_TIMEOUT_SECONDS", 5.0)
    engine.answer_heartbeats = False
    on_message = AsyncMock()
    client = make_client(on_message=on_message)
    await client.connect()
    await engine.wait_for(client._monitor.is_suspect)

    await talk(client, "a")
    await engine.wait_for(lambda: talk_cids(engine) == ["a"])
    assert failures == []

    await announce_acks(engine, client, True)
    assert client._monitor.is_suspect()
    await talk(client, "b")
    await engine.wait_for(lambda: failures == ["b"])
    await sync_dispatch(engine, on_message)
    assert talk_cids(engine) == ["a"]


@pytest.mark.asyncio
async def test_evicted_talk_chunk_is_reported(
    engine: FakeEngine, failures: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SignallingClient, "SEND_BUFFER_LIMIT", 2)

    async def record(correlation_id: str) -> None:
        failures.append(correlation_id)

    client = SignallingClient(session_info_for(engine), on_talk_stream_delivery_failed=record)

    for cid in ("a", "b", "c"):
        await talk(client, cid)

    assert [message["payload"]["correlationId"] for message in client._send_buffer] == ["b", "c"]
    await engine.wait_for(lambda: failures == ["a"])
    await client.close()


@pytest.mark.asyncio
async def test_chunk_buffered_before_first_open_is_tracked_on_flush(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    engine.answer_heartbeats = False
    client = make_client()
    await talk(client, "a")
    assert client._monitor.is_tracked("a") is False

    await client.connect()

    await engine.wait_for(lambda: talk_cids(engine, 0) == ["a"])
    assert client._monitor.is_tracked("a")


@pytest.mark.asyncio
async def test_terminal_close_hands_buffered_talk_chunks_to_take_failures(
    engine: FakeEngine, make_client: ClientFactory, failures: list[str]
) -> None:
    on_end = Mock()
    client = make_client(on_end=on_end, max_reconnection_attempts=1)
    await client.connect()
    await announce_acks(engine, client, False)

    await drop_and_wait_for_gap(engine, client)
    await talk(client, "a")
    await talk(client, "a")
    await talk(client, "b")
    engine.refuse_new = True
    end_gap(engine)

    await engine.wait_for(lambda: on_end.call_count == 1)
    assert client.take_failures(include_unconfirmed=True) == ["a", "b"]
    assert client._send_buffer == []
    assert failures == []


@pytest.mark.asyncio
async def test_interruption_behind_slow_listener_drops_later_chunks(
    engine: FakeEngine,
    make_client: ClientFactory,
    failures: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(HeartbeatMonitor, "ACK_TIMEOUT_SECONDS", 5.0)
    engine.answer_heartbeats = False
    release = asyncio.Event()

    async def slow(message: dict[str, Any]) -> None:
        if message.get("actionType") == "block":
            await release.wait()

    on_message = AsyncMock(side_effect=slow)
    client = make_client(on_message=on_message)
    await client.connect()
    await engine.send({"actionType": "block", "payload": {}})
    await engine.wait_for(lambda: on_message.await_count == 1)

    await talk(client, "a")
    await engine.wait_for(lambda: talk_cids(engine) == ["a"])
    await engine.send(
        {
            "actionType": "talkinputstreaminterrupted",
            "sessionId": SESSION_ID,
            "payload": {"correlationId": "a"},
        }
    )
    await engine.wait_for(lambda: not client._monitor.is_tracked("a"))
    await talk(client, "a")
    await engine.drop("abort")
    await round_trip_on(engine, 1)
    release.set()
    await sync_dispatch(engine, on_message)

    assert talk_cids(engine) == ["a"]
    assert failures == []


async def stall_until_replaced(engine: FakeEngine, client: SignallingClient) -> None:
    ws = client._ws
    assert ws is not None
    count = len(engine.connections)
    ws.pause_writing()
    stalled = asyncio.create_task(client.send_agent_audio_input_end())
    await engine.wait_for(
        lambda: len(engine.connections) == count + 1 or client.is_permanently_closed,
        timeout=SignallingClient.WRITE_STALL_SECONDS + 0.5,
    )
    await asyncio.wait_for(stalled, 1.0)
    if not client.is_permanently_closed:
        await engine.wait_for(lambda: client._ws is not None and client._ws is not ws)


@pytest.mark.asyncio
async def test_write_stalls_after_stable_period_do_not_give_up(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    on_end = Mock()
    client = make_client(on_end=on_end, max_reconnection_attempts=1)
    await client.connect()
    await announce_acks(engine, client, False)

    for _ in range(3):
        await asyncio.sleep(SignallingClient.STABLE_CONNECTION_SECONDS + 0.05)
        await stall_until_replaced(engine, client)

    assert len(engine.connections) == 4
    on_end.assert_not_called()


@pytest.mark.asyncio
async def test_quick_write_stalls_give_up(engine: FakeEngine, make_client: ClientFactory) -> None:
    on_end = Mock()
    client = make_client(on_end=on_end, max_reconnection_attempts=1)
    await client.connect()
    await announce_acks(engine, client, False)

    await stall_until_replaced(engine, client)
    await stall_until_replaced(engine, client)

    await engine.wait_for(lambda: on_end.call_count == 1)
    on_end.assert_called_once_with(ConnectionClosedCode.SIGNALLING_FAILURE, ANY)
    assert len(engine.connections) == 2
