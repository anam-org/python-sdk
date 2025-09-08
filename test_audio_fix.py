#!/usr/bin/env python3
"""
Test a potential fix for the audio issue.
"""

import asyncio
import logging
import sys
sys.path.insert(0, '/home/oz/develop/python-playground/python-sdk')

from aiortc import RTCPeerConnection, RTCSessionDescription
from anam_python_sdk.chat.handlers.audio import SimpleAudioTrack

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

async def test_audio_with_ice_gathering():
    """Test audio with proper ICE gathering completion."""
    print("\n🧊 Testing Audio with ICE Gathering")
    print("=" * 50)
    
    # Create peers
    pc_local = RTCPeerConnection()
    pc_remote = RTCPeerConnection()
    
    # Track recv monitoring
    track = SimpleAudioTrack()
    local_recv_count = 0
    remote_frames = 0
    
    # Monitor when track recv is called
    original_recv = track.recv
    async def monitored_recv():
        nonlocal local_recv_count
        local_recv_count += 1
        if local_recv_count == 1:
            logging.info("✅ First recv() call on local track!")
        return await original_recv()
    track.recv = monitored_recv
    
    @pc_remote.on("track")
    async def on_track(remote_track):
        nonlocal remote_frames
        logging.info(f"Remote received {remote_track.kind} track")
        if remote_track.kind == "audio":
            # Receive some frames
            try:
                for i in range(5):
                    frame = await asyncio.wait_for(remote_track.recv(), timeout=2.0)
                    remote_frames += 1
                    if i == 0:
                        logging.info(f"✅ Remote received first audio frame!")
            except Exception as e:
                logging.error(f"Error receiving remote frames: {e}")
    
    # Add ICE candidate exchange
    @pc_local.on("icecandidate")
    async def on_ice_candidate_local(candidate):
        if candidate:
            await pc_remote.addIceCandidate(candidate)
    
    @pc_remote.on("icecandidate")
    async def on_ice_candidate_remote(candidate):
        if candidate:
            await pc_local.addIceCandidate(candidate)
    
    # Add track
    sender = pc_local.addTrack(track)
    logging.info(f"Track added to local peer")
    
    # Create offer
    offer = await pc_local.createOffer()
    await pc_local.setLocalDescription(offer)
    
    # IMPORTANT: Wait for ICE gathering to complete
    while pc_local.iceGatheringState != "complete":
        await asyncio.sleep(0.1)
    logging.info("ICE gathering complete")
    
    # Now set remote description
    await pc_remote.setRemoteDescription(pc_local.localDescription)
    
    # Create answer
    answer = await pc_remote.createAnswer()
    await pc_remote.setLocalDescription(answer)
    
    # Wait for remote ICE gathering
    while pc_remote.iceGatheringState != "complete":
        await asyncio.sleep(0.1)
    
    # Set answer
    await pc_local.setRemoteDescription(pc_remote.localDescription)
    
    logging.info("Offer/Answer exchange complete")
    
    # Wait for ICE connection
    start_time = asyncio.get_event_loop().time()
    while pc_local.iceConnectionState not in ["connected", "completed"]:
        if asyncio.get_event_loop().time() - start_time > 5:
            logging.warning("Timeout waiting for ICE connection")
            break
        await asyncio.sleep(0.1)
    
    logging.info(f"ICE connection state: {pc_local.iceConnectionState}")
    
    # Wait for audio transmission
    await asyncio.sleep(5)
    
    # Results
    print(f"\n📊 Results:")
    print(f"  Local track recv() calls: {local_recv_count}")
    print(f"  Remote frames received: {remote_frames}")
    print(f"  ICE state: {pc_local.iceConnectionState}")
    
    # Check sender state
    if hasattr(sender, '_RTCRtpSender__started'):
        print(f"  Sender started: {sender._RTCRtpSender__started}")
    
    # Cleanup
    track.stop()
    await pc_local.close()
    await pc_remote.close()
    
    return local_recv_count > 0

async def test_with_stun_server():
    """Test with STUN server configuration like in the SDK."""
    print("\n🌐 Testing with STUN Server")
    print("=" * 50)
    
    from aiortc import RTCConfiguration, RTCIceServer
    
    # Use same STUN server as in the logs
    config = RTCConfiguration([
        RTCIceServer(urls=["stun:stun.relay.metered.ca:80"])
    ])
    
    pc_local = RTCPeerConnection(configuration=config)
    pc_remote = RTCPeerConnection(configuration=config)
    
    track = SimpleAudioTrack()
    recv_count = 0
    
    # Monitor recv
    original_recv = track.recv
    async def monitored_recv():
        nonlocal recv_count
        recv_count += 1
        return await original_recv()
    track.recv = monitored_recv
    
    # Add track
    pc_local.addTrack(track)
    
    # Negotiate with ICE candidate exchange
    @pc_local.on("icecandidate")
    async def on_ice_local(candidate):
        if candidate:
            await pc_remote.addIceCandidate(candidate)
    
    @pc_remote.on("icecandidate")
    async def on_ice_remote(candidate):
        if candidate:
            await pc_local.addIceCandidate(candidate)
    
    # Create and exchange offer/answer
    offer = await pc_local.createOffer()
    await pc_local.setLocalDescription(offer)
    await pc_remote.setRemoteDescription(offer)
    
    answer = await pc_remote.createAnswer()
    await pc_remote.setLocalDescription(answer)
    await pc_local.setRemoteDescription(answer)
    
    # Wait for connection
    await asyncio.sleep(3)
    
    print(f"\n📊 With STUN Results:")
    print(f"  Track recv() calls: {recv_count}")
    print(f"  ICE state: {pc_local.iceConnectionState}")
    
    # Cleanup
    track.stop()
    await pc_local.close()
    await pc_remote.close()
    
    return recv_count > 0

if __name__ == "__main__":
    print("🔧 Audio Fix Testing")
    print("=" * 50)
    
    # Test 1: With proper ICE gathering
    success1 = asyncio.run(test_audio_with_ice_gathering())
    
    # Test 2: With STUN server
    success2 = asyncio.run(test_with_stun_server())
    
    print("\n" + "=" * 50)
    print("🏁 Summary:")
    print(f"  ICE gathering test: {'✅ PASS' if success1 else '❌ FAIL'}")
    print(f"  STUN server test: {'✅ PASS' if success2 else '❌ FAIL'}")
    
    if success1 or success2:
        print("\n💡 The issue was related to ICE/transport setup!")
        print("The audio track needs proper ICE connection to start sending.")