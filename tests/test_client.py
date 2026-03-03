"""Tests for AnamClient."""

import pytest

from anam import (
    AnamClient,
    AnamEvent,
    ClientOptions,
    ClientToolEvent,
    PersonaConfig,
    ReasoningMessage,
    ReasoningStreamEvent,
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


class TestClientToolEvent:
    """Tests for client tool event handling."""

    @pytest.mark.asyncio
    async def test_handle_client_tool_event_emits_event(self) -> None:
        """Test that clientToolEvent data channel messages emit CLIENT_TOOL_EVENT_RECEIVED."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")

        received_events: list[ClientToolEvent] = []

        @client.on(AnamEvent.CLIENT_TOOL_EVENT_RECEIVED)
        async def on_tool_event(event: ClientToolEvent) -> None:
            received_events.append(event)

        # Simulate data channel message (WebRTC format)
        data = {
            "messageType": "clientToolEvent",
            "data": {
                "event_uid": "evt-123",
                "session_id": "sess-456",
                "event_name": "redirect",
                "event_data": {"url": "https://example.com"},
                "timestamp": "2024-01-15T10:00:00Z",
                "timestamp_user_action": "2024-01-15T09:59:59Z",
                "user_action_correlation_id": "corr-789",
            },
        }

        await client._handle_data_message(data)

        assert len(received_events) == 1
        event = received_events[0]
        assert event.event_uid == "evt-123"
        assert event.session_id == "sess-456"
        assert event.event_name == "redirect"
        assert event.event_data == {"url": "https://example.com"}
        assert event.timestamp == "2024-01-15T10:00:00Z"
        assert event.timestamp_user_action == "2024-01-15T09:59:59Z"
        assert event.user_action_correlation_id == "corr-789"


class TestReasoningStreamEvent:
    """Tests for reasoning stream event handling."""

    @pytest.mark.asyncio
    async def test_handle_reasoning_text_emits_stream_event(self) -> None:
        """Test that reasoningText data channel messages emit REASONING_STREAM_EVENT_RECEIVED."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")

        received_events: list[ReasoningStreamEvent] = []

        @client.on(AnamEvent.REASONING_STREAM_EVENT_RECEIVED)
        async def on_reasoning_event(event: ReasoningStreamEvent) -> None:
            received_events.append(event)

        data = {
            "messageType": "reasoningText",
            "data": {
                "message_id": "msg-123",
                "content_index": 0,
                "content": "Let me think...",
                "role": "persona",
                "end_of_thought": False,
            },
        }

        await client._handle_data_message(data)

        assert len(received_events) == 1
        event = received_events[0]
        assert event.id == "persona::msg-123"
        assert event.content == "Let me think..."
        assert event.role == "persona"
        assert event.end_of_thought is False

    @pytest.mark.asyncio
    async def test_reasoning_end_of_thought_emits_history_updated(self) -> None:
        """Test that end_of_thought=True emits REASONING_HISTORY_UPDATED."""
        client = AnamClient(api_key="test-key", persona_id="test-persona")

        stream_events: list[ReasoningStreamEvent] = []
        history_updates: list[list[ReasoningMessage]] = []

        @client.on(AnamEvent.REASONING_STREAM_EVENT_RECEIVED)
        async def on_stream(event: ReasoningStreamEvent) -> None:
            stream_events.append(event)

        @client.on(AnamEvent.REASONING_HISTORY_UPDATED)
        async def on_history(messages: list[ReasoningMessage]) -> None:
            history_updates.append(messages)

        # First chunk
        await client._handle_data_message({
            "messageType": "reasoningText",
            "data": {
                "message_id": "msg-1",
                "content_index": 0,
                "content": "First ",
                "role": "persona",
                "end_of_thought": False,
            },
        })
        # Second chunk (end of thought)
        await client._handle_data_message({
            "messageType": "reasoningText",
            "data": {
                "message_id": "msg-1",
                "content_index": 1,
                "content": "part.",
                "role": "persona",
                "end_of_thought": True,
            },
        })

        assert len(stream_events) == 2
        assert stream_events[1].end_of_thought is True
        assert len(history_updates) == 1
        assert len(history_updates[0]) == 1
        assert history_updates[0][0].content == "First part."
        assert history_updates[0][0].id == "persona::msg-1"


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
