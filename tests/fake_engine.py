"""A local WebSocket server that plays the Anam engine's signalling role in tests."""

import asyncio
import json
from typing import Any, Callable, Literal

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from anam.types import SessionInfo

SESSION_ID = "session-1"
CANNED_ANSWER_SDP = "v=0\r\n"


class FakeEngine:
    """Accepts signalling sockets, records what the client sends and answers heartbeats."""

    def __init__(self) -> None:
        self.port = 0
        self.connections: list[ServerConnection] = []
        self.received: list[tuple[int, dict[str, Any]]] = []
        self.paths: list[str] = []
        self.connect_attempts = 0
        self.refuse_new = False
        self.hold_handshake: asyncio.Event | None = None
        self.drop_on_open = False
        self.answer_heartbeats = True
        self.answer_offers: Literal["none", "fake", "aiortc"] = "fake"
        self._server: Server | None = None

    async def __aenter__(self) -> "FakeEngine":
        self._server = await serve(
            self._handle, "127.0.0.1", 0, process_request=self._process_request
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self.hold_handshake is not None:
            self.hold_handshake.set()
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _process_request(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        self.connect_attempts += 1
        self.paths.append(request.path)
        if self.hold_handshake is not None:
            await self.hold_handshake.wait()
        if self.refuse_new:
            return connection.respond(503, "unavailable")
        return None

    async def _handle(self, ws: ServerConnection) -> None:
        index = len(self.connections)
        self.connections.append(ws)
        if self.drop_on_open:
            ws.transport.abort()
            return
        try:
            async for raw in ws:
                message = json.loads(raw)
                self.received.append((index, message))
                await self._answer(ws, message)
        except ConnectionClosed:
            pass

    async def _answer(self, ws: ServerConnection, message: dict[str, Any]) -> None:
        action = message.get("actionType")
        payload = message.get("payload")
        if action == "heartbeat" and self.answer_heartbeats:
            if isinstance(payload, dict) and type(payload.get("seq")) is int:
                await ws.send(
                    json.dumps(
                        {
                            "actionType": "heartbeat",
                            "sessionId": SESSION_ID,
                            "payload": {"seq": payload["seq"]},
                        }
                    )
                )
        elif action == "offer" and self.answer_offers == "fake":
            await ws.send(
                json.dumps(
                    {
                        "actionType": "answer",
                        "sessionId": SESSION_ID,
                        "payload": {"sdp": CANNED_ANSWER_SDP, "type": "answer"},
                    }
                )
            )

    async def drop(self, mode: Literal["abort", "clean"] = "abort") -> None:
        """Drop the newest socket: abort makes the client see 1006, clean sends 1001."""
        ws = self.connections[-1]
        if mode == "abort":
            ws.transport.abort()
        else:
            await ws.close(1001, "going away")

    async def send(self, message: dict[str, Any]) -> None:
        await self.connections[-1].send(json.dumps(message))

    def received_on(self, index: int, action: str | None = None) -> list[dict[str, Any]]:
        return [
            message
            for conn, message in self.received
            if conn == index and (action is None or message.get("actionType") == action)
        ]

    def received_actions(self, action: str) -> list[tuple[int, dict[str, Any]]]:
        return [
            (conn, message) for conn, message in self.received if message["actionType"] == action
        ]

    async def wait_for(self, predicate: Callable[[], bool], timeout: float = 2.0) -> None:
        await wait_for(predicate, timeout)


async def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll a predicate until it holds; fail the test on timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"condition not met within {timeout} s")
        await asyncio.sleep(0.005)


async def stays(predicate: Callable[[], bool], duration: float) -> bool:
    """Check that a predicate holds for a whole bounded window (for negative assertions)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    while loop.time() < deadline:
        if not predicate():
            return False
        await asyncio.sleep(0.005)
    return predicate()


def session_info_for(engine: FakeEngine, **overrides: Any) -> SessionInfo:
    values: dict[str, Any] = {
        "session_id": SESSION_ID,
        "engine_host": f"127.0.0.1:{engine.port}",
        "engine_protocol": "http",
        "signalling_endpoint": "/ws",
        "heartbeat_interval_seconds": 0.05,
        "max_reconnection_attempts": 3,
    }
    values.update(overrides)
    return SessionInfo(**values)


__all__ = [
    "FakeEngine",
    "SESSION_ID",
    "session_info_for",
    "stays",
    "wait_for",
]
