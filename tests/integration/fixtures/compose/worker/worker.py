#!/usr/bin/env python3
"""A tiny worker for the Compose scenarios: no port, no web, one heartbeat line a second.

It stands for what ``licitaciones`` is in production: a stack that fetches things and sends
mail, running and healthy while publishing nothing. Noust has to judge it by its container.

VERSION (a file baked into the image, changed by committing it)
    ``v1``, ``v2``, ...: beat every second until stopped.
    ``crash``: exit 1 after a second, again and again under the restart policy.
"""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

VERSION = (Path(__file__).resolve().parent / "VERSION").read_text().strip()


def stop(*_: object) -> None:
    """Leave on SIGTERM, which is how Compose stops a container."""
    sys.exit(0)


def main() -> None:
    """Beat until stopped, or fail after a second when this version is the crashing one."""
    signal.signal(signal.SIGTERM, stop)
    beat = 0
    while True:
        beat += 1
        print(f"worker {VERSION} heartbeat {beat}", flush=True)
        if VERSION == "crash" and beat >= 1:
            print("worker: this version crashes", file=sys.stderr, flush=True)
            time.sleep(1)
            sys.exit(1)
        time.sleep(1)


if __name__ == "__main__":
    main()
