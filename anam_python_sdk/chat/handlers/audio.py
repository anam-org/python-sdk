"""Handlers for audio data in the webRTC connection."""
import logging
import asyncio
import wave

import av
import numpy as np
import sounddevice as sd
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

    def handle_avatar_audio(self, track):
        self.logger.debug("Setting up audio playback")

        async def play_audio():
            while True:
                try:
                    self.logger.debug("Awaiting audio ... ")
                    frame = await track.recv()
                    self.logger.debug("Playing audio ... ")
                    sd.play(frame.to_ndarray(), samplerate=48000)

                    if not self.avatar_speaking:
                        self.avatar_speaking = True
                        self.logger.debug("Avatar started speaking")
                except MediaStreamError:
                    self.logger.debug("Error while playing audio")
                    if self.avatar_speaking:
                        self.avatar_speaking = False
                        self.logger.debug("Avatar stopped speaking")
                    break
        asyncio.create_task(play_audio())

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

class AudioStreamTrack(MediaStreamTrack):
    """Audio stream track for handling audio data."""
    kind = "audio"

    def __init__(self, device_name, audio_handler):
        super().__init__()
        self.device_name = device_name
        self.audio_handler = audio_handler
        self.stream = sd.InputStream(
            device=device_name,
            channels=1,
            samplerate=48000,
            blocksize=960
        )
        self.stream.start()
        logging.info(f"AudioStreamTrack initialized with device: {device_name}")

    async def recv(self):
        """Continuously read audio data from the microphone and return it as an audio frame."""
        try:
            audio_data = self.stream.read(960)[0]
            frame = av.AudioFrame.from_ndarray(
                audio_data,
                format='s16', 
                layout='mono'
            )
            frame.pts = None
            frame.time_base = av.Rational(1, 48000)

            # Delegate speaking detection to the audio handler
            self.audio_handler.process_audio_data(audio_data)

            return frame
        except Exception as e:
            logging.error(f"Error reading audio data: {str(e)}")
            raise
