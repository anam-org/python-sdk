#!/usr/bin/env python3
"""
Test why audio track recv() is not being called in WebRTC.
"""

import asyncio
import logging
import sys
sys.path.insert(0, '/home/oz/develop/python-playground/python-sdk')

from aiortc import RTCPeerConnection, RTCRtpSender
from anam_python_sdk.chat.handlers.audio import SimpleAudioTrack
import time

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

class DiagnosticSimpleAudioTrack(SimpleAudioTrack):
    """SimpleAudioTrack with additional diagnostics."""
    
    def __init__(self):
        super().__init__()
        self.recv_count = 0
        logging.info("DiagnosticSimpleAudioTrack initialized")
        
    async def recv(self):
        self.recv_count += 1
        logging.info(f"recv() called! Count: {self.recv_count}")
        return await super().recv()

async def test_sender_behavior():
    """Test RTCRtpSender behavior with audio track."""
    print("\n🔍 Testing RTCRtpSender Behavior")
    print("=" * 50)
    
    # Create components
    pc = RTCPeerConnection()
    track = DiagnosticSimpleAudioTrack()
    
    # Add track and get sender
    sender = pc.addTrack(track)
    print(f"\nSender created: {sender}")
    print(f"Sender track: {sender.track}")
    print(f"Track kind: {sender.track.kind if sender.track else 'None'}")
    
    # Check sender state
    print(f"\nChecking sender properties:")
    print(f"  - Sender ID: {getattr(sender, '_sender_id', 'N/A')}")
    print(f"  - Transport: {getattr(sender, '_transport', 'None')}")
    
    # Create offer to trigger negotiation
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    
    print(f"\nAfter creating offer:")
    print(f"  - Transport: {getattr(sender, '_transport', 'None')}")
    
    # Wait to see if recv gets called
    print("\n⏳ Waiting 3 seconds...")
    await asyncio.sleep(3)
    
    print(f"\nTrack recv() called {track.recv_count} times")
    
    # Cleanup
    track.stop()
    await pc.close()
    
    return track.recv_count > 0

async def test_with_full_connection():
    """Test with complete WebRTC connection."""
    print("\n🔗 Testing with Full WebRTC Connection")
    print("=" * 50)
    
    # Create local and remote peers
    pc_local = RTCPeerConnection()
    pc_remote = RTCPeerConnection()
    
    # Track for diagnostics
    track = DiagnosticSimpleAudioTrack()
    remote_frames = 0
    
    @pc_remote.on("track")
    async def on_track(remote_track):
        nonlocal remote_frames
        print(f"\n✅ Remote received {remote_track.kind} track!")
        if remote_track.kind == "audio":
            # Try to receive frames
            for i in range(5):
                try:
                    frame = await asyncio.wait_for(remote_track.recv(), timeout=1.0)
                    remote_frames += 1
                    print(f"  Remote received frame {i+1}")
                except asyncio.TimeoutError:
                    print(f"  Timeout waiting for frame {i+1}")
                    break
    
    # Add track
    sender = pc_local.addTrack(track)
    print(f"Added track to local peer: {sender}")
    
    # Full negotiation
    offer = await pc_local.createOffer()
    await pc_local.setLocalDescription(offer)
    await pc_remote.setRemoteDescription(offer)
    
    answer = await pc_remote.createAnswer()
    await pc_remote.setLocalDescription(answer)
    await pc_local.setRemoteDescription(answer)
    
    print("\nNegotiation complete!")
    print(f"Local signaling state: {pc_local.signalingState}")
    print(f"Remote signaling state: {pc_remote.signalingState}")
    
    # Wait for connection and data flow
    print("\n⏳ Waiting for audio transmission...")
    
    # Monitor for up to 10 seconds
    start_time = time.time()
    while time.time() - start_time < 10:
        await asyncio.sleep(0.5)
        if track.recv_count > 0:
            print(f"\n🎉 Track recv() started! Count: {track.recv_count}")
            break
        if (time.time() - start_time) % 2 < 0.5:
            print(f"  Still waiting... recv_count: {track.recv_count}")
    
    # Final results
    print(f"\n📊 Final Results:")
    print(f"  Local track recv() calls: {track.recv_count}")
    print(f"  Remote frames received: {remote_frames}")
    print(f"  ICE connection state: {pc_local.iceConnectionState}")
    
    # Cleanup
    track.stop()
    await pc_local.close()
    await pc_remote.close()
    
    return track.recv_count > 0

async def test_sender_start():
    """Test if we need to explicitly start the sender."""
    print("\n🚀 Testing Sender Start Mechanism")
    print("=" * 50)
    
    pc = RTCPeerConnection()
    track = DiagnosticSimpleAudioTrack()
    
    sender = pc.addTrack(track)
    
    # Check if sender has a start method or needs activation
    print(f"\nSender methods: {[m for m in dir(sender) if not m.startswith('_')]}")
    
    # Create offer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    
    # Check RTP sender internals
    if hasattr(sender, '_RTCRtpSender__started'):
        print(f"Sender started flag: {sender._RTCRtpSender__started}")
    
    if hasattr(sender, '_track_id'):
        print(f"Track ID in sender: {sender._track_id}")
        
    # Try to access internal transport
    if hasattr(sender, '_transport'):
        print(f"Sender transport: {sender._transport}")
        if sender._transport:
            print(f"Transport state: {getattr(sender._transport, 'state', 'N/A')}")
    
    await asyncio.sleep(2)
    
    track.stop()
    await pc.close()

if __name__ == "__main__":
    print("🎤 WebRTC Audio Sender Diagnostic")
    print("=" * 50)
    
    # Test 1: Basic sender behavior
    asyncio.run(test_sender_behavior())
    
    # Test 2: Full connection
    asyncio.run(test_with_full_connection())
    
    # Test 3: Sender start mechanism
    asyncio.run(test_sender_start())