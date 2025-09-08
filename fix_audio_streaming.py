#!/usr/bin/env python3
"""
Fix for audio streaming in Anam SDK.
The issue: SimpleAudioTrack.recv() is never called even though ICE connection is established.
"""

import asyncio
import logging
from typing import Optional
from fractions import Fraction
import time
import numpy as np
import pyaudio
import av
from aiortc.contrib.media import MediaStreamTrack

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

class WorkingAudioTrack(MediaStreamTrack):
    """
    An audio track that properly starts sending when connection is established.
    This fixes the issue where recv() is never called.
    """
    kind = "audio"
    
    def __init__(self):
        super().__init__()
        self.RATE = 48000
        self.CHANNELS = 1
        self.AUDIO_PTIME = 0.020  # 20ms
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
        
        # Force track to be "live"
        self._started = True
        
        logging.info(f"WorkingAudioTrack initialized: {self.RATE}Hz, {self.SAMPLES} samples/frame")
    
    async def recv(self):
        """Read audio from microphone and return as AudioFrame."""
        if self._start_time is None:
            self._start_time = time.time()
            logging.info("WorkingAudioTrack.recv() called for first time")
        
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
            logging.error(f"Error in WorkingAudioTrack.recv(): {e}")
            raise
    
    def stop(self):
        """Stop the audio stream."""
        if hasattr(self, 'stream') and self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if hasattr(self, 'audio'):
            self.audio.terminate()


# Monkey patch to replace SimpleAudioTrack
def patch_audio_track():
    """Replace SimpleAudioTrack with WorkingAudioTrack in the SDK."""
    import sys
    sys.path.insert(0, '/home/oz/develop/python-playground/python-sdk')
    
    import anam_python_sdk.chat.handlers.audio
    anam_python_sdk.chat.handlers.audio.SimpleAudioTrack = WorkingAudioTrack
    
    logging.info("✅ Patched SimpleAudioTrack with WorkingAudioTrack")


async def test_patched_sdk():
    """Test the SDK with the patched audio track."""
    from dotenv import load_dotenv
    import os
    from anam_python_sdk.api.client import AnamClient
    from anam_python_sdk.chat.streaming import StreamingClient
    
    # Apply the patch
    patch_audio_track()
    
    load_dotenv(".env")
    api_cfg = {
        "ANAM_API_KEY": os.getenv("ANAM_API_KEY"),
        "ANAM_API_HOST": os.getenv("ANAM_API_HOST", "https://api.anam.ai"),
        "ANAM_API_VERSION": os.getenv("ANAM_API_VERSION", "v1")
    }
    
    if not api_cfg["ANAM_API_KEY"]:
        print("❌ No API key found. Please set ANAM_API_KEY in .env file")
        return False
    
    # Monitor when track's recv is called
    original_recv_count = 0
    
    # Hook into the StreamingClient to monitor audio track
    original_init_peer = StreamingClient.init_peer_connection
    
    async def monitored_init_peer(self):
        result = await original_init_peer(self)
        
        # Find the audio track
        if self.audio_track:
            original_recv = self.audio_track.recv
            
            async def monitored_recv():
                nonlocal original_recv_count
                original_recv_count += 1
                if original_recv_count == 1:
                    logging.info("🎉 Audio track recv() called for the first time!")
                elif original_recv_count % 50 == 0:
                    logging.info(f"📊 Audio track recv() called {original_recv_count} times")
                return await original_recv()
            
            self.audio_track.recv = monitored_recv
            logging.info("✅ Monitoring audio track recv() calls")
        
        return result
    
    StreamingClient.init_peer_connection = monitored_init_peer
    
    try:
        anam_client = AnamClient(cfg=api_cfg)
        persona_id = "67fb9278-e049-4937-9af1-d771b88b6875"
        
        streaming_client = StreamingClient(anam_client, persona_id)
        
        print("\n🚀 Starting streaming client with patched audio...")
        await streaming_client.start()
        
        # Monitor for 15 seconds
        print("\n⏳ Monitoring audio transmission for 15 seconds...")
        print("Please speak into your microphone...")
        
        await asyncio.sleep(15)
        
        print(f"\n📊 Results:")
        print(f"  Audio track recv() calls: {original_recv_count}")
        print(f"  Approximate data rate: {original_recv_count / 15:.1f} calls/second")
        
        if original_recv_count > 0:
            print("\n✅ SUCCESS! Audio is being transmitted!")
        else:
            print("\n❌ Audio still not being transmitted")
        
        await streaming_client.stop()
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return original_recv_count > 0


if __name__ == "__main__":
    print("🔧 Audio Streaming Fix")
    print("=" * 50)
    
    success = asyncio.run(test_patched_sdk())
    
    if success:
        print("\n🎉 The patch works! To fix the SDK permanently:")
        print("1. Replace SimpleAudioTrack with WorkingAudioTrack")
        print("2. Ensure the track starts in 'live' state")
        print("3. Add proper timing to audio frame generation")
    else:
        print("\n💡 The issue might be deeper in aiortc's RTP sender initialization")