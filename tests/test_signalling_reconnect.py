import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import ANY, AsyncMock, Mock

import pytest
import pytest_asyncio
from websockets.asyncio.client import ClientConnection

from anam._signalling import SignallingClient
from anam.types import AgentAudioInputPayload, ConnectionClosedCode

from .fake_engine import SESSION_ID, FakeEngine, session_info_for, stays

ClientFactory = Callable[..., SignallingClient]


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SignallingClient, "RECONNECT_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(SignallingClient, "STABLE_CONNECTION_SECONDS", 0.3)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[FakeEngine]:
    async with FakeEngine() as fake:
        yield fake


@pytest_asyncio.fixture
async def make_client(engine: FakeEngine) -> AsyncIterator[ClientFactory]:
    created: list[SignallingClient] = []

    def factory(
        on_message: AsyncMock | None = None,
        on_open: AsyncMock | None = None,
        on_end: Mock | None = None,
        **info: Any,
    ) -> SignallingClient:
        client = SignallingClient(
            session_info_for(engine, **info),
            on_message=on_message or AsyncMock(),
            on_open=on_open or AsyncMock(),
            on_end=on_end or Mock(),
        )
        created.append(client)
        return client

    yield factory
    for client in created:
        await client.close()


def audio(seq: int) -> AgentAudioInputPayload:
    return AgentAudioInputPayload(
        audio_data="AAAA", encoding="pcm_s16le", sample_rate=24000, channels=1, sequence_number=seq
    )


def audio_seqs(engine: FakeEngine, index: int) -> list[int]:
    return [
        message["payload"]["sequenceNumber"]
        for message in engine.received_on(index, "agentaudioinput")
    ]


def owned_tasks_done(client: SignallingClient) -> bool:
    return all(task.done() for task in client._owned_tasks())


async def drop_and_wait_for_gap(engine: FakeEngine, client: SignallingClient) -> None:
    engine.hold_handshake = asyncio.Event()
    await engine.drop("abort")
    await engine.wait_for(lambda: engine.connect_attempts == 2 and client._ws is None)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["abort", "clean"])
async def test_drop_reconnects_to_same_url(
    engine: FakeEngine, make_client: ClientFactory, mode: str
) -> None:
    on_open = AsyncMock()
    on_end = Mock()
    client = make_client(on_open=on_open, on_end=on_end)
    await client.connect()

    await engine.drop(mode)
    await engine.wait_for(lambda: bool(engine.received_on(1)))

    assert engine.paths == [f"/ws?session_id={SESSION_ID}"] * 2
    on_end.assert_not_called()
    on_open.assert_awaited_once()
    assert not client.is_permanently_closed


@pytest.mark.asyncio
async def test_retries_exhausted_calls_on_end_once(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    on_end = Mock()
    client = make_client(on_end=on_end, max_reconnection_attempts=3)
    await client.connect()

    engine.refuse_new = True
    await engine.drop("abort")
    await engine.wait_for(lambda: on_end.call_count == 1)

    assert await stays(lambda: engine.connect_attempts == 1 + 3, 0.1)
    on_end.assert_called_once_with(ConnectionClosedCode.SIGNALLING_FAILURE, ANY)
    assert client.is_permanently_closed


@pytest.mark.asyncio
async def test_open_then_drop_still_exhausts(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    on_end = Mock()
    client = make_client(on_end=on_end, max_reconnection_attempts=3)
    await client.connect()

    engine.drop_on_open = True
    await engine.drop("abort")
    await engine.wait_for(lambda: on_end.call_count == 1)

    assert await stays(lambda: engine.connect_attempts == 1 + 3, 0.1)
    on_end.assert_called_once()


@pytest.mark.asyncio
async def test_stable_socket_resets_attempts(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    on_end = Mock()
    client = make_client(on_end=on_end, max_reconnection_attempts=2)
    await client.connect()

    for index in range(1, 5):
        await asyncio.sleep(SignallingClient.STABLE_CONNECTION_SECONDS + 0.05)
        await engine.drop("abort")
        await engine.wait_for(lambda index=index: bool(engine.received_on(index)))

    assert len(engine.connections) == 5
    on_end.assert_not_called()


@pytest.mark.asyncio
async def test_close_during_backoff_stops_reconnect(
    engine: FakeEngine, make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SignallingClient, "RECONNECT_BACKOFF_SECONDS", 0.5)
    client = make_client()
    await client.connect()

    await engine.drop("abort")
    await engine.wait_for(lambda: client._ws is None)
    await client.close()

    assert await stays(lambda: engine.connect_attempts == 1, 0.8)
    assert owned_tasks_done(client)


@pytest.mark.asyncio
async def test_close_during_pending_handshake(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    client = make_client()
    await client.connect()

    await drop_and_wait_for_gap(engine, client)
    await client.close()
    assert engine.hold_handshake is not None
    engine.hold_handshake.set()

    assert await stays(lambda: engine.connect_attempts == 2, 0.3)
    await engine.wait_for(lambda: all(ws.state.name == "CLOSED" for ws in engine.connections))
    assert owned_tasks_done(client)


@pytest.mark.asyncio
async def test_close_started_from_on_end_does_not_hang(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    holder: list[SignallingClient] = []
    closes: list[asyncio.Task[None]] = []

    def on_end(code: ConnectionClosedCode, reason: str | None) -> None:
        closes.append(asyncio.create_task(holder[0].close()))

    client = make_client(on_end=Mock(side_effect=on_end), max_reconnection_attempts=1)
    holder.append(client)
    await client.connect()

    engine.refuse_new = True
    await engine.drop("abort")
    await engine.wait_for(lambda: len(closes) == 1)
    await asyncio.wait_for(closes[0], 2.0)
    await engine.wait_for(lambda: owned_tasks_done(client))


@pytest.mark.asyncio
async def test_close_from_on_message_callback_does_not_hang(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    holder: list[SignallingClient] = []

    async def on_message(message: dict[str, Any]) -> None:
        if message.get("actionType") == "test":
            await holder[0].close()

    client = make_client(on_message=AsyncMock(side_effect=on_message))
    holder.append(client)
    await client.connect()

    await engine.send({"actionType": "test", "payload": {}})
    await engine.wait_for(lambda: owned_tasks_done(client) and client._ws is None)
    assert client.is_permanently_closed


@pytest.mark.asyncio
async def test_endsession_ends_without_reconnect(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    on_end = Mock()
    client = make_client(on_end=on_end)
    await client.connect()

    await engine.send({"actionType": "endsession", "payload": "done"})
    await engine.wait_for(lambda: on_end.call_count == 1)
    await engine.drop("clean")

    assert await stays(lambda: engine.connect_attempts == 1, 0.3)
    assert client.server_ended
    on_end.assert_called_once_with(ConnectionClosedCode.SERVER_CLOSED, "done")


@pytest.mark.asyncio
async def test_messages_buffered_in_gap_flush_in_order(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    client = make_client()
    await client.connect()
    await client.send_agent_audio_input(audio(0))
    await engine.wait_for(lambda: audio_seqs(engine, 0) == [0])

    await drop_and_wait_for_gap(engine, client)
    for seq in (1, 2, 3):
        await client.send_agent_audio_input(audio(seq))
    await client.send_offer("sdp", "offer")
    assert engine.hold_handshake is not None
    engine.hold_handshake.set()

    await engine.wait_for(lambda: len(engine.received_actions("offer")) == 1)
    assert audio_seqs(engine, 1) == [1, 2, 3]
    assert audio_seqs(engine, 0) == [0]
    assert await stays(lambda: len(engine.received_actions("offer")) == 1, 0.1)


@pytest.mark.asyncio
async def test_send_waits_behind_flush(
    engine: FakeEngine, make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client()
    await client.connect()
    first_socket = client._ws
    flush_started = asyncio.Event()
    release_flush = asyncio.Event()
    original_send = ClientConnection.send

    async def gated_send(self: ClientConnection, message: Any, **kwargs: Any) -> None:
        if self is not first_socket and not flush_started.is_set():
            flush_started.set()
            await release_flush.wait()
        await original_send(self, message, **kwargs)

    await drop_and_wait_for_gap(engine, client)
    for seq in (1, 2):
        await client.send_agent_audio_input(audio(seq))
    monkeypatch.setattr(ClientConnection, "send", gated_send)
    assert engine.hold_handshake is not None
    engine.hold_handshake.set()
    await asyncio.wait_for(flush_started.wait(), 2.0)

    late = asyncio.create_task(client.send_agent_audio_input(audio(99)))
    assert await stays(lambda: not late.done(), 0.05)
    release_flush.set()
    await late

    await engine.wait_for(lambda: len(audio_seqs(engine, 1)) == 3)
    assert audio_seqs(engine, 1) == [1, 2, 99]


@pytest.mark.asyncio
async def test_failed_write_on_open_socket_is_not_rebuffered(
    engine: FakeEngine, make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client()
    await client.connect()
    ws = client._ws
    assert ws is not None
    original_send = ws.send

    async def failing_send(message: Any, **kwargs: Any) -> None:
        monkeypatch.setattr(ws, "send", original_send)
        raise OSError("write failed")

    monkeypatch.setattr(ws, "send", failing_send)
    await client.send_agent_audio_input(audio(1))
    await client.send_agent_audio_input(audio(2))

    await engine.wait_for(lambda: audio_seqs(engine, 0) == [2])
    assert client._send_buffer == []


@pytest.mark.asyncio
async def test_buffer_overflow_drops_oldest_audio_first(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SignallingClient, "SEND_BUFFER_LIMIT", 3)
    client = SignallingClient(session_info_for(engine))

    await client.send_offer("sdp", "offer")
    for seq in (1, 2, 3):
        await client.send_agent_audio_input(audio(seq))
    assert [message["actionType"] for message in client._send_buffer] == [
        "offer",
        "agentaudioinput",
        "agentaudioinput",
    ]
    await client.send_agent_audio_input_end()

    actions = [message["actionType"] for message in client._send_buffer]
    assert actions == ["offer", "agentaudioinput", "agentaudioinputend"]
    assert client._send_buffer[1]["payload"]["sequenceNumber"] == 3


@pytest.mark.asyncio
async def test_buffer_never_evicts_offer(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SignallingClient, "SEND_BUFFER_LIMIT", 2)
    client = SignallingClient(session_info_for(engine))

    await client.send_offer("sdp", "offer")
    await client.send_agent_audio_input_end()
    await client.send_ice_candidate("candidate", "0", 0)

    actions = [message["actionType"] for message in client._send_buffer]
    assert actions == ["offer", "icecandidate"]


@pytest.mark.asyncio
async def test_buffer_cleared_on_terminal_close(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    on_end = Mock()
    client = make_client(on_end=on_end, max_reconnection_attempts=2)
    await client.connect()

    await drop_and_wait_for_gap(engine, client)
    await client.send_agent_audio_input(audio(1))
    assert len(client._send_buffer) == 1
    engine.refuse_new = True
    assert engine.hold_handshake is not None
    engine.hold_handshake.set()
    await engine.wait_for(lambda: on_end.call_count == 1)

    assert client._send_buffer == []
    await client.send_agent_audio_input(audio(2))
    assert client._send_buffer == []
    assert audio_seqs(engine, 0) == []


@pytest.mark.asyncio
async def test_on_open_failure_closes_client(engine: FakeEngine) -> None:
    client = SignallingClient(
        session_info_for(engine), on_open=AsyncMock(side_effect=RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError):
        await client.connect()

    assert client.is_permanently_closed
    assert client._ws is None
    assert owned_tasks_done(client)
    await engine.wait_for(lambda: all(ws.state.name == "CLOSED" for ws in engine.connections))


@pytest.mark.asyncio
async def test_terminal_close_without_on_end_finishes_tasks(engine: FakeEngine) -> None:
    client = SignallingClient(session_info_for(engine, max_reconnection_attempts=1))
    await client.connect()

    engine.refuse_new = True
    await engine.drop("abort")

    await engine.wait_for(lambda: client.is_permanently_closed and owned_tasks_done(client))
    await client.close()


@pytest.mark.asyncio
async def test_close_while_reopened_socket_waits_for_lock(
    engine: FakeEngine, make_client: ClientFactory
) -> None:
    client = make_client()
    await client.connect()

    await client._send_lock.acquire()
    await engine.drop("abort")
    await engine.wait_for(lambda: len(engine.connections) == 2)
    close = asyncio.create_task(client.close())
    await asyncio.sleep(0)
    client._send_lock.release()
    await close

    await engine.wait_for(lambda: all(ws.state.name == "CLOSED" for ws in engine.connections))
    assert owned_tasks_done(client)
