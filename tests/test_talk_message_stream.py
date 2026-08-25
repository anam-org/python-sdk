"""Tests for TalkMessageStream."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from anam import AnamEvent
from anam._signalling import SignallingClient
from anam._talk_message_stream import TalkMessageStream, TalkMessageStreamState
from anam.types import SessionInfo

UTTERANCE_A = "68fd86b6-0b3a-4f42-bf92-5866cd84f8ac"
UTTERANCE_B = "d990d82a-29a5-4874-b870-a9f07e024108"


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
            utterance_id=None,
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
            utterance_id=None,
        )

    @pytest.mark.asyncio
    async def test_forwards_utterance_id_for_each_chunk(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
    ) -> None:
        """Each chunk can identify the utterance it belongs to."""
        await stream.send("Hello", utterance_id=UTTERANCE_A)
        await stream.send(" world", utterance_id=UTTERANCE_B)

        assert mock_signalling_client.send_talk_stream_input.await_args_list[0].kwargs == {
            "content": "Hello",
            "correlation_id": "test-correlation-123",
            "start_of_speech": True,
            "end_of_speech": False,
            "utterance_id": UTTERANCE_A,
        }
        assert mock_signalling_client.send_talk_stream_input.await_args_list[1].kwargs == {
            "content": " world",
            "correlation_id": "test-correlation-123",
            "start_of_speech": False,
            "end_of_speech": False,
            "utterance_id": UTTERANCE_B,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_id",
        [
            "",
            "utterance-1",
            "a8098c1a-f86e-11da-bd1a-00112444be1e",
            UTTERANCE_A.upper(),
            UTTERANCE_A.replace("-", ""),
        ],
    )
    async def test_rejects_noncanonical_or_non_v4_utterance_id(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
        bad_id: str,
    ) -> None:
        """Invalid or noncanonical UUID v4 IDs are rejected before anything is sent."""
        with pytest.raises(ValueError, match="canonical UUID v4"):
            await stream.send("Hello", utterance_id=bad_id)

        mock_signalling_client.send_talk_stream_input.assert_not_called()
        assert stream.state == TalkMessageStreamState.UNSTARTED

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
            utterance_id=None,
        )
        assert stream.state == TalkMessageStreamState.ENDED

    @pytest.mark.asyncio
    async def test_end_reuses_last_utterance_id(
        self,
        stream: TalkMessageStream,
        mock_signalling_client: MagicMock,
    ) -> None:
        """The terminator stays inside the utterance the last tagged chunk opened."""
        await stream.send("Hello", utterance_id=UTTERANCE_A)
        await stream.send(" world", utterance_id=UTTERANCE_B)
        await stream.send(" again")
        mock_signalling_client.reset_mock()

        await stream.end()

        mock_signalling_client.send_talk_stream_input.assert_called_once_with(
            content="",
            correlation_id="test-correlation-123",
            start_of_speech=False,
            end_of_speech=True,
            utterance_id=UTTERANCE_B,
        )

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


class TestTalkMessageStreamSignalling:
    """Tests for talk stream WebSocket payloads."""

    @staticmethod
    def signalling_client() -> SignallingClient:
        return SignallingClient(
            SessionInfo(
                session_id="session-1",
                engine_host="engine.example.com",
                engine_protocol="https",
                signalling_endpoint="/ws",
                heartbeat_interval_seconds=5,
                max_reconnection_attempts=5,
            )
        )

    @pytest.mark.asyncio
    async def test_includes_utterance_id_when_provided(self) -> None:
        client = self.signalling_client()
        client.send_message = AsyncMock()

        await client.send_talk_stream_input(
            content="Hello",
            correlation_id="correlation-1",
            utterance_id=UTTERANCE_A,
        )

        assert client.send_message.await_args.args[0]["payload"] == {
            "content": "Hello",
            "startOfSpeech": True,
            "endOfSpeech": True,
            "correlationId": "correlation-1",
            "utteranceId": UTTERANCE_A,
        }

    @pytest.mark.asyncio
    async def test_omits_utterance_id_when_not_provided(self) -> None:
        client = self.signalling_client()
        client.send_message = AsyncMock()

        await client.send_talk_stream_input(
            content="Hello",
            correlation_id="correlation-1",
        )

        assert "utteranceId" not in client.send_message.await_args.args[0]["payload"]
