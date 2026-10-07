#!/usr/bin/env python3
"""
Start a Compose recipe on this machine's Docker, ask its health check, and clean up.

A recipe's document is read by a closed schema and its pins by ``recipe_pins.py``, but
neither starts anything: whether the images agree with the settings, whether a database
is ready when its application asks, whether the administrator a recipe says it creates
exists, is only known when the stack runs. This tool runs it, the way a deployment would:

- the variables are resolved by Noust's own code (generated secrets included) and written
  to the stack's ``.env`` with ``PORT``, beside the document and the files the recipe ships;
- the stack starts as ``noust-smoke-<recipe>``, a throwaway Compose project, with its web
  service on ``127.0.0.1:<port>``;
- the health gate's request is made exactly as the gate makes it: ``http://127.0.0.1:<port>``
  plus the recipe's path, the default ``Host`` header, no forwarded headers, and no redirect
  followed when the recipe states what it expects (an application that checks the host
  header answers the gate with a 400 or a 404, which is the finding this tool exists for);
- the same path is asked the way nginx asks it (the domain as ``Host``, ``X-Forwarded-*``),
  for information;
- after a settling time, every container is inspected for a restart loop and an out-of-memory
  kill, and the log is searched for lines that look like errors;
- the project and its volumes are removed, unless ``--keep`` says otherwise.

It makes no change to a server and is meant for the machine of whoever writes a recipe:
it needs Docker and the Compose plugin, pulls the recipe's images and uses the loopback
port you give it. It never touches a container it did not create.

Usage:
    scripts/recipe_smoke.py NAME [--port N] [--wait SECONDS] [--var K=V ...] [--extra K=V ...]
                            [--domain D] [--http] [--evidence DIR] [--keep]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.recipes import get_recipe, read_asset  # noqa: E402
from noust.recipes.model import Recipe  # noqa: E402
from noust.recipes.variables import DeploymentContext, resolve_variables  # noqa: E402
from noust.validators.health import parse_health_expect  # noqa: E402

#: Lines of a log that look like a failure; a count, not a verdict: applications log
#: expected errors while they start.
ERROR_LINE = re.compile(r"\b(error|fatal|exception|traceback|panic)\b", re.IGNORECASE)

#: Seconds to let the stack settle after the gate passes, before looking for restarts.
SETTLE_SECONDS = 25


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand a redirect back as the answer, as the health gate does."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        """
        Refuse to follow a redirect.

        Args:
            args: Ignored.
            kwargs: Ignored.

        Returns:
            None, which makes ``urllib`` raise the redirect as an ``HTTPError``.
        """
        return None


@dataclass
class SmokeReport:
    """
    What a run found.

    Attributes:
        recipe: The recipe's name.
        version: Its version.
        checked_at: When, in UTC.
        gate_path: The path the gate asked.
        gate_expected: What the recipe expects, as written.
        gate_status: The answer the gate's request got, or why there was none.
        gate_seconds: How long the stack took to answer as the gate wants.
        proxied_status: The same path asked the way nginx asks it.
        containers: One entry per container: name, state, restarts, whether it was killed
            for memory.
        error_lines: How many log lines look like errors, and the first few.
        problems: What stops the recipe from being accepted; empty when it passes.
    """

    recipe: str
    version: str
    checked_at: str
    gate_path: str = ""
    gate_expected: str | None = None
    gate_status: str = ""
    gate_seconds: int = 0
    proxied_status: str = ""
    containers: list[dict[str, Any]] = field(default_factory=list)
    error_lines: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def gate_accepts(expect: str | None, status: int) -> bool:
    """
    Tell whether an answer is what the health gate would call healthy.

    Args:
        expect: The recipe's ``health.expect``, or None when it has none.
        status: The HTTP status.

    Returns:
        True for a status inside the expectation; with none stated, only 200 counts.
    """
    if expect is None:
        return status == 200
    return any(low <= status <= high for low, high in parse_health_expect(expect))


def write_project(
    recipe: Recipe,
    directory: Path,
    *,
    port: int,
    domain: str,
    ssl: bool,
    given: Mapping[str, str],
) -> dict[str, str]:
    """
    Write a Compose recipe's project where a deployment would: document, files and ``.env``.

    Args:
        recipe: The recipe.
        directory: An empty directory.
        port: The loopback port the web service publishes on.
        domain: The domain the application believes it has.
        ssl: Whether that domain is served over TLS, for ``from: url``.
        given: What the operator gives: ``--var``, and every required variable's value.

    Returns:
        The variables written to ``.env`` (``PORT`` included).
    """
    overrides = dict(given)
    for variable in recipe.variables:
        if variable.is_required and variable.name not in overrides:
            overrides[variable.name] = "given@example.com"
    context = DeploymentContext(
        domain=domain,
        ssl=ssl,
        port=port,
        app_name=domain.replace(".", "-"),
        app_path="/var/www/apps/smoke",
    )
    resolved = resolve_variables(
        recipe.variables, context=context, provisioned={}, overrides=overrides
    )
    values = {**resolved.values, "PORT": str(port)}
    (directory / "docker-compose.yml").write_text(recipe.compose or "", encoding="utf-8")
    for path, asset in recipe.files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(read_asset(recipe.name, asset), encoding="utf-8")
    (directory / ".env").write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
    )
    return values


def ask(
    url: str, *, follow: bool, headers: Mapping[str, str] | None = None
) -> tuple[int | str, str]:
    """
    Make one GET and report the answer, whatever it is.

    Args:
        url: What to ask.
        follow: Whether to follow redirects.
        headers: Extra request headers.

    Returns:
        The status (or ``conn:<ErrorName>`` when nothing answered) and the Location header.
    """
    request = urllib.request.Request(url, headers=dict(headers or {}))
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=20) as response:
            return int(response.status), str(response.headers.get("Location") or "")
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc.headers.get("Location") or "")
    except (urllib.error.URLError, OSError) as exc:
        return f"conn:{type(exc).__name__}", ""


class Project:
    """A throwaway Compose project of a recipe."""

    def __init__(self, recipe: Recipe, directory: Path) -> None:
        """
        Args:
            recipe: The recipe.
            directory: Where its document and ``.env`` are.
        """
        self.name = f"noust-smoke-{recipe.name}"
        self.directory = directory
        self._base = [
            "docker",
            "compose",
            "-p",
            self.name,
            "-f",
            str(directory / "docker-compose.yml"),
            "--env-file",
            str(directory / ".env"),
        ]

    def run(self, *arguments: str, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
        """
        Run a Compose command in the project.

        Args:
            arguments: What follows ``docker compose -p ... -f ...``.
            timeout: Seconds before it is given up.

        Returns:
            The finished process.
        """
        return subprocess.run(
            [*self._base, *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=self.directory,
            check=False,
        )

    def inspect(self) -> list[dict[str, Any]]:
        """
        Describe every container of the project.

        Returns:
            Name, state, restart count and whether the kernel killed it for memory.
        """
        found: list[dict[str, Any]] = []
        for identifier in self.run("ps", "-q", "-a").stdout.split():
            result = subprocess.run(
                ["docker", "inspect", identifier],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            try:
                data = json.loads(result.stdout)[0]
            except (ValueError, IndexError):
                continue
            state = data.get("State", {})
            found.append(
                {
                    "name": str(data.get("Name", "")).lstrip("/"),
                    "state": state.get("Status"),
                    "restarts": data.get("RestartCount", 0),
                    "oom_killed": bool(state.get("OOMKilled")),
                    "health": (state.get("Health") or {}).get("Status"),
                }
            )
        return found


def smoke(
    recipe: Recipe,
    *,
    port: int,
    wait: int,
    given: Mapping[str, str],
    extra: Sequence[str],
    domain: str,
    ssl: bool,
    keep: bool,
) -> SmokeReport:
    """
    Run one recipe's stack and report what the health gate and the containers say.

    Args:
        recipe: A Compose recipe.
        port: The loopback port its web service publishes on.
        wait: Seconds the gate may take to answer as the recipe expects.
        given: Variables the operator gives.
        extra: ``K=V`` lines appended to ``.env`` as an operator editing it would.
        domain: The domain the application believes it has.
        ssl: Whether it is served over TLS.
        keep: Leave the project running when done.

    Returns:
        The report; ``problems`` is empty when the recipe passes.
    """
    report = SmokeReport(
        recipe=recipe.name,
        version=recipe.version,
        checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    health = recipe.health
    report.gate_path = health.path if health else "/"
    report.gate_expected = health.expect if health else None
    directory = Path(tempfile.mkdtemp(prefix=f"noust-smoke-{recipe.name}-"))
    project = Project(recipe, directory)
    try:
        write_project(recipe, directory, port=port, domain=domain, ssl=ssl, given=given)
        if extra:
            with (directory / ".env").open("a", encoding="utf-8") as env:
                env.write("".join(f"{line}\n" for line in extra))
        started = project.run("up", "-d", "--wait-timeout", "180")
        if started.returncode:
            report.problems.append(f"docker compose up failed: {started.stderr.strip()[-300:]}")
            return report
        url = f"http://127.0.0.1:{port}{report.gate_path}"
        begin = time.monotonic()
        status: int | str = ""
        while time.monotonic() - begin < wait:
            status, _ = ask(url, follow=report.gate_expected is None)
            if isinstance(status, int) and gate_accepts(report.gate_expected, status):
                break
            time.sleep(5)
        report.gate_status = str(status)
        report.gate_seconds = int(time.monotonic() - begin)
        if not (isinstance(status, int) and gate_accepts(report.gate_expected, status)):
            report.problems.append(
                f"the health gate's request to {report.gate_path} got {status}, "
                f"the recipe expects {report.gate_expected or '200'}"
            )
            return report
        proxied = {
            "Host": domain,
            "X-Forwarded-Proto": "https" if ssl else "http",
            "X-Forwarded-For": "203.0.113.9",
            "X-Real-IP": "203.0.113.9",
            "X-Forwarded-Host": domain,
        }
        report.proxied_status = str(ask(url, follow=False, headers=proxied)[0])
        if project.run("config", "-q").returncode:
            report.problems.append("docker compose config rejects the document")
        time.sleep(SETTLE_SECONDS)
        report.containers = project.inspect()
        for container in report.containers:
            if container["state"] != "running" and container["state"] != "exited":
                report.problems.append(f"{container['name']} is {container['state']}")
            if container["restarts"]:
                report.problems.append(
                    f"{container['name']} restarted {container['restarts']} times"
                )
            if container["oom_killed"]:
                report.problems.append(f"{container['name']} was killed for memory")
        lines = project.run("logs", "--no-color", "--tail", "400").stdout.splitlines()
        errors = [line[:240] for line in lines if ERROR_LINE.search(line)]
        report.error_lines = {"count": len(errors), "first": errors[:5]}
        return report
    finally:
        if keep:
            print(f"kept: {directory} (project {project.name})")
        else:
            project.run("down", "-v", "--remove-orphans", "--timeout", "10")
            shutil.rmtree(directory, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the tool.

    Args:
        argv: The arguments; ``sys.argv`` when None.

    Returns:
        0 when every recipe passes, 1 when one does not.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("names", nargs="+", help="the Compose recipes to start, one after another")
    parser.add_argument("--port", type=int, default=18080, help="loopback port for the web service")
    parser.add_argument("--wait", type=int, default=300, help="seconds the gate may take")
    parser.add_argument("--var", action="append", default=[], help="NAME=VALUE the operator gives")
    parser.add_argument("--extra", action="append", default=[], help="NAME=VALUE appended to .env")
    parser.add_argument("--domain", default="smoke.example.com")
    parser.add_argument("--http", action="store_true", help="serve the public address over http")
    parser.add_argument("--keep", action="store_true", help="leave the project running")
    parser.add_argument("--evidence", type=Path, help="write each report as JSON in this directory")
    args = parser.parse_args(argv)

    if shutil.which("docker") is None:
        print("docker is not installed", file=sys.stderr)
        return 2
    given = dict(item.split("=", 1) for item in args.var)
    failed = 0
    for name in args.names:
        recipe = get_recipe(name)
        if recipe.kind != "compose" or not recipe.available:
            print(f"{name}: not a Compose recipe that can be installed", file=sys.stderr)
            failed += 1
            continue
        report = smoke(
            recipe,
            port=args.port,
            wait=args.wait,
            given=given,
            extra=args.extra,
            domain=args.domain,
            ssl=not args.http,
            keep=args.keep,
        )
        verdict = "ok" if not report.problems else "FAILS"
        print(
            f"{name}: {verdict}; gate {report.gate_path} -> {report.gate_status} after "
            f"{report.gate_seconds}s; through nginx-like headers -> {report.proxied_status}"
        )
        for problem in report.problems:
            print(f"  {problem}")
        failed += bool(report.problems)
        if args.evidence is not None:
            args.evidence.mkdir(parents=True, exist_ok=True)
            (args.evidence / f"{name}.json").write_text(
                json.dumps(report.__dict__, indent=2) + "\n", encoding="utf-8"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
