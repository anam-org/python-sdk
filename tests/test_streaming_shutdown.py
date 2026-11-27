"""Tests for signalling and streaming shutdown."""

import asyncio
import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from websockets.protocol import State

from anam import AnamClient, AnamEvent, Session
from anam._signalling import SignallingClient
from anam._streaming import StreamingClient
from anam.errors import SessionEndedError, SessionError
from anam.types import ConnectionClosedCode, SessionInfo


@pytest.fixture
def session_info() -> SessionInfo:
    return SessionInfo(
        session_id="session-1",
        engine_host="engine.test",
        engine_protocol="https",
        signalling_endpoint="/ws",
        heartbeat_interval_seconds=5,
        max_reconnection_attempts=3,
    )


@pytest.fixture
def signalling() -> MagicMock:
    client = MagicMock(spec=SignallingClient)
    client.send_end_session = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def websocket() -> MagicMock:
    ws = MagicMock()
    ws.state = State.OPEN
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_local_close_sends_end_session_before_signalling_close(
    session_info: SessionInfo, signalling: MagicMock
) -> None:
    client = StreamingClient(session_info)
    client._signalling_client = signalling
    calls = MagicMock()
    calls.attach_mock(signalling.send_end_session, "send_end_session")
    calls.attach_mock(signalling.close, "close")

    await client.close()
    await client.close()

    signalling.send_end_session.assert_awaited_once_with()
    signalling.close.assert_awaited_once_with()
    assert calls.mock_calls == [call.send_end_session(), call.close()]


@pytest.mark.asyncio
@pytest.mark.parametrize("reenter_close", [False, True])
async def test_server_end_session_does_not_echo(
    session_info: SessionInfo, signalling: MagicMock, reenter_close: bool
) -> None:
    completed: list[bool] = []

    async def on_closed(code: str, reason: str | None) -> None:
        signalling.close.assert_awaited_once_with()
        if reenter_close:
            await asyncio.wait_for(client.close(), timeout=1.0)
        completed.append(True)

    on_connection_closed = AsyncMock(side_effect=on_closed)
    client = StreamingClient(session_info, on_connection_closed=on_connection_closed)
    client._signalling_client = signalling
    client._started = True

    client._terminate(ConnectionClosedCode.SERVER_CLOSED, "done")
    await client.close()

    on_connection_closed.assert_awaited_once_with("server_closed", "done")
    assert completed == [True]
    signalling.send_end_session.assert_not_awaited()
    signalling.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_listener_raising_cancelled_error_does_not_stop_notification(
    session_info: SessionInfo, signalling: MagicMock
) -> None:
    signalling.take_failures.return_value = ["cid-1"]
    on_failed = AsyncMock(side_effect=asyncio.CancelledError)
    on_closed = AsyncMock()
    client = StreamingClient(
        session_info, on_connection_closed=on_closed, on_talk_stream_delivery_failed=on_failed
    )
    client._signalling_client = signalling
    client._started = True

    client._terminate(ConnectionClosedCode.SERVER_CLOSED, "done")
    await client.close()

    on_failed.assert_awaited_once_with("cid-1")
    on_closed.assert_awaited_once_with("server_closed", "done")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, State.CONNECTING, State.CLOSING, State.CLOSED])
async def test_end_session_skips_unavailable_socket(
    session_info: SessionInfo, websocket: MagicMock, state: State | None
) -> None:
    client = SignallingClient(session_info)
    websocket.state = state
    client._ws = websocket if state is not None else None

    await client.send_end_session()

    assert client._stop_signal
    websocket.send.assert_not_awaited()
    assert client._send_buffer == []


@pytest.mark.asyncio
async def test_end_session_stops_before_sending(
    session_info: SessionInfo, websocket: MagicMock
) -> None:
    client = SignallingClient(session_info)
    client._ws = websocket
    stop_states = []

    async def send(message: str) -> None:
        stop_states.append(client._stop_signal)

    websocket.send.side_effect = send
    await client.send_end_session()
    websocket.send.assert_awaited_once()
    assert stop_states == [True]
    assert json.loads(websocket.send.await_args.args[0]) == {
        "actionType": "endsession",
        "sessionId": "session-1",
        "payload": {},
    }


@pytest.mark.asyncio
async def test_cancelled_end_session_allows_cleanup_retry(
    session_info: SessionInfo, signalling: MagicMock
) -> None:
    client = StreamingClient(session_info)
    client._signalling_client = signalling
    client._is_connected = True
    peer = MagicMock()
    peer.close = AsyncMock()
    client._peer_connection = peer
    signalling.send_end_session.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await client.close()

    assert not client.is_connected
    signalling.send_end_session.side_effect = None
    await client.close()

    signalling.close.assert_awaited_once_with()
    peer.close.assert_awaited_once_with()
    assert client._signalling_client is None
    assert client._peer_connection is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "error"])
async def test_failed_end_session_does_not_block_cleanup(
    session_info: SessionInfo, websocket: MagicMock, failure: str
) -> None:
    signalling = SignallingClient(session_info)
    signalling.SHUTDOWN_TIMEOUT = 0.01
    signalling._ws = websocket
    client = StreamingClient(session_info)
    client._signalling_client = signalling
    peer = MagicMock()
    peer.close = AsyncMock()
    client._peer_connection = peer

    async def send(message: str) -> None:
        if failure == "error":
            raise OSError("send failed")
        await asyncio.Event().wait()

    websocket.send.side_effect = send
    await asyncio.wait_for(client.close(), timeout=1.0)

    websocket.close.assert_awaited_once_with()
    peer.close.assert_awaited_once_with()
    assert signalling._send_buffer == []
    assert signalling._ws is None


@pytest.mark.asyncio
@pytest.mark.parametrize("has_transport", [False, True])
async def test_socket_close_timeout_clears_socket(
    session_info: SessionInfo, websocket: MagicMock, has_transport: bool
) -> None:
    client = SignallingClient(session_info)
    client.SHUTDOWN_TIMEOUT = 0.01
    client._ws = websocket
    transport = websocket.transport
    if not has_transport:
        del websocket.transport

    async def close() -> None:
        await asyncio.Event().wait()

    websocket.close.side_effect = close
    await asyncio.wait_for(client.close(), timeout=1.0)

    assert client._ws is None
    assert client._stop_signal
    if has_transport:
        transport.abort.assert_called_once_with()


@pytest.mark.asyncio
async def test_eager_dispatch_waits_for_the_end_of_the_session(session_info: SessionInfo) -> None:
    if sys.version_info < (3, 12):
        pytest.skip("eager_task_factory is new in 3.12")
    loop = asyncio.get_running_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        closed_at_call: list[bool] = []

        async def on_failed(correlation_id: str) -> None:
            closed_at_call.append(client.is_permanently_closed)

        client = SignallingClient(session_info, on_talk_stream_delivery_failed=on_failed)
        client._monitor.track_chunk("cid-1")
        client._handle_incoming(
            json.dumps({"actionType": "endsession", "sessionId": "session-1", "payload": "bye"})
        )
        await asyncio.sleep(0)

        assert closed_at_call == []
        assert client.take_failures(include_unconfirmed=True) == ["cid-1"]
    finally:
        loop.set_task_factory(None)


def _session_with_held_teardown(
    session_info: SessionInfo, signalling: MagicMock
) -> tuple[Session, StreamingClient, asyncio.Event]:
    """An active session whose teardown blocks in the signalling close until the event is set."""
    release_teardown = asyncio.Event()
    signalling.close = AsyncMock(side_effect=release_teardown.wait)
    streaming = StreamingClient(session_info)
    streaming._signalling_client = signalling
    streaming._started = True
    client = AnamClient(api_key="test-key", persona_id="test-persona")
    client._streaming_client = streaming
    client._is_streaming = True
    return Session(client), streaming, release_teardown


@pytest.mark.asyncio
async def test_interrupt_fails_at_once_while_the_session_is_ending(
    session_info: SessionInfo, signalling: MagicMock
) -> None:
    session, streaming, release_teardown = _session_with_held_teardown(session_info, signalling)
    streaming._terminate(ConnectionClosedCode.SIGNALLING_FAILURE, "gone")
    try:
        assert session.is_active

        with pytest.raises(SessionEndedError, match="Session has ended"):
            await asyncio.wait_for(session.interrupt(), 1)
    finally:
        release_teardown.set()
        assert streaming._end_task is not None
        await asyncio.wait_for(streaming._end_task, 5)


@pytest.mark.asyncio
async def test_interrupt_waiting_for_the_data_channel_fails_when_the_session_ends(
    session_info: SessionInfo, signalling: MagicMock
) -> None:
    session, streaming, release_teardown = _session_with_held_teardown(session_info, signalling)
    interrupt = asyncio.create_task(session.interrupt())
    await asyncio.sleep(0.15)
    assert not interrupt.done()

    streaming._terminate(ConnectionClosedCode.SIGNALLING_FAILURE, "gone")
    try:
        with pytest.raises(SessionEndedError, match="Session has ended"):
            await asyncio.wait_for(interrupt, 1)
    finally:
        release_teardown.set()
        assert streaming._end_task is not None
        await asyncio.wait_for(streaming._end_task, 5)


@pytest.mark.asyncio
async def test_interrupt_on_a_live_session_without_a_data_channel_is_not_an_ended_session(
    session_info: SessionInfo, signalling: MagicMock
) -> None:
    session, streaming, _ = _session_with_held_teardown(session_info, signalling)
    streaming.wait_for_data_channel = AsyncMock(return_value=False)  # type: ignore[method-assign]

    with pytest.raises(SessionError, match="Data channel did not open in time") as raised:
        await asyncio.wait_for(session.interrupt(), 1)

    assert not isinstance(raised.value, SessionEndedError)
    assert not streaming.is_ending


@pytest.mark.asyncio
async def test_signalling_warning_emits_server_warning(
    session_info: SessionInfo, caplog: pytest.LogCaptureFixture
) -> None:
    client = AnamClient(api_key="test-key", persona_id="test-persona")
    received: list[str] = []

    @client.on(AnamEvent.SERVER_WARNING)
    async def on_warning(payload: str) -> None:
        received.append(payload)

    streaming = StreamingClient(
        session_info,
        on_server_warning=client._handle_server_warning,
    )
    warning = "The published video was interrupted. Reconnecting."
    with caplog.at_level(logging.WARNING, logger="anam._streaming"):
        await streaming._handle_signal_message({"actionType": "warning", "payload": warning})

    assert received == [warning]
    assert any(record.message == f"Server warning: {warning}" for record in caplog.records)
