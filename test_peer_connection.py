#!/usr/bin/env python3
"""
Test WebRTC peer connection to check if audio is actually being sent.
This creates a local peer connection to verify audio track behavior.
"""

import asyncio
import logging
from aiortc import RTCPeerConnection, RTCSessionDescription
import json

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

async def test_peer_connection_with_audio():
    """Test creating a peer connection with audio and checking the SDP."""
    print("\n🔍 Testing WebRTC Peer Connection with Audio")
    print("=" * 50)
    
    # Import the SDK's audio track
    import sys
    sys.path.insert(0, '/home/oz/develop/python-playground/python-sdk')
    from anam_python_sdk.chat.handlers.audio import SimpleAudioTrack
    
    # Create peer connections (local and remote for testing)
    pc_local = RTCPeerConnection()
    pc_remote = RTCPeerConnection()
    
    # Track stats
    stats = {
        'local_audio_sent': False,
        'remote_audio_received': False,
        'ice_connected': False,
        'frames_sent': 0,
        'frames_received': 0
    }
    
    # Add event handlers
    @pc_local.on("iceconnectionstatechange")
    async def on_ice_change_local():
        state = pc_local.iceConnectionState
        logging.info(f"Local ICE state: {state}")
        if state == "connected":
            stats['ice_connected'] = True
    
    @pc_remote.on("track")
    async def on_track(track):
        logging.info(f"Remote received {track.kind} track")
        if track.kind == "audio":
            stats['remote_audio_received'] = True
            # Try to receive some frames
            try:
                for i in range(10):
                    frame = await track.recv()
                    stats['frames_received'] += 1
                    if i == 0:
                        logging.info(f"First frame received: pts={frame.pts}, samples={frame.samples}")
            except Exception as e:
                logging.error(f"Error receiving frames: {e}")
    
    try:
        # Create and add audio track
        audio_track = SimpleAudioTrack()
        sender = pc_local.addTrack(audio_track)
        stats['local_audio_sent'] = True
        logging.info(f"Added audio track to local peer: {sender}")
        
        # Create offer
        offer = await pc_local.createOffer()
        await pc_local.setLocalDescription(offer)
        
        # Log the SDP to check audio is included
        sdp_lines = offer.sdp.split('\n')
        audio_lines = [line for line in sdp_lines if 'audio' in line or 'm=audio' in line]
        
        print("\n📝 SDP Audio Lines:")
        for line in audio_lines[:10]:  # First 10 audio-related lines
            print(f"  {line}")
        
        # Check if audio is in the SDP
        has_audio = any('m=audio' in line for line in sdp_lines)
        print(f"\n✅ Audio in SDP: {has_audio}")
        
        # Set offer on remote peer
        await pc_remote.setRemoteDescription(offer)
        
        # Create answer
        answer = await pc_remote.createAnswer()
        await pc_remote.setLocalDescription(answer)
        
        # Set answer on local peer
        await pc_local.setRemoteDescription(answer)
        
        # Wait for connection
        print("\n⏳ Waiting for connection...")
        await asyncio.sleep(2)
        
        # Get stats
        stats_report = await pc_local.getStats()
        
        print("\n📊 Connection Stats:")
        for report_key, stat in stats_report.items():
            if hasattr(stat, 'type') and stat.type == "outbound-rtp":
                if hasattr(stat, 'kind') and stat.kind == "audio":
                    print(f"  Audio packets sent: {getattr(stat, 'packetsSent', 0)}")
                    print(f"  Audio bytes sent: {getattr(stat, 'bytesSent', 0)}")
        
        # Test sending for a bit
        print("\n🎤 Testing audio transmission for 3 seconds...")
        await asyncio.sleep(3)
        
        # Final stats
        final_stats = await pc_local.getStats()
        for report_key, stat in final_stats.items():
            if hasattr(stat, 'type') and stat.type == "outbound-rtp":
                if hasattr(stat, 'kind') and stat.kind == "audio":
                    print(f"\n📊 Final Stats:")
                    print(f"  Total packets sent: {getattr(stat, 'packetsSent', 0)}")
                    print(f"  Total bytes sent: {getattr(stat, 'bytesSent', 0)}")
        
    finally:
        # Cleanup
        audio_track.stop()
        await pc_local.close()
        await pc_remote.close()
    
    # Summary
    print("\n" + "=" * 50)
    print("📋 Test Summary:")
    print(f"  Audio track added: {'✅' if stats['local_audio_sent'] else '❌'}")
    print(f"  Remote received audio: {'✅' if stats['remote_audio_received'] else '❌'}")
    print(f"  ICE connected: {'✅' if stats['ice_connected'] else '❌'}")
    print(f"  Frames received by remote: {stats['frames_received']}")
    
    return stats['frames_received'] > 0

async def test_sdk_streaming_client():
    """Test the actual SDK streaming client to see what's happening."""
    print("\n🎬 Testing SDK StreamingClient")
    print("=" * 50)
    
    from dotenv import load_dotenv
    import os
    from anam_python_sdk.api.client import AnamClient
    from anam_python_sdk.chat.streaming import StreamingClient
    
    # Monkey-patch to add debugging
    original_init_peer = StreamingClient.init_peer_connection
    
    async def debug_init_peer(self):
        result = await original_init_peer(self)
        
        # Log what tracks were added
        senders = self.peer_connection.getSenders()
        print(f"\n📡 RTP Senders after init: {len(senders)}")
        for i, sender in enumerate(senders):
            print(f"  Sender {i}: track={sender.track}, kind={sender.track.kind if sender.track else 'None'}")
        
        return result
    
    StreamingClient.init_peer_connection = debug_init_peer
    
    # Also patch createOffer to log SDP
    original_setup_rtc = StreamingClient.setup_rtc_connection
    
    async def debug_setup_rtc(self):
        await original_setup_rtc(self)
        
        # Log the offer SDP
        if self.peer_connection and self.peer_connection.localDescription:
            sdp = self.peer_connection.localDescription.sdp
            sdp_lines = sdp.split('\n')
            
            print("\n📄 Offer SDP Analysis:")
            # Check for audio
            audio_media = [line for line in sdp_lines if line.startswith('m=audio')]
            if audio_media:
                print(f"  ✅ Audio media line found: {audio_media[0]}")
                # Get the port number
                parts = audio_media[0].split()
                if len(parts) > 1 and parts[1] != '0':
                    print(f"  ✅ Audio port is active: {parts[1]}")
                else:
                    print(f"  ❌ Audio port is 0 (disabled)!")
            else:
                print("  ❌ No audio media line found!")
    
    StreamingClient.setup_rtc_connection = debug_setup_rtc
    
    try:
        load_dotenv(".env")
        api_cfg = {
            "ANAM_API_KEY": os.getenv("ANAM_API_KEY"),
            "ANAM_API_HOST": os.getenv("ANAM_API_HOST", "https://api.anam.ai"),
            "ANAM_API_VERSION": os.getenv("ANAM_API_VERSION", "v1")
        }
        
        if not api_cfg["ANAM_API_KEY"]:
            print("❌ No API key found. Please set ANAM_API_KEY in .env file")
            return False
        
        anam_client = AnamClient(cfg=api_cfg)
        persona_id = "67fb9278-e049-4937-9af1-d771b88b6875"
        
        streaming_client = StreamingClient(anam_client, persona_id)
        
        print("Starting streaming client...")
        await streaming_client.start()
        
        # Wait a bit to see what happens
        print("\n⏳ Monitoring for 10 seconds...")
        await asyncio.sleep(10)
        
        await streaming_client.stop()
        print("\n✅ Test completed")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    print("🔧 WebRTC Peer Connection Diagnostic")
    print("=" * 50)
    
    # Test 1: Local peer connection
    success1 = asyncio.run(test_peer_connection_with_audio())
    
    if success1:
        print("\n✅ Local peer connection test passed!")
        print("Audio is being sent correctly in a local WebRTC connection.")
        
        # Test 2: Actual SDK connection
        print("\n" + "=" * 50)
        print("Now testing with the actual SDK...")
        asyncio.run(test_sdk_streaming_client())
    else:
        print("\n❌ Local peer connection test failed!")
        print("There's an issue with the WebRTC audio setup.")