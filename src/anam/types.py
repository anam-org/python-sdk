"""Type definitions for the Anam SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


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

    # Persona events
    TALK_STREAM_INTERRUPTED = "talk_stream_interrupted"

    # Tool call events
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_FAILED = "tool_call_failed"

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
        persona_id: The ID of the persona to use - Only uses pre-defined personas. All other parameters are ignored, except enable_audio_passthrough.
        name: Display name for the persona (optional).
        avatar_id: The avatar to use for video (from: https://lab.anam.ai/avatars). Do not use persona_id as avatar_id.
        avatar_model: Avatar model version (e.g., 'cara-3') (optional).
        voice_id: The voice to use for audio (optional, uses persona default).
        system_prompt: Custom system prompt for the persona (optional).
        language_code: Language code (e.g., 'en', 'es') (optional).
        llm_id: LLM model to use (optional). Set to 'CUSTOMER_CLIENT_V1' to disable
            Anam's default brain for custom LLM integration.
        max_session_length_seconds: Maximum session duration (optional).
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
class SessionOptions:
    """Configuration for an Anam session.

    Args:
        enable_session_replay: If True (default), session is recorded. Set False to disable.
    """

    enable_session_replay: bool = True

    def __post_init__(self) -> None:
        self._session_replay = SessionReplayOptions(
            enable_session_replay=self.enable_session_replay
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        result["sessionReplay"] = self._session_replay.to_dict()
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
    """

    id: str
    content: str
    role: MessageRole
    content_index: int
    end_of_speech: bool
    interrupted: bool = False


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


# --- Tool Call Types ---


@dataclass
class ToolCallStartedPayload:
    """Payload emitted when a tool call starts.

    Attributes:
        event_uid: Unique event identifier.
        tool_call_id: Unique ID from the LLM for this tool call.
        tool_name: Name of the tool being called.
        tool_type: Type of tool ("client" or "server").
        tool_subtype: Subtype for server tools (e.g., "webhook", "rag").
        arguments: Arguments passed to the tool.
        timestamp: ISO timestamp of the event.
    """

    event_uid: str
    tool_call_id: str
    tool_name: str
    tool_type: str
    tool_subtype: str | None
    arguments: dict[str, Any]
    timestamp: str


@dataclass
class ToolCallCompletedPayload:
    """Payload emitted when a tool call completes successfully.

    Attributes:
        event_uid: Unique event identifier.
        tool_call_id: Unique ID from the LLM for this tool call.
        tool_name: Name of the tool that was called.
        tool_type: Type of tool ("client" or "server").
        tool_subtype: Subtype for server tools (e.g., "webhook", "rag").
        result: The result returned by the tool.
        execution_time: Time in milliseconds between started and completed.
        timestamp: ISO timestamp of the event.
        documents_accessed: Documents accessed by RAG tools (if applicable).
    """

    event_uid: str
    tool_call_id: str
    tool_name: str
    tool_type: str
    tool_subtype: str | None
    result: Any
    execution_time: float
    timestamp: str
    documents_accessed: list[str] | None = None


@dataclass
class ToolCallFailedPayload:
    """Payload emitted when a tool call fails.

    Attributes:
        event_uid: Unique event identifier.
        tool_call_id: Unique ID from the LLM for this tool call.
        tool_name: Name of the tool that failed.
        tool_type: Type of tool ("client" or "server").
        tool_subtype: Subtype for server tools (e.g., "webhook", "rag").
        error_message: Description of the error.
        execution_time: Time in milliseconds between started and failed.
        timestamp: ISO timestamp of the event.
    """

    event_uid: str
    tool_call_id: str
    tool_name: str
    tool_type: str
    tool_subtype: str | None
    error_message: str
    execution_time: float
    timestamp: str


@runtime_checkable
class ToolCallHandler(Protocol):
    """Protocol for tool call lifecycle handlers.

    Register handlers via ``AnamClient.register_tool_call_handler()`` to
    respond to tool call lifecycle events for a specific tool name.

    For **client** tools, returning a string from ``on_start`` causes the SDK
    to emit a local ``TOOL_CALL_COMPLETED`` event with that result. Raising an
    exception in ``on_start`` causes a local ``TOOL_CALL_FAILED`` event to be
    emitted. These events are handled within the SDK and do not themselves
    send results back to the engine over the data channel.

    For **server** tools, ``on_start`` is informational only — the engine
    handles execution and sends completed/failed events.
    """

    async def on_start(self, payload: ToolCallStartedPayload) -> str | None:
        """Called when a tool call starts.

        For client tools, return a string to auto-complete the call by
        emitting a local ``TOOL_CALL_COMPLETED`` event with that result.
        Return ``None`` to handle completion separately (for example, by
        emitting the completion or failure event at a later time).
        """
        ...

    async def on_complete(self, payload: ToolCallCompletedPayload) -> None:
        """Called when a tool call completes successfully."""
        ...

    async def on_fail(self, payload: ToolCallFailedPayload) -> None:
        """Called when a tool call fails."""
        ...
