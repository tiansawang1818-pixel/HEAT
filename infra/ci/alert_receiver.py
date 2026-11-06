"""Disposable CI notification sink. Never contacts people or external systems."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

received_names = set()
lock = threading.Lock()


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size > 65536:
            self.send_error(413); return
        body = json.loads(self.rfile.read(size))
        with lock:
            received_names.update(alert.get("labels", {}).get("alertname", "") for alert in body.get("alerts", []))
        self.send_response(200); self.end_headers()

    def do_GET(self):
        with lock:
            body = json.dumps({"alert_names": sorted(received_names)}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8080), Receiver).serve_forever()
