const signalingUrl = "ws://localhost:8080";
let pc = new RTCPeerConnection({
    iceServers: [
        { urls: "stun:stun.l.google.com:19302" }  // Sample STUN server
    ]
});

// Setup track handler for media streams
pc.ontrack = (event) => {
    const videoElement = document.getElementById('localVideo');
    videoElement.srcObject = event.streams[0];
    console.log("Assigned local stream to video element.");
};

// Check if ICE gathering state changes (debugging)
pc.onicegatheringstatechange = () => {
    console.log("ICE gathering state changed to:", pc.iceGatheringState);
};

pc.onicecandidate = event => {
    console.log("ICE candidate event fired:", event);
    if (event.candidate) {
        console.log("ICE candidate gathered:", event.candidate.candidate);
        ws.send(JSON.stringify({
            peer_id: "publisher",
            to: "subscriber",
            candidate: event.candidate
        }));
        console.log("Sent ICE candidate to subscriber");
    } else {
        console.log("ICE candidate gathering completed.");
    }
};

navigator.mediaDevices.getUserMedia({ video: true, audio: true })
    .then(stream => {
        console.log("Media stream obtained:", stream);
        const videoElement = document.getElementById('localVideo');
        videoElement.srcObject = stream;
        stream.getTracks().forEach(track => pc.addTrack(track, stream));
        console.log("Media stream added to peer connection");

        // Create offer after adding tracks (!Important)
        pc.createOffer().then(offer => {
            console.log("Created SDP offer:", offer.sdp);
            return pc.setLocalDescription(offer);
        }).then(() => {
            ws.send(JSON.stringify({
                peer_id: "publisher",
                to: "subscriber",
                type: pc.localDescription.type,
                sdp: pc.localDescription.sdp
            }));
            console.log("Sent SDP offer to subscriber");
        }).catch(error => {
            console.error("Error during offer creation or sending:", error);
        });
    })
    .catch(error => {
        console.error("Error accessing media devices:", error);
    });

let ws = new WebSocket(signalingUrl);

ws.onopen = () => {
    console.log("WebSocket connection opened for Publisher");
    ws.send(JSON.stringify({ peer_id: "publisher", origin: "publisher.js" }));
};

ws.onmessage = async (message) => {
    let data = JSON.parse(message.data);
    console.log("Received message from signaling server:", data);

    if (data.type === 'answer') {
        await pc.setRemoteDescription(new RTCSessionDescription(data));
        console.log("Set remote description from subscriber");
    } else if (data.candidate) { 
        await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
        console.log("Added ICE candidate from subscriber");
    }
};

ws.onerror = (error) => {
    console.error("WebSocket error observed:", error);
};