# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Diagnose why an application is down.

An operator staring at a 502 has to correlate several independent sources by
hand: is the unit even running, is it listening on the port Noust thinks it is,
does nginx reach it, what does the process's own log say, did the kernel kill
it for memory, is the certificate still valid, did the last deploy even
succeed, is the disk full. Each of those lives behind a different command.
This module runs all of them and orders the answer by how likely each one is
to be the actual cause, so the answer to "why is my app down" is the first
line, not a page of systemctl output to read yourself.

Two things shape the design:

- **Every probe is isolated.** A host with certbot uninstalled or a journal
  that has rotated away must not stop the port check from running. Each probe
  is called by :func:`diagnose`, and only :class:`~noust.core.exceptions.NoustError`,
  :class:`OSError` and :class:`ValueError` - the exceptions a probe can
  actually raise - are caught there and turned into a ``skip`` check carrying
  the error as evidence. A probe that returns cleanly always reports on its
  own check; nothing here swallows one check's failure into another's.
- **Evidence is verbatim.** CONTRIBUTING.md's rule for the panel applies here too: a
  system error is never paraphrased. ``journalctl``, ``ss`` and nginx's own
  error log reach the operator exactly as those programs printed them; only
  the summary line and the probable cause are Noust's own words.

The verdict and probable cause come from an explicit, ordered list of rules in
:func:`_decide`, each backed by its own test: a rule earlier in the list wins
over one later, and a diagnosis with no clear cause reports the aggregate
severity of the checks instead of guessing.
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError

from noust.core import cert_life, cgroup_events, descriptors
from noust.core.appdir.layout import app_root
from noust.core.appdir.releases import Release, ReleaseManager
from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.notifications.formatting import format_bytes
from noust.core.runner import CommandRunner, get_runner
from noust.core.server_text import ServerText, say
from noust.core.store import (
    BLUE_GREEN_COLORS,
    App,
    DeploymentStatus,
    NoustStore,
    get_store,
    is_compose_backed,
    runs_as_instances,
)
from noust.core.utils import domain_to_app_name
from noust.deployers.bluegreen import (
    color_port,
    could_update_without_downtime,
    instance_unit,
    leftover_advice,
    leftover_unit,
    other_color,
    serving_port,
)
from noust.deployers.helpers.health_gate import HealthCheck
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.service_manager import ServiceManager
from noust.monitor.plan import CGROUP_MOUNT, is_cgroup_v2

log = logging.getLogger(__name__)

#: Where nginx writes its error log. A module constant rather than a Config
#: field: it is not something an operator configures per deployment, and
#: tests point it at a disposable file by assigning this name.
NGINX_ERROR_LOG = Path("/var/log/nginx/error.log")

#: A local HTTP request that has not answered in this long is not going to.
_HTTP_PROBE_TIMEOUT = 3.0

#: nginx's plain HTTP listener. Diagnosis probes this rather than 443 so it
#: never has to negotiate TLS to ask "does nginx route this Host at all".
_NGINX_PROBE_PORT = 80

#: How far back OOM activity is still considered relevant to a current outage.
_OOM_WINDOW = "-7d"

#: How many bytes of the nginx error log are read from the end. Bounded so a
#: log nobody has rotated in a year cannot make this command slow.
_NGINX_LOG_TAIL_BYTES = 65536

#: How many matching nginx error log lines are kept as evidence.
_NGINX_LOG_MAX_LINES = 20

#: Disk usage past this is the probable cause by itself.
_DISK_FAIL_PERCENT = 95.0

#: Disk usage past this is worth a warning on the check, short of being named
#: the cause.
_DISK_WARN_PERCENT = 90.0

CheckStatus = Literal["ok", "warn", "fail", "skip"]
Verdict = Literal["healthy", "degraded", "down"]

#: What an HTTP probe returns: the status code it got, or the error it hit.
#: Exactly one of the two is set.
HttpGet = Callable[[str, Mapping[str, str]], tuple[int | None, str | None]]

#: What reads free disk space. Matches shutil.disk_usage's signature so the
#: real function is the default with no wrapping needed.
DiskUsage = Callable[[str], "shutil._ntuple_diskusage"]


@dataclass(frozen=True)
class Check:
    """
    The result of one diagnostic probe.

    Attributes:
        name: Stable identifier for the probe, such as ``"unit"`` or ``"port"``.
        status: ``"ok"`` when nothing is wrong, ``"warn"`` for something worth
            an operator's attention that is not the cause, ``"fail"`` for
            something that likely is, ``"skip"`` when the probe itself could
            not run.
        summary: One line, in Noust's own words.
        evidence: The raw output the probe collected - journal lines, ss
            output, an exception message - verbatim and unmodified.
        coded: ``summary`` as a sentence with a code and its values, for the console to
            translate; the English text is the catalog entry with the values put in.
    """

    name: str
    status: CheckStatus
    summary: str
    evidence: str = ""
    coded: ServerText | None = None


def _check(name: str, status: CheckStatus, coded: ServerText, evidence: str = "") -> Check:
    """
    Make a check out of the sentence that says what it found.

    Args:
        name: The probe's name.
        status: How it went.
        coded: The sentence; its text is the summary.
        evidence: What the probe collected, verbatim.

    Returns:
        The check, whose summary and code are one thing.
    """
    return Check(name, status, coded.text, evidence, coded)


@dataclass(frozen=True)
class Diagnosis:
    """
    The full answer to "why is this app down".

    Attributes:
        domain: The domain that was diagnosed.
        verdict: ``"healthy"`` when every check is clean, ``"degraded"`` when
            something needs attention but the app is likely still serving,
            ``"down"`` when it most likely is not.
        probable_cause: One sentence naming the most likely explanation, or
            None when the checks disagree with each other or are all clean.
        checks: Every probe that ran, in the order :data:`_PROBES` lists them.
        probable_cause_coded: ``probable_cause`` as a sentence with a code, for the console to
            translate; None when there is no cause.
    """

    domain: str
    verdict: Verdict
    probable_cause: str | None
    checks: tuple[Check, ...]
    probable_cause_coded: ServerText | None = None


@dataclass
class _Context:
    """Shared, read-only state every probe needs. Built once per diagnosis."""

    domain: str
    app_name: str
    app: App | None
    runner: CommandRunner
    store: NoustStore
    now: datetime
    http_get: HttpGet
    disk_usage: DiskUsage
    config: Config
    service_manager: ServiceManager
    cert_manager: CertManager
    #: Where the cgroups are mounted: the kernel's own count of what it killed is read from there.
    cgroup_mount: Path = CGROUP_MOUNT


ProbeResult = tuple[Check, dict[str, Any]]

#: What an instance without a release link shows as its release.
_NO_RELEASE = Release(id="(none)", path=Path(), commit=None, created_at="", active=False)
Probe = Callable[[_Context], ProbeResult]


# -- HTTP probing -------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report a redirect as the answer instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """
        Decline every redirect, so it surfaces through the response status.

        Returns:
            None, which makes urllib return the redirect response as-is.
        """
        return None


def _default_http_get(url: str, headers: Mapping[str, str]) -> tuple[int | None, str | None]:
    """
    Make one real HTTP GET request with a short timeout.

    Args:
        url: Address to request. Always http://127.0.0.1:<port><path>, built
            by the caller from a path the store validated.
        headers: Extra request headers, such as an explicit Host.

    Returns:
        The status code and None, or None and the error, depending on whether
        the request reached a server at all.
    """
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with opener.open(request, timeout=_HTTP_PROBE_TIMEOUT) as response:
            return response.status, None
    except HTTPError as exc:
        return exc.code, None
    except (URLError, OSError, ValueError) as exc:
        return None, str(exc)


# -- Small parsers -------------------------------------------------------------


def _parse_systemctl_show(output: str) -> dict[str, str]:
    """
    Parse ``systemctl show``'s ``Key=Value`` lines.

    Args:
        output: Standard output of a ``systemctl show`` call.

    Returns:
        Property name to value, for every line that had one.
    """
    fields: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def _as_int(value: str | None) -> int:
    """
    Read a systemctl property as an integer, defensively.

    Args:
        value: The raw property value, or None when it was absent.

    Returns:
        The integer, or 0 when the value is missing or not a number.
    """
    try:
        return int(str(value).strip() or 0)
    except ValueError:
        return 0


@dataclass(frozen=True)
class _ListenEntry:
    """One listening socket, as ``ss`` reported it."""

    port: int
    processes: tuple[tuple[str, str], ...]  # (process name, pid), pid as text


#: Matches one ``("name",pid=123,...)`` group inside ss's users: column. A
#: single port can list several processes - nginx's workers all inherit the
#: same listening fd - so every match is kept.
_SS_PROCESS_RE = re.compile(r'\("([^"]+)",pid=(\d+)')


def _parse_ss_output(output: str) -> list[_ListenEntry]:
    """
    Parse ``ss -ltnpH`` into the listening sockets it reports.

    Args:
        output: Standard output of the ss call.

    Returns:
        One entry per LISTEN line that named a port.
    """
    entries: list[_ListenEntry] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "LISTEN":
            continue
        port_match = re.search(r":(\d+)$", parts[3])
        if not port_match:
            continue
        processes = tuple(
            (name, pid)
            for token in parts[4:]
            if token.startswith("users:")
            for name, pid in _SS_PROCESS_RE.findall(token)
        )
        entries.append(_ListenEntry(port=int(port_match.group(1)), processes=processes))
    return entries


# -- Probes ---------------------------------------------------------------


#: How many lines of the PHP-FPM journal a PHP application's diagnosis reads.
_FPM_JOURNAL_LINES = 200

#: How many of them, about the application's pool, are kept as evidence.
_FPM_JOURNAL_KEPT = 30


def _check_php_fpm(ctx: _Context) -> ProbeResult:
    """
    Inspect a PHP application's pool: FPM, the pool file, its socket, the FastCGI probe.

    A PHP application has no unit of its own; this is its unit, port and
    journal checks in one. FPM's journal is shared by every pool, so only
    the lines naming this pool are kept (all of the last ones when none do).
    """
    from noust.deployers.php_fpm import inspect_pool

    if ctx.app is None:
        return _check("php_fpm", "skip", say("diagnose.php_fpm.no_record")), {}
    pool = inspect_pool(ctx.app, runner=ctx.runner)
    facts: dict[str, Any] = {
        "service": pool.service,
        "service_state": pool.service_state,
        "enabled": pool.enabled,
        "disabled": pool.disabled,
        "socket_exists": pool.socket_exists,
        "answered": pool.answered,
    }
    evidence = [
        f"{pool.service}: {pool.service_state}",
        f"pool file {pool.pool_file}: "
        + ("present" if pool.enabled else "disabled" if pool.disabled else "missing"),
        f"socket {pool.socket}: " + ("present" if pool.socket_exists else "missing"),
    ]
    if pool.enabled:
        config = ctx.runner.run([pool.binary, "-t"], timeout=30)
        output = "\n".join(p.strip() for p in (config.stderr, config.stdout) if p.strip())
        facts["config_ok"] = config.success
        evidence.append(f"php-fpm -t: {'ok' if config.success else 'refused'}")
        if output and not config.success:
            evidence.append(output)
    if pool.answered is not None:
        evidence.append(
            "FastCGI health check: " + ("answered" if pool.answered else pool.detail or "failed")
        )
    journal = ctx.runner.run(
        ["journalctl", "-u", pool.service, "-n", str(_FPM_JOURNAL_LINES), "--no-pager"],
        timeout=15,
    )
    if journal.success and journal.stdout.strip():
        lines = journal.stdout.strip().splitlines()
        mine = [line for line in lines if pool.pool_file.stem in line]
        evidence.append("\n".join((mine or lines)[-_FPM_JOURNAL_KEPT:]))

    cause: ServerText | None = None
    if not pool.enabled and pool.disabled:
        cause = say("diagnose.php_fpm.pool_disabled", command=f"noust start {ctx.domain}")
    elif not pool.enabled:
        cause = say(
            "diagnose.php_fpm.pool_missing",
            pool_file=str(pool.pool_file),
            command=f"noust update {ctx.domain}",
        )
    elif not pool.fpm_up:
        cause = say("diagnose.php_fpm.service_down", service=pool.service, state=pool.service_state)
    elif facts.get("config_ok") is False:
        cause = say("diagnose.php_fpm.config_refused", command="php-fpm -t")
    elif pool.answered is False:
        cause = say("diagnose.php_fpm.no_answer")
    facts["cause"] = cause
    status: CheckStatus = "fail" if cause else "ok"
    said = cause or say("diagnose.php_fpm.ok", service=pool.service, state=pool.service_state)
    return _check("php_fpm", status, said, "\n".join(evidence)), facts


#: Probes that ask a port, HTTP or the site: meaningless for a stack that
#: publishes no port, which is judged by its containers instead.
_WEB_PROBES = frozenset({"port", "http_direct", "http_nginx", "nginx_log", "certificate"})


def _check_compose_stack(ctx: _Context) -> ProbeResult:
    """
    Judge a Compose stack that publishes no port by its containers (owner item 57).

    Args:
        ctx: The diagnosis context.

    Returns:
        The check, and ``headless`` in the facts when the stack is one; the
        evidence is the containers' state and the last lines of their output,
        secrets masked.
    """
    from noust.deployers.docker_compose import (
        COMPOSE_LOG_LINES,
        headless_stack_state,
        stack_deployer,
    )

    if ctx.app is None:
        return _check("containers", "skip", say("diagnose.containers.no_app")), {}
    state = headless_stack_state(ctx.app, runner=ctx.runner)
    if state is None:
        return (
            _check("containers", "skip", say("diagnose.containers.publishes_port")),
            {},
        )
    deployer = stack_deployer(ctx.app, runner=ctx.runner)
    deployer._discover_compose_file()
    # Through the deployer's one reader: a viewer reads this, and a
    # container's output carries the passwords its entrypoint printed.
    _answered, logs = deployer.container_logs(COMPOSE_LOG_LINES)
    evidence = "\n\n".join(
        part
        for part in (
            state.output,
            f"docker compose logs --tail {COMPOSE_LOG_LINES}:\n" + (logs.strip() or "(no output)"),
        )
        if part
    )
    facts: dict[str, Any] = {
        "headless": True,
        "healthy": state.healthy,
        "summary": state.summary,
        "recorded_port": state.recorded_port,
    }
    worker = say("diagnose.containers.worker", summary=state.summary)
    if not state.healthy:
        return _check("containers", "fail", worker, evidence), facts
    if state.recorded_port:
        with_port = say(
            "diagnose.containers.worker_with_port",
            summary=state.summary,
            port=state.recorded_port,
            command=f"noust app headless {ctx.domain}",
        )
        return _check("containers", "warn", with_port, evidence), facts
    return _check("containers", "ok", worker, evidence), facts


def _no_unit(ctx: _Context) -> str | None:
    """
    Say why an application has no unit to check, when it has none.

    Args:
        ctx: The diagnosis context.

    Returns:
        ``php`` or ``static``, or None when it runs as a unit.
    """
    from noust.deployers.helpers.php_fpm import is_php_fpm

    if ctx.app is None or not ctx.app.is_static:
        return None
    return "php" if is_php_fpm(ctx.app) else "static"


def _check_unit(ctx: _Context) -> ProbeResult:
    """Ask systemd what state the app's unit is in."""
    reason = _no_unit(ctx)
    if reason == "php":
        return _check("unit", "skip", say("diagnose.unit.skipped_php")), {}
    if reason is not None:
        return _check("unit", "skip", say("diagnose.unit.skipped_static")), {}

    info = ctx.service_manager.inspect_unit(ctx.app_name)
    if not info.exists:
        return (
            _check(
                "unit",
                "fail",
                say("diagnose.unit.missing", unit=info.unit),
                f"Expected a unit at {info.path}",
            ),
            {"exists": False},
        )

    result = ctx.runner.run(
        [
            "systemctl",
            "show",
            "-p",
            "ActiveState,SubState,Result,ExecMainStatus,NRestarts",
            info.unit_file,
        ],
        timeout=15,
    )
    fields = _parse_systemctl_show(result.stdout)
    active_state = fields.get("ActiveState", "")
    sub_state = fields.get("SubState", "")
    result_code = fields.get("Result", "")
    exec_main_status = fields.get("ExecMainStatus", "")
    restarts = _as_int(fields.get("NRestarts"))

    managed = info.managed
    # A Compose unit runs `docker compose up -d` once and stays active:
    # "exited" is its running state; the containers are checked on their own.
    compose_up = (
        ctx.app is not None and is_compose_backed(ctx.app.app_type) and sub_state == "exited"
    )
    if active_state == "active" and (sub_state == "running" or compose_up):
        status: CheckStatus = "ok"
        said = say(
            "diagnose.unit.active" if managed else "diagnose.unit.active_unmanaged",
            unit_file=info.unit_file,
        )
    elif sub_state == "auto-restart" or active_state == "activating":
        status = "fail"
        said = say(
            "diagnose.unit.restarting" if managed else "diagnose.unit.restarting_unmanaged",
            unit_file=info.unit_file,
            restarts=restarts,
        )
    elif active_state == "failed" or sub_state == "failed":
        status = "fail"
        said = say(
            "diagnose.unit.failed" if managed else "diagnose.unit.failed_unmanaged",
            unit_file=info.unit_file,
            result=result_code or "unknown",
        )
    else:
        status = "warn"
        said = say(
            "diagnose.unit.state" if managed else "diagnose.unit.state_unmanaged",
            unit_file=info.unit_file,
            active_state=active_state or "unknown",
            sub_state=sub_state or "unknown",
        )

    facts = {
        "exists": True,
        "managed": info.managed,
        "active_state": active_state,
        "sub_state": sub_state,
        "result": result_code,
        "exec_main_status": exec_main_status,
        "restarts": restarts,
    }
    return _check("unit", status, said, result.stdout.strip()), facts


def _check_port(ctx: _Context) -> ProbeResult:
    """Check whether anything listens on the port Noust recorded for this app."""
    reason = _no_unit(ctx)
    if reason == "php":
        return _check("port", "skip", say("diagnose.port.skipped_php")), {}
    if reason is not None:
        return _check("port", "skip", say("diagnose.port.skipped_static")), {}

    # In zero-downtime mode, the port of the instance that serves.
    recorded_port = serving_port(ctx.app) if ctx.app else None
    if recorded_port is None:
        return _check("port", "skip", say("diagnose.port.not_recorded")), {}

    result = ctx.runner.run(["ss", "-ltnpH"], timeout=10)
    entries = _parse_ss_output(result.stdout)
    evidence = result.stdout.strip()

    on_recorded_port = [e for e in entries if e.port == recorded_port]
    if on_recorded_port:
        procs = ", ".join(f"{name} (pid {pid})" for name, pid in on_recorded_port[0].processes)
        listening = (
            say("diagnose.port.listening_process", port=recorded_port, processes=procs)
            if procs
            else say("diagnose.port.listening", port=recorded_port)
        )
        return _check("port", "ok", listening, evidence), {
            "recorded_port": recorded_port,
            "listening": True,
        }

    # Nothing answers on the recorded port. Before calling it dead, check
    # whether the app's own process is listening somewhere else - a config
    # drift between the unit's PORT and what Noust has on record.
    info = ctx.service_manager.inspect_unit(ctx.app_name)
    main_pid = None
    if info.exists:
        pid_result = ctx.runner.run(
            ["systemctl", "show", "-p", "MainPID", info.unit_file], timeout=10
        )
        main_pid = _parse_systemctl_show(pid_result.stdout).get("MainPID")

    actual_port = None
    if main_pid and main_pid != "0":
        for entry in entries:
            if any(pid == main_pid for _, pid in entry.processes):
                actual_port = entry.port
                break

    if actual_port is not None:
        return (
            _check(
                "port",
                "fail",
                say(
                    "diagnose.port.elsewhere",
                    actual_port=actual_port,
                    recorded_port=recorded_port,
                ),
                evidence,
            ),
            {
                "recorded_port": recorded_port,
                "listening": False,
                "actual_port": actual_port,
                "mismatch": True,
            },
        )

    return (
        _check("port", "fail", say("diagnose.port.silent", port=recorded_port), evidence),
        {
            "recorded_port": recorded_port,
            "listening": False,
            "actual_port": None,
            "mismatch": False,
        },
    )


def _check_http_direct(ctx: _Context) -> ProbeResult:
    """
    Probe the app directly on 127.0.0.1:<port>, bypassing nginx.

    Asks what the health gate asks - the application's path, judged by its
    expectation - so a diagnosis never calls healthy what a deploy would
    roll back, or the reverse.
    """
    port = serving_port(ctx.app) if ctx.app is not None else None
    if ctx.app is None or ctx.app.is_static or not port:
        return _check("http_direct", "skip", say("diagnose.http_direct.no_port")), {}

    check = HealthCheck.for_app(ctx.app)
    url = check.url(port)
    code, error = ctx.http_get(url, {"Host": check.host} if check.host else {})
    if error is not None:
        return (
            _check(
                "http_direct",
                "fail",
                say("diagnose.http_direct.unreachable", port=port),
                error,
            ),
            {"ok": False, "status": None, "error": error},
        )

    ok = code is not None and check.accepts(code)
    status: CheckStatus = "ok" if ok else "fail"
    return (
        _check(
            "http_direct",
            status,
            say("diagnose.http_direct.status", status=str(code), port=port),
            f"GET {url} -> {code} (healthy: {check.describe_expect()})",
        ),
        {"ok": ok, "status": code, "error": None},
    )


def _check_http_nginx(ctx: _Context) -> ProbeResult:
    """
    Probe the app through nginx, with the domain as the Host header.

    The same path and expectation as the direct probe, with one allowance:
    port 80 redirecting (to HTTPS, usually) is the site working, not the
    application failing its expectation.
    """
    check = HealthCheck.for_app(ctx.app)
    url = check.public_url(_NGINX_PROBE_PORT)
    code, error = ctx.http_get(url, {"Host": ctx.domain})
    if error is not None:
        return (
            _check(
                "http_nginx",
                "fail",
                say("diagnose.http_nginx.unreachable", domain=ctx.domain),
                error,
            ),
            {"ok": False, "status": None, "error": error},
        )

    ok = code is not None and (check.accepts(code) or 300 <= code < 400)
    status: CheckStatus = "ok" if ok else "fail"
    evidence = f"GET {url} Host: {ctx.domain} -> {code}"
    return (
        _check(
            "http_nginx",
            status,
            say("diagnose.http_nginx.status", status=str(code), domain=ctx.domain),
            evidence,
        ),
        {"ok": ok, "status": code, "error": None},
    )


def _check_journal(ctx: _Context) -> ProbeResult:
    """Read the unit's last 50 journal lines, verbatim."""
    reason = _no_unit(ctx)
    if reason == "php":
        return _check("journal", "skip", say("diagnose.journal.skipped_php")), {}
    if reason is not None:
        return _check("journal", "skip", say("diagnose.journal.skipped_static")), {}

    info = ctx.service_manager.inspect_unit(ctx.app_name)
    if not info.exists:
        return _check("journal", "skip", say("diagnose.journal.no_unit", unit=info.unit)), {}

    result = ctx.runner.run(
        ["journalctl", "-u", info.unit_file, "-n", "50", "--no-pager", "-o", "short-iso"],
        timeout=15,
    )
    if not result.success:
        return (
            _check(
                "journal",
                "skip",
                say("diagnose.journal.unreadable", unit_file=info.unit_file),
                result.stderr.strip(),
            ),
            {},
        )

    text = result.stdout.strip()
    if not text:
        return _check("journal", "ok", say("diagnose.journal.empty", unit_file=info.unit_file)), {}

    lines = len(text.splitlines())
    tail = (
        say("diagnose.journal.lines_one", unit_file=info.unit_file)
        if lines == 1
        else say("diagnose.journal.lines_several", count=lines, unit_file=info.unit_file)
    )
    return _check("journal", "ok", tail, text), {}


def _check_blue_green(ctx: _Context) -> ProbeResult:
    """
    Say which instance of a zero-downtime application serves, and how the other is.

    The serving instance is what every other probe looks at. The idle one is
    stopped by design; when it failed, its journal is why the last
    activation was refused, which is worth seeing even though traffic never
    reached it. And nginx's upstream must name the port of the instance the
    store says serves, or nginx proxies to a port nothing listens on.
    """
    app = ctx.app
    if app is None or not app.zero_downtime:
        return _check("blue_green", "skip", say("diagnose.blue_green.off")), {}

    serving = app.active_color
    if serving not in BLUE_GREEN_COLORS:
        return (
            _check(
                "blue_green",
                "fail",
                say("diagnose.blue_green.no_instance"),
                f"Turn it off and on again: noust app zero-downtime {app.domain} off",
            ),
            {"no_color": True},
        )
    idle = other_color(serving)
    port = color_port(app, serving)
    releases = ReleaseManager(app_root(app))
    serving_release = releases.color_release(serving)
    idle_unit = instance_unit(app, idle)

    upstream_port = NginxManager(runner=ctx.runner).upstream_port(app.domain)
    lines = [
        f"{instance_unit(app, color)}: port {color_port(app, color)}, release "
        f"{(releases.color_release(color) or _NO_RELEASE).id}"
        + (" (serving)" if color == serving else "")
        for color in BLUE_GREEN_COLORS
    ]
    lines.append(f"nginx upstream: 127.0.0.1:{upstream_port if upstream_port else '(none)'}")
    facts: dict[str, Any] = {"serving": serving, "port": port, "upstream_port": upstream_port}

    if upstream_port != port:
        facts["upstream_mismatch"] = True
        mismatch = (
            say(
                "diagnose.blue_green.upstream_mismatch",
                upstream_port=upstream_port,
                serving=serving,
                port=port,
            )
            if upstream_port
            else say("diagnose.blue_green.upstream_none", serving=serving, port=port)
        )
        return _check("blue_green", "fail", mismatch, "\n".join(lines)), facts

    leftover = leftover_unit(app, store=ctx.store, runner=ctx.runner)
    if leftover is not None:
        # Serving is fine; the next activation is not: blue starts on the
        # port this unit holds, and the unit starts again at every boot.
        what, fix = leftover_advice(leftover, app.domain)
        facts["leftover_unit"] = leftover
        return (
            _check(
                "blue_green",
                "warn",
                say(
                    "diagnose.blue_green.leftover",
                    unit=leftover,
                    port=color_port(app, "blue"),
                ),
                "\n".join(lines) + f"\n\n{what}.\n{fix}",
            ),
            facts,
        )

    shown = ctx.runner.run(
        ["systemctl", "show", "-p", "ActiveState,SubState,Result", f"{idle_unit}.service"],
        timeout=15,
    )
    idle_state = _parse_systemctl_show(shown.stdout)
    serves: dict[str, str | int] = {
        "serving": serving,
        "port": port,
        "idle": idle,
        "idle_state": idle_state.get("ActiveState") or "unknown",
    }
    if serving_release is not None:
        serves["release"] = serving_release.id
    if idle_state.get("ActiveState") == "failed" or idle_state.get("Result") not in (
        None,
        "",
        "success",
    ):
        journal = ctx.runner.run(
            [
                "journalctl",
                "-u",
                f"{idle_unit}.service",
                "-n",
                "50",
                "--no-pager",
                "-o",
                "short-iso",
            ],
            timeout=15,
        )
        facts["idle_failed"] = True
        evidence = (
            "\n".join(lines)
            + f"\n\nLast journal lines of {idle_unit}:\n"
            + (journal.stdout.strip() or journal.stderr.strip())
        )
        failed = say(
            "diagnose.blue_green.idle_failed_release"
            if serving_release is not None
            else "diagnose.blue_green.idle_failed",
            **serves,
        )
        return _check("blue_green", "warn", failed, evidence), facts
    well = say(
        "diagnose.blue_green.serving_release"
        if serving_release is not None
        else "diagnose.blue_green.serving",
        **serves,
    )
    return _check("blue_green", "ok", well, "\n".join(lines)), facts


def _check_nginx_log(ctx: _Context) -> ProbeResult:
    """Read the tail of nginx's error log, filtered to this domain and upstream errors."""
    try:
        with NGINX_ERROR_LOG.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _NGINX_LOG_TAIL_BYTES))
            data = handle.read()
    except OSError as exc:
        return (
            _check(
                "nginx_log",
                "skip",
                say("diagnose.nginx_log.unreadable", path=str(NGINX_ERROR_LOG)),
                str(exc),
            ),
            {},
        )

    text = data.decode("utf-8", errors="replace")
    needle = ctx.domain.lower()
    matches = [
        line for line in text.splitlines() if needle in line.lower() or "upstream" in line.lower()
    ]

    if not matches:
        return _check("nginx_log", "ok", say("diagnose.nginx_log.clean")), {}

    tail = matches[-_NGINX_LOG_MAX_LINES:]
    mentioned = (
        say("diagnose.nginx_log.lines_one")
        if len(matches) == 1
        else say("diagnose.nginx_log.lines_several", count=len(matches))
    )
    return _check("nginx_log", "warn", mentioned, "\n".join(tail)), {"matches": len(matches)}


def _check_certificate(ctx: _Context) -> ProbeResult:
    """Check the certificate covering this domain, reusing CertManager's own parsing."""
    cert = ctx.cert_manager.get_cert_info(ctx.domain)
    ssl_expected = ctx.app.ssl_enabled if ctx.app is not None else None

    if cert is None:
        if ssl_expected:
            return (
                _check(
                    "certificate",
                    "fail",
                    say("diagnose.certificate.missing", domain=ctx.domain),
                ),
                {"found": False},
            )
        return _check("certificate", "skip", say("diagnose.certificate.none", domain=ctx.domain)), {
            "found": False
        }

    label = cert.name or ctx.domain
    evidence = cert.expiry_full or f"Expiry Date: {cert.expiry}"

    if not cert.expiry:
        return _check(
            "certificate", "warn", say("diagnose.certificate.no_expiry", label=label), evidence
        ), {"found": True}

    # The same answer noust health and the overview give: a certificate is judged on its own life,
    # so one of 45 days is not in warning at the age one of 90 is, and one of 6 days is not in alarm.
    life = cert.life(ctx.now)
    days_left = life.days_left
    if life.state == cert_life.UNKNOWN or days_left is None:
        return _check(
            "certificate", "warn", say("diagnose.certificate.bad_expiry", label=label), evidence
        ), {"found": True}

    facts = {
        "found": True,
        "expiry": cert.expiry,
        "days_left": days_left,
        "expired": life.state == cert_life.EXPIRED,
        "state": life.state,
        "life_days": life.life_days,
    }
    if life.state == cert_life.EXPIRED:
        return _check(
            "certificate",
            "fail",
            say("diagnose.certificate.expired", label=label, expiry=cert.expiry),
            evidence,
        ), facts
    if life.needs_renewal:
        expires = (
            say("diagnose.certificate.expires_one", label=label)
            if days_left == 1
            else say("diagnose.certificate.expires_several", label=label, days=days_left)
        )
        return _check("certificate", "warn", expires, evidence), facts
    valid = (
        say("diagnose.certificate.valid_one", label=label)
        if days_left == 1
        else say("diagnose.certificate.valid_several", label=label, days=days_left)
    )
    return _check("certificate", "ok", valid, evidence), facts


def _check_last_deployment(ctx: _Context) -> ProbeResult:
    """Read the outcome of the last deployment recorded for this domain."""
    deployments = ctx.store.list_deployments(ctx.domain, limit=1)
    if not deployments:
        return _check("last_deployment", "skip", say("diagnose.last_deployment.none")), {}

    record = deployments[0]
    evidence_lines = [
        f"status={record.status}",
        f"started_at={record.started_at}",
        f"finished_at={record.finished_at}",
    ]
    if record.git_commit:
        evidence_lines.append(f"git_commit={record.git_commit}")
    if record.error:
        evidence_lines.append(f"error={record.error}")
    evidence = "\n".join(evidence_lines)

    if record.cancelled:
        stopped = (
            say("diagnose.last_deployment.cancelled_by", actor=record.cancelled_by)
            if record.cancelled_by
            else say("diagnose.last_deployment.cancelled")
        )
        return _check("last_deployment", "ok", stopped, evidence), {"failed": False}

    if record.status == DeploymentStatus.FAILED.value:
        failed = (
            say("diagnose.last_deployment.failed_with", error=record.error)
            if record.error
            else say("diagnose.last_deployment.failed")
        )
        return _check("last_deployment", "fail", failed, evidence), {"failed": True}

    if record.status == DeploymentStatus.SUCCESS.value:
        return _check(
            "last_deployment", "ok", say("diagnose.last_deployment.succeeded"), evidence
        ), {"failed": False}

    return _check(
        "last_deployment",
        "warn",
        say("diagnose.last_deployment.state", status=str(record.status)),
        evidence,
    ), {"failed": False}


def _check_oom(ctx: _Context) -> ProbeResult:
    """
    Say whether the kernel killed this application for running out of memory.

    The kernel's log line names a process and a pid, not a unit, so on its own it cannot say whose
    it was. In cgroup v2 the kernel counts the kills by cgroup: ``oom_kill`` in the ``memory.events``
    of the unit's cgroup is exactly this application's (item 175), and ``Result=oom-kill`` is what
    systemd records when the kill ended a run. Where neither can be read (no cgroup v2, no such
    file, a cgroup that restarting the unit made again) the check is what it always was, the log of
    the last week, and says that it cannot tell whose.
    """
    attribution = _attribute_oom(ctx)
    result = ctx.runner.run(
        ["journalctl", "-k", "--since", _OOM_WINDOW, "--grep", "oom", "-o", "short-iso"],
        timeout=15,
    )
    logged = result.stdout.strip() if result.success else ""
    facts = {"found": bool(logged), **attribution}

    if attribution.get("attributed"):
        count = int(attribution["kills"])
        since = str(attribution.get("since") or "its last start")
        if count == 0:
            said = say("diagnose.oom.unit_result")
        elif count == 1:
            said = say("diagnose.oom.killed_one", since=since)
        else:
            said = say("diagnose.oom.killed_several", count=count, since=since)
        return _check("oom", "warn", said, _oom_evidence(attribution, logged)), facts
    if not result.success:
        return _check("oom", "skip", say("diagnose.oom.unreadable"), result.stderr.strip()), {}
    if not logged:
        return _check("oom", "ok", say("diagnose.oom.none")), facts
    reason = attribution.get("cannot_say")
    if reason == "no_cgroup_v2":
        said = say("diagnose.oom.found_no_cgroup_v2")
    elif reason == "no_file":
        said = say("diagnose.oom.found_no_file")
    else:
        said = say("diagnose.oom.found")
    return _check("oom", "warn", said, logged), facts


def _attribute_oom(ctx: _Context) -> dict[str, Any]:
    """
    Read what the kernel and systemd say about this application's unit.

    Args:
        ctx: The diagnosis context.

    Returns:
        ``attributed`` (the kernel counted kills in the unit's cgroup, or systemd records that its
        last run ended in an out-of-memory kill) with ``kills``, ``limit``, ``peak``, ``since`` (when
        the unit started, which is where the count begins), ``result`` and ``control_group``; or
        ``cannot_say`` (``no_cgroup_v2`` or ``no_file``) when this machine cannot tell whose a kill
        was; or nothing at all for an application that has no unit to ask.
    """
    if _no_unit(ctx) is not None:
        return {}
    info = ctx.service_manager.inspect_unit(ctx.app_name)
    if not info.exists:
        return {}
    shown = ctx.runner.run(
        ["systemctl", "show", "-p", "ControlGroup,ActiveEnterTimestamp,Result", info.unit_file],
        timeout=15,
    )
    fields = _parse_systemctl_show(shown.stdout)
    unit_result = fields.get("Result", "")
    control_group = fields.get("ControlGroup", "").strip()
    since = fields.get("ActiveEnterTimestamp", "").strip()

    kernel = cgroup_events.OomFacts()
    why = ""
    if not is_cgroup_v2(ctx.cgroup_mount):
        why = "no_cgroup_v2"
    elif control_group:
        kernel = cgroup_events.read_facts(ctx.cgroup_mount / control_group.lstrip("/"))
        if not kernel.readable:
            why = "no_file"
    else:
        why = "no_file"

    found: dict[str, Any] = {
        "kills": kernel.kills,
        "limit": kernel.limit,
        "peak": kernel.peak,
        "since": since,
        "result": unit_result,
        "control_group": control_group,
    }
    if kernel.kills > 0 or unit_result == "oom-kill":
        return {**found, "attributed": True}
    return {**found, "cannot_say": why} if why else found


def _oom_evidence(attribution: dict[str, Any], logged: str) -> str:
    """
    Args:
        attribution: What :func:`_attribute_oom` read.
        logged: What the kernel log said of running out of memory, verbatim.

    Returns:
        The figures as the kernel wrote them, then the log's own lines: nothing paraphrased.
    """
    limit = attribution["limit"]
    lines = [
        f"oom_kill={attribution['kills']}",
        f"memory.max={limit if limit is not None else 'max'}",
    ]
    if attribution["peak"] is not None:
        lines.append(f"memory.peak={attribution['peak']}")
    if attribution["since"]:
        lines.append(f"ActiveEnterTimestamp={attribution['since']}")
    if attribution["result"]:
        lines.append(f"Result={attribution['result']}")
    return "\n".join([*lines, *([logged] if logged else [])])


#: The step a suggested limit is rounded up to, in megabytes.
_LIMIT_STEP_MB = 64


def _suggested_megabytes(limit: int | None, peak: int | None) -> int:
    """
    Args:
        limit: The memory limit it had, in bytes, or None.
        peak: The most it used, in bytes, or None.

    Returns:
        A limit half as large again as the larger of the two, in whole steps of 64 MB, so the
        command the diagnosis suggests is one that works as it is written.
    """
    base = max(limit or 0, peak or 0) / (1024 * 1024)
    wanted = max(base * 1.5, float(_LIMIT_STEP_MB))
    return int(-(-wanted // _LIMIT_STEP_MB) * _LIMIT_STEP_MB)


def _oom_cause(ctx: _Context, oom: dict[str, Any]) -> ServerText:
    """
    Say why the kernel killed the application and what to do, from its limit against its peak.

    Args:
        ctx: The diagnosis context.
        oom: What :func:`_attribute_oom` read, with ``attributed``.

    Returns:
        The probable cause.
    """
    limit, peak = oom.get("limit"), oom.get("peak")
    if oom.get("kills", 0) == 0:
        # Only systemd's record: its cgroup was made again when the unit restarted, and with it the count.
        return say(
            "diagnose.cause.oom_unit", domain=ctx.domain, command=f"noust app limits {ctx.domain}"
        )
    suggested = _suggested_megabytes(limit, peak)
    command = f"noust app limits {ctx.domain} --memory {suggested}M --restart"
    if limit is None:
        return say(
            "diagnose.cause.oom_machine",
            domain=ctx.domain,
            peak=format_bytes(int(peak)) if peak is not None else "?",
            command=command,
        )
    return say(
        "diagnose.cause.oom_limit",
        domain=ctx.domain,
        limit=format_bytes(int(limit)),
        peak=format_bytes(int(peak)) if peak is not None else format_bytes(int(limit)),
        command=command,
    )


def _check_disk(ctx: _Context) -> ProbeResult:
    """Check free space on the filesystem holding the apps directory."""
    apps_dir = ctx.config.apps_directory
    usage = ctx.disk_usage(str(apps_dir))
    percent_used = ((usage.total - usage.free) / usage.total * 100) if usage.total else 0.0
    free_gb = usage.free / (1024**3)
    evidence = f"total={usage.total} used={usage.total - usage.free} free={usage.free}"
    said = say(
        "diagnose.disk.usage",
        free=f"{free_gb:.1f}GB",
        percent=round(percent_used),
        path=str(apps_dir),
    )

    if percent_used > _DISK_FAIL_PERCENT:
        status: CheckStatus = "fail"
    elif percent_used > _DISK_WARN_PERCENT:
        status = "warn"
    else:
        status = "ok"
    return _check("disk", status, said, evidence), {"percent_used": percent_used}


#: Every probe, in the order they run and appear in a diagnosis. A probe's
#: name here is what a failure is reported under if the probe itself raises.
_PROBES: tuple[tuple[str, Probe], ...] = (
    ("unit", _check_unit),
    ("port", _check_port),
    ("http_direct", _check_http_direct),
    ("http_nginx", _check_http_nginx),
    ("journal", _check_journal),
    ("nginx_log", _check_nginx_log),
    ("certificate", _check_certificate),
    ("last_deployment", _check_last_deployment),
    ("oom", _check_oom),
    ("disk", _check_disk),
)


# -- Verdict ----------------------------------------------------------------


def _decide(
    ctx: _Context, checks: tuple[Check, ...], facts: dict[str, dict[str, Any]]
) -> tuple[Verdict, ServerText | None]:
    """
    Turn the collected facts into a verdict and a probable cause.

    Rules are ordered from most specific and most severe to least; the first
    one that matches wins. A diagnosis where nothing specific matches falls
    back to the aggregate severity of the checks, with no named cause.

    Args:
        ctx: The context the checks ran with.
        checks: Every check that ran.
        facts: Each check's name to the internal facts it collected.

    Returns:
        The verdict and the probable cause as a sentence, or None when no rule matched.
    """
    unit = facts.get("unit", {})
    port = facts.get("port", {})
    http_direct = facts.get("http_direct", {})
    http_nginx = facts.get("http_nginx", {})
    cert = facts.get("certificate", {})
    disk = facts.get("disk", {})
    oom = facts.get("oom", {})

    if facts.get("descriptors", {}).get("exhausted"):
        # First: what could not be read says nothing of the application, and every other rule
        # reads what was.
        return "degraded", say(
            "diagnose.cause.descriptors", domain=ctx.domain, command="noust web restart"
        )

    active_state = unit.get("active_state", "")
    sub_state = unit.get("sub_state", "")
    crash_looping = sub_state == "auto-restart" or active_state == "activating"
    unit_failed = active_state == "failed" or sub_state == "failed"

    php = facts.get("php_fpm", {})
    if php.get("cause"):
        return "down", php["cause"]

    containers = facts.get("containers", {})
    if containers.get("headless") and not containers.get("healthy"):
        return "down", say("diagnose.cause.worker", summary=str(containers.get("summary")))

    if unit.get("exists") is False:
        return "down", say("diagnose.cause.no_unit", unit=ctx.app_name)

    if oom.get("attributed") and (unit_failed or crash_looping):
        return "down", _oom_cause(ctx, oom)

    if (unit_failed or crash_looping) and oom.get("found"):
        return "down", say("diagnose.cause.oom")

    if crash_looping:
        exit_status = unit.get("exec_main_status") or "unknown"
        return "down", say("diagnose.cause.crash_loop", status=str(exit_status))

    if unit_failed:
        result_code = unit.get("result") or "unknown"
        exit_status = unit.get("exec_main_status") or "unknown"
        return "down", say(
            "diagnose.cause.unit_failed", result=str(result_code), status=str(exit_status)
        )

    blue_green = facts.get("blue_green", {})
    if blue_green.get("upstream_mismatch"):
        if blue_green.get("upstream_port"):
            return "down", say(
                "diagnose.cause.upstream_mismatch",
                domain=ctx.domain,
                upstream_port=blue_green["upstream_port"],
                serving=str(blue_green.get("serving")),
                port=blue_green["port"],
            )
        return "down", say(
            "diagnose.cause.upstream_none",
            domain=ctx.domain,
            serving=str(blue_green.get("serving")),
            port=blue_green["port"],
        )

    if port.get("recorded_port") is not None and not port.get("listening"):
        recorded_port = port["recorded_port"]
        if port.get("mismatch"):
            return "down", say(
                "diagnose.cause.port_elsewhere",
                actual_port=port["actual_port"],
                recorded_port=recorded_port,
            )
        return "down", say("diagnose.cause.port_silent", port=recorded_port)

    if http_direct.get("ok") and not http_nginx.get("ok"):
        nginx_status = http_nginx.get("status")
        if nginx_status is not None:
            return "down", say(
                "diagnose.cause.nginx_status", status=str(nginx_status), domain=ctx.domain
            )
        return "down", say(
            "diagnose.cause.nginx_unreachable",
            error=str(http_nginx.get("error")),
            domain=ctx.domain,
        )

    if cert.get("expired"):
        return "down", say(
            "diagnose.cause.certificate_expired", domain=ctx.domain, expiry=str(cert.get("expiry"))
        )

    if containers.get("headless") and containers.get("recorded_port"):
        return "degraded", say(
            "diagnose.cause.worker_port",
            domain=ctx.domain,
            port=containers["recorded_port"],
            command=f"noust app headless {ctx.domain}",
        )

    if disk.get("percent_used", 0) > _DISK_FAIL_PERCENT:
        return "degraded", say("diagnose.cause.disk_full", percent=round(disk["percent_used"]))

    if oom.get("attributed"):
        # It is up, and the kernel has been killing it: what it serves now is a restart's worth.
        return "degraded", _oom_cause(ctx, oom)

    statuses = {c.status for c in checks}
    if "fail" in statuses:
        return "down", None
    if "warn" in statuses:
        return "degraded", None
    return "healthy", None


# -- Entry point --------------------------------------------------------------


def _safe_get_app(store: NoustStore, domain: str) -> App | None:
    """
    Look up an application record, tolerating a store that cannot answer.

    Args:
        store: The store to read.
        domain: Domain to look up.

    Returns:
        The application record, or None when it does not exist or the store
        could not be read.
    """
    try:
        return store.get_app(domain)
    except (NoustError, sqlite3.Error) as exc:
        log.debug(f"Could not read {domain} from the store: {exc}")
        return None


def diagnose(
    domain: str,
    *,
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
    now: Callable[[], datetime] | None = None,
    http_get: HttpGet | None = None,
    disk_usage: DiskUsage | None = None,
    cgroup_mount: Path | None = None,
) -> Diagnosis:
    """
    Correlate everything Noust can read about a domain into one diagnosis.

    Args:
        domain: The domain to diagnose.
        runner: Command runner every probe executes through. Defaults to the
            process-wide runner.
        store: Store every probe reads through. Defaults to the process-wide
            store.
        now: Returns the moment to measure certificate expiry against.
            Defaults to the current UTC time.
        http_get: Performs the direct and nginx HTTP probes. Defaults to a
            real, short-timeout request; tests inject a fake so no probe ever
            opens a socket.
        disk_usage: Reads free space for a path, with :func:`shutil.disk_usage`'s
            signature. Defaults to :func:`shutil.disk_usage`.
        cgroup_mount: Where cgroups are mounted, for the kernel's own count of what it killed.
            Defaults to the system's; tests point it at a fake tree.

    Returns:
        Every probe's result and the verdict derived from them.
    """
    resolved_runner = runner or get_runner()
    resolved_store = store or get_store()

    app = _safe_get_app(resolved_store, domain)
    ctx = _Context(
        domain=domain,
        # In zero-downtime mode every probe of "the unit" is of the instance
        # that serves; the idle one has a probe of its own.
        app_name=(
            instance_unit(app, app.active_color)
            if app is not None and runs_as_instances(app) and app.active_color in BLUE_GREEN_COLORS
            else domain_to_app_name(domain)
        ),
        app=app,
        runner=resolved_runner,
        store=resolved_store,
        now=(now or (lambda: datetime.now(timezone.utc)))(),
        http_get=http_get or _default_http_get,
        disk_usage=disk_usage or shutil.disk_usage,
        config=Config(),
        service_manager=ServiceManager(runner=resolved_runner),
        cert_manager=CertManager(runner=resolved_runner),
        cgroup_mount=cgroup_mount or CGROUP_MOUNT,
    )

    checks: list[Check] = []
    facts: dict[str, dict[str, Any]] = {}
    #: What the first probe that ran out of file descriptors said; empty while none did.
    ran_out = ""
    from noust.deployers.helpers.php_fpm import is_php_fpm

    probes = _PROBES
    if app is not None and app.zero_downtime:
        # Only then: which instance serves comes before everything else.
        probes = (("blue_green", _check_blue_green), *_PROBES)
    if app is not None and is_php_fpm(app):
        # Its pool is what runs it, so the pool comes first.
        probes = (("php_fpm", _check_php_fpm), *_PROBES)
    if app is not None and app.app_type == "docker-compose":
        # Whether it is a web at all decides what the rest may ask.
        probes = (("containers", _check_compose_stack), *probes)
    for name, probe in probes:
        if name in _WEB_PROBES and facts.get("containers", {}).get("headless"):
            continue
        try:
            check, probe_facts = probe(ctx)
        except (NoustError, OSError, ValueError) as exc:
            check = _check(name, "skip", say("diagnose.check.incomplete"), str(exc))
            probe_facts = {}
            if descriptors.is_exhaustion(exc) and not ran_out:
                ran_out = str(exc)
        checks.append(check)
        facts[name] = probe_facts

    if ran_out:
        # The console itself, not the application: say it once, beside the checks it spoiled.
        checks.append(_check("descriptors", "fail", say("diagnose.descriptors.exhausted"), ran_out))
        facts["descriptors"] = {"exhausted": True}

    if app is not None and could_update_without_downtime(app, store=resolved_store):
        # Not a cause of anything: what an operator who just watched an update
        # take the application down for a moment can do about it.
        checks.append(
            _check(
                "zero_downtime",
                "skip",
                say("diagnose.zero_downtime.could"),
                f"Turn it on: noust app zero-downtime {domain} on",
            )
        )

    verdict, cause = _decide(ctx, tuple(checks), facts)
    return Diagnosis(
        domain=domain,
        verdict=verdict,
        probable_cause=cause.text if cause else None,
        checks=tuple(checks),
        probable_cause_coded=cause,
    )
