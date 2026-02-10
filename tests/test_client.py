"""Tests for AnamClient."""

import pytest

from anam import AnamClient, AnamEvent, ClientOptions, PersonaConfig
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
