"""Agent audio input stream for sending PCM audio data to the backend."""

import base64
import logging
from typing import Union

from ._signalling import SignallingClient
from .types import AgentAudioInputConfig, AgentAudioInputPayload

logger = logging.getLogger(__name__)


class AgentAudioInputStream:
    """Stream for sending PCM audio data to the agent.

    This class allows you to send raw PCM audio chunks to the backend,
    which will be processed as voice input for the agent.

    Example:
        ```python
        stream = session.create_agent_audio_input_stream(
            AgentAudioInputConfig(
                encoding="pcm_s16le",
                sample_rate=24000,
                channels=1,
            )
        )

        # Send audio chunks
        pcm_data = b"..."  # Raw PCM bytes
        await stream.send_audio_chunk(pcm_data)

        # Signal end of sequence
        await stream.end_sequence()
        ```
    """

    def __init__(
        self,
        config: AgentAudioInputConfig,
        signalling_client: SignallingClient,
    ):
        """Initialize the agent audio input stream.

        Args:
            config: Audio format configuration.
            signalling_client: Signalling client for sending messages.
        """
        self._config = config
        self._signalling_client = signalling_client
        self._sequence_number = 0

    async def send_audio_chunk(self, audio_data: Union[bytes, bytearray, str]) -> None:
        """Send PCM audio chunk to server.

        Args:
            audio_data: Raw PCM audio bytes or base64-encoded string.

        Raises:
            ValueError: If audio_data format is invalid.
        """
        # Convert to base64 if needed
        if isinstance(audio_data, str):
            base64_data = audio_data
        elif isinstance(audio_data, (bytes, bytearray)):
            base64_data = base64.b64encode(audio_data).decode("ascii")
        else:
            raise ValueError(f"audio_data must be bytes, bytearray, or str, got {type(audio_data)}")

        payload = AgentAudioInputPayload(
            audio_data=base64_data,
            encoding=self._config.encoding,
            sample_rate=self._config.sample_rate,
            channels=self._config.channels,
            sequence_number=self._sequence_number,
        )

        await self._signalling_client.send_agent_audio_input(payload)
        self._sequence_number += 1

    async def end_sequence(self) -> None:
        """Signal end of the current audio sequence/turn.

        Sends AGENT_AUDIO_INPUT_END signal message and resets sequence number.
        """
        await self._signalling_client.send_agent_audio_input_end()
        self._sequence_number = 0

    def get_sequence_number(self) -> int:
        """Get the current sequence number.

        Returns:
            Number of chunks sent in current sequence.
        """
        return self._sequence_number

    def get_config(self) -> AgentAudioInputConfig:
        """Get the audio format configuration for this stream.

        Returns:
            The audio configuration.
        """
        return self._config
