#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Measure the battery of the built-in assistant and write down what it says.

The battery is part of pytest (``tests/test_assistant_battery_*.py``): a scripted model, the real console, no
network and no cost. This runs it with ``NOUST_BATTERY_RECORDS`` pointing at a folder, so that every case
leaves a summary, merges the summaries into ``tests/integration/results/assistant-battery.json`` and writes
the numbers into ``docs/assistant-battery.md``, between its two markers. Nothing in either has a time or a
text a model wrote, so the same code gives the same files.

Usage:
    scripts/assistant_battery.py --write       Run the battery and write the report and the page.
    scripts/assistant_battery.py --check       Exit 1 when what is committed is not what the scenarios and
                                               the page say (nothing is run).
    scripts/assistant_battery.py --records DIR Merge the summaries already in DIR (no run) and write them.
    scripts/assistant_battery.py --render      Write the page's table again from the committed report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tests.support.assistant_battery.report import (  # noqa: E402
    RECORDS_ENV,
    case_id,
    inject_table,
    merge,
    render_table,
)
from tests.support.assistant_battery.scenario import CARRIERS, all_scenarios  # noqa: E402

REPORT = ROOT / "tests" / "integration" / "results" / "assistant-battery.json"
PAGE = ROOT / "docs" / "assistant-battery.md"
FILES = sorted((ROOT / "tests").glob("test_assistant_battery_*.py"))
#: The modules that only prove the battery's own parts, and make no summary.
NOT_CASES = ("oracles", "scenarios", "report")


def expected_cases() -> list[str]:
    """Returns the name of every case the scenario files expand to."""
    names: list[str] = []
    for scenario in all_scenarios():
        if scenario.family == "injection":
            names += [case_id(scenario.id, c) for c in (scenario.carriers or CARRIERS)]
        else:
            names.append(case_id(scenario.id, None))
    return sorted(names)


def limits_expected() -> int:
    """Returns how many scenarios of the limits family say that a limit cuts."""
    return sum(1 for s in all_scenarios() if s.family == "limits" and s.expect.get("stop"))


def run(records: Path, workers: int) -> int:
    """
    Run the battery, one summary per case.

    Args:
        records: Where the summaries are written.
        workers: How many processes pytest uses.

    Returns:
        pytest's exit status.
    """
    modules = [str(f) for f in FILES if not any(f.stem.endswith(f"_{name}") for name in NOT_CASES)]
    env = {**os.environ, RECORDS_ENV: str(records), "PYTHONPATH": str(ROOT / "src")}
    command = [sys.executable, "-m", "pytest", "-q", "-n", str(workers), *modules]
    print("+", " ".join(command))
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def report_from(records: Path) -> dict[str, Any]:
    """
    Args:
        records: A folder of summaries.

    Returns:
        The report they make.
    """
    # The version of the tree that was measured, from the one place it is kept (the installed metadata
    # may be of another checkout).
    found = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.MULTILINE)
    version = found.group(1) if found else "unknown"
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(records.glob("*.json"))]
    return merge(cases, limits_expected=limits_expected(), version=version)


def write(report: dict[str, Any]) -> None:
    """
    Args:
        report: What :func:`report_from` made.
    """
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    PAGE.write_text(
        inject_table(PAGE.read_text(encoding="utf-8"), render_table(report)), encoding="utf-8"
    )


def check() -> list[str]:
    """
    Returns:
        What is out of date or wrong in what is committed, empty when it is all as it should be.
    """
    problems: list[str] = []
    if not REPORT.exists():
        return [
            f"{REPORT.relative_to(ROOT)} does not exist: run scripts/assistant_battery.py --write"
        ]
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    listed = sorted(r["case"] for r in report["results"])
    wanted = expected_cases()
    for name in sorted(set(wanted) - set(listed)):
        problems.append(f"the report has no case {name}: the scenarios changed since it was made")
    for name in sorted(set(listed) - set(wanted)):
        problems.append(f"the report has a case {name} that no scenario makes")
    problems += [f"{r['case']} did not pass" for r in report["results"] if not r["passed"]]
    expected_page = inject_table(PAGE.read_text(encoding="utf-8"), render_table(report))
    if expected_page != PAGE.read_text(encoding="utf-8"):
        problems.append(f"{PAGE.relative_to(ROOT)} does not say what the report says")
    return problems


def main(argv: list[str] | None = None) -> int:
    """
    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The exit status.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="run the battery and write the report")
    group.add_argument(
        "--check", action="store_true", help="exit 1 when what is committed is stale"
    )
    group.add_argument("--records", type=Path, help="merge the summaries in this folder")
    group.add_argument(
        "--render",
        action="store_true",
        help="write the page's table again from the committed report",
    )
    parser.add_argument("--workers", type=int, default=3, help="processes pytest uses (default 3)")
    args = parser.parse_args(argv)
    if args.check:
        problems = check()
        for line in problems:
            print(line)
        return 1 if problems else 0
    if args.render:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        PAGE.write_text(
            inject_table(PAGE.read_text(encoding="utf-8"), render_table(report)), encoding="utf-8"
        )
        return 0
    if args.records is not None:
        write(report_from(args.records))
        return 0
    with tempfile.TemporaryDirectory(prefix="noust-battery-") as folder:
        status = run(Path(folder), args.workers)
        report = report_from(Path(folder))
    write(report)
    criterion = report["criterion"]
    print(json.dumps({k: v for k, v in criterion.items() if k != "forms"}, indent=1))
    return status


if __name__ == "__main__":
    sys.exit(main())
