"""Type definitions for the Anam SDK."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
from numpy.typing import NDArray


class AnamEvent(str, Enum):
    """Events emitted by the Anam client."""

    # Connection events
    CONNECTION_ESTABLISHED = "connection_established"
    CONNECTION_CLOSED = "connection_closed"
    SESSION_READY = "session_ready"

    # Stream events
    VIDEO_FRAME = "video_frame"
    AUDIO_FRAME = "audio_frame"
    VIDEO_STREAM_STARTED = "video_stream_started"
    AUDIO_STREAM_STARTED = "audio_stream_started"
    INPUT_AUDIO_STREAM_STARTED = "input_audio_stream_started"

    # Message events
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_HISTORY_UPDATED = "message_history_updated"

    # Persona events
    TALK_STREAM_INTERRUPTED = "talk_stream_interrupted"

    # Error events
    ERROR = "error"
    SERVER_WARNING = "server_warning"


class ConnectionClosedCode(str, Enum):
    """Codes indicating why a connection was closed."""

    NORMAL = "normal"
    SERVER_CLOSED = "server_closed"
    WEBRTC_FAILURE = "webrtc_failure"
    SIGNALLING_FAILURE = "signalling_failure"
    TIMEOUT = "timeout"
    ERROR = "error"


class MessageRole(str, Enum):
    """Role of a message in the conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class PersonaConfig:
    """Configuration for an Anam persona.

    Args:
        persona_id: The ID of the persona to use.
        name: Display name for the persona (optional).
        avatar_id: The avatar to use for video (optional, uses persona default).
        voice_id: The voice to use for audio (optional, uses persona default).
        system_prompt: Custom system prompt for the persona (optional).
        language_code: Language code (e.g., 'en', 'es') (optional).
        llm_id: LLM model to use (optional).
        max_session_length_seconds: Maximum session duration (optional).
        brain_type: Brain type to use. Set to 'CUSTOMER_CLIENT_V1' to disable LLM
            (required for audio passthrough mode).
        enable_audio_passthrough: If True, enables audio passthrough mode where TTS audio
            is sent directly through the socket without transcription, LLM, or TTS processing.
            For ephemeral personas, this must be set explicitly. For stateful personas,
            this setting comes from the persona configuration in the database.
            When using audio passthrough, set brain_type='CUSTOMER_CLIENT_V1' to disable LLM.
    """

    persona_id: str
    name: str | None = None
    avatar_id: str | None = None
    voice_id: str | None = None
    system_prompt: str | None = None
    language_code: str | None = None
    llm_id: str | None = None
    max_session_length_seconds: int | None = None
    brain_type: str | None = None
    enable_audio_passthrough: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API requests."""
        result: dict[str, Any] = {"personaId": self.persona_id}
        if self.name is not None:
            result["name"] = self.name
        if self.avatar_id is not None:
            result["avatarId"] = self.avatar_id
        if self.voice_id is not None:
            result["voiceId"] = self.voice_id
        if self.system_prompt is not None:
            result["systemPrompt"] = self.system_prompt
        if self.language_code is not None:
            result["languageCode"] = self.language_code
        if self.llm_id is not None:
            result["llmId"] = self.llm_id
        if self.max_session_length_seconds is not None:
            result["maxSessionLengthSeconds"] = self.max_session_length_seconds
        if self.brain_type is not None:
            result["brainType"] = self.brain_type
        if self.enable_audio_passthrough is not None:
            result["enableAudioPassthrough"] = self.enable_audio_passthrough
        return result


@dataclass
class ClientOptions:
    """Optional configuration for AnamClient.

    Args:
        api_base_url: Base URL for the Anam API.
        api_version: API version to use.
        disable_input_audio: If True, don't capture/send microphone audio.
        ice_servers: Custom ICE servers for WebRTC (optional).
    """

    api_base_url: str = "https://api.anam.ai"
    api_version: str = "v1"
    disable_input_audio: bool = False
    ice_servers: list[dict[str, Any]] | None = None


@dataclass
class VideoFrame:
    """A video frame received from the avatar.

    Attributes:
        data: Raw frame data bytes.
        width: Frame width in pixels.
        height: Frame height in pixels.
        timestamp: Frame timestamp in seconds.
        format: Pixel format (e.g., 'bgr24', 'rgb24').
    """

    data: bytes
    width: int
    height: int
    timestamp: float
    format: str = "bgr24"

    def to_ndarray(self) -> NDArray[np.uint8]:
        """Convert to numpy array with shape (height, width, 3).

        Returns:
            NumPy array in BGR format by default.
        """
        arr = np.frombuffer(self.data, dtype=np.uint8)
        return arr.reshape((self.height, self.width, 3))


@dataclass
class AudioFrame:
    """An audio frame received from the avatar.

    Attributes:
        data: Raw audio data bytes.
        sample_rate: Audio sample rate in Hz.
        channels: Number of audio channels.
        timestamp: Frame timestamp in seconds.
        format: Sample format (e.g., 's16' for signed 16-bit).
    """

    data: bytes
    sample_rate: int
    channels: int
    timestamp: float
    format: str = "s16"

    def to_ndarray(self) -> NDArray[np.int16]:
        """Convert to numpy array of audio samples.

        Returns:
            NumPy array of shape (samples,) for mono or (samples, channels) for stereo.
        """
        arr = np.frombuffer(self.data, dtype=np.int16)
        if self.channels > 1:
            arr = arr.reshape((-1, self.channels))
        return arr

    def to_float32(self) -> NDArray[np.float32]:
        """Convert to float32 array normalized to [-1.0, 1.0].

        Returns:
            NumPy array of float32 samples.
        """
        arr = self.to_ndarray().astype(np.float32)
        return arr / 32768.0


@dataclass
class Message:
    """A message in the conversation.

    Attributes:
        role: Who sent the message (user, assistant, system).
        content: The text content of the message.
        timestamp: When the message was sent (ISO format).
    """

    role: MessageRole
    content: str
    timestamp: str


@dataclass
class AgentAudioInputConfig:
    """Configuration for agent audio input stream.

    Args:
        encoding: Audio encoding format ('pcm_s16le' for 16-bit signed PCM).
        sample_rate: Sample rate in Hz (e.g., 16000, 24000, 44100).
        channels: Number of audio channels (1 = mono, 2 = stereo).
    """

    encoding: str = "pcm_s16le"
    sample_rate: int = 24000
    channels: int = 1


@dataclass
class AgentAudioInputPayload:
    """Payload for agent audio input messages.

    Args:
        audio_data: Base64-encoded PCM audio data.
        encoding: Audio encoding format ('pcm_s16le').
        sample_rate: Sample rate in Hz.
        channels: Number of audio channels.
        sequence_number: Sequence number for ordering (starts at 0, resets on endSequence).
    """

    audio_data: str
    encoding: str
    sample_rate: int
    channels: int
    sequence_number: int


@dataclass
class SessionInfo:
    """Information about an active streaming session.

    This is returned by the API when starting a session.
    """

    session_id: str
    engine_host: str
    engine_protocol: str
    signalling_endpoint: str
    heartbeat_interval_seconds: int
    max_reconnection_attempts: int
    ice_servers: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "SessionInfo":
        """Create SessionInfo from API response."""
        client_config = data.get("clientConfig", {})
        return cls(
            session_id=data["sessionId"],
            engine_host=data["engineHost"],
            engine_protocol=data["engineProtocol"],
            signalling_endpoint=data["signallingEndpoint"],
            heartbeat_interval_seconds=client_config.get("heartbeatIntervalSeconds", 5),
            max_reconnection_attempts=client_config.get("maxWsReconnectionAttempts", 5),
            ice_servers=client_config.get("iceServers", []),
        )
