# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The canonical set of notifications every renderer test and the gallery share.

One function builds a :class:`~noust.core.notifications.model.Notification`
for every code Noust sends, in either language, from the real composers, with
a fixed clock and fixed ids so a snapshot only changes when the text does. The
same function feeds the snapshot tests, the invariants, and
``scripts/notification_gallery.py`` (which imports it from here), so what is
reviewed is what is tested.

A second set, :func:`hostile`, is the same events with text nobody should
trust: markup, mentions, terminal escapes, right-to-left overrides, an emoji
in a branch name, a ten-thousand-character line.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from noust.core.exceptions import DeploymentError, NoustError, RolledBackError
from noust.core.messages import Locale
from noust.core.notifications.composers import (
    PreviewOf,
    compose_app_recovered,
    compose_app_unreachable,
    compose_approval_decided,
    compose_approval_requested,
    compose_backup_completed,
    compose_backup_failed,
    compose_backup_schedule_missing,
    compose_backup_upload_failed,
    compose_certificate,
    compose_deploy,
    compose_disk,
    compose_disk_recovered,
    compose_node_host_key_changed,
    compose_node_recovered,
    compose_node_unreachable,
    compose_observations,
    compose_restore,
    compose_server_back,
    compose_server_rebooted,
    compose_test,
    compose_unit_failure,
    compose_unit_recovered,
)
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Fact, Notification, Section, State
from noust.deployers.deploy_events import DeployEvent, DeployEventKind
from noust.deployers.helpers.hooks import compose_deploy_hook_failed
from noust.deployers.helpers.sandbox_trial import compose_sandbox_trial_failed

FIXED_TS = datetime(2026, 9, 29, 10, 45, 51, tzinfo=timezone.utc)
SERVER = "web-1"
CONSOLE = "https://console.example.com/n/web-1"


def make_journal(cycles: int = 4) -> str:
    """
    Build the journal of a unit that cannot start: a few restart cycles of the
    same seven lines, the shape of a real one (and of the 4.5 kB wall the old
    rollback message carried).

    Args:
        cycles: How many restart cycles.

    Returns:
        The journal, one line per ``\\n``.
    """
    lines: list[str] = []
    second = 12
    for cycle in range(cycles):
        pid = 48211 + cycle * 76

        def stamp(offset: int, base: int = second) -> str:
            return f"Sep 29 10:45:{base + offset:02d} web-1"

        lines += [
            f"{stamp(0)} systemd[1]: Started shop-example-com.service - Noust: shop.example.com (nextjs).",
            f"{stamp(1)} npm[{pid}]: > shop@1.4.2 start",
            f"{stamp(1)} npm[{pid}]: > next start -p 3004",
            f"{stamp(2)} npm[{pid + 16}]: Error: Could not find a production build in the '.next' directory. Try building your app with 'next build' before starting the production server.",
            f"{stamp(3)} systemd[1]: shop-example-com.service: Main process exited, code=exited, status=1/FAILURE",
            f"{stamp(3)} systemd[1]: shop-example-com.service: Failed with result 'exit-code'.",
            f"{stamp(6)} systemd[1]: shop-example-com.service: Scheduled restart job, restart counter is at {cycle + 1}.",
        ]
        second += 6
    return "\n".join(lines)


#: What a Node service that throws at startup leaves in the journal by the time
#: systemd gives up on it: the error, its stack, and systemd's own lines, which
#: are the last twelve and hide it (the real-machine harness, 3.1).
NODE_CRASH = [
    "Sep 30 09:14:02 web-1 systemd[1]: Started shop-example-com.service - Noust: shop.example.com (nodejs).",
    "Sep 30 09:14:02 web-1 node[73120]: /var/www/apps/shop-example-com/releases/20260930-091358-3f2a1bc/server.js:4",
    'Sep 30 09:14:02 web-1 node[73120]:   throw new Error("broken on purpose");',
    "Sep 30 09:14:02 web-1 node[73120]:   ^",
    "Sep 30 09:14:02 web-1 node[73120]: ",
    "Sep 30 09:14:02 web-1 node[73120]: Error: broken on purpose",
    "Sep 30 09:14:02 web-1 node[73120]:     at Object.<anonymous> (/var/www/apps/shop-example-com/releases/20260930-091358-3f2a1bc/server.js:4:9)",
    "Sep 30 09:14:02 web-1 node[73120]:     at Module._compile (node:internal/modules/cjs/loader:1364:14)",
    "Sep 30 09:14:02 web-1 node[73120]:     at Module._extensions..js (node:internal/modules/cjs/loader:1422:10)",
    "Sep 30 09:14:02 web-1 node[73120]:     at Module.load (node:internal/modules/cjs/loader:1203:32)",
    "Sep 30 09:14:02 web-1 node[73120]:     at Module._load (node:internal/modules/cjs/loader:1019:12)",
    "Sep 30 09:14:02 web-1 node[73120]:     at Function.executeUserEntryPoint [as runMain] (node:internal/modules/run_main:128:12)",
    "Sep 30 09:14:02 web-1 node[73120]:     at node:internal/main/run_main_module:28:49",
    "Sep 30 09:14:02 web-1 node[73120]: ",
    "Sep 30 09:14:02 web-1 node[73120]: Node.js v18.19.1",
    "Sep 30 09:14:02 web-1 systemd[1]: shop-example-com.service: Main process exited, code=exited, status=1/FAILURE",
    "Sep 30 09:14:02 web-1 systemd[1]: shop-example-com.service: Failed with result 'exit-code'.",
    "Sep 30 09:14:07 web-1 systemd[1]: shop-example-com.service: Scheduled restart job, restart counter is at 5.",
    "Sep 30 09:14:07 web-1 systemd[1]: Stopped shop-example-com.service - Noust: shop.example.com (nodejs).",
    "Sep 30 09:14:07 web-1 systemd[1]: shop-example-com.service: Start request repeated too quickly.",
    "Sep 30 09:14:07 web-1 systemd[1]: shop-example-com.service: Failed with result 'exit-code'.",
    "Sep 30 09:14:07 web-1 systemd[1]: Failed to start shop-example-com.service - Noust: shop.example.com (nodejs).",
]
NODE_ERROR = "Sep 30 09:14:02 web-1 node[73120]: Error: broken on purpose"


def health_evidence() -> str:
    """
    Returns:
        What the health gate writes when a release does not answer: the
        summary, the probes and the journal.
    """
    return (
        "The application did not answer the health check.\n\n"
        "Health check attempts 1-15 failed: <urlopen error [Errno 111] Connection refused>\n\n"
        f"Last lines of the journal of shop-example-com:\n{make_journal()}"
    )


def context(
    locale: Locale, *, public_url: str = CONSOLE, server: str = SERVER
) -> NotificationContext:
    """
    Args:
        locale: The language.
        public_url: The console's public URL.
        server: The server's name.

    Returns:
        The context the catalog is composed in.
    """
    return NotificationContext(locale=locale, server=server, public_url=public_url)


def _deploy_event(kind: DeployEventKind, operation: str, **overrides: Any) -> DeployEvent:
    fields: dict[str, Any] = {
        "domain": "shop.example.com",
        "deployment_id": 42,
        "trigger": "webhook",
        "commit": "a1b2c3d",
        "branch": "main",
        "commit_message": "Fix cart total",
        "release_id": "20260929-104449",
        "duration_s": 42 if kind is DeployEventKind.SUCCEEDED else 72,
        "operation": operation,
        "ts": FIXED_TS,
    }
    if kind is DeployEventKind.STARTED:
        fields.update(duration_s=None, release_id=None, commit_message=None)
    fields.update(overrides)
    return DeployEvent(kind=kind, **fields)


def _deploy_family(ctx: NotificationContext) -> dict[str, Notification]:
    out: dict[str, Notification] = {}
    rolled = RolledBackError(
        "Release 20260929-104449 did not pass its health check; release 20260928-173010 "
        "is active again",
        details=health_evidence(),
    )
    build_error = DeploymentError(
        "npm run build failed",
        details=(
            "> shop@1.4.2 build\n> next build\n\n"
            "Failed to compile.\n\n./app/cart/page.tsx\n"
            "Type error: Property 'total' does not exist on type 'Cart'.\n\n"
            "npm error Lifecycle script `build` failed with error:\nnpm error code 1"
        ),
    )
    for operation, outcomes in {
        "deploy": ("started", "succeeded", "failed", "rolled_back"),
        "update": ("started", "succeeded", "failed", "rolled_back"),
        "rollback": ("started", "succeeded", "failed", "rolled_back"),
        "activate": ("started", "succeeded", "failed", "rolled_back"),
        "migrate": ("started", "succeeded", "failed"),
    }.items():
        for outcome in outcomes:
            kind = DeployEventKind(outcome)
            overrides: dict[str, Any] = {}
            if outcome == "rolled_back":
                overrides = {"error_message": rolled.message, "error_output": rolled.details}
            elif outcome == "failed":
                error = (
                    build_error
                    if operation in ("deploy", "update")
                    else DeploymentError(
                        f"{operation} step failed",
                        details="systemctl: Job for shop-example-com.service failed",
                    )
                )
                overrides = {"error_message": error.message, "error_output": error.details}
            notification = compose_deploy(_deploy_event(kind, operation, **overrides), ctx)
            out[notification.code] = notification
    return out


def catalog(locale: Locale) -> dict[str, Notification]:
    """
    Build one notification for every code, deterministically.

    Args:
        locale: The language.

    Returns:
        Code to notification, in a stable order.
    """
    ctx = context(locale)
    out = _deploy_family(ctx)
    out["deploy.hook_failed"] = compose_deploy_hook_failed(
        "shop.example.com",
        ctx,
        command="./scripts/purge-cache.sh --all",
        output="curl: (22) The requested URL returned error: 403\npurge failed",
        deployment_id=42,
    )

    restore_error = NoustError(
        "Restore failed",
        "tar: Unexpected EOF in archive\ntar: Error is not recoverable: exiting now",
    )
    out["restore.completed"] = compose_restore(
        ok=True, domain="shop.example.com", backup_id="20260928-020000", error=None, ctx=ctx
    )
    out["restore.failed"] = compose_restore(
        ok=False,
        domain="shop.example.com",
        backup_id="20260928-020000",
        error=str(restore_error),
        ctx=ctx,
    )
    out["backup.failed"] = compose_backup_failed(
        "shop.example.com",
        str(
            NoustError(
                "Backup of shop.example.com failed",
                "tar: /var/www/apps/shop-example-com/shared/uploads: Cannot write: No space left on device",
            )
        ),
        ctx,
    )
    out["backup.upload_failed"] = compose_backup_upload_failed(
        "shop.example.com",
        "s3-eu",
        str(
            NoustError(
                "Failed to upload the backup",
                "2026/09/29 02:00:14 Failed to copy: AccessDenied: Access Denied\n\tstatus code: 403",
            )
        ),
        ctx,
    )
    out["backup.schedule_missing"] = compose_backup_schedule_missing("shop.example.com", ctx)
    out["backup.completed"] = compose_backup_completed(
        "shop.example.com",
        "20260929-020000",
        ctx,
        size_bytes=48 * 1024 * 1024,
        destinations=["s3-eu"],
    )
    out["cert.expiring"] = compose_certificate(
        "shop.example.com", ["shop.example.com", "www.shop.example.com"], "2026-10-06", 7, ctx
    )
    out["cert.expired"] = compose_certificate(
        "shop.example.com", ["shop.example.com", "www.shop.example.com"], "2026-07-11", -80, ctx
    )
    unit_journal = "\n".join(make_journal(1).splitlines()[-6:])
    out["unit.failed"] = compose_unit_failure(
        "failed", "shop-example-com", ctx, result="exit-code", exit_status=1, journal=unit_journal
    )
    out["unit.crash_loop"] = compose_unit_failure(
        "crash_loop",
        "shop-example-com",
        ctx,
        result="exit-code",
        exit_status=1,
        restarts=9,
        restarts_grew=3,
        journal=make_journal(2),
    )
    out["unit.stopped_on_failure"] = compose_unit_failure(
        "stopped_on_failure", "shop-example-com", ctx, result="signal", exit_status=9
    )
    out["unit.recovered"] = compose_unit_recovered("shop-example-com", ctx)
    out["disk.threshold"] = compose_disk(
        "/",
        91.7,
        90.0,
        ctx,
        used_bytes=int(18.34 * 1024**3),
        total_bytes=20 * 1024**3,
        free_bytes=int(1.66 * 1024**3),
    )
    out["disk.recovered"] = compose_disk_recovered("/", 71.2, ctx)
    out["test"] = compose_test("telegram", ctx)
    out["node.unreachable"] = compose_node_unreachable(
        "web-2",
        ctx,
        reason="ssh: connect to host 10.0.0.2 port 22: Connection timed out",
        address="10.0.0.2",
        since=datetime(2026, 9, 29, 10, 30, tzinfo=timezone.utc),
    )
    out["node.recovered"] = compose_node_recovered("web-2", ctx, down_for_s=754)
    out["node.host_key_changed"] = compose_node_host_key_changed(
        "web-2",
        ctx,
        address="10.0.0.2",
        pinned="SHA256:kQ3vP1xN0m0e5sYt3n2c1H0b9k2q3Wz8nq0w1d0aAbc",
        presented="SHA256:Zr9LmTq7bV2d8Yh1uJ0aXc4eF6gH3iK5oP7sRtUvWxy",
        command="noust node trust web-2",
    )
    sections = (
        Section(
            "Warning: xmrig (PID 1234)" if locale == "en" else "Aviso: xmrig (PID 1234)",
            (
                Fact("signal", "Signal" if locale == "en" else "Señal", "name-pattern"),
                Fact("user", "User" if locale == "en" else "Usuario", "nobody"),
                Fact("cpu", "CPU", "97.5%"),
                Fact(
                    "detail",
                    "Detail" if locale == "en" else "Detalle",
                    "Executable name matches the known-malware pattern 'xmrig'.",
                ),
                Fact(
                    "command",
                    "Command" if locale == "en" else "Comando",
                    "/tmp/.x/xmrig -o pool.example.org:3333",
                    mono=True,
                ),
            ),
            State.WARNING,
        ),
    )
    out["report.observations"] = compose_observations(sections, processes=1, warnings=1, ctx=ctx)
    out.update(_integration_events(ctx))
    return {
        code: _freeze(index, notification) for index, (code, notification) in enumerate(out.items())
    }


def _integration_events(ctx: NotificationContext) -> dict[str, Notification]:
    """
    The events 3.1 added after the first catalog: the server, approvals, databases.

    Kept after the others so the fixed ids of the earlier events do not move.

    Args:
        ctx: The context.

    Returns:
        One notification per code.
    """
    from noust.managers.database.backup_notifications import (
        compose_database_backup_completed,
        compose_database_backup_failed,
        compose_database_backup_upload_failed,
        compose_database_restore,
    )

    back = datetime(2026, 9, 29, 4, 2, 10, tzinfo=timezone.utc)
    out: dict[str, Notification] = {}
    out["server.rebooted"] = compose_server_rebooted(ctx, returned_at=back)
    out["server.back"] = compose_server_back(
        ctx,
        requested_by="alice",
        scheduled_for=datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc),
        took_s=130,
        returned_at=back,
    )
    approval = {
        "approval_id": 12,
        "action": "fleet.node.add",
        "call": "POST /api/nodes",
        "requester": "alice",
    }
    out["approval.requested"] = compose_approval_requested(
        ctx,
        **approval,  # type: ignore[arg-type]
        reason="Adding the new database server of the shop",
        expires_at=datetime(2026, 9, 30, 10, 45, tzinfo=timezone.utc),
    )
    out["approval.approved"] = compose_approval_decided(
        ctx,
        **approval,  # type: ignore[arg-type]
        approved=True,
        decider="bob",
        comment="Checked with the shop's owner",
        use_before=datetime(2026, 9, 29, 11, 15, tzinfo=timezone.utc),
    )
    out["approval.rejected"] = compose_approval_decided(
        ctx,
        **approval,  # type: ignore[arg-type]
        approved=False,
        decider="bob",
        comment="Not before the maintenance window",
    )
    dump_error = (
        "The dump was taken but failed its check\n"
        "  Details: pg_restore: error: could not read input file: end of file"
    )
    out["database.backup.failed"] = compose_database_backup_failed(
        "postgresql", "shop", dump_error, ctx
    )
    out["database.backup.upload_failed"] = compose_database_backup_upload_failed(
        "postgresql",
        "shop",
        "offsite-s3",
        "Upload failed\n  Details: Failed to copy: AccessDenied: Access Denied",
        ctx,
    )
    out["database.backup.completed"] = compose_database_backup_completed(
        "postgresql",
        "shop",
        "shop-20260929-104551.dump",
        ctx,
        size_bytes=48 * 1024**2,
        destinations=("offsite-s3",),
    )
    out["database.restore.completed"] = compose_database_restore(
        True, "postgresql", "shop", "shop-20260929-104551.dump", None, ctx
    )
    out["database.restore.failed"] = compose_database_restore(
        False,
        "postgresql",
        "shop",
        "shop-20260929-104551.dump",
        'Restore failed\n  Details: pg_restore: error: relation "orders" already exists',
        ctx,
    )
    out["sandbox.trial_failed"] = compose_sandbox_trial_failed(
        "shop.example.com",
        ctx,
        commit="4f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39",
        detail=(
            "npm ci failed (exit code 1)\n"
            "npm ERR! code E401\n"
            "npm ERR! 401 Unauthorized - GET https://npm.registry.local/@shop%2fui"
        ),
    )
    out["app.unreachable"] = compose_app_unreachable(
        "shop.example.com",
        ctx,
        since=datetime(2026, 10, 2, 14, 5, tzinfo=timezone.utc),
        probe="GET http://127.0.0.1:3004/ -> <urlopen error [Errno 111] Connection refused>",
    )
    out["app.recovered"] = compose_app_recovered("shop.example.com", ctx, down_for_s=754)
    return out


def _freeze(index: int, notification: Notification) -> Notification:
    """
    Args:
        index: The notification's position in the catalog.
        notification: A composed notification.

    Returns:
        The same with a fixed id and clock, so a snapshot changes only when
        the text does.
    """
    return replace(notification, id=f"00000000-0000-4000-8000-{index:012d}", ts=FIXED_TS)


#: Text nobody should trust, in the places an attacker controls.
HOSTILE_BRANCH = (
    "<b>x</b>&amp;<!channel> @everyone `tick` _under_ *star* ~tilde~ [a](http://evil.example)"
)
HOSTILE_MESSAGE = "fix\u202egnp.exe @here <@U123> <#C1> \x1b[31mred\x1b[0m emoji \U0001f680"
HOSTILE_OUTPUT = "\n".join(
    [
        "<urlopen error [Errno 111] Connection refused>",
        "```",
        "@everyone <!channel> <@U123|admin> &lt;b&gt;",
        "\x1b[31mred\x1b[0m *bold* _italic_ ~strike~ `code`",
        "\u202enoitcnuf",
        "x" * 10_000,
    ]
)


def hostile(locale: Locale) -> dict[str, Notification]:
    """
    Build events whose every free-text field is hostile.

    Args:
        locale: The language.

    Returns:
        Name to notification.
    """
    ctx = context(locale, server="w" * 64)
    failed = compose_deploy(
        _deploy_event(
            DeployEventKind.FAILED,
            "deploy",
            branch=HOSTILE_BRANCH,
            commit_message=HOSTILE_MESSAGE,
            error_message="Release did not pass <its> health & check",
            error_output=HOSTILE_OUTPUT,
        ),
        ctx,
    )
    long_output = compose_deploy(
        _deploy_event(
            DeployEventKind.FAILED,
            "update",
            error_message="npm run build failed",
            error_output="\n".join(f"line {i}: " + "word " * 40 for i in range(500)),
        ),
        ctx,
    )
    no_link = compose_deploy(
        _deploy_event(DeployEventKind.SUCCEEDED, "deploy"), context(locale, public_url="")
    )
    preview = compose_deploy(
        _deploy_event(DeployEventKind.SUCCEEDED, "deploy", domain="pr-12.shop.example.com"),
        ctx,
        preview=PreviewOf("shop.example.com", 12),
    )
    no_commit = compose_deploy(
        _deploy_event(
            DeployEventKind.SUCCEEDED, "update", commit=None, branch=None, release_id=None
        ),
        ctx,
    )
    huge_subject = compose_backup_failed("d" * 5000 + ".example.com", "boom", ctx)
    items = {
        "hostile.deploy_failed": failed,
        "hostile.long_output": long_output,
        "hostile.no_link": no_link,
        "hostile.preview": preview,
        "hostile.no_commit": no_commit,
        "hostile.huge_subject": huge_subject,
    }
    return {name: _freeze(1000 + i, n) for i, (name, n) in enumerate(items.items())}


# -- what a reader sees, per channel -----------------------------------------


_VOID = frozenset({"meta", "br", "img", "link", "hr", "input"})


class _Visible(HTMLParser):
    """Collect the text of an email's HTML a person would read."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._skip = 0
        self._stack: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "p", "tr", "div", "pre", "h1", "table"):
            self.lines.append("\n")
        if tag in _VOID:
            return
        style = dict(attrs).get("style") or ""
        hidden = tag in ("head", "title", "style") or "display:none" in style.replace(" ", "")
        self._stack.append(hidden)
        if hidden:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if self._stack and self._stack.pop():
            self._skip -= 1
        if tag in ("p", "tr", "div", "pre", "h1", "table"):
            self.lines.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.lines.append(data)


def html_text(page: str) -> str:
    """
    Args:
        page: An HTML document.

    Returns:
        The text a reader sees: no head, no hidden preheader, no markup.
    """
    parser = _Visible()
    parser.feed(page)
    return "".join(parser.lines)


_TAG = re.compile(r"<[^>]+>")


def telegram_text(payload: dict[str, Any]) -> str:
    """
    Args:
        payload: A ``sendMessage`` body.

    Returns:
        What Telegram shows: the text without tags, entities decoded.
    """
    text = payload["text"]
    if payload.get("parse_mode") == "HTML":
        text = html.unescape(_TAG.sub("", text))
    return text


def json_strings(value: Any) -> list[str]:
    """
    Args:
        value: Any JSON value.

    Returns:
        Every string in it, in order.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in json_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in json_strings(v)]
    return []


def dump(value: Any) -> str:
    """
    Args:
        value: A JSON value.

    Returns:
        The stable text a snapshot stores.
    """
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
