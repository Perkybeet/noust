#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the scenarios that run inside an engine share: how they report, how they run a program of the engine's
machine, and how they set up and take down what they build.

See ``docker_run.py`` for what the harness is. The scenarios are in ``docker_inside_door.py`` (the door to
Docker, the zone) and ``docker_inside_changes.py`` (versions, Compose), and ``docker_inside.py`` runs one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, os.environ.get("W3RD_SOURCE", "/noust/src"))

#: The local registry the driver starts in the engine, and the images it holds.
REGISTRY = os.environ.get("W3RD_REGISTRY", "localhost:5000")
BUSYBOX = f"{REGISTRY}/w3rd/busybox:1.36"
SMALL = f"{REGISTRY}/w3rd/small"

#: What every object a scenario makes carries, so a leftover is found by it.
LABEL = "noust.test=1"
SCENARIO_LABEL = "w3rd.scenario"

#: Where the scenarios keep their files: a folder of the engine's own file system, which is "the machine".
WORK = Path(os.environ.get("W3RD_WORK", "/srv/w3rd"))

ENGINE = os.environ.get("W3RD_ENGINE", "")
VARIANT = os.environ.get("W3RD_VARIANT", "bundled")


class Skip(Exception):
    """A scenario that cannot run here, and why."""


class Run:
    """What one scenario found, printed as it goes."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.failed = 0
        self.passed = 0

    @staticmethod
    def _emit(row: dict[str, Any]) -> None:
        print("@@" + json.dumps(row, default=str), flush=True)

    def check(self, name: str, condition: bool, detail: Any = "") -> bool:
        """
        Record one thing that must be true.

        Args:
            name: What was checked, as a sentence.
            condition: Whether it held.
            detail: What was found, kept when it did not.

        Returns:
            The condition.
        """
        if condition:
            self.passed += 1
        else:
            self.failed += 1
        self._emit(
            {
                "check": name,
                "ok": bool(condition),
                "detail": "" if condition else str(detail)[:1500],
            }
        )
        return bool(condition)

    def note(self, key: str, value: Any) -> None:
        """
        Record a fact the real engine gave, for the report.

        Args:
            key: What it is.
            value: What it said.
        """
        self._emit({"note": key, "value": value})

    def skip(self, name: str, why: str) -> None:
        """
        Record a check that cannot be made on this engine, and why.

        Args:
            name: What was not checked.
            why: The reason.
        """
        self._emit({"skip": name, "why": why})


# ------------------------------------------------------------------------------- the engine


def sh(
    *argv: str,
    input_text: str | None = None,
    timeout: int = 180,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run a program of the engine's machine, as the harness (not as Noust).

    Args:
        *argv: The program and its arguments.
        input_text: Standard input.
        timeout: Seconds.
        cwd: Where.
        env: Variables added to this process's.

    Returns:
        The completed process; a program that is not found or that is too slow is a result with a code.
    """
    try:
        return subprocess.run(
            list(argv),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            list(argv), 124, str(exc.stdout or ""), f"timed out after {timeout}s"
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(list(argv), 127, "", str(exc))


def docker(*argv: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """
    Args:
        *argv: ``docker`` arguments.
        **kwargs: What :func:`sh` takes.

    Returns:
        The result of the engine's own ``docker``.
    """
    return sh("docker", *argv, **kwargs)


def must(result: subprocess.CompletedProcess[str], what: str) -> str:
    """
    Args:
        result: A finished command.
        what: What it was for.

    Returns:
        Its standard output.

    Raises:
        RuntimeError: It failed, with what it said.
    """
    if result.returncode != 0:
        raise RuntimeError(
            f"{what} failed ({result.returncode}): {(result.stderr or result.stdout).strip()[-600:]}"
        )
    return result.stdout


def wait_for(condition: Callable[[], bool], seconds: float, every: float = 0.5) -> bool:
    """
    Args:
        condition: What to wait for.
        seconds: The longest.
        every: Seconds between looks.

    Returns:
        Whether it became true.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(every)
    return bool(condition())


def compose_version_short() -> str:
    """
    Returns:
        What ``docker compose version --short`` says, without a leading ``v``; empty when it says nothing.
    """
    done = docker("compose", "version", "--short", timeout=30)
    return done.stdout.strip().lstrip("v") if done.returncode == 0 else ""


def numbers(text: str) -> tuple[int, ...]:
    """
    Args:
        text: ``2.40.3`` or ``v2.0.0-rc.1``.

    Returns:
        The numbers before any suffix.
    """
    found = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(part) for part in found.group(1).split(".")) if found else ()


def reset(scenario_name: str) -> None:
    """
    Remove what an earlier run of a scenario left, by its label, and nothing else.

    Args:
        scenario_name: The scenario.
    """
    flt = f"label={SCENARIO_LABEL}={scenario_name}"
    ids = docker("ps", "-aq", "--filter", flt).stdout.split()
    if ids:
        docker("rm", "-fv", *ids)
    names = docker("volume", "ls", "-q", "--filter", flt).stdout.split()
    if names:
        docker("volume", "rm", "-f", *names)
    nets = docker("network", "ls", "-q", "--filter", flt).stdout.split()
    if nets:
        docker("network", "rm", *nets)
    shutil.rmtree(WORK / scenario_name, ignore_errors=True)


def project_down(project: str, file: Path) -> None:
    """
    Take a Compose project down with its volumes, whatever state it is in.

    Args:
        project: Its name.
        file: Its compose file.
    """
    docker(
        "compose",
        "-p",
        project,
        "-f",
        str(file),
        "down",
        "-v",
        "--remove-orphans",
        "-t",
        "1",
        timeout=120,
    )


def write_stack(folder: Path, text: str, name: str = "compose.yaml") -> Path:
    """
    Args:
        folder: The stack's directory.
        text: The compose document.
        name: The file's name.

    Returns:
        The file's path.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def up(project: str, file: Path, *extra: str, timeout: int = 240) -> str:
    """
    Start a Compose project the way a person does, with Compose's own words kept.

    Args:
        project: Its name.
        file: Its compose file.
        *extra: More arguments for ``up``.
        timeout: Seconds.

    Returns:
        Compose's standard error and output together.

    Raises:
        RuntimeError: It did not come up.
    """
    done = docker(
        "compose",
        "-p",
        project,
        "-f",
        str(file),
        "up",
        "-d",
        *extra,
        timeout=timeout,
        cwd=file.parent,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"compose up of {project} failed: {(done.stderr or done.stdout).strip()[-800:]}"
        )
    return done.stderr + done.stdout


def web_service(extra: str = "") -> str:
    """
    Args:
        extra: More lines for the service, already indented by four spaces.

    Returns:
        A service that answers on 8080 with BusyBox's HTTP server and passes its health check.
    """
    return (
        f"    image: {BUSYBOX}\n"
        "    command: sh -c 'mkdir -p /www && echo ok > /www/index.html && exec httpd -f -p 8080 -h /www'\n"
        "    healthcheck:\n"
        '      test: ["CMD", "wget", "-q", "-O", "-", "http://127.0.0.1:8080/"]\n'
        "      interval: 1s\n"
        "      timeout: 2s\n"
        "      retries: 3\n"
        "      start_period: 1s\n" + extra
    )


# ------------------------------------------------------------------------------ scenarios

SCENARIOS: dict[str, Callable[[Run], None]] = {}


def scenario(name: str) -> Callable[[Callable[[Run], None]], Callable[[Run], None]]:
    """
    Register a scenario under a name.

    Args:
        name: Its name, which ``docker_run.py`` asks for.

    Returns:
        The decorator.
    """

    def register(function: Callable[[Run], None]) -> Callable[[Run], None]:
        SCENARIOS[name] = function
        return function

    return register


def runner() -> Any:
    """
    Returns:
        The runner Noust runs every command through, the real one.
    """
    from noust.core.runner import get_runner

    return get_runner()


def make_store(tag: str) -> Any:
    """
    Make a store of Noust's own, in a folder of this scenario's.

    Args:
        tag: The scenario's name.

    Returns:
        The store.
    """
    from noust.core.store import NoustStore

    folder = WORK / tag / "state"
    folder.mkdir(parents=True, exist_ok=True)
    return NoustStore(folder / "noust.db")
