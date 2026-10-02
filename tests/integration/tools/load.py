#!/usr/bin/env python3
"""
Ask nginx for one host at a fixed interval and log every answer.

Runs inside the integration container, under ``systemd-run``, while the
blue/green scenario switches an application between its instances. Each
request opens a new connection, the way an unrelated visitor would, and
writes one line to the log:

    OK <unix time> <status> <body>
    FAIL <unix time> <status or "error"> <what went wrong>

The harness counts the lines each phase added, so a switch that drops even
one request shows up as a FAIL line with the moment it happened.

Usage:
    load.py HOST LOGFILE [INTERVAL_SECONDS] [PATH] [ACCEPTED]

PATH is what to ask for (default ``/``). ACCEPTED is the leading digits of the statuses
that count as served: ``2`` (the default) for 2xx only, ``23`` for an application that
answers with a redirect, which is a served request and not an outage.
"""

from __future__ import annotations

import http.client
import sys
import time


def main() -> None:
    host, out = sys.argv[1], sys.argv[2]
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 0.05
    path = sys.argv[4] if len(sys.argv) > 4 else "/"
    accepted = sys.argv[5] if len(sys.argv) > 5 else "2"
    with open(out, "a", buffering=1) as log:
        while True:
            started = time.time()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 80, timeout=5)
                conn.request("GET", path, headers={"Host": host})
                response = conn.getresponse()
                body = response.read().decode(errors="replace").strip().replace("\n", " ")
                conn.close()
                verdict = "OK" if str(response.status)[0] in accepted else "FAIL"
                log.write(f"{verdict} {started:.3f} {response.status} {body[:60]}\n")
            except (OSError, http.client.HTTPException) as exc:
                log.write(f"FAIL {started:.3f} error {type(exc).__name__}: {exc}\n")
            time.sleep(max(0.0, interval - (time.time() - started)))


if __name__ == "__main__":
    main()
