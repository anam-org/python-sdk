"""Type definitions for the Anam SDK."""

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class AnamEvent(str, Enum):
    """Events emitted by the Anam client."""

    # Connection events
    CONNECTION_ESTABLISHED = "connection_established"
    CONNECTION_CLOSED = "connection_closed"
    SESSION_READY = "session_ready"

    # Message events
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_STREAM_EVENT_RECEIVED = "message_stream_event_received"
    MESSAGE_HISTORY_UPDATED = "message_history_updated"
    USER_SPEECH_STARTED = "user_speech_started"
    USER_SPEECH_ENDED = "user_speech_ended"

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
class DirectorNotes:
    """Director-notes settings applied at session start via ``PersonaConfig``.

    These fields are optional and serialized only when set.
    """

    custom_style_prompt: str | None = None
    preset_style: str | None = None
    expressivity: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.custom_style_prompt is not None:
            result["customStylePrompt"] = self.custom_style_prompt
        if self.preset_style is not None:
            result["presetStyle"] = self.preset_style
        if self.expressivity is not None:
            # Non-finite floats are invalid JSON
            if not math.isfinite(self.expressivity):
                raise ValueError("expressivity must be a finite number")
            result["expressivity"] = self.expressivity
        return result


@dataclass
class PersonaConfig:
    """Configuration for an Anam persona.

    Args:
        persona_id: The ID of the persona to use - Only uses pre-defined personas. All other parameters are ignored, except enable_audio_passthrough.
        name: Display name for the persona (optional).
        avatar_id: The avatar to use for video (from: https://lab.anam.ai/avatars). Do not use persona_id as avatar_id.
        avatar_model: Avatar model version (e.g., 'cara-4') (optional).
        voice_id: The voice to use for audio (optional, uses persona default).
        system_prompt: Custom system prompt for the persona (optional).
        language_code: Language code (e.g., 'en', 'es') (optional).
        llm_id: LLM model to use (optional). Set to 'CUSTOMER_CLIENT_V1' to disable
            Anam's default brain for custom LLM integration.
        max_session_length_seconds: Maximum session duration (optional).
        director_notes: Optional director-notes defaults applied at session start.
        enable_audio_passthrough: If True, bypasses Anam's orchestration layer
            and allows to ingest TTS audio directly through the socket.
    """

    persona_id: str | None = None
    name: str | None = None
    avatar_id: str | None = None
    avatar_model: str | None = None
    voice_id: str | None = None
    system_prompt: str | None = None
    language_code: str | None = None
    llm_id: str | None = None
    max_session_length_seconds: int | None = None
    director_notes: DirectorNotes | None = None
    enable_audio_passthrough: bool | None = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API requests."""
        result: dict[str, Any] = {}
        if self.persona_id is not None:
            result["personaId"] = self.persona_id
        if self.name is not None:
            result["name"] = self.name
        if self.avatar_id is not None:
            result["avatarId"] = self.avatar_id
        if self.avatar_model is not None:
            result["avatarModel"] = self.avatar_model
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
        if self.director_notes is not None:
            director_notes = self.director_notes.to_dict()
            if director_notes:
                result["directorNotes"] = director_notes
        if self.enable_audio_passthrough is not None:
            result["enableAudioPassthrough"] = self.enable_audio_passthrough
        return result


@dataclass
class SessionReplayOptions:
    """Session replay options. Maps to anam-lab sessionReplay schema.

    Args:
        enable_session_replay: If True (default), session is recorded. Set False to disable.
    """

    enable_session_replay: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"enableSessionReplay": self.enable_session_replay}


@dataclass
class EgressDailyOptions:
    """Daily-specific egress credentials.

    Args:
        room_url: Daily room URL the avatar joins as a publisher.
        token: Optional Daily meeting token. Omit for public rooms.
        user_name: Optional display name for the avatar participant (default: "anam-avatar").
    """

    room_url: str
    token: str | None = None
    user_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"roomUrl": self.room_url}
        if self.token:
            result["token"] = self.token
        if self.user_name:
            result["userName"] = self.user_name
        return result


@dataclass
class EgressOptions:
    """Direct egress to a third-party real-time transport.

    When set, the avatar's audio + video is published into the named
    provider's room. The WebRTC peer connection still exists for signalling
    (interrupts, status messages).

    Args:
        mode: Egress provider. Only ``"daily"`` is currently supported.
        daily: Required when ``mode`` is ``"daily"``.
    """

    mode: Literal["daily"]
    daily: EgressDailyOptions

    def __post_init__(self) -> None:
        if self.mode == "daily" and self.daily is None:
            raise ValueError('EgressOptions(mode="daily") requires a daily=... block')

    def to_dict(self) -> dict[str, Any]:
        if self.mode == "daily":
            return {"mode": "daily", "daily": self.daily.to_dict()}
        raise ValueError(f"Unsupported egress mode: {self.mode!r}")


@dataclass
class SessionOptions:
    """Configuration for an Anam session.

    Args:
        enable_session_replay: If True (default), session is recorded. Set False to disable.
        video_quality: Video quality profile to pin the video quality. Supported values are "high" (default) and "auto".
        video_width: Requested video output width. Must be provided with video_height.
        video_height: Requested video output height. Must be provided with video_width.
        egress: Optional direct egress to a third-party transport (e.g. Daily). See :class:`EgressOptions`.
        show_ai_avatar_disclosure: Show Anam's AI avatar disclosure watermark throughout
            the session. Defaults to Anam's default behavior, which is off.
        region: Requested engine region. See https://docs.anam.ai for available
            regions; additional regions may be introduced over time.
        region_policy: "preferred" permits cross-region capacity failover; "strict"
            keeps the session in the requested region.
    """

    enable_session_replay: bool = True
    video_quality: Literal["high", "auto"] = "high"
    video_width: int | None = None
    video_height: int | None = None
    egress: EgressOptions | None = None
    show_ai_avatar_disclosure: bool | None = None
    region: str | None = None
    region_policy: Literal["preferred", "strict"] | None = None

    def __post_init__(self) -> None:
        self._session_replay = SessionReplayOptions(
            enable_session_replay=self.enable_session_replay
        )
        if self.video_quality not in {"high", "auto"}:
            raise ValueError('video_quality must be either "high" or "auto"')
        if (self.video_width is None) != (self.video_height is None):
            raise ValueError("video_width and video_height must be provided together")
        if self.region_policy is not None and self.region_policy not in {
            "preferred",
            "strict",
        }:
            raise ValueError('region_policy must be either "preferred" or "strict"')
        if self.region_policy == "strict" and self.region is None:
            raise ValueError('region_policy="strict" requires region to be set')
        for name, value in (
            ("video_width", self.video_width),
            ("video_height", self.video_height),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "sessionReplay": self._session_replay.to_dict(),
            "videoQuality": self.video_quality,
        }
        if self.video_width is not None and self.video_height is not None:
            result["videoWidth"] = self.video_width
            result["videoHeight"] = self.video_height
        if self.egress is not None:
            result["egress"] = self.egress.to_dict()
        if self.show_ai_avatar_disclosure is not None:
            result["showAIAvatarDisclosure"] = self.show_ai_avatar_disclosure
        if self.region is not None:
            result["region"] = self.region
        if self.region_policy is not None:
            result["regionPolicy"] = self.region_policy
        return result


@dataclass
class ClientOptions:
    """Optional configuration for AnamClient.

    Args:
        api_base_url: Base URL for the Anam API.
        api_version: API version to use.
        ice_servers: Custom ICE servers for WebRTC (optional).
        client_label: Custom label for session tracking (optional).
            Defaults to 'python-sdk' if not specified.
    """

    api_base_url: str = "https://api.anam.ai"
    api_version: str = "v1"
    ice_servers: list[dict[str, Any]] | None = None
    client_label: str | None = None


@dataclass
class Message:
    """A message in the conversation.

    Attributes:
        id: Unique identifier for the message.
        role: Who sent the message (user, assistant, system).
        content: The text content of the message.
        timestamp: When the message was sent (ISO format).
        interrupted: Whether the message was interrupted (for persona messages).
    """

    id: str
    role: MessageRole
    content: str
    timestamp: str = ""
    interrupted: bool = False


@dataclass
class MessageStreamEvent:
    """A streaming message event for incremental updates.

    This represents a chunk of a message that may be part of a larger message.
    Similar to the JavaScript SDK's MessageStreamEvent.

    Attributes:
        id: Unique identifier for the message (same for all chunks of the same message).
        content: The text content of this chunk.
        role: Who sent the message (user or persona).
        content_index: Index of this chunk in the message (0 = first chunk/start of speech).
        end_of_speech: Whether this is the final chunk of the message.
        interrupted: Whether the message was interrupted (for persona messages).
        correlation_id: Correlation ID for this turn, matching early user speech events when
            provided by the backend.
    """

    id: str
    content: str
    role: MessageRole
    content_index: int
    end_of_speech: bool
    interrupted: bool = False
    correlation_id: str | None = None


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

    Args:
        region: Actual region that served the session. See https://docs.anam.ai
            for available regions; additional regions may be introduced over
            time. Treat unrecognized values as informational.
    """

    session_id: str
    engine_host: str
    engine_protocol: str
    signalling_endpoint: str
    heartbeat_interval_seconds: int
    max_reconnection_attempts: int
    ice_servers: list[dict[str, Any]] = field(default_factory=list)
    region: str | None = None

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
            region=data.get("region"),
        )
