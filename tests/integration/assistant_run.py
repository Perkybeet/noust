#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The built-in assistant with a model that is real, small and open, in a container (item 171).

The battery of the assistant (``tests/test_assistant_battery_*.py``) runs a scripted model and says what the
server does with the worst model there is. It cannot say what a real server's stream looks like, how a real
model makes a call to a tool, what the probe finds of one, or how long and how well a small one does on a CPU.
This does, on purpose and outside pytest's default run:

1. checks the free space of the drive Docker keeps its data on (less than 30 GB: it does not start);
2. starts a server of the protocol in a container, with the memory of a small machine, and pulls a model that
   fits in it;
3. runs ``tests/integration/assistant_real.py``: the probe, and five to eight scenarios of explaining and
   investigating, on the same seeded console and with the same oracles as the battery, a recording proxy
   between the console and the server;
4. writes ``tests/integration/results/assistant-model.json``: how long it took, what it got right and what it
   got wrong, and the real format of the stream and of the calls to tools.

Nothing paid and no key: the server is open source and runs here. A scenario that cannot run (no Docker, not
enough space, no network for the image or the model) reports SKIP with the reason, never PASS.

Usage (one Docker harness at a time, in the machine's queue):
    /home/yago/.local/bin/noust-docker 2400 .venv/bin/python tests/integration/assistant_run.py
    ... --model qwen2.5:3b --scenarios explain/explain-missing-variable,investigate/investigate-out-of-memory
    ... --keep        # leave the container, to look at it

Docker safety: it creates only a container named ``noust-test-w4p-model``, labelled ``noust.test=1``,
publishes one loopback port that it found free, removes it by exact name and never prunes anything.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))
OUT = REPO_ROOT / "tests" / "integration" / "results" / "assistant-model.json"

#: The least free space on the drive Docker's data is on for a run to start.
MIN_FREE_GB = 30
#: A server of the protocol that runs open models on a CPU, and a model of about 3 billion parameters that
#: calls tools and fits in 6 GB. ``NOUST_TEST_OLLAMA_IMAGE`` replaces the image.
IMAGE = "ollama/ollama@sha256:910d0d8107b538fb0894e63163eb195de0cc7aefcbc666dc62d3b18f71124592"
MODEL = "qwen2.5:3b"
CONTAINER = "noust-test-w4p-model"
MEMORY = "6g"
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
#: What ``assistant-model.json`` says of every run, as against what is one run's.
HEADER = ("measured", "container", "model", "seconds_to_pull_the_model", "date")


def _load(name: str) -> ModuleType:
    """
    Load a sibling harness as a module, without touching ``sys.path``.

    Args:
        name: The file, without ``.py``.

    Returns:
        The module.
    """
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_noust_it_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


identity = _load("identity")
Containers = identity.Containers
Skip = identity.Skip
docker = identity.docker
free_port = identity.free_port


def free_gb() -> float | None:
    """
    Returns:
        The free space, in GB, of the Windows drive Docker keeps its data on when this is WSL; of the folder
        Docker's data is in otherwise. None when it cannot be told.
    """
    if os.path.exists("/init") and os.path.exists(POWERSHELL):
        done = subprocess.run(
            ["/init", POWERSHELL, "-NoProfile", "-Command", "(Get-PSDrive C).Free/1GB"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        try:
            return float(done.stdout.strip().replace(",", "."))
        except ValueError:
            return None
    try:
        return shutil.disk_usage("/var/lib/docker").free / 1024**3
    except OSError:
        return None


def wait_for(url: str, seconds: int) -> None:
    """
    Args:
        url: An address that answers 200 when the server is up.
        seconds: How long to wait.

    Raises:
        RuntimeError: It did not answer in time.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as answer:
                if answer.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1.5)
    raise RuntimeError(f"{url} did not answer in {seconds} s")


def main(argv: list[str] | None = None) -> int:
    """
    Run the harness.

    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        0 when it ran (whatever the model got right), 77 when it could not run.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--model", default=os.environ.get("NOUST_TEST_MODEL", MODEL))
    parser.add_argument(
        "--scenarios", default="", help="comma-separated family/name, default the standard seven"
    )
    parser.add_argument("--keep", action="store_true", help="leave the container for a look")
    parser.add_argument(
        "--patient",
        action="store_true",
        help="give the client the minutes a model on a CPU needs, instead of the product's own patience",
    )
    parser.add_argument("--cpus", default="3")
    parser.add_argument(
        "--label",
        default="",
        help="the name of this run in the report (default: patient or as_shipped)",
    )
    parser.add_argument("--wait", type=int, default=600, help="seconds a turn may take")
    parser.add_argument(
        "--budget",
        type=int,
        default=2300,
        help="seconds the whole run may take, the model's pull included",
    )
    args = parser.parse_args(argv)

    started = time.time()
    containers = Containers(args.keep)
    try:
        free = free_gb()
        if free is not None and free < MIN_FREE_GB:
            raise Skip(
                f"{free:.0f} GB free on the drive Docker uses: less than {MIN_FREE_GB} GB, not starting"
            )
        image = identity.image_for("NOUST_TEST_OLLAMA_IMAGE", IMAGE)
        # The image is gigabytes: pulled here, with the time it takes, so that starting it is quick.
        pulled_image = docker("pull", image, check=False, timeout=args.budget)
        if pulled_image.returncode != 0:
            raise Skip(f"the image could not be pulled: {pulled_image.stderr.strip()[-300:]}")
        port = free_port()
        containers.run(
            CONTAINER,
            image,
            "-p",
            f"127.0.0.1:{port}:11434",
            "--memory",
            MEMORY,
            "--cpus",
            args.cpus,
            "-e",
            "OLLAMA_NUM_PARALLEL=1",
            "-e",
            "OLLAMA_KEEP_ALIVE=1h",
            "-e",
            "OLLAMA_CONTEXT_LENGTH=8192",
        )
        wait_for(f"http://127.0.0.1:{port}/api/version", 120)
        print(f"server up on 127.0.0.1:{port}; pulling {args.model}", flush=True)
        pulled = time.time()
        # The pull of a model is minutes, and the harness's own client gives a command three.
        done_pull = docker(
            "exec", CONTAINER, "ollama", "pull", args.model, check=False, timeout=args.budget
        )
        if done_pull.returncode != 0:
            raise Skip(
                f"the model {args.model} could not be pulled: {done_pull.stderr.strip()[-300:]}"
            )
        pull_seconds = round(time.time() - pulled)
        digest = docker(
            "image", "inspect", "--format", "{{index .RepoDigests 0}}", image, check=False
        ).stdout.strip()

        results = Path(tempfile.mkdtemp(prefix="noust-test-w4p-results-"))
        env = {
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "NOUST_REAL_MODEL_URL": f"http://127.0.0.1:{port}/v1",
            "NOUST_REAL_MODEL": args.model,
            "NOUST_REAL_RESULTS": str(results),
            "NOUST_REAL_WAIT": str(args.wait),
            "NOUST_REAL_SCENARIOS": args.scenarios,
            "NOUST_REAL_PATIENT": "1" if args.patient else "",
        }
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
            "-q",
            "-s",
            str(REPO_ROOT / "tests" / "integration" / "assistant_real.py"),
        ]
        print("+", " ".join(command), flush=True)
        remaining = max(60, args.budget - round(time.time() - started) - 60)
        try:
            status = subprocess.run(
                command, cwd=REPO_ROOT, env=env, check=False, timeout=remaining
            ).returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            # What the scenarios that finished found is kept: a model too slow for the budget is a result.
            status, timed_out = -9, True
        rows = [json.loads(p.read_text()) for p in sorted(results.glob("*.json"))]
        report = write_report(
            args, digest, pull_seconds, rows, round(time.time() - started), status, timed_out
        )
        shutil.rmtree(results, ignore_errors=True)
        print(json.dumps(report["totals"], indent=1))
        return 0
    except Skip as skipped:
        print(f"SKIP: {skipped}")
        return 77
    finally:
        containers.cleanup()


def write_report(
    args: argparse.Namespace,
    digest: str,
    pull_seconds: int,
    rows: list[dict[str, Any]],
    seconds: int,
    status: int,
    timed_out: bool,
) -> dict[str, Any]:
    """
    Write ``assistant-model.json``.

    Args:
        args: The options of the run.
        digest: The image's digest, as Docker reports it.
        pull_seconds: How long pulling the model took.
        rows: What each scenario found.
        seconds: How long the whole run took.
        status: pytest's exit status.
        timed_out: The budget ran out before every scenario had.

    Returns:
        What was written.
    """
    probe = next((r for r in rows if r["scenario"] == "probe"), None)
    cases = [r for r in rows if r["scenario"] != "probe"]
    label = args.label or ("patient" if args.patient else "as_shipped")
    run: dict[str, Any] = {
        "cpus": args.cpus,
        "patience": (
            "the client waits as long as a model on a CPU needs (9 minutes a read)"
            if args.patient
            else "the product's own: 30 seconds for the probe's reading, 60 for a turn's"
        ),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "seconds_of_the_run": seconds,
        "pytest_status": status,
        "ran_out_of_budget": timed_out,
        "probe": probe,
        "scenarios": cases,
        "totals": {
            "scenarios": len(cases),
            "ended": sum(1 for c in cases if c["state"] == "done"),
            "found_the_cause": sum(1 for c in cases if c.get("cause_found")),
            "called_a_tool": sum(1 for c in cases if c["steps"]),
            "with_a_failed_call": sum(1 for c in cases if c["steps_failed"] or c["doors"]),
            "secrets_leaked_or_executed_without_approval": sum(len(c["oracles"]) for c in cases),
            "mean_seconds": round(sum(c["seconds"] for c in cases) / len(cases)) if cases else None,
        },
    }
    report: dict[str, Any] = {}
    if OUT.exists():
        report = json.loads(OUT.read_text(encoding="utf-8"))
        if "runs" not in report:
            # A file of the first shape: one run, as the product ships.
            report = {
                "runs": {"as_shipped": {k: report[k] for k in report if k not in HEADER}},
                **{k: report[k] for k in report if k in HEADER},
            }
    report.update(
        {
            "measured": "tests/integration/assistant_run.py: a small open model on a CPU, in a container",
            "container": {"image": digest, "memory": MEMORY, "cpus": args.cpus, "name": CONTAINER},
            "model": args.model,
            "seconds_to_pull_the_model": pull_seconds,
        }
    )
    report.setdefault("runs", {})[label] = run
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return run


if __name__ == "__main__":
    sys.exit(main())
