"""User audio input track for sending raw audio samples to Anam via WebRTC.

This module provides a mechanism for accepting raw audio samples from Pipecat
and converting them to WebRTC-compatible format (48kHz mono) for transmission.
"""

import asyncio
import fractions
import logging
import time
from collections import deque
from typing import Optional

import numpy as np
from aiortc.mediastreams import AudioStreamTrack
from av.audio.frame import AudioFrame

logger = logging.getLogger(__name__)

# WebRTC standard audio sample rate
WEBRTC_AUDIO_SAMPLE_RATE = 48000


class UserAudioInputTrack(AudioStreamTrack):
    """AudioStreamTrack that accepts raw audio samples and converts to WebRTC format.

    This track accepts raw audio bytes (16-bit PCM) with variable sample rates
    and channel counts, and converts them to WebRTC-compatible format (48kHz mono).
    The track is created lazily when first audio arrives, minimizing latency.

    The track buffers audio in small chunks (10ms) and handles resampling/conversion
    only when necessary. If WebRTC/Opus can handle the input format directly,
    minimal processing is performed.
    """

    def __init__(self):
        """Initialize the user audio input track.

        The track determines audio format (sample rate, channels) from the actual
        audio data received via add_audio_samples(). No assumptions are made about
        the input format.
        """
        super().__init__()
        self._output_sample_rate = WEBRTC_AUDIO_SAMPLE_RATE
        self._samples_per_10ms = self._output_sample_rate * 10 // 1000
        self._bytes_per_10ms = self._samples_per_10ms * 2  # 16-bit (2 bytes per sample)
        self._timestamp = 0
        self._start = time.time()

        # Queue of (audio_bytes, sample_rate, num_channels) tuples
        # Audio is queued in 10ms chunks at input sample rate
        self._audio_queue: deque[tuple[bytes, int, int]] = deque()

        # Track current sample rate for timing calculations
        # Set from actual audio data when first chunk is processed
        self._current_sample_rate: Optional[int] = None

        # Flag to indicate if connection is closed - prevents generating frames after disconnect
        self._is_closed = False

        # Flag to track if this is the first recv() call - flush buffer on first call
        # This handles the case where audio arrives between connection established and WebRTC starting to pull
        self._first_recv = True

        # Maximum queue size for backpressure (approximately 1 second at 16kHz = 100 chunks)
        # If queue exceeds this, we drop old audio to prevent unbounded growth
        self._max_queue_size = 100

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

    def flush(self) -> None:
        """Flush the audio queue to discard any buffered audio.

        This should be called when the WebRTC connection is established to ensure
        we start with live audio immediately instead of catching up on buffered audio.

        Preserves the last chunk(s) from the queue to maintain format information
        (sample rate, channels) so we don't generate silence at wrong format.
        """
        queue_size = len(self._audio_queue)
        if not self._audio_queue or queue_size == 0:
            logger.debug("Audio queue is empty - nothing to flush")
            return

        # Preserve the last chunk to maintain format information
        # This ensures we know the sample rate/channels before generating any silence
        last_chunk = self._audio_queue[-1]

        # Clear the queue
        self._audio_queue.clear()

        # Put the last chunk back if we have one
        # This preserves format info while discarding old buffered audio
        self._audio_queue.append(last_chunk)
        # Update current sample rate from the preserved chunk
        _, sample_rate, _ = last_chunk
        self._current_sample_rate = sample_rate
        logger.info(
            f"Flushed audio queue: discarded {queue_size - 1} buffered audio chunks, "
            f"preserved last chunk at {sample_rate}Hz to maintain format"
        )

    def close(self) -> None:
        """Mark track as closed and clear audio queue to prevent further frame generation.

        This method should be called when the connection is closing to stop WebRTC
        from continuing to pull audio frames. After this is called, recv() will raise
        MediaStreamError to signal WebRTC to stop calling it.
        """
        self._is_closed = True
        # Clear the queue to prevent processing any remaining queued audio
        # Note: We don't use the lock here because this is called during cleanup
        # and we want to be sure the flag is set immediately
        self._audio_queue.clear()
        logger.debug("UserAudioInputTrack closed - cleared audio queue and marked as closed")

    def add_audio_samples(
        self,
        audio_bytes: bytes,
        sample_rate: int,
        num_channels: int,
    ) -> None:
        """Add raw audio samples to the track buffer.

        This method accepts raw 16-bit PCM audio bytes and queues them for
        transmission. The audio format (sample rate, channels) is determined
        from the actual audio data provided.

        Args:
            audio_bytes: Raw audio data (16-bit PCM).
            sample_rate: Sample rate of the input audio (Hz).
            num_channels: Number of channels in the input audio (1=mono, 2=stereo).
        """
        # Convert to numpy array for processing
        samples = np.frombuffer(audio_bytes, dtype=np.int16)

        # Handle multi-channel audio (convert to mono if needed)
        if num_channels > 1:
            samples = samples.reshape(-1, num_channels).mean(axis=1).astype(np.int16)

        # Calculate samples per 10ms at input sample rate
        samples_per_10ms_input = sample_rate * 10 // 1000

        # Break into 10ms chunks at input sample rate for minimal buffering
        for i in range(0, len(samples), samples_per_10ms_input):
            chunk_samples = samples[i:i + samples_per_10ms_input]

            # Pad last chunk if needed to make it exactly 10ms
            if len(chunk_samples) < samples_per_10ms_input:
                padding_samples = samples_per_10ms_input - len(chunk_samples)
                padding = np.zeros(padding_samples, dtype=np.int16)
                chunk_samples = np.concatenate([chunk_samples, padding])

            chunk_bytes = chunk_samples.astype(np.int16).tobytes()

            # Apply backpressure: if queue is too large, drop oldest audio
            # This prevents unbounded memory growth when audio arrives faster than WebRTC can consume
            if len(self._audio_queue) >= self._max_queue_size:
                self._audio_queue.popleft()
                logger.debug(
                    f"Queue full ({len(self._audio_queue)} items) - dropping oldest chunk "
                    f"to apply backpressure"
                )

            self._audio_queue.append((chunk_bytes, sample_rate, 1))  # Always mono after conversion

    async def recv(self) -> AudioFrame:
        """Return the next audio frame for WebRTC transmission.

        This method sends audio at its original sample rate. The Opus encoder
        handles resampling internally.

        Returns:
            An AudioFrame containing the next 10ms of audio data at original sample rate.

        Raises:
            MediaStreamError: If the track has been closed.
        """
        # Check if track has been closed - raise error to stop WebRTC from calling recv()
        if self._is_closed:
            from aiortc.mediastreams import MediaStreamError
            raise MediaStreamError("Track has been closed")

        # Flush buffer on first recv() call to catch any audio that arrived between
        # connection established and WebRTC starting to pull frames
        if self._first_recv:
            self._first_recv = False
            self.flush()

        audio_data = None
        current_sample_rate = None
        samples_per_chunk = None

        async with self._lock:
            # Double-check after acquiring lock (race condition protection)
            if self._is_closed:
                from aiortc.mediastreams import MediaStreamError
                raise MediaStreamError("Track has been closed")

            if self._audio_queue:
                # Process audio from queue - format is determined from actual audio data
                audio_bytes, sample_rate, num_channels = self._audio_queue.popleft()
                current_sample_rate = sample_rate
                samples_per_chunk = sample_rate * 10 // 1000

                # Convert bytes to numpy array (already mono, 16-bit PCM)
                samples = np.frombuffer(audio_bytes, dtype=np.int16)

                # Use audio at original sample rate - Opus encoder handles resampling internally
                audio_data = samples[None, :]  # Shape: (1, num_samples)

                # Update current sample rate tracking
                self._current_sample_rate = sample_rate

        # Generate silence if no audio available, using known sample rate
        if audio_data is None:
            if self._current_sample_rate is not None:
                # We've seen audio before - generate silence at the same sample rate
                current_sample_rate = self._current_sample_rate
                samples_per_chunk = current_sample_rate * 10 // 1000
                audio_data = np.zeros((1, samples_per_chunk), dtype=np.int16)
            else:
                from aiortc.mediastreams import MediaStreamError
                raise MediaStreamError("aiortc called recv() but no samples have been queued.")
        else:
            # Ensure we have exactly 10ms worth of samples at current sample rate
            if audio_data.shape[1] < samples_per_chunk:
                # Pad with silence if we have less than 10ms
                padding = np.zeros((1, samples_per_chunk - audio_data.shape[1]), dtype=np.int16)
                audio_data = np.concatenate([audio_data, padding], axis=1)
            elif audio_data.shape[1] > samples_per_chunk:
                # Queue the rest if we have more than 10ms
                remaining_samples = audio_data[:, samples_per_chunk:]
                remaining_bytes = remaining_samples.astype(np.int16).tobytes()
                async with self._lock:
                    self._audio_queue.appendleft((remaining_bytes, current_sample_rate, 1))
                audio_data = audio_data[:, :samples_per_chunk]

        # Compute required wait time for synchronization using current sample rate
        if self._timestamp > 0:
            wait = self._start + (self._timestamp / current_sample_rate) - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

        # Create AudioFrame for WebRTC at original sample rate
        # Opus encoder handles resampling internally
        frame = AudioFrame.from_ndarray(audio_data, layout="mono")
        frame.sample_rate = current_sample_rate
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, current_sample_rate)
        self._timestamp += samples_per_chunk
        return frame
