"""WebSocket signalling client for Anam services."""

import asyncio
import json
import logging
from enum import Enum
from typing import Any, Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.protocol import State

from .types import AgentAudioInputPayload, SessionInfo

logger = logging.getLogger(__name__)


class SignalAction(str, Enum):
    """Signal message action types."""

    OFFER = "offer"
    ANSWER = "answer"
    ICE_CANDIDATE = "icecandidate"
    END_SESSION = "endsession"
    HEARTBEAT = "heartbeat"
    WARNING = "warning"
    SESSION_READY = "sessionready"
    TALK_STREAM_INPUT = "talkstream"
    TALK_STREAM_INTERRUPTED = "talkinputstreaminterrupted"
    AGENT_AUDIO_INPUT = "agentaudioinput"
    AGENT_AUDIO_INPUT_END = "agentaudioinputend"


class SignallingClient:
    """WebSocket client for signalling with Anam engine.

    Handles the WebSocket connection for WebRTC signalling,
    including offer/answer exchange and ICE candidate trickling.
    """

    DEFAULT_HEARTBEAT_INTERVAL = 5
    DEFAULT_MAX_RECONNECTION_ATTEMPTS = 5

    def __init__(
        self,
        session_info: SessionInfo,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_open: Callable[[], Awaitable[None]] | None = None,
        on_close: Callable[[int, str], Awaitable[None]] | None = None,
    ):
        """Initialize the signalling client.

        Args:
            session_info: Session information from API.
            on_message: Callback for received messages.
            on_open: Callback when connection opens.
            on_close: Callback when connection closes.
        """
        self._session_info = session_info
        self._session_id = session_info.session_id
        self._heartbeat_interval = (
            session_info.heartbeat_interval_seconds or self.DEFAULT_HEARTBEAT_INTERVAL
        )
        self._max_reconnection_attempts = (
            session_info.max_reconnection_attempts or self.DEFAULT_MAX_RECONNECTION_ATTEMPTS
        )

        self._on_message = on_message
        self._on_open = on_open
        self._on_close = on_close

        self._ws: ClientConnection | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._stop_signal = False
        self._connection_attempts = 0
        self._send_buffer: list[dict[str, Any]] = []

        self._ws_url = self._construct_websocket_url()
        logger.info("SignallingClient WebSocket URL: %s", self._ws_url)

    def _construct_websocket_url(self) -> str:
        """Build the WebSocket URL from session info."""
        protocol = self._session_info.engine_protocol
        host = self._session_info.engine_host
        endpoint = self._session_info.signalling_endpoint

        ws_protocol = "wss" if protocol == "https" else "ws"

        # Extract just the hostname if engine_host contains a path
        # (e.g., "connect-eu.anam.ai/v1/webrtc/engines/xxx")
        if "/" in host:
            hostname = host.split("/")[0]
        else:
            hostname = host

        # The signalling endpoint should be the full path
        base_url = f"{ws_protocol}://{hostname}{endpoint}"
        return f"{base_url}?session_id={self._session_id}"

    async def connect(self) -> None:
        """Establish WebSocket connection.

        Raises:
            ConnectionError: If connection fails after max attempts.
        """
        logger.debug("Connecting to signalling server: %s", self._ws_url)
        try:
            self._ws = await websockets.asyncio.client.connect(self._ws_url, close_timeout=2.0)
            self._connection_attempts = 0
            logger.info("WebSocket connection established")

            # Start heartbeat
            self._start_heartbeat()

            # Notify connection open
            if self._on_open:
                await self._on_open()

            # Start message handling
            self._receive_task = asyncio.create_task(self._handle_messages())

        except Exception as e:
            logger.error("Failed to connect to signalling server: %s", e)
            raise

    async def _handle_messages(self) -> None:
        """Handle incoming WebSocket messages."""
        if not self._ws:
            logger.error("WebSocket not connected")
            return

        try:
            async for message in self._ws:
                if self._stop_signal:
                    break

                try:
                    data = json.loads(message)
                    action_type = data.get("actionType", "").lower()

                    # Skip heartbeat logging
                    if action_type != SignalAction.HEARTBEAT.value:
                        logger.debug("Received signal: %s", action_type)

                    if self._on_message:
                        await self._on_message(data)

                except json.JSONDecodeError as e:
                    logger.error("Failed to parse message: %s", e)
                except Exception as e:
                    logger.error("Error handling message: %s", e)

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("WebSocket closed: code=%s reason=%s", e.code, e.reason)
            if self._on_close:
                await self._on_close(e.code, e.reason or "")
            if not self._stop_signal:
                await self._try_reconnect()

    async def _try_reconnect(self) -> None:
        """Attempt to reconnect after disconnection."""
        self._connection_attempts += 1

        if self._connection_attempts > self._max_reconnection_attempts:
            logger.error("Max reconnection attempts reached")
            return

        delay = 0.1 * self._connection_attempts
        logger.info(
            "Reconnecting in %.1f seconds (attempt %d/%d)",
            delay,
            self._connection_attempts,
            self._max_reconnection_attempts,
        )

        await asyncio.sleep(delay)

        try:
            await self.connect()
        except Exception as e:
            logger.error("Reconnection failed: %s", e)
            await self._try_reconnect()

    def _start_heartbeat(self) -> None:
        """Start the heartbeat task."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            return

        self._heartbeat_task = asyncio.create_task(self._send_heartbeats())

    async def _send_heartbeats(self) -> None:
        """Send periodic heartbeat messages."""
        heartbeat_message = {
            "actionType": SignalAction.HEARTBEAT.value,
            "sessionId": self._session_id,
            "payload": "",
        }

        while not self._stop_signal:
            await asyncio.sleep(self._heartbeat_interval)

            if self._stop_signal:
                break

            if self._is_ws_open():
                try:
                    await self._ws.send(json.dumps(heartbeat_message))  # type: ignore
                except Exception as e:
                    logger.error("Failed to send heartbeat: %s", e)

    def _is_ws_open(self) -> bool:
        """Check if WebSocket is connected and open."""
        if not self._ws:
            return False
        return self._ws.state == State.OPEN

    async def send_message(self, message: dict[str, Any]) -> None:
        """Send a message through the WebSocket.

        Args:
            message: The message to send.
        """
        # Convert enum to string if needed
        if "actionType" in message and isinstance(message["actionType"], SignalAction):
            message["actionType"] = message["actionType"].value

        if self._is_ws_open():
            try:
                await self._ws.send(json.dumps(message))  # type: ignore
            except Exception as e:
                logger.error("Failed to send message: %s", e)
                self._send_buffer.append(message)
        else:
            self._send_buffer.append(message)

    async def send_offer(self, sdp: str, sdp_type: str) -> None:
        """Send WebRTC offer to the server.

        Args:
            sdp: The SDP offer string.
            sdp_type: The SDP type (usually "offer").
        """
        message = {
            "actionType": SignalAction.OFFER.value,
            "sessionId": self._session_id,
            "payload": {
                "connectionDescription": {
                    "sdp": sdp,
                    "type": sdp_type,
                },
                "userUid": self._session_id,
            },
        }
        logger.debug("Sending offer")
        await self.send_message(message)

    async def send_ice_candidate(
        self,
        candidate: str,
        sdp_mid: str | None,
        sdp_mline_index: int | None,
    ) -> None:
        """Send an ICE candidate to the server.

        Args:
            candidate: The ICE candidate string.
            sdp_mid: The media stream ID.
            sdp_mline_index: The media line index.
        """
        message = {
            "actionType": SignalAction.ICE_CANDIDATE.value,
            "sessionId": self._session_id,
            "payload": {
                "candidate": candidate,
                "sdpMid": sdp_mid,
                "sdpMLineIndex": sdp_mline_index,
            },
        }
        await self.send_message(message)

    async def flush_send_buffer(self) -> None:
        """Send all buffered messages."""
        while self._send_buffer and self._is_ws_open():
            message = self._send_buffer.pop(0)
            try:
                await self._ws.send(json.dumps(message))  # type: ignore
            except Exception as e:
                logger.error("Failed to flush message: %s", e)
                self._send_buffer.insert(0, message)
                break

    async def send_agent_audio_input(self, payload: AgentAudioInputPayload) -> None:
        """Send agent audio input message to the server.

        Args:
            payload: The audio input payload.
        """
        # Convert to camelCase to match backend expectations
        message = {
            "actionType": SignalAction.AGENT_AUDIO_INPUT.value,
            "sessionId": self._session_id,
            "payload": {
                "audioData": payload.audio_data,
                "encoding": payload.encoding,
                "sampleRate": payload.sample_rate,
                "channels": payload.channels,
                "sequenceNumber": payload.sequence_number,
            },
        }
        if payload.sample_rate < 16000:
            logger.warning(
                f"Sample rate {payload.sample_rate}Hz is very low. Quality of speech and avatar is negatively impacted. Consider providing 24kHz audio for better results."
            )
        if payload.sample_rate > 24000:
            logger.warning(
                f"Sample rate {payload.sample_rate}Hz is high. Latency is negatively affected for minimal audio quality gain. Consider resampling to 24kHz"
            )
        await self.send_message(message)

    async def send_agent_audio_input_end(self) -> None:
        """Send agent audio input end signal to the server.

        Signals the end of the current audio sequence/turn.
        """
        message = {
            "actionType": SignalAction.AGENT_AUDIO_INPUT_END.value,
            "sessionId": self._session_id,
            "payload": {},
        }
        await self.send_message(message)

    async def send_talk_stream_input(
        self,
        content: str,
        correlation_id: str,
        start_of_speech: bool = True,
        end_of_speech: bool = True,
        utterance_id: str | None = None,
    ) -> None:
        """Send talk stream input to make the avatar speak text directly.

        This bypasses the LLM and sends text directly to TTS.

        Args:
            content: The text for the avatar to speak.
            correlation_id: ID to correlate this message with interruptions.
                Callers should use TalkMessageStream which manages this.
            start_of_speech: Whether this is the start of a speech sequence.
            end_of_speech: Whether this is the end of a speech sequence.
            utterance_id: Optional ID marking the first chunk of a new utterance. None
                continues the current utterance; a different ID queues a new one after it.
                Validated by TalkMessageStream.send(); not re-checked here.
        """
        payload: dict[str, Any] = {
            "content": content,
            "startOfSpeech": start_of_speech,
            "endOfSpeech": end_of_speech,
            "correlationId": correlation_id,
        }
        if utterance_id is not None:
            payload["utteranceId"] = utterance_id

        message = {
            "actionType": SignalAction.TALK_STREAM_INPUT.value,
            "sessionId": self._session_id,
            "payload": payload,
        }
        await self.send_message(message)

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self._stop_signal = True

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        logger.debug("SignallingClient closed")
