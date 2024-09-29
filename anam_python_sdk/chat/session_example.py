{
    'sessionId': '620a746f-08a8-4f10-8690-f212453cb752', 
    'engineHost': 'engine-0-gcp-us-central1-a-gcp-prod-1.engine.anam.ai', 
    'engineProtocol': 'https', 
    'signallingEndpoint': '/ws', 
    'clientConfig': {
        'maxWsReconnectAttempts': 5, 
        'expectedHeartbeatIntervalSecs': 5, 
        'iceServers': [{
            'credentialType': 'password', 
            'urls': ['stun:stun.relay.metered.ca: 80']},
            {
            "credential": "spkGLyU5vosSyGKx",
            "credentialType": "password",
            "urls": [
                "turn:a.relay.metered.ca: 80",
                "turn:a.relay.metered.ca: 80?transport=tcp",
                "turn:a.relay.metered.ca: 443",
                "turn:a.relay.metered.ca: 443?transport=tcp"
            ],
            "username": "75533ce9605644d4873f8be0"
        }
        ]
    }
}