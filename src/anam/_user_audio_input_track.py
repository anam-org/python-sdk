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
from av.audio.resampler import AudioResampler

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

    def __init__(self, expected_sample_rate: Optional[int] = None, expected_channels: Optional[int] = None):
        """Initialize the user audio input track.

        Args:
            expected_sample_rate: Expected input sample rate (Hz). If None, will be
                determined from first audio chunk. Defaults to None.
            expected_channels: Expected number of channels. If None, will be determined
                from first audio chunk. Defaults to None.
        """
        super().__init__()
        self._output_sample_rate = WEBRTC_AUDIO_SAMPLE_RATE
        self._samples_per_10ms = self._output_sample_rate * 10 // 1000
        self._bytes_per_10ms = self._samples_per_10ms * 2  # 16-bit (2 bytes per sample)
        self._timestamp = 0
        self._start = time.time()
        
        # Expected input format (can be None initially)
        self._expected_sample_rate = expected_sample_rate
        self._expected_channels = expected_channels
        
        # Queue of (audio_bytes, sample_rate, num_channels) tuples
        # Audio is queued in 10ms chunks at input sample rate
        self._audio_queue: deque[tuple[bytes, int, int]] = deque()
        
        # Resampler for converting input audio to 48kHz mono
        # Created lazily when we know the input format
        self._resampler: Optional[AudioResampler] = None
        self._resampler_input_rate: Optional[int] = None
        
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

    def add_audio_samples(
        self,
        audio_bytes: bytes,
        sample_rate: int,
        num_channels: int,
    ) -> None:
        """Add raw audio samples to the track buffer.

        This method accepts raw 16-bit PCM audio bytes and queues them for
        transmission. The audio will be resampled/converted to WebRTC format
        (48kHz mono) when recv() is called by WebRTC.

        Args:
            audio_bytes: Raw audio data (16-bit PCM).
            sample_rate: Sample rate of the input audio (Hz).
            num_channels: Number of channels in the input audio (1=mono, 2=stereo).
        """
        # Validate input format matches expected (if set)
        if self._expected_sample_rate is not None and sample_rate != self._expected_sample_rate:
            logger.warning(
                f"Sample rate mismatch: expected {self._expected_sample_rate}Hz, "
                f"got {sample_rate}Hz. Resampling will occur."
            )
        if self._expected_channels is not None and num_channels != self._expected_channels:
            logger.warning(
                f"Channel count mismatch: expected {self._expected_channels}, "
                f"got {num_channels}. Conversion will occur."
            )

        # Convert to numpy array for processing
        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        
        # Handle multi-channel audio (convert to mono if needed)
        if num_channels > 1:
            samples = samples.reshape(-1, num_channels).mean(axis=1).astype(np.int16)
        
        # Calculate samples per 10ms at input sample rate
        samples_per_10ms_input = sample_rate * 10 // 1000
        bytes_per_10ms_input = samples_per_10ms_input * 2  # 16-bit
        
        # Break into 10ms chunks at input sample rate for minimal buffering
        for i in range(0, len(samples), samples_per_10ms_input):
            chunk_samples = samples[i:i + samples_per_10ms_input]
            
            # Pad last chunk if needed to make it exactly 10ms
            if len(chunk_samples) < samples_per_10ms_input:
                padding_samples = samples_per_10ms_input - len(chunk_samples)
                padding = np.zeros(padding_samples, dtype=np.int16)
                chunk_samples = np.concatenate([chunk_samples, padding])
            
            chunk_bytes = chunk_samples.astype(np.int16).tobytes()
            self._audio_queue.append((chunk_bytes, sample_rate, 1))  # Always mono after conversion

    async def recv(self) -> AudioFrame:
        """Return the next audio frame for WebRTC transmission.

        This method is called by WebRTC to get audio frames for encoding to Opus.
        It returns AudioFrame objects at 48kHz mono, resampling/converting input
        audio as necessary.

        Returns:
            An AudioFrame containing the next 10ms of audio data at 48kHz mono.
        """
        # Compute required wait time for synchronization
        if self._timestamp > 0:
            wait = self._start + (self._timestamp / self._output_sample_rate) - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

        audio_data = None
        
        async with self._lock:
            if self._audio_queue:
                audio_bytes, sample_rate, num_channels = self._audio_queue.popleft()
                
                # Convert bytes to numpy array (already mono, 16-bit PCM)
                samples = np.frombuffer(audio_bytes, dtype=np.int16)
                
                # If sample_rate is already 48kHz, no resampling needed
                if sample_rate == self._output_sample_rate:
                    # Audio is already at target sample rate (should be exactly 10ms)
                    audio_data = samples[None, :]  # Shape: (1, num_samples)
                else:
                    # Need to resample to 48kHz
                    # Create resampler lazily if needed
                    if self._resampler is None or self._resampler_input_rate != sample_rate:
                        self._resampler = AudioResampler("s16", "mono", self._output_sample_rate)
                        self._resampler_input_rate = sample_rate
                        logger.debug(
                            f"Created resampler: {sample_rate}Hz -> {self._output_sample_rate}Hz"
                        )
                    
                    # Create AudioFrame from input sample rate for resampling
                    input_frame = AudioFrame.from_ndarray(samples[None, :], layout="mono")
                    input_frame.sample_rate = sample_rate
                    input_frame.pts = 0
                    input_frame.time_base = fractions.Fraction(1, sample_rate)
                    
                    # Resample to 48kHz
                    resampled_frames = self._resampler.resample(input_frame)
                    # Collect all resampled frames and concatenate
                    resampled_arrays = []
                    for resampled_frame in resampled_frames:
                        resampled_arrays.append(resampled_frame.to_ndarray())
                    if resampled_arrays:
                        audio_data = np.concatenate(resampled_arrays, axis=1)
                    else:
                        audio_data = None

        if audio_data is None:
            # Generate silence if no audio available
            audio_data = np.zeros((1, self._samples_per_10ms), dtype=np.int16)
        else:
            # Ensure we have exactly 10ms worth of samples at 48kHz
            if audio_data.shape[1] < self._samples_per_10ms:
                # Pad with silence if we have less than 10ms (shouldn't happen often)
                padding = np.zeros((1, self._samples_per_10ms - audio_data.shape[1]), dtype=np.int16)
                audio_data = np.concatenate([audio_data, padding], axis=1)
            elif audio_data.shape[1] > self._samples_per_10ms:
                # If resampling produced more than 10ms, take first 10ms and queue the rest
                # This can happen due to resampling precision
                remaining_samples = audio_data[:, self._samples_per_10ms:]
                remaining_bytes = remaining_samples.astype(np.int16).tobytes()
                # Put remaining audio back in queue - already at 48kHz, mono
                async with self._lock:
                    self._audio_queue.appendleft((remaining_bytes, self._output_sample_rate, 1))
                audio_data = audio_data[:, :self._samples_per_10ms]

        # Create AudioFrame for WebRTC
        # WebRTC will automatically encode AudioFrame to Opus
        # We provide mono 48kHz PCM - WebRTC handles encoding/transmission
        frame = AudioFrame.from_ndarray(audio_data, layout="mono")
        frame.sample_rate = self._output_sample_rate
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, self._output_sample_rate)
        self._timestamp += self._samples_per_10ms
        
        return frame
