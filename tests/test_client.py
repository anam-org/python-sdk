"""Tests for AnamClient."""

from unittest.mock import AsyncMock

import pytest

from anam import (
    AnamClient,
    AnamEvent,
    ClientOptions,
    MessageRole,
    MessageStreamEvent,
    PersonaConfig,
    SessionOptions,
)
from anam.errors import ConfigurationError


class TestAnamClientInit:
    """Tests for AnamClient initialization."""

    def test_requires_api_key(self) -> None:
        """Test that api_key is required."""
        with pytest.raises(ConfigurationError, match="api_key is required"):
            AnamClient(api_key="", persona_id="test-persona")

    def test_requires_persona(self) -> None:
        """Test that either persona_id or persona is required."""
        with pytest.raises(ConfigurationError, match="Either persona_id or persona"):
            AnamClient(api_key="test-key")

    def test_cannot_provide_both_persona_options(self) -> None:
        """Test that you can't provide both persona_id and persona_config."""
        with pytest.raises(ConfigurationError, match="not both"):
            AnamClient(
                api_key="test-key",
                persona_id="test-persona",
                persona_config=PersonaConfig(persona_id="another-persona"),
            )

    def test_init_with_persona_id(self) -> None:
        """Test initialization with just persona_id."""
        client = AnamClient(
            api_key="test-key",
            persona_id="test-persona",
        )
        assert client._api_key == "test-key"
        assert client._persona_config.persona_id == "test-persona"

    def test_init_with_persona_config(self) -> None:
        """Test initialization with full PersonaConfig."""
        persona = PersonaConfig(
            persona_id="test-persona",
            name="Test Assistant",
            system_prompt="You are a test assistant.",
        )
        client = AnamClient(
            api_key="test-key",
            persona_config=persona,
        )
        assert client._persona_config.name == "Test Assistant"
        assert client._persona_config.system_prompt == "You are a test assistant."

    def test_init_with_options(self) -> None:
        """Test initialization with ClientOptions."""
        options = ClientOptions(
            api_base_url="https://custom.api.com",
        )
        client = AnamClient(
            api_key="test-key",
            persona_id="test-persona",
            options=options,
        )
        assert client._options.api_base_url == "https://custom.api.com"


class TestAnamClientEvents:
    """Tests for event handling."""

    def test_on_decorator(self) -> None:
        """Test the @client.on() decorator."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")

        called = False

        @client.on(AnamEvent.CONNECTION_ESTABLISHED)
        async def handler() -> None:
            nonlocal called
            called = True

        # Check handler was registered
        assert len(client._event_callbacks[AnamEvent.CONNECTION_ESTABLISHED]) == 1

    def test_add_listener(self) -> None:
        """Test add_listener method."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")

        async def handler() -> None:
            pass

        client.add_listener(AnamEvent.CONNECTION_ESTABLISHED, handler)
        assert handler in client._event_callbacks[AnamEvent.CONNECTION_ESTABLISHED]

    def test_remove_listener(self) -> None:
        """Test remove_listener method."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")

        async def handler() -> None:
            pass

        client.add_listener(AnamEvent.CONNECTION_ESTABLISHED, handler)
        client.remove_listener(AnamEvent.CONNECTION_ESTABLISHED, handler)
        assert handler not in client._event_callbacks[AnamEvent.CONNECTION_ESTABLISHED]


class TestAnamClientDataMessages:
    """Tests for data channel message handling."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("message_type", "event"),
        [
            ("userSpeechStarted", AnamEvent.USER_SPEECH_STARTED),
            ("userSpeechEnded", AnamEvent.USER_SPEECH_ENDED),
        ],
    )
    async def test_user_speech_events_emit_correlation_id(
        self,
        message_type: str,
        event: AnamEvent,
    ) -> None:
        """User speech lifecycle events expose the backend correlation ID."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")
        handler = AsyncMock()
        client.add_listener(event, handler)

        await client._handle_data_message(
            {
                "messageType": message_type,
                "data": {"user_action_correlation_id": "corr-123"},
            }
        )

        handler.assert_awaited_once_with("corr-123")

    @pytest.mark.asyncio
    async def test_user_speech_started_allows_missing_correlation_id(self) -> None:
        """User speech events still emit when correlation IDs are unavailable."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")
        handler = AsyncMock()
        client.add_listener(AnamEvent.USER_SPEECH_STARTED, handler)

        await client._handle_data_message(
            {
                "messageType": "userSpeechStarted",
                "data": {},
            }
        )

        handler.assert_awaited_once_with(None)

    @pytest.mark.asyncio
    async def test_speech_text_exposes_correlation_id_on_stream_event(self) -> None:
        """Speech text events include the turn correlation ID for matching VAD events."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")
        handler = AsyncMock()
        client.add_listener(AnamEvent.MESSAGE_STREAM_EVENT_RECEIVED, handler)

        await client._handle_data_message(
            {
                "messageType": "speechText",
                "data": {
                    "message_id": "message-123",
                    "role": "user",
                    "content": "Hello there",
                    "content_index": 0,
                    "end_of_speech": True,
                    "interrupted": False,
                    "timestamp": "2026-03-18T12:00:00Z",
                    "user_action_correlation_id": "corr-123",
                },
            }
        )

        stream_event = handler.await_args.args[0]
        assert isinstance(stream_event, MessageStreamEvent)
        assert stream_event.role == MessageRole.USER
        assert stream_event.correlation_id == "corr-123"


class TestPersonaConfig:
    """Tests for PersonaConfig."""

    def test_to_dict_minimal(self) -> None:
        """Test to_dict with minimal config."""
        config = PersonaConfig(persona_id="test-id")
        result = config.to_dict()
        assert result == {"personaId": "test-id", "enableAudioPassthrough": False}

    def test_to_dict_full(self) -> None:
        """Test to_dict with all fields."""
        config = PersonaConfig(
            persona_id="test-id",
            name="Test",
            avatar_id="avatar-1",
            voice_id="voice-1",
            system_prompt="You are a test.",
            language_code="en",
            llm_id="gpt-4",
            max_session_length_seconds=300,
        )
        result = config.to_dict()
        assert result["personaId"] == "test-id"
        assert result["name"] == "Test"
        assert result["avatarId"] == "avatar-1"
        assert result["voiceId"] == "voice-1"
        assert result["systemPrompt"] == "You are a test."
        assert result["languageCode"] == "en"
        assert result["llmId"] == "gpt-4"
        assert result["maxSessionLengthSeconds"] == 300


class TestSessionOptions:
    """Tests for SessionOptions serialization."""

    def test_to_dict_defaults(self) -> None:
        options = SessionOptions()
        result = options.to_dict()

        assert result == {
            "sessionReplay": {"enableSessionReplay": True},
            "videoQuality": "high",
        }

    def test_to_dict_with_video_quality_high(self) -> None:
        options = SessionOptions(video_quality="high")
        result = options.to_dict()

        assert result == {
            "sessionReplay": {"enableSessionReplay": True},
            "videoQuality": "high",
        }

    def test_to_dict_with_video_quality_auto(self) -> None:
        options = SessionOptions(video_quality="auto")
        result = options.to_dict()

        assert result == {
            "sessionReplay": {"enableSessionReplay": True},
            "videoQuality": "auto",
        }

    def test_invalid_video_quality_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match='video_quality must be either "high" or "auto"'):
            SessionOptions(video_quality="medium")  # type: ignore[arg-type]
