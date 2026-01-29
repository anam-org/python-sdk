"""User audio input track for sending raw audio samples to Anam via WebRTC.

This module provides a mechanism for accepting raw audio samples from Pipecat
and converting them to WebRTC-compatible format for transmission.
"""

import asyncio
import fractions
import logging

import numpy as np
from aiortc.mediastreams import AUDIO_PTIME, AudioStreamTrack, MediaStreamError
from av.audio.frame import AudioFrame

logger = logging.getLogger(__name__)


class UserAudioInputTrack(AudioStreamTrack):
    """AudioStreamTrack that accepts raw audio samples and converts to WebRTC format.

    This track accepts raw audio bytes (16-bit PCM) and converts them to AudioFrames
    for WebRTC transmission. Audio is stored in a byte buffer and converted to
    AudioFrame only when recv() is called.

    To stay close to the live point, the buffer is flushed on the first recv() call,
    keeping only the most recent chunk. This handles the case where audio accumulates
    between track connection and WebRTC starting to pull frames.
    """

    def __init__(self, sample_rate: int, num_channels: int):
        """Initialize the user audio input track.

        Args:
            sample_rate: Sample rate of the audio (Hz), e.g., 16000 or 48000.
            num_channels: Number of channels (1=mono, 2=stereo).
        """
        super().__init__()
        self._sample_rate = sample_rate
        self._num_channels = num_channels

        # Byte buffer for raw 16-bit PCM audio
        self._audio_buffer = bytearray()

        # Calculate samples per chunk (20ms frame)
        self._samples_per_chunk = int(sample_rate * AUDIO_PTIME)
        # 16-bit = 2 bytes per sample, per channel
        self._bytes_per_chunk = self._samples_per_chunk * 2 * num_channels

        # Timestamp for frame pts (in samples)
        self._timestamp = 0

        # Flag to indicate if track is closed
        self._is_closed = False

        # Flag to flush buffer on first recv() - handles audio that accumulated
        # between track connection and WebRTC starting to pull frames
        self._first_recv = True

        # Lock for thread-safe buffer access
        self._lock = asyncio.Lock()

        # Maximum buffer size for backpressure (~500ms of audio)
        # Drop oldest audio if buffer exceeds this to prevent unbounded growth
        self._max_buffer_bytes = self._bytes_per_chunk * 50

        logger.info(
            f"UserAudioInputTrack initialized: {sample_rate}Hz, {num_channels} channel(s), "
            f"{self._bytes_per_chunk} bytes per chunk"
        )

    def close(self) -> None:
        """Mark track as closed and clear audio buffer.

        After this is called, recv() will raise MediaStreamError to signal
        WebRTC to stop calling it.
        """
        self._is_closed = True
        self._audio_buffer = bytearray()
        logger.debug("UserAudioInputTrack closed")

    def add_audio_samples(
        self,
        audio_bytes: bytes,
        sample_rate: int,
        num_channels: int,
    ) -> None:
        """Add raw audio samples to the track buffer.

        Args:
            audio_bytes: Raw audio data (16-bit PCM).
            sample_rate: Sample rate of the input audio (Hz).
            num_channels: Number of channels in the input audio.
        """
        if self._is_closed:
            return

        # Validate format matches initialization
        if sample_rate != self._sample_rate:
            logger.warning(
                f"Sample rate mismatch: expected {self._sample_rate}Hz, got {sample_rate}Hz. "
                "Discarding audio."
            )
            return

        if num_channels != self._num_channels:
            logger.warning(
                f"Channel count mismatch: expected {self._num_channels}, got {num_channels}. "
                "Discarding audio."
            )
            return

        # Append to buffer
        self._audio_buffer.extend(audio_bytes)

        # Backpressure: drop oldest audio if buffer is too large
        if len(self._audio_buffer) > self._max_buffer_bytes:
            excess = len(self._audio_buffer) - self._max_buffer_bytes
            # Align to frame boundary
            excess = (excess // self._bytes_per_chunk) * self._bytes_per_chunk
            if excess > 0:
                self._audio_buffer = self._audio_buffer[excess:]
                logger.debug(f"Dropped {excess} bytes of old audio due to buffer overflow")

    async def recv(self) -> AudioFrame:
        """Return the next audio frame for WebRTC transmission.

        On the first call, flushes the buffer to stay close to the live point,
        keeping only the most recent chunk.

        Returns:
            An AudioFrame containing chunk of audio data.

        Raises:
            MediaStreamError: If the track has been closed.
        """
        if self._is_closed:
            raise MediaStreamError("Track has been closed")

        # On first recv, flush buffer to stay near live point
        # This discards audio that accumulated during connection setup
        if self._first_recv:
            async with self._lock:
                if len(self._audio_buffer) > self._bytes_per_chunk:
                    # Keep only the last chunk
                    self._audio_buffer = self._audio_buffer[-self._bytes_per_chunk :]
                    logger.debug("Flushed audio buffer on first recv to stay near live point")
            self._first_recv = False

        # Wait for enough data (chunk)
        while len(self._audio_buffer) < self._bytes_per_chunk:
            if self._is_closed:
                raise MediaStreamError("Track has been closed")
            await asyncio.sleep(0.001)  # 1ms poll

        # Extract one chunk from buffer
        async with self._lock:
            if self._is_closed:
                raise MediaStreamError("Track has been closed")

            chunk_bytes = bytes(self._audio_buffer[: self._bytes_per_chunk])
            self._audio_buffer = self._audio_buffer[self._bytes_per_chunk :]

        # Convert bytes to numpy array (16-bit PCM)
        samples = np.frombuffer(chunk_bytes, dtype=np.int16)

        # Shape for AudioFrame
        if self._num_channels == 1:
            audio_data = samples[None, :]  # Shape: (1, num_samples)
            layout = "mono"
        else:
            # Reshape interleaved stereo to (2, num_samples)
            audio_data = samples.reshape((-1, self._num_channels)).T
            layout = "stereo"

        # Create AudioFrame
        frame = AudioFrame.from_ndarray(audio_data, layout=layout)
        frame.sample_rate = self._sample_rate
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, self._sample_rate)

        self._timestamp += self._samples_per_chunk

        return frame
