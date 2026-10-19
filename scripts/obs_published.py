#!/usr/bin/env python3
"""
Wait until every distribution repository serves a Noust version, and say who does not.

The release workflow uploads the sources to OBS and OBS builds one repository per
distribution (fifteen to thirty minutes, and Debian 12 may finish while Ubuntu 24.04
is still building). A release is announced only when this passes: nothing is
announced while some supported distribution still offers the previous version
(checklist N-30, owner items 80 and 81).

It reads the published indexes the package managers read, through
``noust.managers.server.publication``; the list of
distributions is ``noust.managers.server.platforms.TARGETS``, so a new release added
to the table is gated from the day it is added.

Usage:
    scripts/obs_published.py 3.4.0                  Check once; exit 1 if any is behind.
    scripts/obs_published.py v3.4.0 --wait 90       Poll for up to 90 minutes.

A repository of a best-effort release is reported and never blocks. With
``GITHUB_STEP_SUMMARY`` set, the table is written to the run's summary.

Exit status: 0 every required repository serves the version, 1 some do not (after
the wait), 2 a usage error.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.core import package_index  # noqa: E402
from noust.managers.server import publication  # noqa: E402

#: A request to download.opensuse.org is redirected to a mirror; three seconds
#: (the update check's own deadline) is too short for a CI runner on a bad hour.
REQUEST_TIMEOUT = 20


def parse_version(text: str) -> str:
    """
    Accept a tag or a version.

    Args:
        text: ``v3.4.0`` or ``3.4.0``.

    Returns:
        The version without the leading ``v``.

    Raises:
        ValueError: When it is not a dotted number such as ``3.4.0``.
    """
    version = text.strip().removeprefix("v")
    parts = version.split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"{text!r} is not a version such as 3.4.0")
    return version


def run(
    version: str,
    *,
    wait_minutes: float,
    interval: float,
    check: Callable[[str], publication.Publication] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    out: Callable[[str], None] = print,
) -> publication.Publication:
    """
    Check the repositories until they all serve the version or the wait ends.

    Args:
        version: The version being published.
        wait_minutes: How long to keep asking; 0 asks once.
        interval: Seconds between two looks.
        check: Reads the repositories (replaced in tests).
        sleep: Waits between looks (replaced in tests).
        clock: A monotonic clock (replaced in tests).
        out: Where progress goes.

    Returns:
        The last state read.
    """
    read = check or publication.check
    deadline = clock() + wait_minutes * 60
    while True:
        state = read(version)
        if state.ready:
            out(f"Every required repository serves Noust {version}.")
            return state
        for repo in state.missing:
            out(
                f"waiting: {repo.repo} ({', '.join(repo.releases)}): {'; '.join(repo.lagging(version))}"
            )
        if clock() + interval > deadline:
            return state
        sleep(interval)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """
    Run the gate.

    Args:
        argv: The arguments after the program name.
        env: The environment (for ``GITHUB_STEP_SUMMARY``).

    Returns:
        The exit status.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("version", help="the release tag or version, such as v3.4.0")
    parser.add_argument(
        "--wait", type=float, default=0, metavar="MINUTES", help="keep asking this long"
    )
    parser.add_argument(
        "--interval", type=float, default=120, metavar="SECONDS", help="between two looks"
    )
    parser.add_argument(
        "--timeout", type=float, default=REQUEST_TIMEOUT, metavar="SECONDS", help="per request"
    )
    args = parser.parse_args(argv)
    environment = os.environ if env is None else env
    try:
        version = parse_version(args.version)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    package_index.TIMEOUT = int(args.timeout)
    state = run(version, wait_minutes=args.wait, interval=args.interval)
    table = state.to_markdown()
    print(table)
    summary = environment.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(table)
    if state.late:
        names = ", ".join(r.repo for r in state.late)
        print(f"note: best-effort repositories not serving {version}: {names} (not blocking)")
    return 0 if state.ready else 1


if __name__ == "__main__":
    sys.exit(main())
