"""
This module provides a client for managing Anam chat sessions using WebRTC.

Classes:
    AnamChatClient: A client for managing Anam chat sessions using WebRTC.

Exceptions:
    SessionStartError: Exception raised when a session fails to start.
    SessionDataError: Exception raised when session data is not found.
"""

import asyncio
import logging
from typing import Dict, Optional

from aiortc import (
    RTCConfiguration,
    RTCDataChannel,
    RTCIceCandidate,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)

from anam_python_sdk.api.client import AnamClient
from anam_python_sdk.chat.handlers.audio import AudioHandler, AudioStreamTrack
from anam_python_sdk.chat.handlers.data import DataHandler
from anam_python_sdk.chat.handlers.video import VideoHandler
from anam_python_sdk.chat.signaling import ActionType, SignallingClient


class StreamingClient:
    """
    A client for managing Anam chat sessions using WebRTC.

    This class handles the setup and management of a WebRTC connection
    for audio and video communication with an Anam persona.
    """

    def __init__(self, lab_client: AnamClient, persona_id: str):
        """
        Initialize the StreamingClient.

        Args:
            lab_client (AnamClient): The Anam client for starting sessions.
            persona_id (str): The ID of the persona to chat with.
        """
        self.lab_client = lab_client
        self.persona_id = persona_id

        # Logger
        self.logger = self._setup_logger()
        self.logger.debug("Initializing StreamingClient for persona_id: %s", persona_id)

        # WebRTC Connection
        self.signalling_client: Optional[SignallingClient] = None
        self.peer_connection: Optional[RTCPeerConnection] = None
        self.session_data: Optional[Dict] = {}
        self.connection_received_answer = False
        self.remote_ice_candidate_buffer = []
        self.remote_description_set = False
        self.peer_connection: Optional[RTCPeerConnection] = None
        self.data_channel: Optional[RTCDataChannel] = None
        self.use_data_channel = True
        self.use_audio_channel = True
        self.use_video_channel = False

        # Event Handlers
        self.audio_handler = AudioHandler(self.logger)
        self.video_handler = VideoHandler(self.logger)
        self.data_handler = DataHandler(self.logger)

    class SessionStartError(Exception):
        """Exception raised when a session fails to start."""

    class SessionDataError(Exception):
        """Exception raised when session data is not found."""

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        # Add a stream handler if you want to see logs in the console
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "[%(asctime)s %(filename)s:%(lineno)s %(funcName)s] %(levelname)s: %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    async def start(self):
        """
        Start the chat session and initialize the WebRTC connection.

        Raises:
            Exception: If the session fails to start.
        """
        self.logger.debug("Starting chat session")
        self.session_data = self.lab_client.start_session(self.persona_id)
        if not self.session_data:
            self.logger.error("Failed to start session")
            raise self.SessionStartError("Failed to start session")
        self.logger.debug("Session started successfully")

        # Initialize the signalling client
        self.signalling_client = SignallingClient(self.session_data)

        # Setup RTCPeerConnection once the websocket connection is opened.
        self.signalling_client.set_on_open_callback(self.on_signalling_open)

        # Setup message handling: different action types.
        self.signalling_client.set_on_message_callback(self.on_signalling_message)

        # Connect to the signalling server
        await self.signalling_client.connect()

    async def stop(self):
        """Stop the chat session and clean up resources."""
        if self.peer_connection:
            await self.peer_connection.close()
        if self.signalling_client:
            await self.signalling_client.ws.close()
        
        # Ensure that the data channel is closed.
        if self.data_channel:
            self.data_channel.close()

    async def on_signalling_open(self):
        """Callback for when the signalling connection is opened."""
        self.logger.debug("Signalling connection opened")
        await self.setup_rtc_connection()

    async def on_signalling_message(self, message: Dict):
        """
        Handle incoming signalling messages.

        Args:
            message (Dict): The received signalling message.
        """
        action_type = ActionType(message["actionType"])
        self.logger.debug("Received signalling message: %s", action_type)

        if action_type == ActionType.ANSWER:
            await self.handle_answer(message["payload"])
            self.connection_received_answer = True
            await self.flush_remote_ice_candidate_buffer()
        elif action_type == ActionType.ICECANDIDATE:
            # Incoming ICE candidates from the remote server. 
            candidate = self.create_ice_candidate(message["payload"])
            if self.connection_received_answer:
                self.logger.debug(
                    "Received answer: adding new ice candidate to peer connection.",
                    # self.peer_connection.iceConnectionState,
                    # candidate,
                )
                await self.peer_connection.addIceCandidate(candidate)
            else:
                self.logger.debug(
                    "No answer received: adding ice candidate to remote buffer.",
                    # self.peer_connection.iceConnectionState,
                    # candidate,
                )
                self.remote_ice_candidate_buffer.append(candidate)

    async def flush_remote_ice_candidate_buffer(self):
        """Flush any remaining ICE candidates in the buffer."""
        self.logger.debug("Flushing the remote ICE candidate buffer. ")
        for candidate in self.remote_ice_candidate_buffer:
            await self.peer_connection.addIceCandidate(candidate)
        self.remote_ice_candidate_buffer.clear()

    async def attach_events(self):
        """Attach ICE events to the peer connection."""

        if not self.peer_connection:
            self.logger.warning(
                "Peer connection not initialized. Cannot attach ICE events.")
            return
        
        @self.peer_connection.on("icegatheringstatechange")
        def on_ice_gathering_state_change():
            if self.peer_connection:
                self.logger.debug(
                    "ICE gathering state changed! %s", 
                    self.peer_connection.iceGatheringState
                )

        @self.peer_connection.on("iceconnectionstatechange")
        def on_ice_connection_state_change():
            if not self.peer_connection.iceConnectionState:
                self.logger.warning(
                    "ICE connection state not available. Cannot attach ICE events."
                )
                return

            state = self.peer_connection.iceConnectionState
            self.logger.debug("ICE connection state changed: %s", state)
            if state == "failed":
                self.logger.error((
                    "ICE connection failed."
                     "Check network configuration and ICE server settings."
                ))
            elif state == "disconnected":
                self.logger.warning(
                    "ICE connection disconnected. Attempting to reconnect..."
                )
            elif state == "completed":
                self.logger.debug("ICE connection completed successfully.")
        
        @self.peer_connection.on("signalingstatechange")
        def on_signaling_state_change():
            if not self.peer_connection.signalingState:
                self.logger.warning(
                    "Signaling connection state not available. Cannot attach ICE events."
                )
                return

            state = self.peer_connection.signalingState
            self.logger.debug("Signaling state changed: %s", state)
            if state == "closed":
                self.logger.error((
                    "Signaling connection closed."
                ))
            elif state == "have-local-offer":
                self.logger.debug(
                    "Local offer ready. "
                )
            elif state == "have-remote-offer":
                self.logger.debug(
                    "Remote offer ready. "
                )
        
        @self.peer_connection.on("track")
        def on_track(track):
            self.logger.debug("Received %s track", track.kind)
            if track.kind == "audio":
                self.audio_handler.handle_avatar_audio(track)
            elif track.kind == "video":
                self.video_handler.handle_video_track(track)
    
    async def init_peer_connection(self) -> bool:
        """Construct a peer connection object with the appropriate configurations/
        and handlers."""
        # Configure ICE servers (from Anam session object)
        config = RTCConfiguration()
        config.iceServers = []
        for server in self.session_data.get("clientConfig", {}).get("iceServers", []):
            # Get a server with all the necessary fields.
            # if "username" in server and "credential" in server and "urls" in server:
            ice_server = RTCIceServer(
                urls=server["urls"],
                username=server.get("username"),
                credential=server.get("credential")
            )
            config.iceServers.append(ice_server)
            # else: 
            #     self.logger.warning("ICE server is missing some fields: %s", server)

        self.peer_connection = RTCPeerConnection(configuration=config)
        await self.attach_events()

    
        # THERE IS SOME WEIRD BEHAVIOR, resulting in a SDP offer that is rejected.
        # - If we uncomment only audio, or video, the offer is accepted.
        # - If we uncomment both audio and video, the offer is rejected.
        # - If we only uncomment data, the offer is rejected.
        # - If we uncomment all three, the offer is rejected.

                # --------------------------------------------------------------------------
        # Setup Data Channel
        if self.use_data_channel:
            self.data_channel = self.peer_connection.createDataChannel(
            label="chat",
            ordered=True
        )
            self.data_channel.on("open", self.data_handler.on_data_channel_open)
            self.data_channel.on("message", self.data_handler.on_data_channel_message)

            @self.peer_connection.on("datachannel")
            def on_datachannel(channel):
                channel.on("message", self.data_handler.on_data_channel_message)
        
        # --------------------------------------------------------------------------
        # Setup track for sending audio
        if self.use_audio_channel:
            self.logger.debug("Attaching audio track to peer connection. ")
            self.peer_connection.addTrack(
                track=AudioStreamTrack(
                device_name=None,
                audio_handler=self.audio_handler  # Use default device
                )
            )
        # --------------------------------------------------------------------------
        # Setup video track for receiving video (TODO: why is this different from audio?)
        if self.use_video_channel:
            self.logger.debug("Attaching video track to peer connection. ")
            self.peer_connection.addTransceiver(
            "video", 
                direction="recvonly"
            )
        
        return True

    # Setup Helpers
    async def setup_rtc_connection(self):
        """
        Set up the RTC (Real-Time Communication) connection.

        This method configures the RTCPeerConnection with ICE servers from the Anam session,
        sets up event handlers for incoming tracks, initializes data, audio, and video channels, creates an offer, and sends it through the signalling client.

        The method performs the following steps:
        1. Configures an RTCPeerConnection with the configured ICE servers.
            2.1. Configures ICE servers using data from the Anam session object.
            2.2. Sets up handlers for tracks (data, audio and video).
        2. Creates an offer using the peer connection.
        3. Sets the local description of the peer connection.
        4. Sends the offer using the signalling client.

        Raises:
            Warning: If any of the channel setups (data, audio, video) fail.

        Note:
            This method is crucial for establishing the WebRTC connection and should be
            called before any media transmission can occur.
        """

        if not self.signalling_client or not self.session_data:
            self.logger.debug(
                "Cannot setup connection without signalling client or session data."
            )
            return False

        # 1. Create a peer connection
        self.logger.debug("Setting up RTC connection")
        success = await self.init_peer_connection()

        if not success:
            self.logger.error("Failed to initialize peer connection")
            return False

        # 2. Create an offer
        self.logger.debug("Creating offer")
        offer = await self.peer_connection.createOffer()
        local_desc = RTCSessionDescription(
            # sdp=self.modify_sdp(offer.sdp),
            sdp=offer.sdp,
            type="offer"
        )
        await self.peer_connection.setLocalDescription(
            sessionDescription=local_desc
        )

        # 3. Send the offer
        self.logger.debug("Sending offer")
        await self.signalling_client.send_offer(
            self.peer_connection.localDescription,
            # offer=new_session_desc,
            # Using sessionId as userUid
            user_uid=self.session_data.get("sessionId", ""),
        )

    # SIGNALING
    async def handle_answer(self, answer_payload: Dict):
        """
        Handle the SDP answer from the remote peer.

        Args:
            answer_payload (Dict): The SDP answer payload.
        """
        if self.peer_connection and not self.remote_description_set:
            self.logger.debug(
                "Setting remote description with answer: %s", answer_payload
            )
            answer = RTCSessionDescription(
                sdp=answer_payload["sdp"], type=answer_payload["type"]
            )
            await self.peer_connection.setRemoteDescription(answer)
            self.remote_description_set = True
        else:
            self.logger.warning(
                "Remote description already set, ignoring additional answer."
            )

    # Removed to simplify the API. 
    async def send_message(self, message: str):
        """
        Send a message through the data channel.

        Args:
            message (str): The message to send.
        """
        if self.data_channel and self.data_channel.readyState == "open":
            self.data_channel.send(message)
            self.logger.debug(f"Sent message: {message}")
        else:
            self.logger.warning("Data channel is not open. Message not sent.")

    # Utilities
    def modify_sdp(self, sdp):
        """
        Modify the SDP to ensure consistent ICE credentials across media sections.

        This function only replaces ice-ufrag and ice-pwd in all media sections
        with the values from the first media section that has them.

        Args:
            sdp (str): The original SDP string.

        Returns:
            str: The modified SDP string with consistent ICE credentials.
        """
        lines = sdp.split("\r\n")
        modified_lines = []
        first_ice_ufrag = None
        first_ice_pwd = None
        in_media_section = False

        for line in lines:
            if line.startswith("m="):
                in_media_section = True

            if in_media_section:
                if first_ice_ufrag is None and line.startswith("a=ice-ufrag:"):
                    first_ice_ufrag = line
                elif first_ice_pwd is None and line.startswith("a=ice-pwd:"):
                    first_ice_pwd = line

                if line.startswith("a=ice-ufrag:") and first_ice_ufrag:
                    modified_lines.append(first_ice_ufrag)
                elif line.startswith("a=ice-pwd:") and first_ice_pwd:
                    modified_lines.append(first_ice_pwd)
                else:
                    modified_lines.append(line)
            else:
                modified_lines.append(line)

        if not first_ice_ufrag or not first_ice_pwd:
            self.logger.warning(
                "Could not find ice-ufrag and ice-pwd in any media section."
            )
            return sdp

        modified_sdp = "\r\n".join(modified_lines)
        return modified_sdp

    def create_ice_candidate(self, payload):
        """
        Create an RTCIceCandidate object from the payload.

        Args:
            payload (Dict): The ICE candidate payload.

        Returns:
            RTCIceCandidate: The created ICE candidate object.
        """
        self.logger.debug("Creating ICE candidate.")

        candidate_parts = payload["candidate"].split(" ")

        candidate = RTCIceCandidate(
            component=int(candidate_parts[1]),
            foundation=candidate_parts[0].split(":")[1],
            ip=candidate_parts[4],
            port=int(candidate_parts[5]),
            priority=int(candidate_parts[3]),
            protocol=candidate_parts[2],
            type=candidate_parts[7],
            sdpMid=payload.get("sdpMid", ""),
            sdpMLineIndex=payload.get("sdpMLineIndex", 0),
        )

        self.logger.debug("Created ICE candidate.")
        return candidate
