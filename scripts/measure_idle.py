#!/usr/bin/env python3
"""
Measure what Noust's two long-running processes use when nothing is happening (checklist N-04).

"Fits in 1 GB" is a claim, so it is measured here and the method is the one anybody can repeat:

- the console, ``noust-web``: the real FastAPI application under uvicorn, as
  ``scripts/console_server.py`` serves it (a seeded store, a model of systemd instead of the
  machine), left alone for the measuring window after a warm-up;
- the monitor, ``noust-monitor``: ``noust monitor run`` with its own scratch data directory.

For each, the resident memory (the process and its children), the processor time it used and the
number of threads and open files are read from the kernel with psutil once a second. The report
gives the median and the peak. It is a measure of Noust's own idle cost, not of the applications
it runs: those are whatever they are.

Usage:
    scripts/measure_idle.py                  Measure 60 seconds after a 15 second warm-up.
    scripts/measure_idle.py --seconds 120 --markdown   Print the table docs/requirements.md uses.
    scripts/measure_idle.py --json           Print the numbers as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parent.parent
MB = 1024 * 1024


def tree_usage(process: psutil.Process) -> tuple[int, int, int, float]:
    """
    Read the usage of a process and its children.

    Args:
        process: The process.

    Returns:
        ``(resident bytes, threads, open files, cpu seconds)`` summed over the tree.
    """
    resident = threads = files = 0
    cpu = 0.0
    for member in [process, *process.children(recursive=True)]:
        try:
            resident += member.memory_info().rss
            threads += member.num_threads()
            files += member.num_fds()
            times = member.cpu_times()
            cpu += times.user + times.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return resident, threads, files, cpu


def sample(process: psutil.Process, seconds: int) -> dict[str, float]:
    """
    Watch a process for a while.

    Args:
        process: The process.
        seconds: How long, one reading per second.

    Returns:
        The median and peak of the resident memory in megabytes, the processor use as a
        percentage of one core over the whole window, and the median threads and open files.
    """
    readings = []
    start_cpu = tree_usage(process)[3]
    started = time.monotonic()
    for _ in range(seconds):
        time.sleep(1)
        readings.append(tree_usage(process))
    elapsed = time.monotonic() - started
    end_cpu = readings[-1][3]
    resident = [r[0] / MB for r in readings]
    return {
        "memory_mb_median": round(statistics.median(resident), 1),
        "memory_mb_peak": round(max(resident), 1),
        "cpu_percent_of_one_core": round(100 * (end_cpu - start_cpu) / elapsed, 2),
        "threads": statistics.median(r[1] for r in readings),
        "open_files": statistics.median(r[2] for r in readings),
    }


def measure_console(seconds: int, warmup: int) -> dict[str, float]:
    """
    Start the console as the tests and the console's development use it, and measure it idle.

    Args:
        seconds: The measuring window.
        warmup: Seconds to let it settle first.

    Returns:
        The readings of :func:`sample`.
    """
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "console_server.py"), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        if server.stdout is None:
            raise RuntimeError("the console server has no standard output")
        server.stdout.readline()  # the JSON line it prints once it accepts connections
        time.sleep(warmup)
        return sample(psutil.Process(server.pid), seconds)
    finally:
        server.terminate()
        server.wait(timeout=30)


def measure_monitor(seconds: int, warmup: int) -> dict[str, float]:
    """
    Run the monitor's loop in a scratch directory and measure it idle.

    Args:
        seconds: The measuring window.
        warmup: Seconds to let it settle first.

    Returns:
        The readings of :func:`sample`.
    """
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "NOUST_DATA_DIR": scratch}
        monitor = subprocess.Popen(
            [sys.executable, "-m", "noust", "monitor", "run"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=ROOT,
        )
        try:
            time.sleep(warmup)
            return sample(psutil.Process(monitor.pid), seconds)
        finally:
            monitor.terminate()
            monitor.wait(timeout=30)


def environment() -> dict[str, Any]:
    """Returns: What the measurement ran on, so a number is never separated from its machine."""
    return {
        "python": platform.python_version(),
        "kernel": platform.release(),
        "cpus": psutil.cpu_count(logical=True),
        "memory_gb": round(psutil.virtual_memory().total / 1024**3, 1),
    }


def markdown(result: dict[str, Any]) -> str:
    """
    Render the readings as a table.

    Args:
        result: The measurement.

    Returns:
        Markdown.
    """
    env = result["environment"]
    rows = [
        "| Process | Memory (median) | Memory (peak) | Processor | Threads | Open files |",
        "|---|---|---|---|---|---|",
    ]
    for name in ("console", "monitor"):
        r = result[name]
        rows.append(
            f"| `noust-{'web' if name == 'console' else 'monitor'}` | {r['memory_mb_median']} MB | "
            f"{r['memory_mb_peak']} MB | {r['cpu_percent_of_one_core']} % of one core | "
            f"{int(r['threads'])} | {int(r['open_files'])} |"
        )
    window = result["seconds"]
    return (
        "\n".join(rows)
        + f"\n\nMeasured over {window} seconds after a {result['warmup']} second warm-up, on Python "
        f"{env['python']}, {env['cpus']} logical processors and {env['memory_gb']} GB of memory.\n"
    )


def main(argv: list[str] | None = None) -> int:
    """
    Command line entry point.

    Args:
        argv: Arguments, defaulting to ``sys.argv``.

    Returns:
        0.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--seconds", type=int, default=60, help="the measuring window")
    parser.add_argument("--warmup", type=int, default=15, help="seconds to settle first")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("--markdown", action="store_true", help="print the table the docs use")
    args = parser.parse_args(argv)
    result = {
        "seconds": args.seconds,
        "warmup": args.warmup,
        "environment": environment(),
        "console": measure_console(args.seconds, args.warmup),
        "monitor": measure_monitor(args.seconds, args.warmup),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.markdown:
        print(markdown(result))
    else:
        for name in ("console", "monitor"):
            print(name, json.dumps(result[name]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
