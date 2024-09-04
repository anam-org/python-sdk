"""This module will manage the WebRTC connection using aiortc."""
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.signaling import BYE
import asyncio
import websockets
import json

class WebRTCClient:
    def __init__(self, peer_id, signaling_server_url):
        self.pc = RTCPeerConnection()
        self.peer_id = peer_id
        self.signaling_server_url = signaling_server_url

    async def connect(self):
        async with websockets.connect(self.signaling_server_url) as websocket:
            # Register with the signaling server
            await websocket.send(json.dumps({"peer_id": self.peer_id}))

            # Send offer
            offer = await self.pc.createOffer()
            await self.pc.setLocalDescription(offer)
            await websocket.send(json.dumps({
                "peer_id": self.peer_id,
                "to": "receiver",  # You can dynamically determine the receiver's ID
                "type": "offer",
                "sdp": offer.sdp
            }))

            # Receive and process the answer
            async for message in websocket:
                data = json.loads(message)

                if data["type"] == "answer":
                    answer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
                    await self.pc.setRemoteDescription(answer)
                    break

            # Handle ICE candidates here (not covered in this basic example)

    async def add_audio_video_stream(self, audio_track, video_track):
        self.pc.addTrack(audio_track)
        self.pc.addTrack(video_track)

    async def close(self):
        await self.pc.close()