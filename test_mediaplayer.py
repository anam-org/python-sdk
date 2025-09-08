#!/usr/bin/env python3
"""
Test MediaPlayer audio input for WebRTC.
"""

import asyncio
import logging
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

async def test_mediaplayer_audio():
    """Test MediaPlayer with different audio sources."""
    print("\n🎬 Testing MediaPlayer Audio Sources")
    print("=" * 50)
    
    sources_to_test = [
        # Linux ALSA
        ("ALSA default", "default", {"format": "alsa", "options": {"sample_rate": "48000", "channels": "1"}}),
        ("ALSA hw:0", "hw:0", {"format": "alsa", "options": {"sample_rate": "48000", "channels": "1"}}),
        # Pulse Audio
        ("PulseAudio", "default", {"format": "pulse", "options": {"sample_rate": "48000", "channels": "1"}}),
        # V4L2 (video for linux, but can have audio)
        ("V4L2 /dev/video0", "/dev/video0", {"format": "v4l2"}),
    ]
    
    working_players = []
    
    for name, device, kwargs in sources_to_test:
        print(f"\n📌 Testing {name}...")
        try:
            player = MediaPlayer(device, **kwargs)
            
            # Check if audio track exists
            if player.audio:
                print(f"  ✅ Audio track created")
                
                # Try to get a frame
                try:
                    frame = await asyncio.wait_for(player.audio.recv(), timeout=2.0)
                    print(f"  ✅ Got audio frame: pts={frame.pts}, samples={frame.samples}")
                    working_players.append((name, player))
                except asyncio.TimeoutError:
                    print(f"  ❌ Timeout waiting for audio frame")
                    player.close()
                except Exception as e:
                    print(f"  ❌ Error getting frame: {e}")
                    player.close()
            else:
                print(f"  ❌ No audio track available")
                player.close()
                
        except Exception as e:
            print(f"  ❌ Failed to create MediaPlayer: {e}")
    
    print("\n" + "=" * 50)
    print(f"📊 Working audio sources: {len(working_players)}")
    
    # Test the first working player in a peer connection
    if working_players:
        name, player = working_players[0]
        print(f"\n🔌 Testing {name} in peer connection...")
        
        pc_local = RTCPeerConnection()
        pc_remote = RTCPeerConnection()
        
        frames_received = 0
        
        @pc_remote.on("track")
        async def on_track(track):
            nonlocal frames_received
            if track.kind == "audio":
                logging.info("Remote received audio track")
                try:
                    for i in range(10):
                        frame = await asyncio.wait_for(track.recv(), timeout=1.0)
                        frames_received += 1
                        if i == 0:
                            logging.info(f"First remote frame: pts={frame.pts}")
                except Exception as e:
                    logging.error(f"Error receiving: {e}")
        
        # Add track
        pc_local.addTrack(player.audio)
        
        # Negotiate
        offer = await pc_local.createOffer()
        await pc_local.setLocalDescription(offer)
        await pc_remote.setRemoteDescription(offer)
        
        answer = await pc_remote.createAnswer()
        await pc_remote.setLocalDescription(answer)
        await pc_local.setRemoteDescription(answer)
        
        # Wait
        await asyncio.sleep(3)
        
        print(f"\n✅ Frames received by remote: {frames_received}")
        
        # Cleanup
        await pc_local.close()
        await pc_remote.close()
        
        for _, p in working_players:
            p.close()
            
        return frames_received > 0
    
    return False

async def test_pyav_direct():
    """Test using PyAV directly to create audio input."""
    print("\n🎥 Testing PyAV Direct Audio Input")
    print("=" * 50)
    
    import av
    import numpy as np
    from aiortc.mediastreams import MediaStreamTrack
    from fractions import Fraction
    
    class PyAVAudioTrack(MediaStreamTrack):
        kind = "audio"
        
        def __init__(self):
            super().__init__()
            self._started = False
            self._timestamp = 0
            
        async def recv(self):
            if not self._started:
                self._started = True
                logging.info("PyAVAudioTrack recv() started")
            
            # Generate silence for testing
            samples = 960  # 20ms at 48kHz
            audio_data = np.zeros((samples,), dtype=np.int16)
            
            # Create frame
            frame = av.AudioFrame(format='s16', layout='mono', samples=samples)
            frame.planes[0].update(audio_data.tobytes())
            frame.pts = self._timestamp
            frame.sample_rate = 48000
            frame.time_base = Fraction(1, 48000)
            
            self._timestamp += samples
            
            # Small delay to simulate real-time
            await asyncio.sleep(0.02)
            
            return frame
    
    # Test in peer connection
    track = PyAVAudioTrack()
    pc = RTCPeerConnection()
    pc.addTrack(track)
    
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    
    # Wait and check if recv was called
    await asyncio.sleep(2)
    
    print(f"\nTrack started: {'✅' if track._started else '❌'}")
    
    await pc.close()
    
    return track._started

if __name__ == "__main__":
    print("🔊 WebRTC Audio Input Testing")
    print("=" * 50)
    
    # Test 1: MediaPlayer sources
    success1 = asyncio.run(test_mediaplayer_audio())
    
    # Test 2: Direct PyAV
    success2 = asyncio.run(test_pyav_direct())
    
    print("\n" + "=" * 50)
    print("🏁 Final Results:")
    print(f"  MediaPlayer: {'✅ Working' if success1 else '❌ Not working'}")
    print(f"  PyAV Direct: {'✅ Working' if success2 else '❌ Not working'}")
    
    if not success1 and not success2:
        print("\n💡 The issue appears to be that aiortc doesn't automatically")
        print("start reading from audio tracks until ICE connection is established.")
        print("The SDK needs to ensure proper ICE candidate exchange.")