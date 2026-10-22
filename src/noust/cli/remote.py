# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The commands that run on another server (J-12).

With ``--remote URL`` (or ``--context NAME``, or ``$NOUST_REMOTE``) the command
line stops being the emergency channel that runs as root on the machine and
becomes a client of a server's API. Every command here is a few lines that call
one endpoint and print what it answered; none reaches a manager, so the role of
the token, the team it is bound to, sudo mode and the audit trail are the
server's. Nothing is audited on this machine: the server records who did it
(the token's owner), from where, and what.

Commands that have no form here refuse with the way to run them: most of the
tree reaches the machine itself (a unit, a firewall, a package), and that is
what a root shell, or the console, is for.

An answer's failure keeps the server's own words: the probes of a health gate
that refused an activation, nginx's or systemd's output, arrive exactly as the
server sent them.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustCommand, NoustGroup, json_option, pass_context
from noust.client import ApiClient, RemoteError, RemoteSettings, Target, resolve_target
from noust.client.jobs import DEFAULT_WAIT, follow
from noust.core.logger import Logger

#: Exit codes beyond 0 (ok) and 1 (failed): the call is still running.
EXIT_STILL_RUNNING = 4


class RemoteCommand(NoustCommand):
    """
    A command that runs on another server.

    Not audited on this machine: the audit trail that matters is the server's,
    which records the token's owner and the call. Everything else is a
    :class:`~noust.cli.app.NoustCommand` (``--json`` is refused unless the
    command supports it).
    """

    def invoke(self, ctx: click.Context) -> Any:
        """
        Run the command without a local audit record.

        Args:
            ctx: The command's context.

        Returns:
            What the command returns.

        Raises:
            click.UsageError: When ``--json`` is given to a command without it.
        """
        state = ctx.ensure_object(Context)
        if state.json_output and not self._declares_json_option():
            raise click.UsageError(
                f"'{ctx.command_path}' has no JSON output; drop --json to run it."
            )
        return click.Command.invoke(self, ctx)


class RemoteGroup(NoustGroup):
    """The group every remote command is built from."""

    command_class = RemoteCommand
    group_class = type


@click.group("remote", cls=RemoteGroup)
def remote() -> None:
    """The commands that run on another server through its API."""


def settings_of(params: dict[str, Any]) -> RemoteSettings:
    """
    Args:
        params: The root command's parsed options.

    Returns:
        What they say about another server.
    """
    return RemoteSettings(
        remote=params.get("remote"),
        context=params.get("context"),
        token=params.get("token"),
        token_file=params.get("token_file"),
        token_env=params.get("token_env"),
        ca_file=params.get("ca_file"),
        local=bool(params.get("local")),
    )


def is_remote(params: dict[str, Any]) -> bool:
    """
    Decide, without touching a token, whether a command runs on another server.

    Args:
        params: The root command's parsed options.

    Returns:
        True when an address or a context is in play and ``--local`` is not.
    """
    from noust.client.contexts import ENV_CONTEXT, ENV_REMOTE, ContextStore

    settings = settings_of(params)
    if settings.local:
        return False
    import os

    return bool(
        settings.remote
        or settings.context
        or os.environ.get(ENV_REMOTE)
        or os.environ.get(ENV_CONTEXT)
        or ContextStore().current() is not None
    )


def remote_commands() -> dict[str, click.Command]:
    """
    Returns:
        Every command that has a form on another server. The groups defined in
        modules of their own (``manifest``) register themselves when imported,
        so they are imported here, once, before the table is read.
    """
    import importlib

    importlib.import_module("noust.cli.commands.manifest")
    return dict(remote.commands)


def target_of(ctx: Context) -> Target:
    """
    Args:
        ctx: The shared context, carrying the root command's options.

    Returns:
        The server and its token.

    Raises:
        RemoteError: When no server is in play (a defect: the dispatcher only
            routes here when one is).
    """
    cached = getattr(ctx, "_target", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    resolved = resolve_target(ctx.remote or RemoteSettings())
    if resolved is None:
        raise RemoteError(
            "This works on a server through its API, and none is given",
            code="refused_locally",
            details="Name one: --context NAME (see 'noust context add'), or --remote URL with "
            "--token-file FILE.",
        )
    ctx._target = resolved  # type: ignore[attr-defined]
    if not ctx.json_output:
        click.echo(f"noust: running on {resolved.label}", err=True)
    return resolved


def api(ctx: Context) -> ApiClient:
    """
    Args:
        ctx: The shared context.

    Returns:
        A client of the server the command runs on.
    """
    return target_of(ctx).client()


def emit(ctx: Context, body: Any) -> bool:
    """
    Print a body as JSON when that was asked for.

    Args:
        ctx: The shared context.
        body: What the server answered.

    Returns:
        True when it was printed, and the command has nothing else to say.
    """
    if ctx.json_output:
        click.echo(json.dumps(body))
        return True
    return False


def short(value: Any, width: int = 60) -> str:
    """
    Args:
        value: Anything a table cell holds.
        width: Longest text kept.

    Returns:
        It as one trimmed line, or a dash for nothing.
    """
    if value is None or value == "":
        return "-"
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 1] + "..."


def follow_job(
    client: ApiClient,
    job_id: str,
    logger: Logger,
    *,
    timeout: float = DEFAULT_WAIT,
    show_log: bool = True,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """
    Wait for a job on the server, printing its log as it grows.

    Args:
        client: The server's client.
        job_id: The job.
        logger: Where the log lines go.
        timeout: Seconds to wait before giving up (the job keeps running).
        show_log: Print the job's log lines as they appear.
        sleep: Replaces waiting, for tests.
        clock: Replaces the monotonic clock, for tests.

    Returns:
        The job as the server last described it; ``state`` says how it ended,
        or that it is still running when the wait ran out.
    """
    return follow(
        client,
        job_id,
        on_line=logger.info if show_log else None,
        timeout=timeout,
        sleep=sleep,
        clock=clock,
    )


def finish_job(ctx: Context, client: ApiClient, job: dict[str, Any], what: str) -> None:
    """
    Report how a job ended and exit with the code that says so.

    Args:
        ctx: The shared context.
        client: The server's client.
        job: The job as the server last described it.
        what: What it was, for the message ("Update of shop.example.com").

    Raises:
        click.exceptions.Exit: With 1 when it failed or was cancelled and 4
            when it is still running.
    """
    state = job.get("state")
    logger = ctx.logger
    if ctx.json_output:
        click.echo(json.dumps(job))
    if state == "succeeded":
        if not ctx.json_output:
            logger.success(f"{what} succeeded")
            _deployment_line(ctx, client, job)
        return
    if state in ("failed", "cancelled"):
        if not ctx.json_output:
            logger.error(f"{what} {state}")
            if job.get("error"):
                # The probes and the journal of a refused activation, as the server wrote them.
                for line in str(job["error"]).rstrip("\n").splitlines():
                    logger.info(line)
        raise click.exceptions.Exit(1)
    if not ctx.json_output:
        logger.warning(f"{what} is still running on the server (job {job.get('id')})")
        logger.info(f"Follow it with: noust jobs wait {job.get('id')}")
    raise click.exceptions.Exit(EXIT_STILL_RUNNING)


def _deployment_line(ctx: Context, client: ApiClient, job: dict[str, Any]) -> None:
    """
    Print the commit a successful deployment went live with.

    Args:
        ctx: The shared context.
        client: The server's client.
        job: The finished job.
    """
    deployment = job.get("deployment_id")
    if deployment is None:
        return
    try:
        record = client.get(f"/deployments/{deployment}")
    except RemoteError:
        return  # the job succeeded; a missing record is not worth failing for
    commit = str(record.get("git_commit") or "")[:12]
    ctx.logger.info(
        f"Deployment {record.get('id')}: {record.get('status')}"
        + (f", commit {commit}" if commit else "")
        + (f" in {record['duration_s']:.0f}s" if record.get("duration_s") else "")
    )


def _domain(value: str) -> str:
    """
    Args:
        value: An application's domain as typed.

    Returns:
        It, trimmed and lower-cased.
    """
    return value.strip().lower()


# ---------------------------------------------------------------- applications


@remote.command("list", read_only=True)
@click.option("--status", default=None, help="Only applications in this status.")
@click.option("--team", default=None, help="Only this team's.")
@click.option("--project", default=None, help="Only this project's.")
@click.option("--env", "environment", default=None, help="Only this environment's.")
@json_option("Print the applications as JSON.")
@pass_context
def list_command(
    ctx: Context,
    status: str | None,
    team: str | None,
    project: str | None,
    environment: str | None,
) -> None:
    """List the applications on the server."""
    body = api(ctx).get("/apps", status=status, team=team, project=project, environment=environment)
    if emit(ctx, body):
        return
    apps = body.get("apps", [])
    if not apps:
        ctx.logger.info("No applications.")
        return
    ctx.logger.table(
        ["Domain", "Type", "Status", "Where"],
        [
            [
                item.get("domain", ""),
                item.get("app_type", ""),
                item.get("status", ""),
                "/".join(
                    x for x in (item.get("team"), item.get("project"), item.get("environment")) if x
                )
                or "-",
            ]
            for item in apps
        ],
    )


@remote.command("status", read_only=True)
@click.argument("domain")
@json_option("Print the application as JSON.")
@pass_context
def status_command(ctx: Context, domain: str) -> None:
    """Show one application."""
    body = api(ctx).get(f"/apps/{_domain(domain)}")
    if emit(ctx, body):
        return
    for key in ("domain", "app_type", "status", "branch", "port", "team", "project", "environment"):
        if body.get(key) not in (None, ""):
            ctx.logger.key_value(key, str(body[key]))


def _wait_options(command: Callable[..., Any]) -> Callable[..., Any]:
    """
    Args:
        command: A command that queues a job.

    Returns:
        It, taking ``--wait/--no-wait`` and ``--timeout``.
    """
    command = click.option(
        "--timeout",
        type=float,
        default=DEFAULT_WAIT,
        show_default=True,
        help="Seconds to wait for it. The job keeps running if this runs out.",
    )(command)
    command = click.option(
        "--wait/--no-wait", default=True, show_default=True, help="Wait for the job to finish."
    )(command)
    return command


def _queue(
    ctx: Context, path: str, body: dict[str, Any], what: str, wait: bool, timeout: float
) -> None:
    """
    Queue a job on the server and, unless told not to, follow it to its end.

    Args:
        ctx: The shared context.
        path: The job route.
        body: Its request.
        what: What it is, for the messages.
        wait: Follow it to its end.
        timeout: Seconds to wait.
    """
    client = api(ctx)
    created = client.post(path, body)
    job = created["job"]
    if not wait:
        if not emit(ctx, created):
            ctx.logger.success(f"{created.get('message', what + ' queued')}: job {job['id']}")
            ctx.logger.info(f"Follow it with: noust jobs wait {job['id']}")
        return
    if not ctx.json_output:
        ctx.logger.info(f"{what}: job {job['id']}")
    final = follow_job(client, job["id"], ctx.logger, timeout=timeout, show_log=not ctx.json_output)
    finish_job(ctx, client, final, what)


@remote.command("update")
@click.argument("domains", nargs=-1)
@click.option("--team", default=None, help="Every application of this team.")
@click.option("--project", default=None, help="Every application of this project.")
@click.option("--env", "environment", default=None, help="Every application of this environment.")
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Every application that carries this tag (repeat for any of several); "
    "with --team, --project or --env, only those among that group's.",
)
@click.option("--force", is_flag=True, help="Rebuild even when the branch has nothing new.")
@click.option(
    "--serial",
    is_flag=True,
    help="A group, one after the other, stopping at the first that fails (--keep-going to go on).",
)
@click.option("--keep-going", is_flag=True, help="With --serial, update the rest after a failure.")
@_wait_options
@json_option("Print the finished job (or the group) as JSON.")
@pass_context
def update_command(
    ctx: Context,
    domains: tuple[str, ...],
    team: str | None,
    project: str | None,
    environment: str | None,
    tags: tuple[str, ...],
    force: bool,
    serial: bool,
    keep_going: bool,
    wait: bool,
    timeout: float,
) -> None:
    """
    Update applications from their branch and wait for the health gate.

    \b
      noust --context prod update shop.example.com
      noust --context prod update --project web --env staging
      noust --context prod update --tag nightly --tag eu
      noust --context prod update --project web --env production --serial

    One application: the exit code says how it went (0 live, 1 refused or
    failed, the server's own probes and journal printed; 4 still running when
    the wait ran out). A group (several domains, or a team, a project, an
    environment or a tag) is queued in one call and each application goes through its
    own health gate; the exit code is 1 when any failed. --serial updates them
    one after the other instead, stopping at the first failure.
    """
    grouped = len(domains) > 1 or bool(team or project or environment or tags)
    if not domains and not grouped:
        raise click.UsageError("Name an application, or a team, project, environment or tag.")
    if not grouped:
        name = _domain(domains[0])
        _queue(
            ctx,
            "/jobs/update",
            {"domain": name, "force": force},
            f"Update of {name}",
            wait,
            timeout,
        )
        return
    client = api(ctx)
    if serial:
        _update_serially(
            ctx, client, domains, team, project, environment, tags, force, keep_going, timeout
        )
        return
    body: dict[str, Any] = {
        "team": team,
        "project": project,
        "environment": environment,
        "domains": [_domain(d) for d in domains],
        "force": force,
    }
    if tags:
        body["tags"] = list(tags)
    answer = client.post("/jobs/update-many", body)
    if not ctx.json_output:
        ctx.logger.info(answer["message"])
        for item in answer["items"]:
            ctx.logger.info(
                f"  {item['domain']}: {item['status']}"
                + (f" ({item['reason']})" if item.get("reason") else "")
            )
    jobs = [item for item in answer["items"] if item.get("job_id")]
    if not wait:
        if ctx.json_output:
            click.echo(json.dumps(answer))
        return
    failed = False
    finals = []
    for item in jobs:
        final = follow_job(client, item["job_id"], ctx.logger, timeout=timeout, show_log=False)
        finals.append({"domain": item["domain"], **final})
        state = final.get("state")
        failed = failed or state in ("failed", "cancelled")
        if not ctx.json_output:
            (ctx.logger.success if state == "succeeded" else ctx.logger.error)(
                f"{item['domain']}: {state}"
            )
            if state in ("failed", "cancelled") and final.get("error"):
                for line in str(final["error"]).rstrip("\n").splitlines():
                    ctx.logger.info(f"    {line}")
    if ctx.json_output:
        click.echo(json.dumps({**answer, "jobs": finals}))
    if failed:
        raise click.exceptions.Exit(1)


def _update_serially(
    ctx: Context,
    client: ApiClient,
    domains: tuple[str, ...],
    team: str | None,
    project: str | None,
    environment: str | None,
    tags: tuple[str, ...],
    force: bool,
    keep_going: bool,
    timeout: float,
) -> None:
    """
    Update a group one application after another.

    Args:
        ctx: The shared context.
        client: The server's client.
        domains: Applications named.
        team: The team whose applications to update.
        project: The project whose applications to update.
        environment: The environment whose applications to update.
        tags: Tags whose applications to update (any of them).
        force: Rebuild even when nothing is new.
        keep_going: Carry on after one fails.
        timeout: Seconds to wait for each.

    Raises:
        click.exceptions.Exit: With 1 when any failed.
    """
    names = [_domain(d) for d in domains]
    if tags:
        # The list filters by one tag at a time; any of several is the union of those lists,
        # each already narrowed by the place when one was given.
        for tag in tags:
            listed = client.get(
                "/apps", tag=tag, team=team, project=project, environment=environment
            )
            names += [item["domain"] for item in listed.get("apps", [])]
    elif team or project or environment:
        listed = client.get("/apps", team=team, project=project, environment=environment)
        names += [item["domain"] for item in listed.get("apps", [])]
    results = []
    failed = False
    for name in sorted(dict.fromkeys(names)):
        try:
            created = client.post("/jobs/update", {"domain": name, "force": force})
        except RemoteError as exc:
            if exc.code == "nothing_new":
                ctx.logger.info(f"{name}: skipped, {exc.message}")
                results.append({"domain": name, "state": "skipped", "error": exc.message})
                continue
            raise
        final = follow_job(
            client, created["job"]["id"], ctx.logger, timeout=timeout, show_log=not ctx.json_output
        )
        results.append({"domain": name, **final})
        state = final.get("state")
        if not ctx.json_output:
            (ctx.logger.success if state == "succeeded" else ctx.logger.error)(f"{name}: {state}")
            if state != "succeeded" and final.get("error"):
                for line in str(final["error"]).rstrip("\n").splitlines():
                    ctx.logger.info(f"    {line}")
        if state != "succeeded":
            failed = True
            if not keep_going:
                break
    if ctx.json_output:
        click.echo(json.dumps({"jobs": results}))
    if failed:
        raise click.exceptions.Exit(1)


@remote.command("rollback")
@click.argument("domain")
@click.option("--backup", "backup_id", default=None, help="The backup to go back to.")
@click.option(
    "--schema-changed-ok",
    is_flag=True,
    help="Go back even past deployments that changed the database's schema.",
)
@_wait_options
@json_option("Print the finished job as JSON.")
@pass_context
def rollback_command(
    ctx: Context,
    domain: str,
    backup_id: str | None,
    schema_changed_ok: bool,
    wait: bool,
    timeout: float,
) -> None:
    """Go back to the previous deployment (or a backup) and wait for the health gate."""
    name = _domain(domain)
    _queue(
        ctx,
        "/jobs/rollback",
        {"domain": name, "backup_id": backup_id, "schema_changed_ok": schema_changed_ok},
        f"Rollback of {name}",
        wait,
        timeout,
    )


def _action(verb: str, summary: str) -> Callable[..., Any]:
    """
    Build a command that posts one action on an application.

    Args:
        verb: ``restart``, ``start`` or ``stop``.
        summary: The first line of its help.

    Returns:
        The command.
    """

    @remote.command(verb)
    @click.argument("domain")
    @json_option("Print the server's answer as JSON.")
    @pass_context
    def command(ctx: Context, domain: str) -> None:
        body = api(ctx).post(f"/apps/{_domain(domain)}/{verb}")
        if not emit(ctx, body):
            ctx.logger.success(str(body.get("message") or f"{verb.capitalize()} done"))

    command.__doc__ = summary
    return command


_action("restart", "Restart an application.")
_action("start", "Start an application.")
_action("stop", "Stop an application.")


@remote.command("logs", read_only=True)
@click.argument("domain")
@click.option("--lines", type=click.IntRange(1, 1000), default=100, show_default=True)
@json_option("Print the answer as JSON.")
@pass_context
def logs_command(ctx: Context, domain: str, lines: int) -> None:
    """Show the end of an application's journal."""
    body = api(ctx).get(f"/apps/{_domain(domain)}/logs", lines=lines)
    if not emit(ctx, body):
        click.echo(str(body.get("logs", "")))


@remote.command("diagnose", read_only=True)
@click.argument("domain")
@json_option("Print the diagnosis as JSON.")
@pass_context
def diagnose_command(ctx: Context, domain: str) -> None:
    """Say why an application is down, most likely cause first."""
    body = api(ctx).get(f"/apps/{_domain(domain)}/diagnose")
    if emit(ctx, body):
        return
    verdict = body.get("verdict") or body.get("summary")
    if verdict:
        ctx.logger.info(str(verdict))
    for finding in body.get("findings", []):
        ctx.logger.info(
            f"{finding.get('severity', '-')}: {finding.get('title', finding.get('summary', ''))}"
        )
        if finding.get("evidence"):
            for line in str(finding["evidence"]).splitlines():
                ctx.logger.info(f"    {line}")


# -------------------------------------------------------------------- releases


@remote.group("releases", cls=RemoteGroup)
def releases() -> None:
    """The releases of an application."""


@releases.command("list", read_only=True)
@click.argument("domain")
@json_option("Print the releases as JSON.")
@pass_context
def releases_list(ctx: Context, domain: str) -> None:
    """List an application's releases."""
    body = api(ctx).get(f"/apps/{_domain(domain)}/releases")
    if emit(ctx, body):
        return
    rows = body.get("releases", [])
    ctx.logger.table(
        ["Release", "Live", "Commit", "Created"],
        [
            [
                str(item.get("id", "")),
                "yes" if item.get("active") or item.get("current") else "",
                short(item.get("git_commit") or item.get("commit"), 12),
                short(item.get("created_at")),
            ]
            for item in rows
        ],
    )


@releases.command("activate")
@click.argument("domain")
@click.argument("release")
@json_option("Print the server's answer as JSON.")
@pass_context
def releases_activate(ctx: Context, domain: str, release: str) -> None:
    """Make an earlier release live again, through the health gate."""
    body = api(ctx).post(f"/apps/{_domain(domain)}/releases/{release}/activate")
    if not emit(ctx, body):
        ctx.logger.success(str(body.get("message") or "Release activated"))


# ------------------------------------------------------------------------ jobs


@remote.group("jobs", cls=RemoteGroup)
def jobs() -> None:
    """The jobs the server runs."""


@jobs.command("list", read_only=True)
@click.option("--status", default=None, help="Only jobs in this status.")
@click.option("--limit", type=click.IntRange(1, 500), default=30, show_default=True)
@json_option("Print the jobs as JSON.")
@pass_context
def jobs_list(ctx: Context, status: str | None, limit: int) -> None:
    """List recent jobs."""
    body = api(ctx).get("/jobs", status=status, limit=limit)
    if emit(ctx, body):
        return
    ctx.logger.table(
        ["Job", "State", "What", "Started"],
        [
            [
                item["id"],
                item.get("state", ""),
                short(item.get("name")),
                short(item.get("started_at")),
            ]
            for item in body.get("jobs", [])
        ],
    )


@jobs.command("show", read_only=True)
@click.argument("job_id")
@json_option("Print the job as JSON.")
@pass_context
def jobs_show(ctx: Context, job_id: str) -> None:
    """Show one job."""
    body = api(ctx).get(f"/jobs/{job_id}")
    if emit(ctx, body):
        return
    for key in ("id", "name", "state", "current_step", "created_at", "completed_at", "actor"):
        if body.get(key) not in (None, ""):
            ctx.logger.key_value(key, str(body[key]))
    if body.get("error"):
        ctx.logger.error(str(body["error"]))


@jobs.command("log", read_only=True)
@click.argument("job_id")
@click.option("--tail", type=click.IntRange(1, 20000), default=500, show_default=True)
@pass_context
def jobs_log(ctx: Context, job_id: str, tail: int) -> None:
    """Print the end of a job's log."""
    body = api(ctx).get(f"/jobs/{job_id}/log", tail=tail)
    click.echo(str(body.get("content", "")))


@jobs.command("cancel")
@click.argument("job_id")
@json_option("Print the server's answer as JSON.")
@pass_context
def jobs_cancel(ctx: Context, job_id: str) -> None:
    """Cancel a job that waits or is still building."""
    body = api(ctx).post(f"/jobs/{job_id}/cancel")
    if not emit(ctx, body):
        ctx.logger.success(str(body.get("message") or "Cancel requested"))


@jobs.command("wait")
@click.argument("job_id")
@click.option("--timeout", type=float, default=DEFAULT_WAIT, show_default=True)
@json_option("Print the finished job as JSON.")
@pass_context
def jobs_wait(ctx: Context, job_id: str, timeout: float) -> None:
    """Wait for a job and exit 0 if it succeeded, 1 if it failed, 4 if it still runs."""
    client = api(ctx)
    final = follow_job(client, job_id, ctx.logger, timeout=timeout, show_log=not ctx.json_output)
    finish_job(ctx, client, final, f"Job {job_id}")


# ---------------------------------------------------------------- the rest


@remote.group("backup", cls=RemoteGroup)
def backup() -> None:
    """Backups of applications."""


@backup.command("list", read_only=True)
@click.option("--app", "domain", default=None, help="Only this application's.")
@json_option("Print the backups as JSON.")
@pass_context
def backup_list(ctx: Context, domain: str | None) -> None:
    """List backups."""
    body = api(ctx).get("/backups", domain=domain)
    if emit(ctx, body):
        return
    ctx.logger.table(
        ["Backup", "Application", "Created", "Size"],
        [
            [
                str(b.get("id", "")),
                b.get("domain") or b.get("app", ""),
                short(b.get("created_at")),
                short(b.get("size")),
            ]
            for b in body.get("backups", [])
        ],
    )


@backup.command("create")
@click.argument("domain")
@click.option("--description", default="", help="A note kept with it.")
@_wait_options
@json_option("Print the finished job as JSON.")
@pass_context
def backup_create(ctx: Context, domain: str, description: str, wait: bool, timeout: float) -> None:
    """Take a backup of an application."""
    name = _domain(domain)
    _queue(
        ctx,
        "/jobs/backup",
        {"domain": name, "description": description},
        f"Backup of {name}",
        wait,
        timeout,
    )


@remote.group("env", cls=RemoteGroup)
def env() -> None:
    """An application's variables. Their values are never shown here."""


@env.command("list", read_only=True)
@click.argument("domain")
@json_option("Print the names as JSON.")
@pass_context
def env_list(ctx: Context, domain: str) -> None:
    """List the names of an application's variables; their values stay on the server."""
    body = api(ctx).get(f"/apps/{_domain(domain)}/env")
    # The answer carries the values, redacted for most credentials; they are dropped here
    # whatever they hold, so a token that may reveal them never prints one by this command.
    names = sorted(str(name) for name in body.get("variables", {}))
    if ctx.json_output:
        click.echo(json.dumps({"domain": _domain(domain), "names": names}))
        return
    for name in names:
        ctx.logger.list_item(name)


def _listing(
    group_name: str,
    route: str,
    key: str,
    columns: list[str],
    row: Callable[[dict[str, Any]], list[str]],
) -> None:
    """
    Add ``<group> list`` for a flat listing.

    Args:
        group_name: The command group's name.
        route: The route listed.
        key: The key of the list in the answer.
        columns: The table's headers.
        row: Turns an item into a table row.
    """

    @remote.group(group_name, cls=RemoteGroup)
    def group() -> None:
        pass

    group.help = f"The {group_name}s of the server."

    @group.command("list", read_only=True)
    @json_option("Print the list as JSON.")
    @pass_context
    def list_command(ctx: Context) -> None:
        body = api(ctx).get(route)
        if not emit(ctx, body):
            ctx.logger.table(columns, [row(item) for item in body.get(key, [])])

    list_command.__doc__ = f"List the {group_name}s you can see."


_listing(
    "team",
    "/teams",
    "teams",
    ["Team", "Name", "Your role"],
    lambda t: [t["slug"], t["name"], t.get("role") or "-"],
)
_listing(
    "project",
    "/projects",
    "projects",
    ["Team", "Project", "Environments"],
    lambda p: [p["team"], p["slug"], ", ".join(p.get("environments", []))],
)
_listing(
    "environment",
    "/environments",
    "environments",
    ["Team", "Project", "Environment"],
    lambda e: [e["team"], e["project"], e["slug"]],
)


@remote.command("health", read_only=True)
@json_option("Print the report as JSON.")
@pass_context
def health_command(ctx: Context) -> None:
    """Show the server's health report."""
    body = api(ctx).get("/system/health")
    if emit(ctx, body):
        return
    for item in body.get("checks", []):
        ctx.logger.check(
            str(item.get("name", "")),
            str(item.get("summary", item.get("detail", ""))),
            str(item.get("status", "info")),
        )
    if not body.get("checks"):
        ctx.logger.info(short(json.dumps(body), 300))


@remote.command("whoami", read_only=True)
@json_option("Print the answer as JSON.")
@pass_context
def whoami_command(ctx: Context) -> None:
    """Say who the token is on the server, and what it may do."""
    body = api(ctx).get("/auth/session")
    if emit(ctx, body):
        return
    account = body.get("account") or {}
    ctx.logger.key_value(
        "server", f"{body.get('hostname', '?')} (Noust {body.get('version', '?')})"
    )
    ctx.logger.key_value("account", str(account.get("username") or body.get("grant") or "token"))
    ctx.logger.key_value("role", str(body.get("role") or "-"))
    ctx.logger.key_value("permissions", f"{len(body.get('permissions', []))}")


@remote.command("api")
@click.argument("method")
@click.argument("path")
@click.option(
    "--query", "-q", multiple=True, metavar="KEY=VALUE", help="A query parameter (repeat)."
)
@click.option(
    "--data",
    "-d",
    default=None,
    metavar="JSON|@FILE|-",
    help="The JSON body: inline, from a file, or from standard input.",
)
@json_option("Print the answer on one line.")
@pass_context
def api_command(
    ctx: Context, method: str, path: str, query: tuple[str, ...], data: str | None
) -> None:
    """
    Call any route of the API and print the JSON it answers.

    \b
      noust --context prod api GET /apps
      noust --context prod api POST /jobs/update -d '{"domain": "shop.example.com"}'

    The route's permission is the token's: a call it may not make is refused by
    the server, with its reason.
    """
    params: dict[str, str] = {}
    for item in query:
        key, separator, value = item.partition("=")
        if not separator or not key:
            raise click.BadParameter(f"{item!r} is not KEY=VALUE", param_hint="--query")
        params[key] = value
    body: Any = None
    if data is not None:
        text = (
            sys.stdin.read()
            if data == "-"
            else Path(data[1:]).read_text(encoding="utf-8")
            if data.startswith("@")
            else data
        )
        try:
            body = json.loads(text)
        except ValueError as exc:
            raise click.BadParameter(f"not JSON: {exc}", param_hint="--data") from exc
    answer = api(ctx).request(method.upper(), path, query=params, body=body)
    click.echo(json.dumps(answer.body, indent=None if ctx.json_output else 2))
