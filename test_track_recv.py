#!/usr/bin/env python3
"""
Test if the audio track's recv() method is being called by WebRTC.
"""

import asyncio
import logging
import sys
sys.path.insert(0, '/home/oz/develop/python-playground/python-sdk')

from anam_python_sdk.chat.handlers.audio import SimpleAudioTrack
from aiortc import RTCPeerConnection, RTCSessionDescription

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

class MonitoredAudioTrack(SimpleAudioTrack):
    """Audio track that monitors if recv() is being called."""
    
    def __init__(self):
        super().__init__()
        self.recv_count = 0
        self.first_recv_time = None
        self.last_recv_time = None
        
    async def recv(self):
        """Override recv to monitor calls."""
        self.recv_count += 1
        
        if self.recv_count == 1:
            self.first_recv_time = asyncio.get_event_loop().time()
            logging.info("✅ recv() called for the first time!")
        
        if self.recv_count % 50 == 0:
            logging.info(f"📊 recv() called {self.recv_count} times")
            
        self.last_recv_time = asyncio.get_event_loop().time()
        
        # Call parent recv
        return await super().recv()

async def test_track_in_connection():
    """Test if track recv is called when added to peer connection."""
    print("\n🔍 Testing Audio Track recv() Calls")
    print("=" * 50)
    
    # Create monitored track
    track = MonitoredAudioTrack()
    
    # Create peer connection
    pc = RTCPeerConnection()
    
    # Add track
    sender = pc.addTrack(track)
    logging.info(f"Track added to peer connection: {sender}")
    
    # Create offer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    
    # Check SDP
    sdp_lines = offer.sdp.split('\n')
    audio_lines = [line for line in sdp_lines if 'm=audio' in line]
    print(f"\nSDP Audio line: {audio_lines[0] if audio_lines else 'NOT FOUND'}")
    
    # Wait to see if recv is called
    print("\n⏳ Waiting 5 seconds to see if recv() is called...")
    start_time = asyncio.get_event_loop().time()
    
    while asyncio.get_event_loop().time() - start_time < 5:
        await asyncio.sleep(0.1)
        if track.recv_count > 0:
            break
    
    # Results
    print("\n" + "=" * 50)
    print("📊 Results:")
    print(f"  recv() called: {'✅ Yes' if track.recv_count > 0 else '❌ No'}")
    print(f"  Total calls: {track.recv_count}")
    
    if track.recv_count == 0:
        print("\n⚠️  recv() was never called!")
        print("This means the track is not being read by the peer connection.")
        print("\nPossible reasons:")
        print("  1. No remote peer to negotiate with")
        print("  2. Track not properly connected")
        print("  3. Need to complete offer/answer exchange")
    
    # Cleanup
    track.stop()
    await pc.close()
    
    return track.recv_count > 0

async def test_with_remote_peer():
    """Test with both local and remote peer to complete negotiation."""
    print("\n🔄 Testing with Remote Peer (Full Negotiation)")
    print("=" * 50)
    
    # Create monitored track
    track = MonitoredAudioTrack()
    
    # Create peer connections
    pc_local = RTCPeerConnection()
    pc_remote = RTCPeerConnection()
    
    # Add handlers
    @pc_remote.on("track")
    async def on_track(remote_track):
        logging.info(f"Remote received {remote_track.kind} track")
        if remote_track.kind == "audio":
            # Try to receive a frame
            try:
                frame = await remote_track.recv()
                logging.info(f"✅ Remote received audio frame: pts={frame.pts}")
            except Exception as e:
                logging.error(f"Error receiving remote frame: {e}")
    
    # Add track to local
    sender = pc_local.addTrack(track)
    logging.info(f"Track added to local peer: {sender}")
    
    # Create and exchange offer/answer
    offer = await pc_local.createOffer()
    await pc_local.setLocalDescription(offer)
    await pc_remote.setRemoteDescription(offer)
    
    answer = await pc_remote.createAnswer()
    await pc_remote.setLocalDescription(answer)
    await pc_local.setRemoteDescription(answer)
    
    logging.info("Offer/Answer exchange completed")
    
    # Wait for connection and data flow
    print("\n⏳ Waiting for audio transmission...")
    await asyncio.sleep(5)
    
    # Results
    print("\n" + "=" * 50)
    print("📊 Results:")
    print(f"  Local track recv() calls: {track.recv_count}")
    print(f"  recv() rate: {track.recv_count / 5:.1f} calls/second (expected ~50)")
    
    if track.recv_count > 200:  # ~4 seconds worth at 50 fps
        print("  ✅ Audio is being transmitted correctly!")
    elif track.recv_count > 0:
        print("  ⚠️  Audio is being transmitted but slowly")
    else:
        print("  ❌ No audio transmission detected!")
    
    # Cleanup
    track.stop()
    await pc_local.close()
    await pc_remote.close()
    
    return track.recv_count > 0

if __name__ == "__main__":
    print("🎤 Audio Track recv() Monitor")
    print("=" * 50)
    
    # Test 1: Just adding to peer connection
    success1 = asyncio.run(test_track_in_connection())
    
    # Test 2: Full negotiation with remote peer
    success2 = asyncio.run(test_with_remote_peer())
    
    print("\n" + "=" * 50)
    print("🏁 Summary:")
    print(f"  Track alone: {'✅' if success1 else '❌'}")
    print(f"  With negotiation: {'✅' if success2 else '❌'}")
    
    if not success1 and success2:
        print("\n💡 Insight: Audio track only starts sending after full negotiation!")
        print("The issue in the SDK might be related to the offer/answer exchange.")