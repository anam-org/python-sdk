import asyncio
from typing import Dict, Optional
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer, MediaRecorder
from anam_python_sdk.lab.client import AnamLabClient
from anam_python_sdk.chat.signaling_client import SignallingClient

class AnamRTCClient:
    def __init__(self, anam_client: AnamLabClient, persona_id: str):
        self.anam_client = anam_client
        self.persona_id = persona_id
        self.signalling_client: Optional[SignallingClient] = None
        self.peer_connection: Optional[RTCPeerConnection] = None
        self.session_data: Optional[Dict] = None
        self.audio_recorder: Optional[MediaRecorder] = None
        self.video_recorder: Optional[MediaRecorder] = None

    async def start(self):
        self.session_data = self.anam_client.start_session(self.persona_id)
        if not self.session_data:
            raise Exception("Failed to start session")

        self.signalling_client = SignallingClient(self.session_data)
        self.signalling_client.set_on_open_callback(self.on_signalling_open)
        self.signalling_client.set_on_message_callback(self.on_signalling_message)

        await self.signalling_client.connect()

    async def on_signalling_open(self):
        await self.setup_rtc_connection()

    async def on_signalling_message(self, message: Dict):
        if message['actionType'] == 'answer':
            await self.handle_answer(message['payload'])
        elif message['actionType'] == 'icecandidate':
            await self.handle_ice_candidate(message['payload'])

    async def setup_rtc_connection(self):
        self.peer_connection = RTCPeerConnection(
            configuration={
                "iceServers": self.session_data['clientConfig']['iceServers']
            }
        )

        @self.peer_connection.on("icecandidate")
        async def on_ice_candidate(candidate):
            if candidate:
                await self.signalling_client.send_message({
                    "actionType": "icecandidate",
                    "sessionId": self.session_data['sessionId'],
                    "payload": candidate.to_json()
                })

        @self.peer_connection.on("track")
        async def on_track(track):
            if track.kind == "audio":
                print("Audio track received")
                self.audio_recorder = MediaRecorder("received_audio.wav")
                self.audio_recorder.addTrack(track)
                await self.audio_recorder.start()
            elif track.kind == "video":
                print("Video track received")
                self.video_recorder = MediaRecorder("received_video.mp4")
                self.video_recorder.addTrack(track)
                await self.video_recorder.start()

        # Set up audio input (you may need to adjust this based on your audio source)
        audio_input = MediaPlayer('default')
        self.peer_connection.addTrack(audio_input.audio)

        # Create and send offer
        offer = await self.peer_connection.createOffer()
        await self.peer_connection.setLocalDescription(offer)
        await self.signalling_client.send_message({
            "actionType": "offer",
            "sessionId": self.session_data['sessionId'],
            "payload": {"sdp": offer.sdp, "type": offer.type}
        })

    async def handle_answer(self, answer_payload: Dict):
        answer = RTCSessionDescription(sdp=answer_payload['sdp'], type=answer_payload['type'])
        await self.peer_connection.setRemoteDescription(answer)

    async def handle_ice_candidate(self, candidate_payload: Dict):
        candidate = candidate_payload['candidate']
        sdp_mid = candidate_payload['sdpMid']
        sdp_mline_index = candidate_payload['sdpMLineIndex']
        await self.peer_connection.addIceCandidate({"candidate": candidate, "sdpMid": sdp_mid, "sdpMLineIndex": sdp_mline_index})

    async def stop(self):
        if self.audio_recorder:
            await self.audio_recorder.stop()
        if self.video_recorder:
            await self.video_recorder.stop()
        if self.peer_connection:
            await self.peer_connection.close()
        if self.signalling_client:
            await self.signalling_client.ws.close()
