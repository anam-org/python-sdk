"""Tests for AnamClient."""

import json
import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from anam import (
    AnamClient,
    AnamEvent,
    ClientOptions,
    DirectorNotes,
    EgressDailyOptions,
    EgressOptions,
    MessageRole,
    MessageStreamEvent,
    PersonaConfig,
    Session,
    SessionOptions,
)
from anam.errors import ConfigurationError, SessionError


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
            director_notes=DirectorNotes(
                preset_style="warm",
                expressivity=0.8,
                custom_style_prompt="speak softly",
            ),
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
        assert result["directorNotes"] == {
            "presetStyle": "warm",
            "expressivity": 0.8,
            "customStylePrompt": "speak softly",
        }

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_director_notes_rejects_non_finite_expressivity(self, value: float) -> None:
        """Non-finite expressivity would serialize to invalid JSON at session start."""
        config = PersonaConfig(director_notes=DirectorNotes(expressivity=value))
        with pytest.raises(ValueError, match="expressivity must be a finite number"):
            config.to_dict()


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

    def test_to_dict_with_video_dimensions(self) -> None:
        options = SessionOptions(video_width=1152, video_height=768)
        result = options.to_dict()

        assert result == {
            "sessionReplay": {"enableSessionReplay": True},
            "videoQuality": "high",
            "videoWidth": 1152,
            "videoHeight": 768,
        }

    @pytest.mark.parametrize("value", [True, False])
    def test_to_dict_with_ai_avatar_disclosure(self, value: bool) -> None:
        options = SessionOptions(show_ai_avatar_disclosure=value)

        assert options.to_dict()["showAiAvatarDisclosure"] is value

    def test_to_dict_omits_ai_avatar_disclosure_by_default(self) -> None:
        assert "showAiAvatarDisclosure" not in SessionOptions().to_dict()

    def test_ai_avatar_disclosure_preserves_positional_egress_argument(self) -> None:
        egress = EgressOptions(
            mode="daily",
            daily=EgressDailyOptions(room_url="https://example.daily.co/room"),
        )
        options = SessionOptions(True, "high", None, None, egress)

        assert options.egress is egress
        assert options.show_ai_avatar_disclosure is None
        assert options.to_dict()["egress"]["mode"] == "daily"

    def test_invalid_video_quality_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match='video_quality must be either "high" or "auto"'):
            SessionOptions(video_quality="medium")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"video_width": 1152},
            {"video_height": 768},
        ],
    )
    def test_video_dimensions_must_be_provided_together(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(
            ValueError,
            match="video_width and video_height must be provided together",
        ):
            SessionOptions(**kwargs)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("video_width", 0),
            ("video_width", -1),
            ("video_width", 1.5),
            ("video_width", True),
            ("video_height", 0),
            ("video_height", -1),
            ("video_height", 1.5),
            ("video_height", True),
        ],
    )
    def test_video_dimensions_must_be_positive_integers(
        self, field: str, value: int | float | bool
    ) -> None:
        kwargs: dict[str, Any] = {"video_width": 1152, "video_height": 768, field: value}

        with pytest.raises(ValueError, match=f"{field} must be a positive integer"):
            SessionOptions(**kwargs)  # type: ignore[arg-type]


class TestDirectorNoteCue:
    """Tests for Session.send_director_note_cue."""

    @pytest.mark.asyncio
    async def test_sends_at_seconds_cue_payload(self) -> None:
        client = AnamClient(
            api_key="test-key",
            persona_config=PersonaConfig(avatar_id="avatar-only"),
        )
        client._streaming_client = MagicMock()
        client._streaming_client._data_channel_open = True
        client._streaming_client.send_data_message = MagicMock(return_value=True)

        session_obj = Session(client)
        await session_obj.send_director_note_cue("curious", at_seconds=0.5)

        client._streaming_client.send_data_message.assert_called_once()
        payload = json.loads(client._streaming_client.send_data_message.call_args.args[0])
        assert payload == {
            "message_type": "director_note_cue",
            "cue": {"tag": "curious"},
            "at_seconds": 0.5,
        }

    @pytest.mark.asyncio
    async def test_sends_in_seconds_cue_payload(self) -> None:
        client = AnamClient(api_key="test-key", persona_id="stateful-persona")
        client._streaming_client = MagicMock()
        client._streaming_client._data_channel_open = True
        client._streaming_client.send_data_message = MagicMock(return_value=True)

        session_obj = Session(client)
        await session_obj.send_director_note_cue("concerned", in_seconds=1.25)

        payload = json.loads(client._streaming_client.send_data_message.call_args.args[0])
        assert payload == {
            "message_type": "director_note_cue",
            "cue": {"tag": "concerned"},
            "in_seconds": 1.25,
        }

    @pytest.mark.asyncio
    async def test_forwards_payload_verbatim(self) -> None:
        """The SDK is pass-through: the backend is the single source of truth."""
        client = AnamClient(api_key="test-key", persona_id="stateful-persona")
        client._streaming_client = MagicMock()
        client._streaming_client._data_channel_open = True
        client._streaming_client.send_data_message = MagicMock(return_value=True)

        session_obj = Session(client)
        await session_obj.send_director_note_cue("warm", at_seconds=-0.1, in_seconds=2.0)

        payload = json.loads(client._streaming_client.send_data_message.call_args.args[0])
        assert payload == {
            "message_type": "director_note_cue",
            "cue": {"tag": "warm"},
            "at_seconds": -0.1,
            "in_seconds": 2.0,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["at_seconds", "in_seconds"])
    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    async def test_rejects_non_finite_timing(self, field: str, value: float) -> None:
        """Non-finite floats would serialize to invalid JSON (Infinity/NaN), which
        breaks the engine's parser, so the SDK rejects them before sending."""
        client = AnamClient(api_key="test-key", persona_id="stateful-persona")
        client._streaming_client = MagicMock()
        client._streaming_client._data_channel_open = True
        client._streaming_client.send_data_message = MagicMock(return_value=True)

        session_obj = Session(client)
        with pytest.raises(ValueError, match=f"{field} must be a finite number"):
            await session_obj.send_director_note_cue("warm", **{field: value})
        client._streaming_client.send_data_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_data_channel_send_fails(self) -> None:
        client = AnamClient(api_key="test-key", persona_id="stateful-persona")
        client._streaming_client = MagicMock()
        client._streaming_client._data_channel_open = True
        client._streaming_client.send_data_message = MagicMock(return_value=False)

        session_obj = Session(client)
        with pytest.raises(SessionError, match="Failed to send director note cue"):
            await session_obj.send_director_note_cue("surprised", at_seconds=0.0)
