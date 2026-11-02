from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._respond(include_body=True)

    def do_HEAD(self):
        self._respond(include_body=False)

    def _respond(self, *, include_body: bool):
        if self.path.split("?", 1)[0] != "/":
            self.send_error(404)
            return

        body = b"I'm alive!"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def run():
    """Run the lightweight healthcheck server used by the hosting platform."""
    ThreadingHTTPServer(("0.0.0.0", 8080), HealthCheckHandler).serve_forever()


def keep_alive():
    """Start the healthcheck web server in a background thread."""
    t = Thread(target=run)
    t.start()
