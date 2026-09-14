"""Tests for signalling and streaming shutdown."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from websockets.protocol import State

from anam._signalling import SignallingClient
from anam._streaming import StreamingClient
from anam.types import SessionInfo


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
    client = StreamingClient(session_info)
    client._signalling_client = signalling

    async def on_closed(code: str, reason: str | None) -> None:
        assert client._server_ended
        if reenter_close:
            await client.close()

    client._on_connection_closed = AsyncMock(side_effect=on_closed)
    await client._handle_signal_message({"actionType": "endsession", "payload": "done"})

    client._on_connection_closed.assert_awaited_once()
    signalling.send_end_session.assert_not_awaited()
    signalling.close.assert_awaited_once_with()


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

    assert not client._closing
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
    assert not client._closing


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
