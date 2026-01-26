"""WebRTC streaming client for Anam services."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable

import aiohttp
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCDataChannel,
    RTCIceCandidate,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from av.audio.frame import AudioFrame
from av.video.frame import VideoFrame

from ._agent_audio_input_stream import AgentAudioInputStream
from ._signalling import SignalAction, SignallingClient
from .types import AgentAudioInputConfig, SessionInfo

logger = logging.getLogger(__name__)


class StreamingClient:
    """WebRTC client for streaming audio/video with Anam.

    Handles peer connection setup, track management, and data channels.
    """

    ICE_CANDIDATE_POOL_SIZE = 2

    def __init__(
        self,
        session_info: SessionInfo,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_connection_established: Callable[[], Awaitable[None]] | None = None,
        on_connection_closed: Callable[[str, str | None], Awaitable[None]] | None = None,
        disable_input_audio: bool = False,
        custom_ice_servers: list[dict[str, Any]] | None = None,
    ):
        """Initialize the streaming client.

        Args:
            session_info: Session information from API.
            on_message: Callback for data channel messages.
            on_connection_established: Callback when connected.
            on_connection_closed: Callback when disconnected.
            disable_input_audio: If True, don't send microphone audio.
            custom_ice_servers: Custom ICE servers (optional).
        """
        self._session_info = session_info
        self._session_id = session_info.session_id

        # Callbacks (only for connection events and messages)
        self._on_message = on_message
        self._on_connection_established = on_connection_established
        self._on_connection_closed = on_connection_closed

        # Configuration
        self._disable_input_audio = disable_input_audio
        self._ice_servers = custom_ice_servers or session_info.ice_servers

        # State
        self._peer_connection: RTCPeerConnection | None = None
        self._signalling_client: SignallingClient | None = None
        self._data_channel: RTCDataChannel | None = None
        self._connection_received_answer = False
        self._remote_ice_buffer: list[RTCIceCandidate] = []
        self._video_track: MediaStreamTrack | None = None
        self._audio_track: MediaStreamTrack | None = None
        self._is_connected = False
        self._agent_audio_input_stream: AgentAudioInputStream | None = None

    async def connect(self, timeout: float = 30.0) -> None:
        """Start the streaming connection.

        Establishes WebSocket signalling and WebRTC peer connection.
        Waits for the connection to be fully established before returning.

        Args:
            timeout: Maximum time to wait for connection (seconds).
        """
        logger.info("Starting streaming connection for session: %s", self._session_id)

        self._connection_ready = asyncio.Event()

        # Create signalling client
        self._signalling_client = SignallingClient(
            session_info=self._session_info,
            on_message=self._handle_signal_message,
            on_open=self._on_signalling_open,
            on_close=self._on_signalling_close,
        )

        # Connect to signalling server
        await self._signalling_client.connect()

        # Wait for WebRTC connection to be established
        try:
            await asyncio.wait_for(self._connection_ready.wait(), timeout=timeout)
            logger.info("Streaming connection ready")
        except asyncio.TimeoutError:
            logger.warning("Connection timed out waiting for WebRTC, proceeding anyway")

    async def _on_signalling_open(self) -> None:
        """Handle signalling connection open."""
        logger.debug("Signalling connected, initializing peer connection")
        await self._init_peer_connection()
        await self._create_and_send_offer()

    async def _on_signalling_close(self, code: int, reason: str) -> None:
        """Handle signalling connection close."""
        logger.warning("Signalling closed: %d %s", code, reason)
        if self._on_connection_closed:
            await self._on_connection_closed("signalling_closed", reason)

    async def _handle_signal_message(self, message: dict[str, Any]) -> None:
        """Handle incoming signalling messages."""
        action_type = message.get("actionType", "").lower()
        payload = message.get("payload")

        logger.debug("Signal message received: %s", action_type)

        if action_type == SignalAction.ANSWER.value:
            await self._handle_answer(payload)

        elif action_type == SignalAction.ICE_CANDIDATE.value:
            await self._handle_ice_candidate(payload)

        elif action_type == SignalAction.END_SESSION.value:
            reason = payload if isinstance(payload, str) else "Session ended by server"
            logger.info("Session ended by server: %s", reason)
            if self._on_connection_closed:
                await self._on_connection_closed("server_closed", reason)
            await self.close()

        elif action_type == SignalAction.WARNING.value:
            logger.warning("Server warning: %s", payload)

        elif action_type == SignalAction.SESSION_READY.value:
            logger.info("Session ready")

        elif action_type == SignalAction.TALK_STREAM_INTERRUPTED.value:
            correlation_id = payload.get("correlationId") if isinstance(payload, dict) else None
            logger.debug("Talk stream interrupted: %s", correlation_id)

    async def _handle_answer(self, payload: dict[str, Any]) -> None:
        """Handle SDP answer from server."""
        if not self._peer_connection:
            logger.error("No peer connection for answer")
            return

        logger.info("Received SDP answer, setting remote description")
        answer = RTCSessionDescription(
            sdp=payload["sdp"],
            type=payload["type"],
        )
        await self._peer_connection.setRemoteDescription(answer)
        self._connection_received_answer = True
        logger.info(
            "Remote description set, flushing %d buffered ICE candidates",
            len(self._remote_ice_buffer),
        )

        # Flush buffered ICE candidates
        await self._flush_ice_buffer()

    async def _handle_ice_candidate(self, payload: dict[str, Any]) -> None:
        """Handle ICE candidate from server."""
        if not self._peer_connection:
            logger.error("No peer connection for ICE candidate")
            return

        candidate = self._create_ice_candidate(payload)
        logger.debug(
            "Remote ICE candidate: %s:%s (%s)", candidate.ip, candidate.port, candidate.type
        )

        if self._connection_received_answer:
            try:
                await self._peer_connection.addIceCandidate(candidate)
                logger.debug("Added ICE candidate to peer connection")
            except Exception as e:
                logger.error("Failed to add ICE candidate: %s", e)
        else:
            self._remote_ice_buffer.append(candidate)
            logger.debug("Buffered ICE candidate (waiting for answer)")

    def _create_ice_candidate(self, payload: dict[str, Any]) -> RTCIceCandidate:
        """Create RTCIceCandidate from payload."""
        candidate_str = payload["candidate"]
        parts = candidate_str.split()

        # Parse the candidate string
        # Format: candidate:foundation component protocol priority ip port type ...
        return RTCIceCandidate(
            component=int(parts[1]),
            foundation=parts[0].split(":")[1],
            ip=parts[4],
            port=int(parts[5]),
            priority=int(parts[3]),
            protocol=parts[2],
            type=parts[7],
            sdpMid=payload.get("sdpMid", ""),
            sdpMLineIndex=payload.get("sdpMLineIndex", 0),
        )

    async def _flush_ice_buffer(self) -> None:
        """Add all buffered ICE candidates to peer connection."""
        if not self._peer_connection:
            return

        for candidate in self._remote_ice_buffer:
            try:
                await self._peer_connection.addIceCandidate(candidate)
            except Exception as e:
                logger.error("Failed to add ICE candidate: %s", e)

        self._remote_ice_buffer.clear()

    async def _init_peer_connection(self) -> None:
        """Initialize the WebRTC peer connection."""
        # Configure ICE servers
        ice_servers = []
        logger.info("Configuring ICE servers: %d server(s)", len(self._ice_servers))
        for server in self._ice_servers:
            urls = server.get("urls", [])
            # Handle both single URL string and list of URLs
            if isinstance(urls, str):
                urls = [urls]
            logger.info(
                "Adding ICE server: urls=%s, username=%s, has_credential=%s",
                urls,
                server.get("username"),
                bool(server.get("credential")),
            )
            ice_servers.append(
                RTCIceServer(
                    urls=urls,
                    username=server.get("username"),
                    credential=server.get("credential"),
                )
            )

        if not ice_servers:
            logger.warning("No ICE servers configured - connection may fail behind NAT/firewall")

        config = RTCConfiguration(iceServers=ice_servers)
        self._peer_connection = RTCPeerConnection(configuration=config)

        # Set up event handlers using decorator pattern (like working old SDK)
        @self._peer_connection.on("icecandidate")
        async def on_ice_candidate(candidate: RTCIceCandidate | None) -> None:
            """Send local ICE candidates to the remote peer."""
            if candidate:
                logger.info(
                    "Local ICE candidate: %s",
                    candidate.candidate[:80] if candidate.candidate else "None",
                )
                if self._signalling_client:
                    ice_message = {
                        "actionType": SignalAction.ICE_CANDIDATE.value,
                        "sessionId": self._session_id,
                        "payload": {
                            "candidate": candidate.candidate,
                            "sdpMid": candidate.sdpMid,
                            "sdpMLineIndex": candidate.sdpMLineIndex,
                        },
                    }
                    await self._signalling_client.send_message(ice_message)
                    logger.info("ICE candidate sent to server")
            else:
                logger.info("ICE gathering completed (null candidate)")

        @self._peer_connection.on("iceconnectionstatechange")
        def on_ice_connection_state_change() -> None:
            if not self._peer_connection:
                return
            state = self._peer_connection.iceConnectionState
            logger.info("ICE connection state: %s", state)
            if state in ("connected", "completed"):
                if not self._is_connected:
                    self._is_connected = True
                    if hasattr(self, "_connection_ready"):
                        self._connection_ready.set()
                    if self._on_connection_established:
                        asyncio.create_task(self._on_connection_established())
            elif state == "failed":
                logger.error(
                    "ICE connection failed - check TURN/STUN server configuration and network connectivity"
                )
                if hasattr(self, "_connection_ready"):
                    self._connection_ready.set()
            elif state == "closed":
                if self._on_connection_closed:
                    asyncio.create_task(self._on_connection_closed("connection_closed", None))

        @self._peer_connection.on("connectionstatechange")
        def on_connection_state_change() -> None:
            if not self._peer_connection:
                return
            state = self._peer_connection.connectionState
            logger.debug("Connection state: %s", state)

        @self._peer_connection.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            logger.info("Received %s track", track.kind)
            if track.kind == "video":
                self._video_track = track
            elif track.kind == "audio":
                self._audio_track = track

        # Set up data channel
        await self._setup_data_channel()

        # Set up transceivers
        # Video: receive only
        self._peer_connection.addTransceiver("video", direction="recvonly")

        # Audio: send/receive or receive only
        if self._disable_input_audio:
            self._peer_connection.addTransceiver("audio", direction="recvonly")
        else:
            self._peer_connection.addTransceiver("audio", direction="sendrecv")
            # Note: Audio input track would be added here if needed

        logger.debug("Peer connection initialized")

    async def _setup_data_channel(self) -> None:
        """Set up the data channel for messaging."""
        if not self._peer_connection:
            return

        self._data_channel = self._peer_connection.createDataChannel(
            "session",
            ordered=True,
        )

        @self._data_channel.on("open")
        def on_open() -> None:
            logger.info("Data channel opened")
            self._data_channel_open = True

        @self._data_channel.on("close")
        def on_close() -> None:
            logger.info("Data channel closed")
            self._data_channel_open = False

        @self._data_channel.on("message")
        async def on_message(message: str) -> None:
            try:
                data = json.loads(message)
                logger.debug("Data channel message: %s", data.get("messageType", "unknown"))
                if self._on_message:
                    await self._on_message(data)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse data channel message: %s", e)

        self._data_channel_open = False

    async def video_frames(self) -> AsyncIterator[VideoFrame]:
        """Get video frames as an async iterator.

        Yields:
            VideoFrame: PyAV VideoFrame objects from the WebRTC stream.

        Raises:
            RuntimeError: If video track is not available.

        Example:
            ```python
            async for frame in streaming_client.video_frames():
                # Process video frame
                image = frame.to_ndarray(format="rgb24")
            ```
        """
        if not self._video_track:
            raise RuntimeError("Video track not available. Wait for connection to be established.")

        logger.debug("Starting video frame iterator")
        frame_count = 0

        while True:
            try:
                frame = await self._video_track.recv()
                frame_count += 1

                if frame_count == 1:
                    logger.info("First video frame received: %dx%d", frame.width, frame.height)
                    # Signal connection established on first actual frame
                    if not self._is_connected:
                        self._is_connected = True
                        if hasattr(self, "_connection_ready"):
                            self._connection_ready.set()
                        if self._on_connection_established:
                            asyncio.create_task(self._on_connection_established())

                yield frame

            except Exception as e:
                if "MediaStreamError" in str(type(e).__name__):
                    logger.debug("Video track ended after %d frames", frame_count)
                    break
                logger.error("Error receiving video frame: %s", e)
                raise

    async def audio_frames(self) -> AsyncIterator[AudioFrame]:
        """Get audio frames as an async iterator.

        Yields:
            AudioFrame: PyAV AudioFrame objects from the WebRTC stream.
            Audio frames are decoded PCM: 16-bit, 48kHz, stereo samples.

        Raises:
            RuntimeError: If audio track is not available.

        Example:
            ```python
            async for frame in streaming_client.audio_frames():
                # Process audio frame
                samples = frame.to_ndarray()
            ```
        """
        if not self._audio_track:
            raise RuntimeError("Audio track not available. Wait for connection to be established.")

        logger.debug("Starting audio frame iterator")
        frame_count = 0

        while True:
            try:
                # Receive audio frame from WebRTC
                # Incoming audio are decoded (PCM) WebRTC OPUS: 16 bit 48kHz stereo samples.
                frame = await self._audio_track.recv()
                frame_count += 1

                if frame_count == 1:
                    logger.debug("First audio frame received: %dHz", frame.sample_rate)
                    logger.debug("Audio frame layout: %s", frame.layout)
                    logger.debug("Audio frame channels: %d", frame.layout.nb_channels)
                    logger.debug("Audio frame layout name: %s", frame.layout.name)
                    logger.debug("Audio frame format: %s", frame.format.name)
                    logger.debug("Audio frame samples: %d", frame.samples)
                    # Signal connection established on first audio frame if video hasn't yet
                    if not self._is_connected:
                        self._is_connected = True
                        if hasattr(self, "_connection_ready"):
                            self._connection_ready.set()
                        if self._on_connection_established:
                            asyncio.create_task(self._on_connection_established())

                yield frame

            except Exception as e:
                if "MediaStreamError" in str(type(e).__name__):
                    logger.debug("Audio track ended after %d frames", frame_count)
                    break
                logger.error("Error receiving audio frame: %s", e)
                raise

    async def _create_and_send_offer(self) -> None:
        """Create and send WebRTC offer."""
        if not self._peer_connection or not self._signalling_client:
            logger.error("Cannot create offer: missing peer connection or signalling")
            return

        # Create offer
        offer = await self._peer_connection.createOffer()
        await self._peer_connection.setLocalDescription(offer)

        # Get the local description (with gathered ICE candidates)
        local_desc = self._peer_connection.localDescription

        logger.debug("Sending WebRTC offer")
        await self._signalling_client.send_offer(
            sdp=local_desc.sdp,
            sdp_type=local_desc.type,
        )

    def send_data_message(self, message: str) -> bool:
        """Send a message through the data channel.

        Args:
            message: The message string to send.

        Returns:
            True if message was sent, False otherwise.
        """
        if self._data_channel and getattr(self, "_data_channel_open", False):
            self._data_channel.send(message)
            return True
        else:
            logger.warning("Data channel not open, message not sent")
            return False

    async def wait_for_data_channel(self, timeout: float = 10.0) -> bool:
        """Wait for the data channel to open.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if data channel opened, False if timeout.
        """
        start = asyncio.get_event_loop().time()
        while not getattr(self, "_data_channel_open", False):
            if asyncio.get_event_loop().time() - start > timeout:
                return False
            await asyncio.sleep(0.1)
        return True

    def send_user_message(self, content: str) -> None:
        """Send a user text message.

        Args:
            content: The message text.
        """
        import datetime

        message = {
            "content": content,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(),
            "session_id": self._session_id,
            "message_type": "speech",
        }
        self.send_data_message(json.dumps(message))

    def send_interrupt(self) -> None:
        """Send interrupt command to stop persona speaking."""
        import datetime

        message = {
            "message_type": "interrupt",
            "session_id": self._session_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.send_data_message(json.dumps(message))

    async def send_talk(self, content: str) -> None:
        """Send text for the avatar to speak directly (bypasses LLM).

        This sends the talk command via REST API to the engine, which is the
        correct method for simple talk commands. For streaming text, use the
        signalling client's send_talk_stream_input method.

        Args:
            content: The text for the avatar to speak.

        Raises:
            RuntimeError: If session info is not available.
            aiohttp.ClientError: If the HTTP request fails.
        """
        # Build engine URL from session info
        # The engine_host includes the full path (e.g., connect-eu.anam.ai/v1/webrtc/engines/xxx)
        protocol = self._session_info.engine_protocol
        host = self._session_info.engine_host

        engine_base_url = f"{protocol}://{host}"
        url = f"{engine_base_url}/talk?session_id={self._session_id}"

        logger.debug("Sending talk command to %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={"Content-Type": "application/json"},
                json={"content": content},
            ) as response:
                if not response.ok:
                    error_text = await response.text()
                    logger.error("Talk command failed: %s %s", response.status, error_text)
                    raise RuntimeError(
                        f"Failed to send talk command: {response.status} {error_text}"
                    )
                logger.info("Talk command sent successfully to %s", url)

    def create_agent_audio_input_stream(
        self, config: AgentAudioInputConfig
    ) -> AgentAudioInputStream:
        """Create an agent audio input stream for sending PCM audio data.

        Args:
            config: Audio format configuration.

        Returns:
            AgentAudioInputStream instance.

        Raises:
            RuntimeError: If signalling client is not available.
        """
        if not self._signalling_client:
            raise RuntimeError(
                "Failed to create agent audio input stream: signalling client is not available"
            )
        self._agent_audio_input_stream = AgentAudioInputStream(config, self._signalling_client)
        return self._agent_audio_input_stream

    def get_agent_audio_input_stream(self) -> AgentAudioInputStream | None:
        """Get the current agent audio input stream if one exists.

        Returns:
            The agent audio input stream or None if not created.
        """
        return self._agent_audio_input_stream

    @property
    def is_connected(self) -> bool:
        """Check if the streaming connection is active."""
        return self._is_connected

    @property
    def video_track(self) -> MediaStreamTrack | None:
        """Get the raw video track (for advanced usage)."""
        return self._video_track

    @property
    def audio_track(self) -> MediaStreamTrack | None:
        """Get the raw audio track (for advanced usage)."""
        return self._audio_track

    async def close(self) -> None:
        """Close the streaming connection and clean up resources."""
        logger.debug("Closing streaming client")

        # Close signalling
        if self._signalling_client:
            try:
                await self._signalling_client.close()
            except Exception as e:
                logger.warning("Error closing signalling client: %s", e)
            finally:
                self._signalling_client = None

        # Close peer connection
        if self._peer_connection:
            try:
                await self._peer_connection.close()
            except Exception as e:
                logger.warning("Error closing peer connection: %s", e)
            finally:
                self._peer_connection = None

        self._is_connected = False
        logger.info("Streaming client closed")

    def __del__(self) -> None:
        """Cleanup on destruction to prevent warnings."""
        # Clear peer connection reference if close() wasn't called explicitly.
        # Note: This won't prevent RTCPeerConnection.__del__ from being called
        # if the object is garbage collected independently, but it helps in
        # cases where StreamingClient is destroyed without calling close().
        # The proper fix is to always call close() explicitly.
        if self._peer_connection is not None:
            try:
                self._peer_connection = None
            except Exception:
                pass
