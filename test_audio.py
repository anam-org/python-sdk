import sounddevice as sd
import numpy as np

# List audio devices
print("Audio devices:")
print(sd.query_devices())

# Test audio playback
print("\nTesting audio playback...")
duration = 1  # seconds
sample_rate = 48000
t = np.linspace(0, duration, int(sample_rate * duration))
# Generate a 440Hz sine wave
audio = 0.3 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
print(f"Audio shape: {audio.shape}, dtype: {audio.dtype}")

try:
    sd.play(audio, samplerate=sample_rate)
    sd.wait()
    print("Audio playback successful!")
except Exception as e:
    print(f"Audio playback error: {e}")

# Test microphone capture
print("\nTesting microphone capture...")
try:
    print("Recording 2 seconds of audio...")
    recording = sd.rec(int(2 * sample_rate), samplerate=sample_rate, channels=1, dtype='int16')
    sd.wait()
    print(f"Recording shape: {recording.shape}, dtype: {recording.dtype}")
    print("Microphone capture successful!")
except Exception as e:
    print(f"Microphone capture error: {e}")