document.addEventListener('DOMContentLoaded', () => {
    const joinButton = document.getElementById('join-button');
    joinButton.addEventListener('click', async () => {
        const username = document.getElementById('username').value.trim();
        if (username) {
            try {
                await joinSession(username);
            } catch (error) {
                console.error('Error joining session:', error);
            }
        } else {
            alert('Please enter your name');
        }
    });
});

async function joinSession(username) {
    const response = await fetch('/offer', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ username })
    });

    if (!response.ok) {
        throw new Error('Failed to join session');
    }

    const data = await response.json();
    // Handle the WebRTC offer and set up the connection
    console.log('Joined session:', data);
}