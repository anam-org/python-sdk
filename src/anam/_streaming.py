"""WebRTC streaming client for Anam services."""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import numpy as np
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCDataChannel,
    RTCIceCandidate,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)

from ._agent_audio_input_stream import AgentAudioInputStream
from ._signalling import SignalAction, SignallingClient
from .types import AgentAudioInputConfig, AudioFrame, SessionInfo, VideoFrame

logger = logging.getLogger(__name__)


class StreamingClient:
    """WebRTC client for streaming audio/video with Anam.

    Handles peer connection setup, track management, and data channels.
    """

    ICE_CANDIDATE_POOL_SIZE = 2

    def __init__(
        self,
        session_info: SessionInfo,
        on_video_frame: Callable[[VideoFrame], Awaitable[None]] | None = None,
        on_audio_frame: Callable[[AudioFrame], Awaitable[None]] | None = None,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_connection_established: Callable[[], Awaitable[None]] | None = None,
        on_connection_closed: Callable[[str, str | None], Awaitable[None]] | None = None,
        disable_input_audio: bool = False,
        custom_ice_servers: list[dict[str, Any]] | None = None,
    ):
        """Initialize the streaming client.

        Args:
            session_info: Session information from API.
            on_video_frame: Callback for video frames.
            on_audio_frame: Callback for audio frames.
            on_message: Callback for data channel messages.
            on_connection_established: Callback when connected.
            on_connection_closed: Callback when disconnected.
            disable_input_audio: If True, don't send microphone audio.
            custom_ice_servers: Custom ICE servers (optional).
        """
        self._session_info = session_info
        self._session_id = session_info.session_id

        # Callbacks
        self._on_video_frame = on_video_frame
        self._on_audio_frame = on_audio_frame
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

        # Tasks
        self._video_task: asyncio.Task[None] | None = None
        self._audio_task: asyncio.Task[None] | None = None

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
                if self._on_video_frame:
                    self._video_task = asyncio.create_task(self._process_video_track(track))
            elif track.kind == "audio":
                self._audio_track = track
                if self._on_audio_frame:
                    self._audio_task = asyncio.create_task(self._process_audio_track(track))

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

    async def _process_video_track(self, track: MediaStreamTrack) -> None:
        """Process incoming video frames."""
        logger.debug("Starting video track processing")
        frame_count = 0

        while True:
            try:
                frame = await track.recv()
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

                # Convert to our VideoFrame type
                img = frame.to_ndarray(format="rgb24")
                video_frame = VideoFrame(
                    data=img.tobytes(),
                    width=frame.width,
                    height=frame.height,
                    timestamp=frame.time if hasattr(frame, "time") else 0.0,
                    format="rgb24",
                )

                if self._on_video_frame:
                    await self._on_video_frame(video_frame)

            except Exception as e:
                if "MediaStreamError" in str(type(e).__name__):
                    logger.debug("Video track ended after %d frames", frame_count)
                    break
                logger.error("Error processing video frame: %s", e)
                break

    def _resample_pcm16_to_24khz(
        self, pcm16_bytes: bytes, orig_sample_rate: int, target_sample_rate: int
    ) -> bytes:
        """Resample PCM16 audio bytes to target sample rate.

        Uses util_audio.resample_pcm16_bytes if available, otherwise falls back
        to simple numpy-based resampling for common cases.
        """
        if orig_sample_rate == target_sample_rate:
            return pcm16_bytes

        # Try to use util_audio if available (from anam-engine)
        try:
            from anam_engine.util_audio import resample_pcm16_bytes

            return resample_pcm16_bytes(pcm16_bytes, orig_sample_rate, target_sample_rate)
        except ImportError:
            # Fallback: simple resampling using numpy for common cases
            # Note: This is a basic fallback. For production use, install resampy/scipy
            # or ensure anam-engine is available for high-quality resampling.
            audio_np = np.frombuffer(pcm16_bytes, dtype=np.int16)

            ratio = orig_sample_rate / target_sample_rate

            if ratio == 2.0:
                # Simple 2:1 downsampling - take every other sample
                resampled = audio_np[::2]
            else:
                # For other ratios, use linear interpolation
                num_samples = int(len(audio_np) / ratio)
                indices = np.linspace(0, len(audio_np) - 1, num_samples)
                resampled = np.interp(indices, np.arange(len(audio_np)), audio_np).astype(np.int16)

            logger.debug(
                "Resampled audio using numpy fallback: %dHz -> %dHz (%d -> %d samples)",
                orig_sample_rate,
                target_sample_rate,
                len(audio_np),
                len(resampled),
            )
            return resampled.tobytes()

    async def _process_audio_track(self, track: MediaStreamTrack) -> None:
        """Process incoming audio frames."""
        logger.debug("Starting audio track processing")
        frame_count = 0

        while True:
            try:
                frame = await track.recv()
                frame_count += 1

                frame_sample_rate = frame.sample_rate if hasattr(frame, "sample_rate") else 24000
                target_sample_rate = 24000

                if frame_count == 1:
                    logger.info(
                        "First audio frame received: %dHz, resampling to %dHz",
                        frame_sample_rate,
                        target_sample_rate,
                    )

                pcm_bytes = frame.to_ndarray().astype(np.int16).tobytes()

                # Resample to 24kHz if needed (using util_audio pattern)
                if frame_sample_rate != target_sample_rate:
                    pcm_bytes = self._resample_pcm16_to_24khz(
                        pcm_bytes, frame_sample_rate, target_sample_rate
                    )

                audio_frame = AudioFrame(
                    data=pcm_bytes,
                    sample_rate=target_sample_rate,
                    channels=1,  # Anam requires mono audio
                    timestamp=frame.time if hasattr(frame, "time") else 0.0,
                    format="s16le",
                )

                if self._on_audio_frame:
                    await self._on_audio_frame(audio_frame)

            except Exception as e:
                if "MediaStreamError" in str(type(e).__name__):
                    logger.debug("Audio track ended after %d frames", frame_count)
                    break
                logger.error("Error processing audio frame: %s", e)
                break

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
            "timestamp": datetime.datetime.utcnow().isoformat(),
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
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        self.send_data_message(json.dumps(message))

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
        self._agent_audio_input_stream = AgentAudioInputStream(
            config, self._signalling_client
        )
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

        # Cancel track processing tasks
        if self._video_task:
            self._video_task.cancel()
            try:
                await self._video_task
            except asyncio.CancelledError:
                pass

        if self._audio_task:
            self._audio_task.cancel()
            try:
                await self._audio_task
            except asyncio.CancelledError:
                pass

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
