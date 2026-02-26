"""Tests for TalkMessageStream."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from anam import AnamEvent
from anam._talk_message_stream import TalkMessageStream, TalkMessageStreamState


@pytest.fixture
def mock_signalling_client() -> MagicMock:
    """Create a mock SignallingClient with async send_talk_stream_input."""
    client = MagicMock()
    client.send_talk_stream_input = AsyncMock()
    return client


@pytest.fixture
def mock_anam_client() -> MagicMock:
    """Create a mock AnamClient with add_listener and remove_listener."""
    client = MagicMock()
    client.add_listener = MagicMock()
    client.remove_listener = MagicMock()
    return client


@pytest.fixture
def stream(
    mock_signalling_client: MagicMock,
    mock_anam_client: MagicMock,
) -> TalkMessageStream:
    """Create a TalkMessageStream with mocked dependencies."""
    return TalkMessageStream(
        correlation_id="test-correlation-123",
        signalling_client=mock_signalling_client,
        client=mock_anam_client,
    )


class TestTalkMessageStreamInit:
    """Tests for TalkMessageStream initialization."""

    def test_registers_interrupt_listener(
        self,
        mock_anam_client: MagicMock,
        mock_signalling_client: MagicMock,
    ) -> None:
        """Test that stream registers for TALK_STREAM_INTERRUPTED on init."""
        TalkMessageStream(
            correlation_id="cid-1",
            signalling_client=mock_signalling_client,
            client=mock_anam_client,
        )
        mock_anam_client.add_listener.assert_called_once()
        call_args = mock_anam_client.add_listener.call_args
        assert call_args[0][0] == AnamEvent.TALK_STREAM_INTERRUPTED
        assert callable(call_args[0][1])

    def test_correlation_id_property(self, stream: TalkMessageStream) -> None:
        """Test correlation_id property returns the configured value."""
        assert stream.correlation_id == "test-correlation-123"

    def test_initial_state(self, stream: TalkMessageStream) -> None:
        """Test initial state is UNSTARTED and is_active is True."""
        assert stream.state == TalkMessageStreamState.UNSTARTED
        assert stream.is_active is True


class TestTalkMessageStreamSend:
    """Tests for TalkMessageStream.send()."""

    @pytest.mark.asyncio
    async def test_first_chunk_has_start_of_speech(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
    ) -> None:
        """Test first send uses start_of_speech=True."""
        await stream.send("Hello", end_of_speech=False)

        mock_signalling_client.send_talk_stream_input.assert_called_once_with(
            content="Hello",
            correlation_id="test-correlation-123",
            start_of_speech=True,
            end_of_speech=False,
        )

    @pytest.mark.asyncio
    async def test_subsequent_chunk_has_start_of_speech_false(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
    ) -> None:
        """Test second send uses start_of_speech=False."""
        await stream.send("Hello", end_of_speech=False)
        mock_signalling_client.reset_mock()

        await stream.send(" world", end_of_speech=False)

        mock_signalling_client.send_talk_stream_input.assert_called_once_with(
            content=" world",
            correlation_id="test-correlation-123",
            start_of_speech=False,
            end_of_speech=False,
        )

    @pytest.mark.asyncio
    async def test_end_of_speech_transitions_to_ended(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
        mock_anam_client: MagicMock,
    ) -> None:
        """Test send with end_of_speech=True transitions to ENDED and deactivates."""
        await stream.send("Done", end_of_speech=True)

        assert stream.state == TalkMessageStreamState.ENDED
        assert stream.is_active is False
        mock_anam_client.remove_listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_after_ended_raises(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
    ) -> None:
        """Test send raises RuntimeError when stream is already ended."""
        await stream.send("Done", end_of_speech=True)

        with pytest.raises(RuntimeError, match="not in an active state"):
            await stream.send("More", end_of_speech=False)

        assert mock_signalling_client.send_talk_stream_input.call_count == 1

    @pytest.mark.asyncio
    async def test_send_after_interrupted_raises(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
    ) -> None:
        """Test send raises RuntimeError when stream was interrupted."""
        await stream.send("Hello", end_of_speech=False)
        stream._on_talk_stream_interrupted("test-correlation-123")

        with pytest.raises(RuntimeError, match="not in an active state"):
            await stream.send("More", end_of_speech=False)


class TestTalkMessageStreamEnd:
    """Tests for TalkMessageStream.end()."""

    @pytest.mark.asyncio
    async def test_end_sends_empty_chunk(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
    ) -> None:
        """Test end() sends empty content with end_of_speech=True."""
        await stream.send("Hello", end_of_speech=False)
        mock_signalling_client.reset_mock()

        await stream.end()

        mock_signalling_client.send_talk_stream_input.assert_called_once_with(
            content="",
            correlation_id="test-correlation-123",
            start_of_speech=False,
            end_of_speech=True,
        )
        assert stream.state == TalkMessageStreamState.ENDED

    @pytest.mark.asyncio
    async def test_end_when_already_ended_is_noop(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
    ) -> None:
        """Test end() when already ended does not send."""
        await stream.send("Done", end_of_speech=True)
        mock_signalling_client.reset_mock()

        await stream.end()

        mock_signalling_client.send_talk_stream_input.assert_not_called()

    @pytest.mark.asyncio
    async def test_end_when_unstarted_does_not_send(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
    ) -> None:
        """Test end() when never sent (UNSTARTED) does not send."""
        await stream.end()

        mock_signalling_client.send_talk_stream_input.assert_not_called()


class TestTalkMessageStreamInterruption:
    """Tests for TALK_STREAM_INTERRUPTED handling."""

    def test_matching_correlation_id_sets_interrupted(
        self,
        stream: TalkMessageStream,
        mock_anam_client: MagicMock,
    ) -> None:
        """Test interrupt event with matching correlation_id sets INTERRUPTED state."""
        assert stream.state == TalkMessageStreamState.UNSTARTED

        stream._on_talk_stream_interrupted("test-correlation-123")

        assert stream.state == TalkMessageStreamState.INTERRUPTED
        assert stream.is_active is False
        mock_anam_client.remove_listener.assert_called_once()

    def test_non_matching_correlation_id_ignored(
        self,
        stream: TalkMessageStream,
        mock_anam_client: MagicMock,
    ) -> None:
        """Test interrupt event with different correlation_id is ignored."""
        stream._on_talk_stream_interrupted("other-correlation-456")

        assert stream.state == TalkMessageStreamState.UNSTARTED
        mock_anam_client.remove_listener.assert_not_called()
