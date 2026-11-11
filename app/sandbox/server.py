"""Sandbox service (PMH-16): runs analysis code for the worker over HTTP.

Deployed as its own container on an `internal` Docker network: no internet access,
no API keys, read-only filesystem, no Linux capabilities. Its only reachable peers are
the worker (which sends code) and the web service's PTC tool proxy.
Run:  python -m app.sandbox.server   (listens on SANDBOX_BIND:SANDBOX_PORT, default 0.0.0.0:8100)"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.sandbox.runner import run_local

MAX_BODY = 256 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "pmh-sandbox"

    def _send(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/run":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._send(413, {"error": "code too large"})
        try:
            req = json.loads(self.rfile.read(length))
            code = req["code"]
            if not isinstance(code, str):
                raise ValueError
        except (ValueError, KeyError):
            return self._send(400, {"error": "expected JSON with a 'code' string"})
        result = run_local(code, ptc_token=str(req.get("ptc_token", "")),
                           timeout_s=req.get("timeout_s"), mem_mb=req.get("mem_mb"))
        self._send(200, result)

    def log_message(self, fmt, *args):  # one short line per request
        print(f"sandbox {self.address_string()} {fmt % args}", flush=True)


def serve(bind: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((bind or os.environ.get("SANDBOX_BIND", "0.0.0.0"),
                                port or int(os.environ.get("SANDBOX_PORT", "8100"))), Handler)


if __name__ == "__main__":
    srv = serve()
    print(f"sandbox listening on {srv.server_address}", flush=True)
    srv.serve_forever()
