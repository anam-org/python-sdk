"""This is the main entry point for users of your SDK."""

from .api import AnamAPI
from .webrtc import WebRTCClient

class AnamClient:
    def __init__(self, api_key, persona_id):
        self.api = AnamAPI(api_key)
        self.persona_id = persona_id

    async def start_session(self):
        session_token = self.api.get_session_token(self.persona_id)
        engine_details = self.api.get_engine_details(session_token)
        self.webrtc_client = WebRTCClient(engine_details)
        await self.webrtc_client.connect()

    def add_audio_stream(self, audio_track):
        self.webrtc_client.add_audio_stream(audio_track)

    def add_video_stream(self, video_track):
        self.webrtc_client.add_video_stream(video_track)

    async def close(self):
        await self.webrtc_client.close()