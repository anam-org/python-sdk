"""Tests for AnamClient."""

import json
import math
from typing import Any, get_type_hints
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
    MessageUtterance,
    PersonaConfig,
    Session,
    SessionOptions,
)
from anam.errors import ConfigurationError, SessionError
from anam.types import SessionInfo


class TestAnamClientInit:
    """Tests for AnamClient initialization."""

    def test_requires_one_authentication_mode(self) -> None:
        """Test that one credential is required."""
        with pytest.raises(ConfigurationError, match="exactly one"):
            AnamClient(api_key="", persona_id="test-persona")

    def test_cannot_provide_both_authentication_modes(self) -> None:
        with pytest.raises(ConfigurationError, match="exactly one"):
            AnamClient(
                api_key="test-key",
                session_token="header.payload.signature",
                persona_id="test-persona",
            )

    def test_requires_persona(self) -> None:
        """Test that either persona_id or persona is required."""
        with pytest.raises(ConfigurationError, match="Either persona_id or persona"):
            AnamClient(api_key="test-key")

    def test_session_token_does_not_accept_persona_configuration(self) -> None:
        with pytest.raises(ConfigurationError, match="cannot be combined"):
            AnamClient(
                session_token="header.payload.signature",
                persona_id="test-persona",
            )

    def test_init_with_session_token(self) -> None:
        client = AnamClient(session_token="header.payload.signature")

        assert client._api_key is None
        assert client._session_token == "header.payload.signature"
        assert client._persona_config is None

    def test_legacy_positional_session_token_is_supported(self) -> None:
        client = AnamClient("header.payload.signature")

        assert client._api_key is None
        assert client._session_token == "header.payload.signature"

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
            environment={"podName": "anam-engine-abc", "engineVersion": "v5.13.2"},
        )
        client = AnamClient(
            api_key="test-key",
            persona_id="test-persona",
            options=options,
        )
        assert client._options.api_base_url == "https://custom.api.com"
        assert client._options.environment == {
            "podName": "anam-engine-abc",
            "engineVersion": "v5.13.2",
        }

    def test_environment_defaults_to_none(self) -> None:
        assert ClientOptions().environment is None

    def test_request_headers_default_to_none(self) -> None:
        assert ClientOptions().request_headers is None


class TestCoreApiClientSessionBody:
    """The session request body matches the selected authentication mode."""

    @staticmethod
    def _fake_session(captured: dict):
        response_payload = {
            "sessionId": "sess-1",
            "engineHost": "engine.example",
            "engineProtocol": "https",
            "signallingEndpoint": "wss://engine.example/ws",
        }

        class _Resp:
            status = 201

            async def json(self) -> dict:
                return response_payload

            async def __aenter__(self) -> "_Resp":
                return self

            async def __aexit__(self, *_: object) -> bool:
                return False

        class _Session:
            async def __aenter__(self) -> "_Session":
                return self

            async def __aexit__(self, *_: object) -> bool:
                return False

            def post(self, url: str, headers=None, json=None):  # noqa: A002
                captured["url"] = url
                captured["headers"] = headers
                captured["body"] = json
                return _Resp()

        return _Session

    @pytest.mark.asyncio
    async def test_environment_is_sent_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from anam._api import CoreApiClient

        captured: dict[str, Any] = {}
        monkeypatch.setattr("aiohttp.ClientSession", self._fake_session(captured))

        overrides = {"podName": "anam-engine-abc", "engineVersion": "v5.13.2"}
        client = CoreApiClient("key", ClientOptions(environment=overrides))
        await client.start_session(PersonaConfig(persona_id="p"), SessionOptions())

        assert captured["body"]["environment"] == overrides

    @pytest.mark.asyncio
    async def test_environment_is_omitted_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from anam._api import CoreApiClient

        captured: dict[str, Any] = {}
        monkeypatch.setattr("aiohttp.ClientSession", self._fake_session(captured))

        client = CoreApiClient("key", ClientOptions())
        await client.start_session(PersonaConfig(persona_id="p"), SessionOptions())

        assert "environment" not in captured["body"]

    @pytest.mark.asyncio
    async def test_pre_minted_session_token_uses_snapshot_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from anam._api import CLIENT_METADATA, CoreApiClient

        captured: dict[str, Any] = {}
        monkeypatch.setattr("aiohttp.ClientSession", self._fake_session(captured))

        token = "header.payload.signature"
        client = CoreApiClient(
            session_token=token,
            options=ClientOptions(
                client_label="must-not-override-token",
                environment={"podName": "must-not-override-token"},
            ),
        )
        await client.start_session(None, SessionOptions(video_quality="auto"))

        assert captured["headers"]["Authorization"] == f"Bearer {token}"
        assert captured["body"] == {"clientMetadata": CLIENT_METADATA}

    @pytest.mark.asyncio
    async def test_additional_request_headers_cannot_override_sdk_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from anam._api import CoreApiClient

        captured: dict[str, Any] = {}
        monkeypatch.setattr("aiohttp.ClientSession", self._fake_session(captured))

        token = "header.payload.signature"
        client = CoreApiClient(
            session_token=token,
            options=ClientOptions(
                request_headers={
                    "x-vercel-protection-bypass": "preview-secret",
                    "Authorization": "Bearer attacker-controlled",
                    "Content-Type": "text/plain",
                }
            ),
        )
        await client.start_session(None, SessionOptions())

        assert captured["headers"] == {
            "x-vercel-protection-bypass": "preview-secret",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }


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


class TestSessionMessages:
    @pytest.mark.asyncio
    async def test_pre_minted_token_can_send_message_without_local_persona(self) -> None:
        client = AnamClient(session_token="header.payload.signature")
        client._streaming_client = MagicMock()
        client._streaming_client._data_channel_open = True

        session = Session(client)
        await session.send_message("Hello")

        client._streaming_client.send_user_message.assert_called_once_with("Hello")


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


class TestAnamClientUtterances:
    """Tests for multi-utterance persona message handling."""

    @staticmethod
    def _persona_chunk(**overrides: Any) -> dict[str, Any]:
        chunk = {
            "message_id": "turn-1",
            "content_index": 0,
            "content": "Hello",
            "role": "persona",
            "end_of_speech": False,
            "interrupted": False,
        }
        chunk.update(overrides)
        return {"messageType": "speechText", "data": chunk}

    @pytest.mark.asyncio
    async def test_utterance_id_exposed_on_stream_event(self) -> None:
        """A persona chunk's utterance_id is passed through onto the stream event."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")
        handler = AsyncMock()
        client.add_listener(AnamEvent.MESSAGE_STREAM_EVENT_RECEIVED, handler)

        await client._handle_data_message(self._persona_chunk(utterance_id="uuid-a"))

        stream_event = handler.await_args.args[0]
        assert stream_event.utterance_id == "uuid-a"
        assert stream_event.id == "persona::turn-1"

    @pytest.mark.asyncio
    async def test_missing_or_empty_utterance_id_omitted(self) -> None:
        """Both a missing and an empty utterance_id collapse to None on the stream event."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")
        handler = AsyncMock()
        client.add_listener(AnamEvent.MESSAGE_STREAM_EVENT_RECEIVED, handler)

        await client._handle_data_message(self._persona_chunk())
        await client._handle_data_message(
            self._persona_chunk(content_index=1, utterance_id="")
        )

        assert handler.await_args_list[0].args[0].utterance_id is None
        assert handler.await_args_list[1].args[0].utterance_id is None

    @pytest.mark.asyncio
    async def test_history_splits_utterances_keeps_turn_shape(self) -> None:
        """Consecutive chunks merge per-utterance; a new utterance id starts a new entry."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")

        await client._handle_data_message(
            self._persona_chunk(content="Hel", utterance_id="uuid-a")
        )
        await client._handle_data_message(
            self._persona_chunk(content="lo.", content_index=1, utterance_id="uuid-a")
        )
        # New utterance in the same turn: the engine prepends the joining space, which must
        # survive verbatim in turn-level content but is stripped from the utterance itself.
        await client._handle_data_message(
            self._persona_chunk(
                content=" Second thought.",
                content_index=2,
                utterance_id="uuid-b",
                end_of_speech=True,
            )
        )

        history = client.get_message_history()
        assert len(history) == 1
        message = history[0]
        assert message.content == "Hello. Second thought."
        assert message.utterances == [
            MessageUtterance(id="uuid-a", content="Hello."),
            MessageUtterance(id="uuid-b", content="Second thought."),
        ]

    @pytest.mark.asyncio
    async def test_history_shape_unchanged_without_utterance_ids(self) -> None:
        """Messages built from chunks with no utterance_id keep utterances as None."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")

        await client._handle_data_message(self._persona_chunk(content="Hello"))
        await client._handle_data_message(
            self._persona_chunk(content=" there.", content_index=1, end_of_speech=True)
        )

        history = client.get_message_history()
        assert len(history) == 1
        assert history[0].content == "Hello there."
        assert history[0].utterances is None

    @pytest.mark.asyncio
    async def test_history_preserves_non_separator_whitespace(self) -> None:
        """Only a single leading joining space on non-first utterances is stripped."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")

        await client._handle_data_message(
            self._persona_chunk(content=" Leading", utterance_id="uuid-a")
        )
        await client._handle_data_message(
            self._persona_chunk(
                content="  Second",
                content_index=1,
                utterance_id="uuid-b",
                end_of_speech=True,
            )
        )

        history = client.get_message_history()
        assert history[0].content == " Leading  Second"
        assert history[0].utterances == [
            MessageUtterance(id="uuid-a", content=" Leading"),
            MessageUtterance(id="uuid-b", content=" Second"),
        ]

    @pytest.mark.asyncio
    async def test_published_message_is_not_mutated_by_later_chunks(self) -> None:
        """A Message snapshot handed to consumers stays frozen once later chunks arrive."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")

        await client._handle_data_message(
            self._persona_chunk(content="Hello", utterance_id="uuid-a", end_of_speech=True)
        )
        published_message = client.get_message_history()[0]

        await client._handle_data_message(
            self._persona_chunk(
                content=" again",
                content_index=1,
                utterance_id="uuid-a",
                end_of_speech=True,
            )
        )

        assert published_message.content == "Hello"
        assert published_message.utterances == [MessageUtterance(id="uuid-a", content="Hello")]
        history = client.get_message_history()
        assert history[0].content == "Hello again"
        assert history[0].utterances == [MessageUtterance(id="uuid-a", content="Hello again")]


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

        assert options.to_dict()["showAIAvatarDisclosure"] is value

    def test_to_dict_omits_ai_avatar_disclosure_by_default(self) -> None:
        assert "showAIAvatarDisclosure" not in SessionOptions().to_dict()

    def test_to_dict_with_session_region(self) -> None:
        options = SessionOptions(region="eu", region_policy="strict")

        assert options.to_dict()["region"] == "eu"
        assert options.to_dict()["regionPolicy"] == "strict"

    def test_to_dict_with_future_session_region(self) -> None:
        options = SessionOptions(region="mars", region_policy="preferred")

        assert options.to_dict()["region"] == "mars"
        assert options.to_dict()["regionPolicy"] == "preferred"

    def test_region_type_accepts_future_values(self) -> None:
        assert get_type_hints(SessionOptions)["region"] == str | None

    def test_to_dict_omits_session_region_by_default(self) -> None:
        result = SessionOptions().to_dict()

        assert "region" not in result
        assert "regionPolicy" not in result

    def test_invalid_region_policy_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match='region_policy must be either "preferred" or "strict"',
        ):
            SessionOptions(region="eu", region_policy="fallback")  # type: ignore[arg-type]

    def test_strict_region_policy_requires_region(self) -> None:
        with pytest.raises(
            ValueError,
            match='region_policy="strict" requires region to be set',
        ):
            SessionOptions(region_policy="strict")

    def test_strict_region_policy_with_region_is_valid(self) -> None:
        options = SessionOptions(region="us", region_policy="strict")

        assert options.region == "us"
        assert options.region_policy == "strict"

    def test_ai_avatar_disclosure_preserves_positional_egress_argument(self) -> None:
        egress = EgressOptions(
            mode="daily",
            daily=EgressDailyOptions(room_url="https://example.daily.co/room"),
        )
        options = SessionOptions(True, "high", None, None, egress, True)

        assert options.egress is egress
        assert options.show_ai_avatar_disclosure is True
        result = options.to_dict()
        assert result["egress"]["mode"] == "daily"
        assert list(result) == [
            "sessionReplay",
            "videoQuality",
            "egress",
            "showAIAvatarDisclosure",
        ]

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


class TestSessionInfo:
    """Tests for engine-session response parsing."""

    @staticmethod
    def api_response(**overrides: Any) -> dict[str, Any]:
        return {
            "sessionId": "session-1",
            "engineHost": "engine.test",
            "engineProtocol": "https",
            "signallingEndpoint": "/ws",
            "clientConfig": {
                "heartbeatIntervalSeconds": 5,
                "maxWsReconnectionAttempts": 3,
                "iceServers": [],
            },
            **overrides,
        }

    @pytest.mark.parametrize("region", ["us", "mars"])
    def test_from_api_response_includes_served_region(self, region: str) -> None:
        info = SessionInfo.from_api_response(self.api_response(region=region))

        assert info.region == region

    def test_region_type_accepts_future_values(self) -> None:
        assert get_type_hints(SessionInfo)["region"] == str | None

    def test_from_api_response_omits_unreported_region(self) -> None:
        info = SessionInfo.from_api_response(self.api_response())

        assert info.region is None

    def test_region_field_preserves_positional_constructor_compatibility(self) -> None:
        info = SessionInfo("session-1", "engine.test", "https", "/ws", 5, 3, [])

        assert info.region is None


class TestSessionRegionAccessors:
    """Tests for served-region access through the public client API."""

    @staticmethod
    def client_with_response(**overrides: Any) -> AnamClient:
        client = AnamClient(api_key="test-key", persona_id="stateful-persona")
        client._session_info = SessionInfo.from_api_response(
            TestSessionInfo.api_response(**overrides)
        )
        return client

    @pytest.mark.parametrize("region", ["us", "mars"])
    def test_accessors_return_served_region(self, region: str) -> None:
        client = self.client_with_response(region=region)
        session = Session(client)

        assert client.region == region
        assert session.region == region

    def test_accessors_return_none_when_region_is_unreported(self) -> None:
        client = self.client_with_response()
        session = Session(client)

        assert client.region is None
        assert session.region is None


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
