#!/usr/bin/env python3
"""A tiny web service for the Compose scenarios: a version, a health endpoint, a few modes.

What it does comes from two files baked into the image, which a scenario changes by
committing them (so a "version" of the stack is a commit):

VERSION
    ``v1``, ``v2``, ...: answer ``<role> <version>`` on ``/`` and ``ok`` on ``/health``;
    the role is the ROLE environment variable, ``web`` when the service sets none.
    ``broken``: exit 3 straight away, the way an image that cannot start does.
    ``unhealthy``: listen, but answer 503 everywhere, the way a stack that starts and
    never serves does.

DELAY
    Seconds to wait before listening (default 0). A recreate of the container then leaves
    a gap a probe can see, which is what a zero-downtime relay has to close.

It handles SIGTERM, because as PID 1 it would otherwise ignore it and ``docker stop``
would wait ten seconds before killing it.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 8080


def read(name: str, default: str) -> str:
    """The text of a file next to this one, or a default when it is not there."""
    path = HERE / name
    return path.read_text().strip() if path.exists() else default


VERSION = read("VERSION", "v0")
ROLE = os.environ.get("ROLE", "web")


class Handler(BaseHTTPRequestHandler):
    """Answers ``/`` with the version and ``/health`` with ``ok``."""

    def do_GET(self) -> None:
        """Answer a GET: the name http.server dispatches on."""
        if VERSION == "unhealthy":
            self.reply(503, "unhealthy")
        elif self.path == "/health":
            self.reply(200, "ok")
        else:
            self.reply(200, f"{ROLE} {VERSION}")

    def reply(self, status: int, body: str) -> None:
        """Send a short plain-text answer."""
        data = (body + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        """Keep the access log out of the way: the scenarios read the journal, not this."""


def stop(*_: object) -> None:
    """Leave on SIGTERM, which is how Compose stops a container."""
    sys.exit(0)


def main() -> None:
    """Wait the configured delay, then serve until stopped."""
    if VERSION == "broken":
        print(f"{ROLE}: this version cannot start", file=sys.stderr, flush=True)
        sys.exit(3)
    signal.signal(signal.SIGTERM, stop)
    time.sleep(float(read("DELAY", "0")))
    print(f"{ROLE} {VERSION} listening on {PORT}", flush=True)
    # All interfaces: it is the only thing in its container, and Compose decides what is published.
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
