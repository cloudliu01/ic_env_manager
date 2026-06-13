#!/usr/bin/env python3
"""Small controllable demo service for local development.

Run from backend/:
    python demo_service.py --host 127.0.0.1 --port 18080
"""

from __future__ import annotations

import argparse
import json
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "ICEnvGuardDemo/0.1"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json({"status": "ok", "service": "demo-service"})
            return
        if self.path == "/":
            self._send_json(
                {
                    "message": "Hello from the IC Env Guard demo service",
                    "health": "/healthz",
                }
            )
            return
        self.send_error(404, "not found")

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)

    def _send_json(self, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a tiny demo HTTP service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)

    def shutdown(_signum: int, _frame: object) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print(f"demo service listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
