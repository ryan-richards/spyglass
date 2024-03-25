import io
import logging
import socketserver
from http import server
from threading import Condition
from spyglass.url_parsing import check_urls_match
from spyglass.exif import create_exif_header
from . import logger

PAGE = """\
<html>
<head>
<title>picamera2 streaming</title>
</head>
<body>
<h1>Picamera2 Streaming</h1>
<img src="/stream" width="640" height="480" />
<br/>
<a href="/snapshot" target="_blank"><button>Capture Hi-Res Image</button></a>
<a href="/burst"><button>Capture Hi-Res Image Burst</button></a>
</body>
</html>
"""

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server(picam2, bind_address, port, output, stream_url='/stream', snapshot_url='/snapshot', orientation_exif=0):
    exif_header = create_exif_header(orientation_exif)

    class StreamingHandler(server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                self.send_response(301)
                self.send_header('Location', '/index.html')
                self.end_headers()
            elif self.path == '/index.html':
                content = PAGE.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', len(content))
                self.end_headers()
                self.wfile.write(content)
            elif check_urls_match(stream_url, self.path):
                self.start_streaming()
            elif check_urls_match(snapshot_url, self.path):
                self.send_snapshot()
            elif self.path == '/burst':
                self.capture_hi_res_image_burst()
            else:
                self.send_error(404)
                self.end_headers()

        def start_streaming(self):
            try:
                self.send_response(200)
                self.send_default_headers()
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
                self.end_headers()
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    if exif_header is None:
                        self.send_jpeg_content_headers(frame)
                        self.end_headers()
                        self.wfile.write(frame)
                        self.wfile.write(b'\r\n')
                    else:
                        self.send_jpeg_content_headers(frame, len(exif_header) - 2)
                        self.end_headers()
                        self.wfile.write(exif_header)
                        self.wfile.write(frame[2:])
                        self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning('Removed streaming client %s: %s', self.client_address, str(e))

        def send_snapshot(self):
            try:
                self.send_response(200)
                self.send_default_headers()
                with output.condition:
                    output.condition.wait()
                    frame = output.frame
                if orientation_exif <= 0:
                    self.send_jpeg_content_headers(frame)
                    self.end_headers()
                    self.wfile.write(frame)
                else:
                    self.send_jpeg_content_headers(frame, len(exif_header) - 2)
                    self.end_headers()
                    self.wfile.write(exif_header)
                    self.wfile.write(frame[2:])
            except Exception as e:
                logging.warning(
                    'Removed client %s: %s',
                    self.client_address, str(e))
                
        def capture_hi_res_image_burst(self):
            picam2.start_and_capture_files("test{:d}.jpg", num_files=5, delay=0.2)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            response = f"<html><body>High-resolution image burst captured<br><a href='/'>Back</a></body></html>"
            self.wfile.write(response.encode('utf-8'))

        def send_default_headers(self):
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')

        def send_jpeg_content_headers(self, frame, extra_len=0):
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(frame) + extra_len))

    logger.info('Server listening on %s:%d', bind_address, port)
    logger.info('Streaming endpoint: %s', stream_url)
    logger.info('Snapshot endpoint: %s', snapshot_url)
    address = (bind_address, port)
    current_server = StreamingServer(address, StreamingHandler)
    current_server.serve_forever()
