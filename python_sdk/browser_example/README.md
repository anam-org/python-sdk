# Serve each HTML file using a simple python webserver

To view these HTML files in your browser, you’ll need to serve them using a simple web server. You can use Python’s built-in HTTP server for this.

1.	Place both HTML files in a directory. For example, let’s say you put them in a directory named webrtc_test.

2.	Start a simple HTTP server:
Open a terminal and navigate to the directory where your HTML files are located.
If you are using Python 3.x, run:

```bash
    python -m http.server 8000
```

3. Access the webpages in your browser 

Open your browser and access the publisher HTML page:
- Go to http://localhost:8000/publisher.html. This will open the publisher page where you can stream video and audio from your webcam.

Open another tab or a different browser and access the subscriber HTML page:
- Go to http://localhost:8000/subscriber.html. This will open the subscriber page where you can receive the video and audio stream.