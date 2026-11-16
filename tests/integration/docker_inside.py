#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Run one scenario of ``docker_run.py`` inside the engine it is in.

``docker_run.py`` starts a real Docker engine in a container (Docker in Docker, the official image of its
age) and runs this file there: ``docker`` is that engine's own command line and ``docker compose`` its own Compose,
as on a server that has Docker and nothing else. The code under test is ``noust.core.docker``,
``noust.managers`` and ``noust.deployers`` as the source tree has them, mounted read-only at ``/noust/src``.

One scenario is one function that prints a line for each thing it checked (``@@`` and a JSON object), so the
driver can build the table of scenarios by engine. Anything else it prints is its own noise and is only shown
when something failed. Not part of pytest (it does not match ``test_*.py``) and not meant to be run by hand.

Usage (what the driver runs):
    python3 /harness/docker_inside.py doctor
"""

from __future__ import annotations

import json
import sys
import traceback

import docker_inside_changes  # noqa: F401 - registers its scenarios
import docker_inside_door  # noqa: F401 - registers its scenarios
import docker_inside_zone  # noqa: F401 - registers its scenarios
from docker_inside_lib import SCENARIOS, Run, Skip


def main(argv: list[str]) -> int:
    """
    Run a scenario.

    Args:
        argv: Its name.

    Returns:
        0 when it ran to its end (the checks say how it went), 2 when it did not.
    """
    if len(argv) != 1 or argv[0] not in SCENARIOS:
        print(f"usage: docker_inside.py {{{'|'.join(sorted(SCENARIOS))}}}", file=sys.stderr)
        return 2
    run = Run(argv[0])
    try:
        SCENARIOS[argv[0]](run)
    except Skip as skipped:
        print("@@" + json.dumps({"na": str(skipped)}), flush=True)
    except Exception:
        print("@@" + json.dumps({"error": traceback.format_exc()[-1800:]}), flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
