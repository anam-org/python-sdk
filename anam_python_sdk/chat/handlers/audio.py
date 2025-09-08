"""Handlers for audio data in the webRTC connection."""
import logging
import asyncio
import wave
import queue
import threading
from fractions import Fraction
import time

import av
import numpy as np
import sounddevice as sd
import pyaudio
from aiortc.contrib.media import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError

class AudioSetupError(Exception):
    """Exception raised when audio setup fails."""
class AudioTrackCreationError(Exception):
    """Exception raised when an AudioTrack cannot be created."""

class AudioHandler:
    """
    AudioHandler class for handling audio data in the webRTC connection.

    This class is responsible for handling audio data, including playing and saving audio.
    """
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.audio_tasks = []
        self.avatar_speaking = False
        self.audio_queue = queue.Queue(maxsize=100)
        self.playback_thread = None
        self.stop_playback = False

    def handle_avatar_audio(self, track):
        self.logger.debug("Setting up audio playback")
        
        # Start the continuous playback thread if not already running
        if self.playback_thread is None or not self.playback_thread.is_alive():
            self.stop_playback = False
            self.playback_thread = threading.Thread(target=self._continuous_playback)
            self.playback_thread.daemon = True
            self.playback_thread.start()

        async def receive_audio():
            sample_rate = 48000  # WebRTC typically decodes to 48kHz
            frame_count = 0
            
            while True:
                try:
                    frame = await track.recv()
                    frame_count += 1
                    
                    # # Log frame properties for first few frames to debug
                    # if frame_count <= 5:
                    #     self.logger.info(f"Frame {frame_count} properties:")
                    #     self.logger.info(f"  - sample_rate: {getattr(frame, 'sample_rate', 'N/A')}")
                    #     self.logger.info(f"  - samples: {getattr(frame, 'samples', 'N/A')}")
                    #     self.logger.info(f"  - format: {getattr(frame.format, 'name', 'N/A') if hasattr(frame, 'format') else 'N/A'}")
                    #     self.logger.info(f"  - pts: {getattr(frame, 'pts', 'N/A')}")
                    #     self.logger.info(f"  - time_base: {getattr(frame, 'time_base', 'N/A')}")
                    
                    # Get frame sample rate if available
                    if hasattr(frame, 'sample_rate') and frame.sample_rate:
                        if frame.sample_rate != sample_rate:
                            sample_rate = frame.sample_rate
                            self.logger.info(f"Sample rate changed to: {sample_rate}Hz")
                    
                    # Get audio data as numpy array
                    audio_data = frame.to_ndarray()
                    
                    # if frame_count <= 5:
                    #     self.logger.info(f"  - audio shape: {audio_data.shape}")
                    #     self.logger.info(f"  - audio dtype: {audio_data.dtype}")
                    
                    # Check for sample count mismatch - this might indicate resampling
                    actual_samples = audio_data.shape[-1] if len(audio_data.shape) >= 1 else 0
                    reported_samples = getattr(frame, 'samples', actual_samples)
                    
                    # The mismatch might be due to Opus decoder resampling
                    # If we're getting 2x samples, the audio might need to be played at the reported rate
                    if actual_samples == 1920 and reported_samples == 960:
                        # This looks like stereo audio being decoded as mono with doubled samples
                        # Or 16kHz being upsampled to 48kHz with interpolation
                        # For now, trust the frame's sample_rate
                        if frame_count == 1:
                            self.logger.info(f"Detected 2x sample expansion (likely from Opus decoder)")
                            self.logger.info(f"Using frame sample rate: {sample_rate}Hz")
                    
                    # Handle different audio layouts
                    if len(audio_data.shape) == 2:
                        # Check if we have the (1, 1920) shape issue
                        if audio_data.shape == (1, 1920) and reported_samples == 960:
                            # This is likely interleaved stereo data
                            # Extract every other sample to get mono audio
                            audio_data = audio_data.flatten()[::2]  # Take left channel only
                            if frame_count == 1:
                                self.logger.info(f"Extracted mono from interleaved stereo: shape {audio_data.shape}")
                        elif audio_data.shape[0] == 1:
                            # Regular mono audio, just flatten
                            audio_data = audio_data.flatten()
                        elif audio_data.shape[0] == 2:
                            # Stereo (2, samples), convert to mono
                            audio_data = audio_data.mean(axis=0)
                        else:
                            # For other shapes, flatten
                            audio_data = audio_data.flatten()
                    
                    # Convert data type if needed
                    if audio_data.dtype != np.float32:
                        if audio_data.dtype == np.int16:
                            # Convert int16 to float32 in range [-1, 1]
                            audio_data = audio_data.astype(np.float32) / 32768.0
                        elif audio_data.dtype == np.int32:
                            # Convert int32 to float32 in range [-1, 1]
                            audio_data = audio_data.astype(np.float32) / 2147483648.0
                        else:
                            # For other types, just convert to float32
                            audio_data = audio_data.astype(np.float32)
                    
                    # Ensure audio is in valid range [-1, 1]
                    audio_data = np.clip(audio_data, -1.0, 1.0)
                    
                    # Add to queue for continuous playback
                    try:
                        self.audio_queue.put_nowait((audio_data, sample_rate))
                        if not self.avatar_speaking:
                            self.avatar_speaking = True
                            self.logger.debug("Avatar started speaking")
                    except queue.Full:
                        self.logger.warning("Audio queue full, dropping frame")
                        
                except MediaStreamError:
                    self.logger.debug("Media stream ended")
                    if self.avatar_speaking:
                        self.avatar_speaking = False
                        self.logger.debug("Avatar stopped speaking")
                    break
                except Exception as e:
                    self.logger.error(f"Error receiving audio: {type(e).__name__}: {str(e)}")
                    await asyncio.sleep(0.1)  # Small delay before retrying
                    continue
                    
        asyncio.create_task(receive_audio())
    
    def _continuous_playback(self):
        """Continuous audio playback thread that consumes from the queue."""
        stream = None
        current_sample_rate = 48000
        
        try:
            while not self.stop_playback:
                try:
                    # Get audio data from queue with timeout
                    audio_data, sample_rate = self.audio_queue.get(timeout=0.1)
                    
                    # If sample rate changed, recreate the stream
                    if stream is None or sample_rate != current_sample_rate:
                        if stream is not None:
                            stream.stop()
                            stream.close()
                        
                        current_sample_rate = sample_rate
                        # Determine number of channels based on audio data shape
                        if audio_data.ndim == 1:
                            channels = 1  # Mono
                        elif audio_data.ndim == 2:
                            channels = audio_data.shape[1]  # Stereo or multi-channel
                        else:
                            channels = 1  # Default to mono
                        
                        # Create a continuous output stream
                        stream = sd.OutputStream(
                            samplerate=current_sample_rate,
                            channels=channels,
                            dtype='float32',
                            blocksize=1024,
                            latency='low'
                        )
                        stream.start()
                        self.logger.debug(f"Created audio stream: {current_sample_rate}Hz, {channels} channel(s)")
                    
                    # Write audio data to stream
                    if stream is not None:
                        stream.write(audio_data)
                        
                except queue.Empty:
                    # No audio data available
                    continue
                except Exception as e:
                    self.logger.error(f"Error in playback thread: {type(e).__name__}: {str(e)}")
                    
        finally:
            if stream is not None:
                stream.stop()
                stream.close()
            self.logger.debug("Audio playback thread stopped")
    
    def stop(self):
        """Stop the audio handler and cleanup resources."""
        self.stop_playback = True
        if self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=1.0)
        # Clear any remaining audio in the queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    async def handle_audio_track_write(self, track):
        """Save the audio track to a WAV file."""
        self.logger.debug("Setting up audio saving")

        wav_file = wave.open("received_audio.wav", "wb")
        wav_file.setnchannels(1)  # Mono audio
        wav_file.setsampwidth(2)  # 16-bit audio
        wav_file.setframerate(48000)  # 48 kHz sample rate

        total_frames = 0

        try:
            self.logger.debug("Audio saving started")
            while True:
                try:
                    frame = await track.recv()
                    audio = frame.to_ndarray().flatten()
                    audio_int16 = (audio * 32767).astype(np.int16)
                    wav_file.writeframes(audio_int16.tobytes())
                    total_frames += len(audio_int16)
                except MediaStreamError:
                    break
        finally:
            wav_file.setnframes(total_frames)
            wav_file.close()
            self.logger.debug(f"Audio saving completed. Total frames: {total_frames}")
        self.logger.debug("Audio file saved successfully")

class SimpleAudioTrack(MediaStreamTrack):
    """Simple audio track using PyAudio for microphone input."""
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.RATE = 48000
        self.CHANNELS = 1
        self.AUDIO_PTIME = 0.020  # 20ms audio packetization
        self.SAMPLES = int(self.AUDIO_PTIME * self.RATE)  # 960 samples
        
        self._timestamp = 0
        self._start_time = None
        
        # Initialize PyAudio
        self.audio = pyaudio.PyAudio()
        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.SAMPLES
        )
        
        # Force track to be "live" - this fixes the issue where recv() is never called
        self._started = True
        
        logging.info(f"SimpleAudioTrack initialized: {self.RATE}Hz, {self.SAMPLES} samples/frame")

    async def recv(self):
        """Read audio from microphone and return as AudioFrame."""
        if self._start_time is None:
            self._start_time = time.time()
            logging.info("SimpleAudioTrack.recv() called for first time")
        
        # Read audio data
        try:
            audio_data = self.stream.read(self.SAMPLES, exception_on_overflow=False)
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            
            # Create audio frame
            frame = av.AudioFrame(format='s16', layout='mono', samples=self.SAMPLES)
            frame.planes[0].update(audio_array.tobytes())
            frame.pts = self._timestamp
            frame.sample_rate = self.RATE
            frame.time_base = Fraction(1, self.RATE)
            
            self._timestamp += self.SAMPLES
            
            # Add timing to simulate real-time capture
            pts_time = self._timestamp / self.RATE
            elapsed_time = time.time() - self._start_time
            wait_time = pts_time - elapsed_time
            
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            
            return frame
        except Exception as e:
            logging.error(f"Error in SimpleAudioTrack.recv(): {e}")
            raise

    def stop(self):
        """Stop the audio stream."""
        if hasattr(self, 'stream') and self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if hasattr(self, 'audio'):
            self.audio.terminate()


class AudioStreamTrack(MediaStreamTrack):
    """Audio stream track for handling audio data."""
    kind = "audio"

    def __init__(self, device_name, audio_handler):
        super().__init__()
        self.device_name = device_name
        self.audio_handler = audio_handler
        self._start = None
        self._sample_rate = 48000
        self._samples_per_frame = 960  # 20ms at 48kHz
        self._audio_samples = 0
        self._recv_called = False
        self.stream = None
        self._initialized = False
        
        logging.info(f"AudioStreamTrack initialized with device: {device_name}")
        
        # Log a warning if recv is never called
        asyncio.get_event_loop().call_later(2.0, self._check_recv_called)

    def _check_recv_called(self):
        """Check if recv has been called after 2 seconds."""
        if not self._recv_called:
            logging.error("AudioStreamTrack.recv() was never called! Track might not be properly connected.")
    
    def _initialize_stream(self):
        """Initialize the audio stream on first use."""
        if not self._initialized:
            self.stream = sd.InputStream(
                device=self.device_name,
                channels=1,
                samplerate=self._sample_rate,
                blocksize=self._samples_per_frame,
                dtype='int16',
                latency='low'
            )
            self.stream.start()
            self._initialized = True
            logging.info(f"Audio stream started for device: {self.device_name}")
    
    async def recv(self):
        """Continuously read audio data from the microphone and return it as an audio frame."""
        self._recv_called = True
        
        # Initialize stream on first call
        if not self._initialized:
            self._initialize_stream()
        
        if self._start is None:
            self._start = asyncio.get_event_loop().time()
            logging.info("AudioStreamTrack.recv() called for the first time")
            logging.info(f"Track ID: {self.id if hasattr(self, 'id') else 'No ID'}")
            logging.info(f"Track kind: {self.kind}")
            logging.info(f"Track readyState: {self.readyState if hasattr(self, 'readyState') else 'No readyState'}")
            # Small delay to ensure audio input is ready
            await asyncio.sleep(0.02)  # 20ms delay
            
        try:
            # Read audio data from microphone
            audio_data, overflowed = self.stream.read(self._samples_per_frame)
            
            if overflowed:
                logging.warning("Audio input overflow detected")
            
            # Check if we actually got data
            if audio_data is None or audio_data.size == 0:
                logging.warning("No audio data available, generating silence")
                audio_data = np.zeros(self._samples_per_frame, dtype=np.int16)
            
            # Log first few frames to debug
            if self._audio_samples < self._samples_per_frame * 5:
                logging.debug(f"Read audio frame {self._audio_samples // self._samples_per_frame}: shape={audio_data.shape}, dtype={audio_data.dtype}, max={np.max(np.abs(audio_data))}")
            
            # Ensure audio_data is 1D array
            if audio_data.ndim > 1:
                audio_data = audio_data.flatten()
            
            # Create AudioFrame with proper configuration
            frame = av.AudioFrame(
                format='s16',
                layout='mono', 
                samples=self._samples_per_frame
            )
            
            # Set frame sample rate
            frame.sample_rate = self._sample_rate
            
            # Copy audio data into the frame
            # Ensure we have the right amount of data
            expected_bytes = self._samples_per_frame * 2  # 2 bytes per sample for int16
            audio_bytes = audio_data.tobytes()
            
            if len(audio_bytes) != expected_bytes:
                logging.warning(f"Audio data size mismatch: expected {expected_bytes}, got {len(audio_bytes)}")
                # Pad or truncate as needed
                if len(audio_bytes) < expected_bytes:
                    audio_bytes = audio_bytes + b'\x00' * (expected_bytes - len(audio_bytes))
                else:
                    audio_bytes = audio_bytes[:expected_bytes]
            
            frame.planes[0].update(audio_bytes)
            
            # Set timestamp using cumulative sample count
            frame.pts = self._audio_samples
            frame.time_base = Fraction(1, self._sample_rate)
            self._audio_samples += self._samples_per_frame

            # Delegate speaking detection to the audio handler if method exists
            if hasattr(self.audio_handler, 'process_audio_data'):
                self.audio_handler.process_audio_data(audio_data)

            return frame
        except Exception as e:
            logging.error(f"Error reading audio data: {str(e)}")
            raise
    
    def stop(self):
        """Stop the audio stream."""
        if hasattr(self, 'stream') and self.stream:
            self.stream.stop()
            self.stream.close()
