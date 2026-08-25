"""Talk message stream for sending streaming text to TTS via WebSocket signalling."""

import logging
import uuid
from enum import Enum
from typing import TYPE_CHECKING

from ._signalling import SignallingClient
from .types import AnamEvent

if TYPE_CHECKING:
    from .client import AnamClient

logger = logging.getLogger(__name__)


class TalkMessageStreamState(str, Enum):
    """State of a talk message stream."""

    UNSTARTED = "unstarted"
    STREAMING = "streaming"
    INTERRUPTED = "interrupted"
    ENDED = "ended"


class TalkMessageStream:
    """Stream for sending text chunks to TTS with a stable correlation ID.

    Manages correlation_id internally so callers don't need to track it across
    chunks. All chunks in the same speech sequence share the same correlation_id,
    which is used for interruption correlation. Callers can optionally identify
    utterances within the sequence by passing utterance_id to send(). Set an ID on the
    first chunk of an utterance, then omit it on continuation chunks. A new ID queues the
    next utterance after the current one. This can keep speech in sequence around a short
    tool call, but the server closes a stream after 15 seconds without a chunk containing
    text. Empty chunks do not reset the timeout; use a new stream for longer tool calls.
    Cara-3 avatars silently ignore utterance IDs. Speech still plays, but the IDs do not
    create utterance boundaries and are not returned on persona message events.

    Example:
        ```python
        import uuid

        # Streaming multiple chunks
        stream = session.create_talk_stream()
        for i, chunk in enumerate(llm_chunks):
            await stream.send(chunk, end_of_speech=(i == len(llm_chunks) - 1))

        # Single message (or use session.send_talk_stream for convenience)
        stream = session.create_talk_stream()
        await stream.send("Hello!", end_of_speech=True)

        # Speech before and after a short tool call
        stream = session.create_talk_stream()
        await stream.send("Let me ", utterance_id=str(uuid.uuid4()))
        await stream.send("check.")  # Continue the same utterance without an ID.
        tool_result_text = await run_tool_call()
        await stream.send(
            tool_result_text,
            end_of_speech=True,
            utterance_id=str(uuid.uuid4()),
        )
        ```
    """

    def __init__(
        self,
        correlation_id: str,
        signalling_client: SignallingClient,
        client: "AnamClient",
    ):
        """Initialize the talk message stream.

        Args:
            correlation_id: ID to correlate this stream with interruptions.
            signalling_client: Signalling client for sending messages.
            client: AnamClient for registering interrupt listener.
        """
        self._correlation_id = correlation_id
        self._signalling_client = signalling_client
        self._client = client
        self._state = TalkMessageStreamState.UNSTARTED
        self._last_utterance_id: str | None = None
        self._interrupt_handler = self._on_talk_stream_interrupted
        client.add_listener(AnamEvent.TALK_STREAM_INTERRUPTED, self._interrupt_handler)

    def _on_talk_stream_interrupted(self, correlation_id: str) -> None:
        """Handle TALK_STREAM_INTERRUPTED event if it matches this stream."""
        if correlation_id == self._correlation_id:
            self._state = TalkMessageStreamState.INTERRUPTED
            self._deactivate()

    @property
    def correlation_id(self) -> str:
        """The correlation ID for this stream (for interruption correlation)."""
        return self._correlation_id

    @property
    def is_active(self) -> bool:
        """Whether the stream can accept more data."""
        return self._state in (
            TalkMessageStreamState.UNSTARTED,
            TalkMessageStreamState.STREAMING,
        )

    @property
    def state(self) -> TalkMessageStreamState:
        """Current state of the stream."""
        return self._state

    async def send(
        self,
        content: str,
        end_of_speech: bool = False,
        utterance_id: str | None = None,
    ) -> None:
        """Send a text chunk to TTS.

        Args:
            content: The text chunk to speak.
            end_of_speech: Whether this is the final chunk of the speech.
            utterance_id: Optional canonical UUID v4 string identifying the utterance this
                chunk starts. Set it on the first chunk, not on every new text chunk; None
                continues the current utterance. A new ID queues the next utterance after
                the current one without ending the speech sequence. This allows speech
                before and after a short tool call, or two ready utterances that must play
                in order. The server closes the stream after 15 seconds without a chunk
                containing text; empty chunks do not reset that timeout. The most recent
                non-None value is reused for the terminator sent by end(). Cara-3 avatars
                silently ignore this value, so it does not create a boundary or appear on
                persona message events.

        Raises:
            RuntimeError: If the stream is not in an active state (already
                ended or interrupted).
            ValueError: If utterance_id is not a canonical UUID v4 string.
        """
        if self._state not in (
            TalkMessageStreamState.UNSTARTED,
            TalkMessageStreamState.STREAMING,
        ):
            raise RuntimeError(f"Talk stream is not in an active state: {self._state}")

        if utterance_id is not None:
            try:
                parsed_utterance_id = uuid.UUID(utterance_id)
            except (AttributeError, TypeError, ValueError):
                parsed_utterance_id = None
            if (
                parsed_utterance_id is None
                or parsed_utterance_id.version != 4
                or str(parsed_utterance_id) != utterance_id
            ):
                raise ValueError(
                    f"utterance_id must be a canonical UUID v4 string, got {utterance_id!r}"
                )

        start_of_speech = self._state == TalkMessageStreamState.UNSTARTED

        await self._signalling_client.send_talk_stream_input(
            content=content,
            correlation_id=self._correlation_id,
            start_of_speech=start_of_speech,
            end_of_speech=end_of_speech,
            utterance_id=utterance_id,
        )

        if utterance_id is not None:
            self._last_utterance_id = utterance_id
        self._state = TalkMessageStreamState.STREAMING
        if end_of_speech:
            self._state = TalkMessageStreamState.ENDED
            self._deactivate()

    async def end(self) -> None:
        """Signal end of speech with an empty chunk.

        Use when you've sent all content chunks but need to explicitly end
        the stream. No-op if the stream is already ended.

        The terminator carries the most recent utterance_id passed to send(), so it
        does not start a new untagged utterance.
        """
        if self._state == TalkMessageStreamState.ENDED:
            logger.debug("Talk stream is already ended via end of speech. No need to call end().")
            return

        if self._state != TalkMessageStreamState.STREAMING:
            logger.warning("Talk stream is not in streaming state: %s", self._state)
            return

        await self._signalling_client.send_talk_stream_input(
            content="",
            correlation_id=self._correlation_id,
            start_of_speech=False,
            end_of_speech=True,
            utterance_id=self._last_utterance_id,
        )
        self._state = TalkMessageStreamState.ENDED
        self._deactivate()

    def _deactivate(self) -> None:
        """Clean up listeners when stream ends or is interrupted."""
        self._client.remove_listener(AnamEvent.TALK_STREAM_INTERRUPTED, self._interrupt_handler)
