# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Import modules one at a time, each in an interpreter that has imported nothing of Noust.

Run as ``python isolated_import_child.py NAME...`` by ``tests/test_import_isolation.py``; it is
not a test. The importing is the point: pytest imports every module in one process, so a module
that works only because another was imported before it, in an order that happened to be that one,
passes there and fails for the operator whose first import is a different one.

A child is forked for each module from a parent that holds only the third-party libraries (what is
being checked is Noust's own order, and loading FastAPI again for every module would take most of
the time), so each child starts with no module of Noust loaded. Nothing but the standard library
is used here, and nothing of Noust is imported by the parent.
"""

from __future__ import annotations

import os
import sys
import traceback

THIRD_PARTY = ("fastapi", "starlette", "pydantic", "yaml", "jinja2", "click", "rich", "psutil")
WORKERS = 4


def import_alone(name: str) -> int:
    """
    Import one module in the current process, a forked child, and report how it went.

    Args:
        name: The dotted module name.

    Returns:
        0 when it imported, 1 when it raised (the traceback is on standard error).
    """
    try:
        __import__(name)
    except BaseException:  # the point is to hear of every way a module can fail to load
        traceback.print_exc()
        return 1
    return 0


def main(names: list[str]) -> int:
    """
    Import each module in a child of its own and list the ones that failed.

    Args:
        names: The dotted module names.

    Returns:
        0 when every module imported, 1 otherwise.
    """
    for third_party in THIRD_PARTY:
        __import__(third_party)
    queue = list(reversed(names))
    running: dict[int, tuple[str, int]] = {}
    failures: dict[str, str] = {}
    while queue or running:
        while queue and len(running) < WORKERS:
            name = queue.pop()
            read_end, write_end = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read_end)
                os.dup2(write_end, 2)
                sys.stdout = open(os.devnull, "w")
                os._exit(import_alone(name))
            os.close(write_end)
            running[pid] = (name, read_end)
        pid, status = os.waitpid(-1, 0)
        name, read_end = running.pop(pid)
        with os.fdopen(read_end, errors="replace") as pipe:
            text = pipe.read().strip()
        if status != 0:
            failures[name] = text or f"exit status {status}"
    for name, text in sorted(failures.items()):
        print(f"--- {name}\n{text}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
