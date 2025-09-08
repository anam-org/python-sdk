#!/usr/bin/env python3
"""
WebRTC Audio Track diagnostic script.
Tests if audio is being properly captured and would be sent through WebRTC.
"""

import asyncio
import logging
import time
import numpy as np
from fractions import Fraction
import av
import pyaudio
from aiortc.contrib.media import MediaStreamTrack

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

class DiagnosticAudioTrack(MediaStreamTrack):
    """Audio track that logs what it's sending."""
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.RATE = 48000
        self.CHANNELS = 1
        self.AUDIO_PTIME = 0.020  # 20ms audio packetization
        self.SAMPLES = int(self.AUDIO_PTIME * self.RATE)  # 960 samples
        
        self._timestamp = 0
        self._start_time = None
        self._frame_count = 0
        self._total_amplitude = 0
        
        # Initialize PyAudio
        self.audio = pyaudio.PyAudio()
        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.SAMPLES
        )
        
        logging.info(f"DiagnosticAudioTrack initialized: {self.RATE}Hz, {self.SAMPLES} samples/frame")

    async def recv(self):
        """Read audio from microphone and return as AudioFrame."""
        if self._start_time is None:
            self._start_time = time.time()
            logging.info("First recv() call - audio capture starting")
        
        try:
            # Read audio data
            audio_data = self.stream.read(self.SAMPLES, exception_on_overflow=False)
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            
            # Calculate amplitude for diagnostics
            max_amplitude = np.max(np.abs(audio_array))
            avg_amplitude = np.mean(np.abs(audio_array))
            self._total_amplitude += avg_amplitude
            self._frame_count += 1
            
            # Log every 50 frames (1 second)
            if self._frame_count % 50 == 0:
                avg_total = self._total_amplitude / self._frame_count
                logging.info(f"Frame {self._frame_count}: max_amp={max_amplitude}, avg_amp={avg_amplitude:.1f}, overall_avg={avg_total:.1f}")
                
                if avg_total < 100:
                    logging.warning("⚠️  Very low audio levels detected! Check microphone volume.")
            
            # Create audio frame
            frame = av.AudioFrame(format='s16', layout='mono', samples=self.SAMPLES)
            frame.planes[0].update(audio_array.tobytes())
            frame.pts = self._timestamp
            frame.sample_rate = self.RATE
            frame.time_base = Fraction(1, self.RATE)
            
            self._timestamp += self.SAMPLES
            
            return frame
            
        except Exception as e:
            logging.error(f"Error in recv(): {e}")
            raise

    def stop(self):
        """Stop the audio stream."""
        if hasattr(self, 'stream') and self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if hasattr(self, 'audio'):
            self.audio.terminate()
        
        if self._frame_count > 0:
            logging.info(f"\n📊 Audio capture summary:")
            logging.info(f"  Total frames: {self._frame_count}")
            logging.info(f"  Average amplitude: {self._total_amplitude / self._frame_count:.1f}")
            logging.info(f"  Duration: {(time.time() - self._start_time):.1f} seconds")

async def test_audio_track():
    """Test the audio track by simulating WebRTC usage."""
    print("\n🎤 Testing WebRTC Audio Track")
    print("=" * 50)
    print("This test will capture audio for 10 seconds.")
    print("Please speak into your microphone...\n")
    
    track = DiagnosticAudioTrack()
    
    # Simulate WebRTC calling recv() repeatedly
    start_time = asyncio.get_event_loop().time()
    frame_count = 0
    
    try:
        while asyncio.get_event_loop().time() - start_time < 10:
            frame = await track.recv()
            frame_count += 1
            
            # Simulate 20ms delay between frames (WebRTC timing)
            await asyncio.sleep(0.020)
            
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    finally:
        track.stop()
        
    print(f"\n✅ Test complete! Processed {frame_count} frames")
    
    if frame_count == 0:
        print("❌ No frames were processed - audio track failed!")
    elif frame_count < 400:  # Less than 8 seconds worth
        print("⚠️  Fewer frames than expected - possible timing issues")
    else:
        print("✅ Frame count looks good!")

async def test_simple_audio_track():
    """Test the SimpleAudioTrack from the SDK."""
    print("\n🎙️  Testing SDK's SimpleAudioTrack")
    print("=" * 50)
    
    # Import the SDK's audio track
    import sys
    sys.path.insert(0, '/home/oz/develop/python-playground/python-sdk')
    from anam_python_sdk.chat.handlers.audio import SimpleAudioTrack
    
    track = SimpleAudioTrack()
    
    print("Testing SimpleAudioTrack for 5 seconds...")
    print("Please speak into your microphone...\n")
    
    start_time = asyncio.get_event_loop().time()
    frame_count = 0
    total_amplitude = 0
    
    try:
        while asyncio.get_event_loop().time() - start_time < 5:
            frame = await track.recv()
            frame_count += 1
            
            # Get audio data from frame to check amplitude
            audio_data = frame.to_ndarray()
            max_amp = np.max(np.abs(audio_data))
            total_amplitude += max_amp
            
            if frame_count % 50 == 0:
                avg_amp = total_amplitude / frame_count
                logging.info(f"SimpleAudioTrack - Frame {frame_count}: current_max={max_amp}, avg_max={avg_amp:.1f}")
            
            await asyncio.sleep(0.020)
            
    except Exception as e:
        logging.error(f"Error testing SimpleAudioTrack: {e}")
    finally:
        track.stop()
        
    print(f"\n📊 SimpleAudioTrack Test Results:")
    print(f"  Frames processed: {frame_count}")
    if frame_count > 0:
        print(f"  Average max amplitude: {total_amplitude / frame_count:.1f}")
    
    return frame_count > 0

if __name__ == "__main__":
    print("🔍 WebRTC Audio Diagnostic Tool")
    print("=" * 50)
    
    # Test 1: Our diagnostic track
    asyncio.run(test_audio_track())
    
    print("\n" + "=" * 50)
    
    # Test 2: The SDK's SimpleAudioTrack
    success = asyncio.run(test_simple_audio_track())
    
    if success:
        print("\n✅ Audio tracks are working! The issue is likely in the WebRTC connection setup.")
        print("Check that:")
        print("  1. The audio track is properly added to the peer connection")
        print("  2. The SDP offer/answer negotiation includes audio")
        print("  3. The remote peer is configured to receive audio")
    else:
        print("\n❌ Audio track test failed!")