#!/usr/bin/env python3
"""
Record every POST it receives, one body per line: a notification webhook.

Runs inside the integration container, under ``systemd-run``, as the endpoint
of WASM's ``webhook`` notification channel. Each request body (the channel's
JSON) is appended to the output file on a line of its own, and answered 204.

Usage:
    recorder.py PORT OUTFILE
"""

from __future__ import annotations

import http.server
import sys


def main() -> None:
    port, out = int(sys.argv[1]), sys.argv[2]

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            with open(out, "ab") as log:
                log.write(body.replace(b"\n", b" ") + b"\n")
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
