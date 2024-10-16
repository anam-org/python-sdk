import asyncio
import logging
import sounddevice as sd
import numpy as np
import wave
from aiortc.mediastreams import MediaStreamError

class AudioHandler:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.audio_tasks = []
        self.avatar_speaking = False

    async def handle_avatar_audio(self, track):
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
