# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An application's tabs (deployments, logs, metrics, environment, ...) and a deploy's actions
(rebuild its commit, roll back to it, an update with nothing new)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .machine import FAILED_JOURNAL_LINES, JOURNAL_LINES, Unit
from .sandbox import Sandbox

# ---------------------------------------------------------------------------
# Console: an application's tabs (deployments, logs, metrics, environment,
# diagnose, settings) need applications whose trees are real, in the sandbox:
# one on the release layout with releases to roll back to, one in place whose
# update runs the real update sequence (slowly enough to watch it stream), and
# two in place that can be migrated to releases. Their journals stream over
# /ws/logs from a model of the machine, since no real journalctl may run.
# ---------------------------------------------------------------------------

#: On the release layout: three releases on disk, one failed build that was
#: removed, resource limits, webhook deliveries and a long deployment history.
TABS_RELEASE_APP = "tienda.example.org"

#: In place, with a real tree: its .env is read and written, and an update runs
#: the real update sequence with the build output streamed a line at a time.
TABS_LIVE_APP = "pedidos.example.org"

#: In place, to be migrated to releases. A migration cannot be undone through
#: the API, so the E2E suite migrates one per theme project.
TABS_MIGRATE_APPS = ("blog.example.org", "docs.example.org")

#: Seconds between the lines of a streamed npm install or build of the live app.
TABS_BUILD_LINE_DELAY = 0.35

#: Seconds between the request lines the modelled journal appends while followed.
TABS_JOURNAL_TICK = 1.5

#: What npm prints while installing and building the live app.
_TABS_NPM_INSTALL = (
    "npm warn deprecated inflight@1.0.6: This module is not supported, and leaks memory.",
    "npm warn deprecated glob@7.2.3: Glob versions prior to v9 are no longer supported",
    "",
    "added 214 packages, and audited 215 packages in 6s",
    "",
    "38 packages are looking for funding",
    "  run `npm fund` for details",
    "",
    "found 0 vulnerabilities",
)
_TABS_NPM_BUILD = (
    "",
    "> pedidos@2.4.0 build",
    "> tsc -p tsconfig.build.json && node scripts/copy-assets.mjs",
    "",
    "Compiling 142 files...",
    "Copied 18 assets to dist/public",
    "Build finished in 4.2s",
)

#: What the modelled journal appends while the Logs tab follows an app.
_TABS_REQUESTS = (
    "GET / 200 in 38ms",
    "GET /api/orders?page=1 200 in 41ms",
    "POST /api/cart 201 in 87ms",
    "GET /_next/static/chunks/app/page-4f1c.js 200 in 2ms",
    "GET /api/health 200 in 3ms",
    "warn: slow query on orders (1204ms): SELECT * FROM orders WHERE status = $1",
    "GET /checkout 200 in 112ms",
    "Error: connect ECONNREFUSED 127.0.0.1:6379 (redis cache unavailable, serving from database)",
    "GET /api/products/88 200 in 19ms",
    "POST /api/checkout 200 in 342ms",
)


def _tabs_serve_ok() -> int:
    """
    Answer HTTP on a free loopback port, as an application that is up would.

    The health gate probes ``http://127.0.0.1:<port>/`` after activating a
    release or migrating; with nothing listening, every rollback and every
    migration would be undone as unhealthy.

    Returns:
        The port.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Ok(BaseHTTPRequestHandler):
        """Answers 200 to anything."""

        def do_GET(self) -> None:
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self) -> None:
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Ok)
    threading.Thread(target=server.serve_forever, name="tabs-health", daemon=True).start()
    return int(server.server_address[1])


def _tabs_node_tree(root: Path, name: str, *, env: str, uploads: bool) -> None:
    """
    Write a small Node application: the files an update and a migration read.

    Args:
        root: Where the tree goes.
        name: Its package name.
        env: The ``.env`` it holds, verbatim.
        uploads: Whether it has written user uploads into its own tree.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "2.4.0",
                "private": True,
                "scripts": {"build": "tsc -p tsconfig.build.json", "start": "node dist/server.js"},
                "dependencies": {"express": "^4.21.0", "pg": "^8.13.0"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    (root / "dist").mkdir(exist_ok=True)
    (root / "dist" / "server.js").write_text(
        "require('http').createServer((q, s) => s.end('ok')).listen(process.env.PORT);\n",
        encoding="utf-8",
    )
    env_file = root / ".env"
    env_file.write_text(env, encoding="utf-8")
    env_file.chmod(0o600)
    if uploads:
        for relative, content in (
            ("uploads/2026/09/invoice-1024.pdf", "%PDF-1.7 seeded invoice\n"),
            ("uploads/2026/09/logo.png", "seeded image\n"),
            ("storage/sessions/sess_4f1c", "seeded session\n"),
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        # The orders it keeps in a SQLite file of its own: the Database tab lists it and can
        # check it, against the real file.
        database = sqlite3.connect(root / "storage" / "orders.db")
        try:
            database.execute(
                "CREATE TABLE orders (id INTEGER PRIMARY KEY, total REAL, placed TEXT)"
            )
            database.execute("CREATE INDEX orders_placed ON orders (placed)")
            database.executemany(
                "INSERT INTO orders (total, placed) VALUES (?, ?)",
                [(round(9.5 + n * 1.25, 2), f"2026-09-{1 + n % 28:02d}") for n in range(60)],
            )
            database.commit()
        finally:
            database.close()


def _tabs_register(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    app: Any,
    *,
    working_directory: Path,
) -> None:
    """
    Record an application, its unit and its site, and model the unit as running.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        app: The application row to create.
        working_directory: Where its unit runs from.
    """
    from noust.core.store import Service, Site
    from noust.core.utils import domain_to_app_name
    from noust.managers.service_manager import UNIT_MARKER

    created = store.create_app(app)
    unit = domain_to_app_name(app.domain)
    unit_file = sandbox.systemd_dir / f"{unit}.service"
    command = "/usr/bin/node dist/server.js"
    unit_file.write_text(
        f"# {UNIT_MARKER}\n"
        "[Unit]\n"
        f"Description={app.domain}\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        "User=www-data\n"
        f"WorkingDirectory={working_directory}\n"
        f"ExecStart={command}\n"
        f"Environment=PORT={app.port}\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n",
        encoding="utf-8",
    )
    store.create_service(
        Service(
            app_id=created.id,
            name=unit,
            unit_file=str(unit_file),
            working_directory=str(working_directory),
            command=command,
            status="active",
            enabled=True,
            port=app.port,
        )
    )
    site_file = sandbox.etc / "nginx" / "sites-available" / app.domain
    site_file.write_text(
        "server {\n"
        f"    server_name {app.domain};\n"
        "    listen 80;\n"
        "    location / {\n"
        f"        proxy_pass http://127.0.0.1:{app.port};\n"
        "        proxy_set_header Host $host;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    store.create_site(
        Site(
            app_id=created.id,
            domain=app.domain,
            webserver="nginx",
            config_path=str(site_file),
            enabled=True,
            proxy_port=app.port,
            ssl_enabled=True,
        )
    )
    units[unit] = Unit(
        active="active",
        enabled=True,
        pid=43000 + (created.id or 0),
        since=datetime.now() - timedelta(hours=2, minutes=(created.id or 0) * 3),
    )
    ports[unit] = int(app.port)
    domains[unit] = app.domain


def _tabs_stamped(start: datetime, steps: Sequence[tuple[float, str]]) -> str:
    """
    Render a captured build log the way :class:`DeploymentRecorder` writes it.

    Args:
        start: When the deployment started.
        steps: Seconds after the start, and the line.

    Returns:
        The log, one ``[YYYY-MM-DD HH:MM:SS] line`` per line.
    """
    return "".join(
        f"[{(start + timedelta(seconds=offset)).strftime('%Y-%m-%d %H:%M:%S')}] {line}\n"
        for offset, line in steps
    )


def _tabs_release_log(
    kind: str, *, source: str, commit: str, release_id: str, previous: str | None, port: int
) -> tuple[list[tuple[float, str]], str | None]:
    """
    The captured log of one deploy of the release app, and its error when it failed.

    Args:
        kind: ``ok``, ``reused`` (same lockfile), ``build`` (a type error) or
            ``health`` (the release never answered).
        source: The repository.
        commit: The commit built.
        release_id: The release it built.
        previous: The release that was serving.
        port: The application's port.

    Returns:
        The lines with their offsets in seconds, and the error text or None.
    """
    lines: list[tuple[float, str]] = [
        (0, "[1/9] Fetching source into a new release..."),
        (0.4, f"      → Source: {source} (main)"),
        (2.1, f"      → HEAD is now at {commit}"),
        (2.3, f"      → Release: {release_id}"),
        (2.4, "      → Linked .env to shared/.env"),
        (2.4, "      → Linked uploads to shared/uploads"),
    ]
    if kind == "reused":
        lines += [
            (2.6, "[2/9] Installing dependencies..."),
            (3.9, f"      → Dependencies reused from {previous}: the lockfile did not change"),
        ]
        at = 4.0
    else:
        lines += [
            (2.6, "[2/9] Installing dependencies..."),
            (2.7, "      → Running: npm ci"),
            (15.8, "added 812 packages, and audited 813 packages in 13s"),
            (15.9, "found 0 vulnerabilities"),
        ]
        at = 16.0
    lines += [
        (at, "[3/9] Building application..."),
        (at + 0.1, "      → Running: npm run build"),
        (at + 1.4, "   ▲ Next.js 15.2.4"),
        (at + 1.5, "   Creating an optimized production build ..."),
    ]
    if kind == "build":
        lines += [
            (at + 24.2, "Failed to compile."),
            (at + 24.2, ""),
            (at + 24.2, "./app/checkout/page.tsx:42:7"),
            (at + 24.2, "Type error: Property 'total' does not exist on type 'Order'."),
            (at + 24.3, "✗ Deployment failed: npm run build exited with status 1"),
            (
                at + 24.4,
                f"      → Removed release {release_id}; {previous or 'nothing'} keeps serving",
            ),
        ]
        return lines, (
            "npm run build exited with status 1\n"
            "./app/checkout/page.tsx:42:7\n"
            "Type error: Property 'total' does not exist on type 'Order'."
        )
    at += 27.0
    lines += [
        (at - 5.6, " ✓ Compiled successfully in 21.4s"),
        (at - 1.2, " ✓ Generating static pages (38/38)"),
        (at, "[4/9] Setting permissions..."),
        (at + 0.8, "[5/9] Creating site configuration..."),
        (at + 1.0, "[6/9] Obtaining SSL certificate..."),
        (at + 1.1, "      → The existing certificate keeps serving what it covers"),
        (at + 1.3, "[7/9] Creating systemd service..."),
        (at + 1.6, "[8/9] Activating release..."),
        (
            at + 1.7,
            f"      → Activated release {release_id}"
            + (f" (was {previous})" if previous is not None else ""),
        ),
        (at + 1.8, f"      → Checking: http://127.0.0.1:{port}/"),
    ]
    if kind == "health":
        lines += [
            (at + 31.8, f"⚠ Release {release_id} did not pass its health check"),
            (at + 31.9, f"      → Going back to release {previous}"),
            (at + 34.0, f"✗ Deployment failed: release {release_id} did not answer"),
        ]
        return lines, (
            f"Release {release_id} did not pass its health check: "
            f"http://127.0.0.1:{port}/ did not answer after 15 attempts "
            "([Errno 111] Connection refused)\n"
            "-- journal --\n"
            "Error: Cannot find module '/var/www/apps/tienda-example-org/current/.next/standalone/server.js'\n"
            "tienda-example-org.service: Main process exited, code=exited, status=1/FAILURE"
        )
    lines += [
        (at + 3.9, "[9/9] Health check..."),
        (at + 4.0, f"✓ Release {release_id} answered 200 in 84 ms"),
        (at + 4.1, "✓ Deployed tienda.example.org"),
    ]
    return lines, None


def _tabs_inplace_log(
    domain: str, *, commit: str, source: str, port: int
) -> list[tuple[float, str]]:
    """
    The captured log of a successful in-place deploy of a Node application.

    Args:
        domain: The application.
        commit: The commit deployed.
        source: The repository.
        port: The application's port.

    Returns:
        The lines with their offsets in seconds.
    """
    return [
        (0, "[1/8] Fetching source code..."),
        (0.3, f"      → Source: {source}"),
        (1.8, f"      → HEAD is now at {commit}"),
        (2.0, "[2/8] Installing dependencies..."),
        (2.1, "      → Running: npm ci"),
        (9.4, "added 214 packages, and audited 215 packages in 7s"),
        (9.6, "[3/8] Building application..."),
        (9.7, "      → Running: npm run build"),
        (14.1, "Build finished in 4.2s"),
        (14.2, "[4/8] Setting permissions..."),
        (14.9, "[5/8] Creating site configuration..."),
        (15.1, "[6/8] Obtaining SSL certificate..."),
        (15.2, "      → The existing certificate keeps serving what it covers"),
        (15.4, "[7/8] Creating systemd service..."),
        (15.9, "[8/8] Starting application..."),
        (18.2, f"      → Checking: http://127.0.0.1:{port}/"),
        (18.4, f"✓ Deployed {domain}"),
    ]


def _tabs_deployment(
    store: Any,
    domain: str,
    *,
    trigger: str,
    commit: str,
    started: datetime,
    lines: Sequence[tuple[float, str]],
    status: str,
    error: str | None = None,
) -> int:
    """
    Record one finished deployment at a moment in the past, with its captured log.

    Args:
        store: The seeded store.
        domain: The application.
        trigger: ``cli``, ``panel`` or ``webhook``.
        commit: The commit deployed.
        started: When it started.
        lines: Its log, as offsets in seconds and lines; the last offset is
            when it finished.
        status: How it ended.
        error: What went wrong, verbatim, when it failed.

    Returns:
        The deployment id.
    """
    deployment_id = store.record_deployment_start(
        domain, trigger, git_commit=commit, git_branch="main"
    )
    seconds = max(offset for offset, _ in lines) if lines else 0.0
    log_dir = store.db_path.parent / "deploy-logs" / domain
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = log_dir / f"{deployment_id}.log"
    log_path.write_text(_tabs_stamped(started, lines), encoding="utf-8")
    log_path.chmod(0o600)
    # finish_deployment stamps "now"; history is written with the times it had.
    with store._transaction() as cursor:
        cursor.execute(
            "UPDATE deployments SET status = ?, error = ?, started_at = ?, finished_at = ?, "
            "duration_s = ?, log_path = ? WHERE id = ?",
            (
                status,
                error,
                started.isoformat(),
                (started + timedelta(seconds=seconds)).isoformat(),
                round(seconds, 1),
                str(log_path),
                deployment_id,
            ),
        )
    return int(deployment_id)


@dataclass
class _TabsApp:
    """
    What the first seeding pass decided for one of the tabs' applications.

    Attributes:
        port: The port its health listener holds.
        deploys: Its deployments, oldest first: when, commit, kind, status and
            the release each built.
    """

    port: int
    deploys: list[tuple[datetime, str, str, str, str]] = field(default_factory=list)

    @property
    def starts(self) -> list[datetime]:
        """When each deployment started."""
        return [started for started, *_ in self.deploys]


#: The release app's history, oldest first: days ago, trigger, commit, kind, status.
_TABS_RELEASE_HISTORY = (
    (27.2, "webhook", "3c1e9a0", "ok", "success"),
    (24.1, "webhook", "7f2d4b1", "ok", "success"),
    (21.0, "panel", "7f2d4b1", "reused", "success"),
    (18.3, "webhook", "a94c0e2", "build", "failed"),
    (18.28, "webhook", "b1e7f93", "ok", "success"),
    (15.4, "cli", "b1e7f93", "reused", "success"),
    (12.2, "webhook", "c5d2a61", "ok", "rolled_back"),
    (9.1, "webhook", "d08e4f7", "health", "failed"),
    (7.3, "webhook", "e3b9c12", "ok", "success"),
    (5.2, "webhook", "f41a8d3", "ok", "success"),
    (3.1, "cli", "0c7e5b9", "ok", "success"),
    (1.05, "webhook", "19d3f6e", "reused", "success"),
    (0.09, "webhook", "2a8b7c4", "ok", "success"),
)

#: The releases of the release app still on disk, by commit.
_TABS_ON_DISK = frozenset({"0c7e5b9", "19d3f6e", "2a8b7c4"})

#: The in-place apps' histories, oldest first: days ago, trigger, commit.
_TABS_INPLACE_HISTORY: dict[str, tuple[tuple[float, str, str], ...]] = {
    TABS_LIVE_APP: (
        (20.4, "cli", "4e1f2a7"),
        (6.2, "panel", "8b3c9d0"),
        (2.1, "webhook", "5a7e1c3"),
    ),
    **dict.fromkeys(TABS_MIGRATE_APPS, ((41.0, "cli", "9d4e2b8"),)),
}


def _tabs_release_id(started: datetime, commit: str) -> str:
    """
    Args:
        started: When the release was built.
        commit: The commit it was built from.

    Returns:
        Its id, the way :class:`ReleaseManager` names a release directory.
    """
    from datetime import timezone

    return f"{started.astimezone(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{commit}"


def seed_app_tabs_history(store: Any) -> dict[str, _TabsApp]:
    """
    Record the tabs' applications' deployment history, before anything else is seeded.

    Their history lies days in the past, and the API orders deployments by id:
    recorded after :func:`seed_console_state`'s deploys of "now", they would
    list as the newest on the overview. Recorded first, ids follow time. Each
    app's health listener starts here too, since its port is in the logs.

    Args:
        store: The store.

    Returns:
        Per domain, its port and its deployments.
    """
    now = datetime.now()
    apps: dict[str, _TabsApp] = {}

    source = "https://github.com/example-org/tienda.git"
    release = _TabsApp(port=_tabs_serve_ok())
    previous: str | None = None
    for days, trigger, commit, kind, status in _TABS_RELEASE_HISTORY:
        started = now - timedelta(days=days)
        rid = _tabs_release_id(started, commit)
        lines, error = _tabs_release_log(
            kind, source=source, commit=commit, release_id=rid, previous=previous, port=release.port
        )
        deployment_id = _tabs_deployment(
            store,
            TABS_RELEASE_APP,
            trigger=trigger,
            commit=commit,
            started=started,
            lines=lines,
            status=status,
            error=error,
        )
        # What the recorder notes once the fetch has named the release: a deployment on
        # releases can be gone back to while its release is on disk and not serving.
        store.annotate_deployment(deployment_id, release_id=rid)
        release.deploys.append((started, commit, kind, status, rid))
        if status == "success":
            previous = rid
    apps[TABS_RELEASE_APP] = release

    from noust.core.utils import domain_to_app_name

    for domain, history in _TABS_INPLACE_HISTORY.items():
        seeded = _TabsApp(port=_tabs_serve_ok())
        for days, trigger, commit in history:
            started = now - timedelta(days=days)
            _tabs_deployment(
                store,
                domain,
                trigger=trigger,
                commit=commit,
                started=started,
                lines=_tabs_inplace_log(
                    domain,
                    commit=commit,
                    source="https://github.com/example-org/" + domain_to_app_name(domain),
                    port=seeded.port,
                ),
                status="success",
            )
            seeded.deploys.append((started, commit, "ok", "success", ""))
        apps[domain] = seeded
    seed_deploy_actions_history(store, apps)
    return apps


#: Commit subjects for the tabs apps' seeded history, keyed by the short hash
#: each row already carries. Real deploys and updates learn theirs from
#: ``git log -1 --format=%s`` on a real checkout (see
#: ``_commit_message_for_recording``); the fake ``git`` handler above answers
#: that for anything the sandbox actually runs. The tabs' history predates the
#: sandbox entirely - :func:`_tabs_deployment` writes each row straight
#: through the store - so nothing ever asks git for theirs.
_TABS_COMMIT_MESSAGES: dict[str, str] = {
    "3c1e9a0": "Add product search filters",
    "7f2d4b1": "Fix checkout total rounding",
    "a94c0e2": "Add order CSV export",
    "b1e7f93": "Bump Next.js to 15.2",
    "c5d2a61": "Rework the cart summary layout",
    "d08e4f7": "Add Stripe webhook retries",
    "e3b9c12": "Cache product listings",
    "f41a8d3": "Add a wishlist page",
    "0c7e5b9": "Tune image loader sizes",
    "19d3f6e": "Update footer legal links",
    "2a8b7c4": "Add gift card redemption",
    "4e1f2a7": "Add delivery tracking page",
    "8b3c9d0": "Fix invoice PDF totals",
    "5a7e1c3": "Add an order status webhook",
    "9d4e2b8": "Initial import",
}


def seed_deploylinks_commit_messages(store: Any) -> None:
    """
    Back-fill ``commit_message`` on the tabs apps' seeded deployment history.

    :func:`_tabs_deployment` records each row directly through the store, not
    through the deploy pipeline that would normally learn the subject line
    from git and annotate it: without this, every one of those rows would be
    the one case in the modelled machine where a deployment has a commit but
    no message, unlike a deploy or update actually run through the sandbox.
    This calls the same store method the real pipeline does, keyed by the
    commit each row was already written with.

    Args:
        store: The seeded store, after :func:`seed_app_tabs_history` ran.
    """
    for domain in (TABS_RELEASE_APP, TABS_LIVE_APP, *TABS_MIGRATE_APPS):
        for record in store.list_deployments(domain=domain, limit=100):
            if record.id is None or record.commit_message is not None:
                continue
            message = _TABS_COMMIT_MESSAGES.get(record.git_commit or "")
            if message is not None:
                store.annotate_deployment(record.id, commit_message=message)


def _tabs_release_app(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    seeded: _TabsApp,
) -> None:
    """
    Seed the release-layout application: its tree, its releases, its unit and site.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        seeded: Its port and history, from :func:`seed_app_tabs_history`.
    """
    from datetime import timezone

    from noust.core.store import App, ReleaseRecord
    from noust.core.utils import domain_to_app_name

    domain = TABS_RELEASE_APP
    root = sandbox.apps_dir / domain_to_app_name(domain)
    port = seeded.port
    env = (
        "NODE_ENV=production\n"
        f"PORT={port}\n"
        "DATABASE_URL=postgres://tienda:K9v2xQ7mLp@127.0.0.1:5432/tienda\n"
        "REDIS_URL=redis://127.0.0.1:6379/2\n"
        "SESSION_SECRET=6b1f0e4c2d9a8b7e5f3c1a0d9e8f7a6b\n"
        "STRIPE_SECRET_KEY=sk_l" + "ive_51Hx8exampleShopSeeded\n"
        "STRIPE_PUBLISHABLE_KEY=pk_l" + "ive_51Hx8exampleShopSeeded\n"
        "NEXT_PUBLIC_SITE_URL=https://tienda.example.org\n"
        "MAIL_FROM=Example Shop <orders@example.org>\n"
        "LOG_LEVEL=info\n"
    )
    shared = root / "shared"
    (shared / "uploads" / "products").mkdir(parents=True, exist_ok=True)
    (shared / "uploads" / "products" / "88.webp").write_text("seeded image\n", encoding="utf-8")
    (shared / ".env").write_text(env, encoding="utf-8")
    (shared / ".env").chmod(0o600)
    (root / "repo").mkdir(parents=True, exist_ok=True)
    (root / "releases").mkdir(parents=True, exist_ok=True)

    app = App(
        domain=domain,
        app_type="nextjs",
        source="https://github.com/example-org/tienda.git",
        branch="main",
        port=port,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
        layout="releases",
        keep_releases=5,
        persistent_paths=["uploads"],
        memory_max_mb=512,
        cpu_quota_percent=150,
        tasks_max=256,
    )
    _tabs_register(sandbox, store, units, ports, domains, app, working_directory=root / "current")
    stored = store.get_app(domain)
    store.set_webhook_secret(domain, "seeded-webhook-secret-not-shown")
    if stored is None or stored.id is None:
        return

    serving: str | None = None
    for number, (started, commit, kind, status, rid) in enumerate(seeded.deploys, start=1):
        if commit in _TABS_ON_DISK and status == "success":
            release_dir = root / "releases" / rid
            release_dir.mkdir(parents=True, exist_ok=True)
            (release_dir / "package.json").write_text(
                json.dumps({"name": "tienda", "version": f"3.{number}.0"}) + "\n",
                encoding="utf-8",
            )
            (release_dir / ".next").mkdir(exist_ok=True)
            (release_dir / ".next" / "BUILD_ID").write_text(commit + "\n", encoding="utf-8")
            for name, target in (
                (".env", "../../shared/.env"),
                ("uploads", "../../shared/uploads"),
            ):
                link = release_dir / name
                if not link.is_symlink():
                    link.symlink_to(target)
        if (status == "success" and commit in _TABS_ON_DISK) or kind == "health":
            store.record_release(
                ReleaseRecord(
                    id=rid,
                    app_id=stored.id,
                    git_commit=commit,
                    created_at=started.astimezone(timezone.utc).isoformat(),
                    activated_at=None
                    if kind == "health"
                    else (started + timedelta(minutes=1)).astimezone(timezone.utc).isoformat(),
                    status="failed" if kind == "health" else "superseded",
                    path=str(root / "releases" / rid),
                )
            )
        if status == "success":
            serving = rid

    if serving is not None:
        (root / "current").symlink_to(Path("releases") / serving)
        store.mark_release_active(stored.id, serving)


def _tabs_inplace_app(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    domain: str,
    seeded: _TabsApp,
) -> Path:
    """
    Seed an application deployed in place, with a real tree in the sandbox.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        domain: The application.
        seeded: Its port and history, from :func:`seed_app_tabs_history`.

    Returns:
        Its directory.
    """
    from noust.core.store import App
    from noust.core.utils import domain_to_app_name

    name = domain_to_app_name(domain)
    root = sandbox.apps_dir / name
    # Recorded as a local directory: an update of a tree that is not a git
    # checkout fetches its recorded source again, which here is this copy.
    source_dir = sandbox.root / "sources" / name
    port = seeded.port
    env = (
        "# Written by noust env configure\n"
        "NODE_ENV=production\n"
        f"PORT={port}\n"
        f"DATABASE_URL=postgres://{name.split('-')[0]}:Wm4p8Zr2@127.0.0.1:5432/{name.split('-')[0]}\n"
        "JWT_SECRET=2f7c9e1a4b6d8f0a3c5e7b9d1f2a4c6e\n"
        f"PUBLIC_URL=https://{domain}\n"
        "SMTP_HOST=smtp.example.org\n"
        "SMTP_PASSWORD=\n"
        "FEATURE_FLAGS=checkout-v2,fast-search\n"
    )
    for tree in (root, source_dir):
        _tabs_node_tree(tree, name.split("-")[0], env=env, uploads=tree == root)
    app = App(
        domain=domain,
        app_type="nodejs",
        source=str(source_dir),
        branch="main",
        port=port,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
    )
    _tabs_register(sandbox, store, units, ports, domains, app, working_directory=root)
    return root


def _tabs_metrics(domain: str, deploys: Sequence[datetime], *, base_mb: float) -> None:
    """
    Write thirty days of CPU, memory, disk and network history for an application.

    The collector reads an app's cgroup, which no sandboxed unit has, so the
    history the Metrics tab draws is written here: denser the more recent,
    as the store's own tiers keep it, with memory falling back after every
    deploy (the process restarted) and a CPU burst while each one warmed up.

    Args:
        domain: The application.
        deploys: When it was deployed.
        base_mb: Its resident memory just after a start, in MB.
    """
    import math

    from noust.web.metrics_collector import get_metrics_store

    store = get_metrics_store()
    now = int(time.time())
    cpu_name, mem_name = f"app.{domain}.cpu.percent", f"app.{domain}.mem.bytes"
    read_name, write_name = f"app.{domain}.io.read_bytes_s", f"app.{domain}.io.write_bytes_s"
    rx_name, tx_name = f"app.{domain}.net.rx_bytes_s", f"app.{domain}.net.tx_bytes_s"
    stamps = [
        *range(now - 30 * 86_400, now - 86_400, 1_800),
        *range(now - 86_400, now - 3_600, 120),
        *range(now - 3_600, now, 10),
    ]
    deploy_times = sorted(int(moment.timestamp()) for moment in deploys)
    for stamp in stamps:
        since = [stamp - t for t in deploy_times if t <= stamp]
        age = min(since) if since else 30 * 86_400
        daily = math.sin((stamp % 86_400) / 86_400 * 2 * math.pi - math.pi / 2)
        wobble = math.sin(stamp / 977.0) * 0.6 + math.sin(stamp / 331.0) * 0.4
        cpu = 3.2 + 2.4 * daily + 1.1 * wobble + (38.0 * math.exp(-age / 240.0))
        memory_mb = base_mb + min(age / 3_600, 96) * 0.9 + 6 * wobble
        # Disk and network follow the day's traffic, and a deploy reads the new release in.
        warm = math.exp(-age / 240.0)
        store.record_many(
            [
                (cpu_name, max(0.1, cpu)),
                (mem_name, memory_mb * 1024 * 1024),
                (read_name, max(0.0, 30_000 + 22_000 * daily + 9_000 * wobble + 4_000_000 * warm)),
                (write_name, max(0.0, 85_000 + 60_000 * daily + 20_000 * wobble + 600_000 * warm)),
                (rx_name, max(0.0, 210_000 + 150_000 * daily + 40_000 * wobble + 1_800_000 * warm)),
                (tx_name, max(0.0, 540_000 + 380_000 * daily + 90_000 * wobble)),
            ],
            ts=stamp,
        )
    store.consolidate(now=now)


def _tabs_slow_live_builds(root: Path) -> None:
    """
    Stream npm's output for the live app a line at a time, as a real build does.

    The runner answers every command at once; an update would be over before
    the console could show it running. Only npm under the live app's tree is
    slowed down; everything else is answered as before.

    Args:
        root: The live app's directory.
    """
    from noust.core.runner import CommandResult, get_runner

    runner = get_runner()
    original = runner.stream

    def stream(argv: Sequence[str], *, on_line: Any, cwd: Path | None = None, **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        inside = cwd is not None and (Path(cwd) == root or root in Path(cwd).parents)
        if not (inside and "npm" in args):
            return original(argv, on_line=on_line, cwd=cwd, **kwargs)
        runner.calls.append(args)
        output = _TABS_NPM_BUILD if "build" in args else _TABS_NPM_INSTALL
        for line in output:
            time.sleep(TABS_BUILD_LINE_DELAY)
            on_line(line)
        return CommandResult(argv=args, exit_code=0, stdout="\n".join(output) + "\n", stderr="")

    runner.stream = stream  # type: ignore[method-assign]


class _TabsJournalFollow:
    """
    ``journalctl -u <unit> -f`` over the modelled machine, as asyncio sees a process.

    The logs WebSocket spawns journalctl itself; here it gets this instead: a
    backlog in ``short-iso`` form from the unit's state, then a request line
    every :data:`TABS_JOURNAL_TICK` seconds while the unit is active.
    """

    def __init__(
        self,
        args: Sequence[str],
        units: dict[str, Unit],
        domains: dict[str, str],
        ports: dict[str, int],
    ) -> None:
        """
        Args:
            args: The journalctl argv.
            units: The modelled units.
            domains: Each unit's domain.
            ports: Each unit's port.
        """
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self._unit = args[args.index("-u") + 1].removesuffix(".service") if "-u" in args else ""
        backlog = int(args[args.index("-n") + 1]) if "-n" in args else 10
        self._state = units.get(self._unit)
        self._domain = domains.get(self._unit, self._unit)
        self._port = ports.get(self._unit, 3000)
        self._count = 0
        for line in self._backlog()[-backlog:]:
            self.stdout.feed_data(f"{line}\n".encode())
        self._task: asyncio.Task[None] | None = (
            asyncio.get_running_loop().create_task(self._follow())
            if "-f" in args and self._state is not None and self._state.active == "active"
            else None
        )

    def _line(self, moment: datetime, message: str) -> str:
        pid = self._state.pid if self._state and self._state.pid else 1
        stamp = moment.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        return f"{stamp} acme {self._unit}[{pid}]: {message}"

    def _backlog(self) -> list[str]:
        failed = self._state is not None and self._state.active == "failed"
        template = FAILED_JOURNAL_LINES if failed else JOURNAL_LINES
        now = datetime.now()
        start = now - timedelta(minutes=70)
        lines = [
            self._line(
                start + timedelta(seconds=i),
                text.format(unit=self._unit, domain=self._domain, port=self._port),
            )
            for i, text in enumerate(template)
        ]
        if not failed:
            for i in range(96):
                moment = start + timedelta(seconds=40 * (i + 1))
                lines.append(self._line(moment, _TABS_REQUESTS[i % len(_TABS_REQUESTS)]))
        return lines

    async def _follow(self) -> None:
        while self.returncode is None:
            await asyncio.sleep(TABS_JOURNAL_TICK)
            message = _TABS_REQUESTS[(self._count * 3) % len(_TABS_REQUESTS)]
            self._count += 1
            self.stdout.feed_data(f"{self._line(datetime.now(), message)}\n".encode())

    def terminate(self) -> None:
        """Stop following, as SIGTERM would."""
        if self.returncode is None:
            self.returncode = -15
            if self._task is not None:
                self._task.cancel()
            self.stdout.feed_eof()

    def kill(self) -> None:
        """Stop following, as SIGKILL would."""
        self.terminate()

    async def wait(self) -> int:
        """
        Returns:
            The exit status.
        """
        return self.returncode if self.returncode is not None else 0


def _tabs_journal_model(
    units: dict[str, Unit], domains: dict[str, str], ports: dict[str, int]
) -> None:
    """
    Answer the logs WebSocket's ``journalctl -f`` from the modelled machine.

    Every other spawn is still refused by :func:`forbid_real_processes`.

    Args:
        units: The modelled units.
        domains: Each unit's domain.
        ports: Each unit's port.
    """
    refuse = asyncio.create_subprocess_exec

    async def spawn(*argv: Any, **kwargs: Any) -> Any:
        args = [str(a) for a in argv]
        if args and args[0] == "journalctl":
            return _TabsJournalFollow(args, units, domains, ports)
        return await refuse(*argv, **kwargs)

    asyncio.create_subprocess_exec = spawn  # type: ignore[assignment]


def _exposed_database_listeners() -> list[str]:
    """
    List, as ``ss -ltnpH`` prints them, the ports databases are opened on.

    Opening a database to the network starts a socket unit that systemd listens on, and
    Noust checks the kernel afterwards that something does. The sandbox starts no unit, so
    the answer is read from the socket files Noust wrote into the sandboxed unit directory.

    Returns:
        One line per ``noust-dbexpose-*.socket`` whose ``ListenStream`` names a port.
    """
    from noust.core import paths
    from noust.managers.service_manager import ServiceManager

    lines: list[str] = []
    directory = Path(ServiceManager.SYSTEMD_DIR)
    for path in sorted(directory.glob(f"{paths.DB_EXPOSE_UNIT_PREFIX}*.socket")):
        for text in path.read_text(encoding="utf-8").splitlines():
            if text.startswith("ListenStream="):
                lines.append(f"LISTEN 0 128 {text.removeprefix('ListenStream=')} 0.0.0.0:* ")
    return lines


def _tabs_diagnose_model(units: dict[str, Unit], ports: dict[str, int]) -> None:
    """
    Answer the questions ``noust diagnose`` asks that the runner could not.

    ``ss -ltnpH`` lists the port of every active modelled unit, owned by its
    main PID, ``systemctl show -p A,B <unit>`` answers the properties asked
    for from the unit's state, and the kernel log (``journalctl -k``) has no
    OOM kills. Without them every app diagnoses as "down: not listening",
    whatever its state.

    Args:
        units: The modelled units.
        ports: Each unit's port.
    """
    from noust.core.runner import CommandResult, get_runner

    runner = get_runner()
    original = runner.run

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        if args[:2] == ("ss", "-ltnpH"):
            runner.calls.append(args)
            listening = [
                f"LISTEN 0 511 127.0.0.1:{ports[name]} 0.0.0.0:* "
                f'users:(("node",pid={unit.pid},fd=19))'
                for name, unit in sorted(units.items())
                if unit.active == "active" and name in ports
            ]
            # A database opened to the network is a socket unit systemd holds the port of
            # (no process of Noust's own): the one `noust-dbexpose-*.socket` file says which.
            listening.extend(_exposed_database_listeners())
            return CommandResult(args, 0, "\n".join(listening) + "\n", "")
        if args[:1] == ("journalctl",) and "-k" in args:
            # The kernel log of the modelled machine has no OOM kills in it; without
            # this, every failed app's diagnosis names memory as the probable cause.
            runner.calls.append(args)
            return CommandResult(args, 0, "", "")
        if args[:3] == ("systemctl", "show", "-p") and len(args) >= 5:
            unit = units.get(args[-1].removesuffix(".service"))
            if unit is not None:
                runner.calls.append(args)
                failed = unit.active == "failed"
                known = {
                    "ActiveState": unit.active,
                    "SubState": unit.sub,
                    "Result": "exit-code" if failed else "success",
                    "ExecMainStatus": "1" if failed else "0",
                    "NRestarts": str(unit.restarts),
                    "MainPID": str(unit.pid),
                }
                wanted = args[3].split(",")
                return CommandResult(
                    args, 0, "".join(f"{key}={known.get(key, '')}\n" for key in wanted), ""
                )
        return original(argv, **kwargs)

    runner.run = run  # type: ignore[method-assign]


def _tabs_port_model(units: dict[str, Unit], ports: dict[str, int]) -> None:
    """
    Say a port answers when an active modelled unit listens on it.

    An application's state checks that its port accepts connections
    (:func:`noust.core.app_state.port_answers`). The seeded units run no
    process, so without this every running app reads "No answer"; a port a
    real listener holds (the health servers above) still answers for real.

    Args:
        units: The modelled units.
        ports: Each unit's port.
    """
    import noust.core.app_state as app_state_module

    real = app_state_module.port_answers

    def port_answers(port: int, *args: Any, **kwargs: Any) -> bool:
        modelled = any(
            unit.active == "active" and ports.get(name) == port for name, unit in units.items()
        )
        return modelled or real(port, *args, **kwargs)

    app_state_module.port_answers = port_answers  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Console: a deploy's actions (rebuild its commit, roll back to it), an update
# with nothing new, a static site's deploy and a source the wizard cannot
# deploy as it is. The tabs' release app already has releases on disk to go
# back to; this adds what else those screens branch on: an in-place
# deployment whose snapshot backup still exists, an app on releases whose
# branch head is the commit that is live, a static site's deploy (no health
# check) and a repository holding a lone Dockerfile.
# ---------------------------------------------------------------------------

#: On releases, with a git source whose branch head (as the modelled remote
#: answers `git ls-remote`) is the commit that is live: an update from the
#: console answers 409 nothing_new.
NOTHING_NEW_APP = "catalogo.example.org"

#: Its repository, and the commit both its live release and the remote's main are at.
NOTHING_NEW_SOURCE = "https://github.com/example-org/catalogo.git"
NOTHING_NEW_COMMIT = "6d1e0b27c4a95f3e8d20b61c7a4f9e5d3b2c1a08"

#: A static site with one deploy of ten seconds in its history: its Health phase does not apply.
STATIC_DEPLOY_APP = "bodas.example.com"

#: The wizard's source that is a lone Dockerfile: the inspection proposes the container type for it
#: (the modelled machine has no Docker, so the verdict says what to install first).
WIZARD_DOCKERFILE_SOURCE = "container-api"

#: The wizard's source that nothing here deploys: a Rust project with no Dockerfile, and no
#: Railpack on the modelled machine. The inspection refuses it, and the verdict says how a
#: container would deploy it.
WIZARD_UNSUPPORTED_SOURCE = "rust-service"


def seed_deploy_actions_history(store: Any, apps: dict[str, _TabsApp]) -> None:
    """
    Record the deployments the deploy actions need, with the tabs' history.

    Recorded before :func:`seed_console_state`'s deploys of "now", for the
    same reason the tabs' are: the API orders deployments by id.

    Args:
        store: The store.
        apps: The tabs' applications, given the nothing-new app's port and
            deployment here.
    """
    now = datetime.now()

    started = now - timedelta(days=3, hours=2)
    commit = NOTHING_NEW_COMMIT[:7]
    rid = _tabs_release_id(started, commit)
    seeded = _TabsApp(port=_tabs_serve_ok())
    deployment_id = _tabs_deployment(
        store,
        NOTHING_NEW_APP,
        trigger="webhook",
        commit=commit,
        started=started,
        lines=[
            (0, "[1/9] Fetching source into a new release..."),
            (0.4, f"      → Source: {NOTHING_NEW_SOURCE} (main)"),
            (1.6, f"      → HEAD is now at {commit}"),
            (1.7, f"      → Release: {rid}"),
            (1.9, "[2/9] Installing dependencies..."),
            (2.0, "      → Running: npm ci"),
            (11.2, "added 402 packages, and audited 403 packages in 9s"),
            (11.4, "[3/9] Building application..."),
            (11.5, "      → Running: npm run build"),
            (29.8, " ✓ Compiled successfully in 17.9s"),
            (30.1, "[8/9] Activating release..."),
            (30.2, f"      → Activated release {rid}"),
            (30.3, f"      → Checking: http://127.0.0.1:{seeded.port}/"),
            (30.6, f"✓ Release {rid} answered 200 in 61 ms"),
            (30.7, f"✓ Deployed {NOTHING_NEW_APP}"),
        ],
        status="success",
    )
    store.annotate_deployment(
        deployment_id, release_id=rid, commit_message="Show stock per warehouse"
    )
    seeded.deploys.append((started, commit, "ok", "success", rid))
    apps[NOTHING_NEW_APP] = seeded

    # A static deploy: the web server serves the files, so nothing is probed.
    _tabs_deployment(
        store,
        STATIC_DEPLOY_APP,
        trigger="panel",
        commit="4c2e9f1",
        started=now - timedelta(days=2, hours=5),
        lines=[
            (0, "[1/6] Fetching source code..."),
            (0.3, "      → Source: https://github.com/you/landing"),
            (1.2, "      → HEAD is now at 4c2e9f1"),
            (1.4, "[2/6] Setting permissions..."),
            (2.0, "[3/6] Creating site configuration..."),
            (2.6, "[4/6] Obtaining SSL certificate..."),
            (2.7, "      → The existing certificate keeps serving what it covers"),
            (6.0, "[5/6] Activating site..."),
            (10.0, f"✓ Deployed {STATIC_DEPLOY_APP}"),
        ],
        status="success",
    )


def _nothing_new_ls_remote(args: tuple[str, ...]) -> Any:
    """
    Answer ``git ls-remote`` for the nothing-new app's repository.

    Args:
        args: The argv the runner was asked to run.

    Returns:
        Its ``main`` at the live commit, or None for any other command.
    """
    from noust.core.runner import CommandResult

    if args[:1] != ("git",) or "ls-remote" not in args or NOTHING_NEW_SOURCE not in args:
        return None
    return CommandResult(args, 0, f"{NOTHING_NEW_COMMIT}\trefs/heads/main\n", "")


def seed_deploy_actions(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    history: dict[str, _TabsApp],
) -> None:
    """
    Seed the states a deploy's actions and an update's "nothing new" branch on.

    - The in-place app's deployment before its live one gets the pre-update
      backup the update after it took, as its snapshot: it can be gone back to.
    - The nothing-new app, on releases with its one release serving.
    - The wizard's lone-Dockerfile source, and the Rust project nothing deploys.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        history: What :func:`seed_app_tabs_history` recorded first.
    """
    from datetime import timezone

    from noust.core.runner import get_runner
    from noust.core.store import App, ReleaseRecord
    from noust.core.utils import domain_to_app_name

    # The snapshot: the older of the two backups seed_backups gave the in-place app.
    backups = sandbox.backup_dir / domain_to_app_name(TABS_LIVE_APP)
    pre_deploy = sorted(
        path.stem
        for path in backups.glob("*.json")
        if "pre-deploy" in json.loads(path.read_text(encoding="utf-8")).get("tags", [])
    )
    live_history = store.list_deployments(domain=TABS_LIVE_APP, limit=100)
    if pre_deploy and len(live_history) >= 2 and live_history[1].id is not None:
        store.set_deployment_snapshot(live_history[1].id, pre_deploy[0])

    seeded = history[NOTHING_NEW_APP]
    started, commit, _, _, rid = seeded.deploys[0]
    root = sandbox.apps_dir / domain_to_app_name(NOTHING_NEW_APP)
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / ".env").write_text(f"NODE_ENV=production\nPORT={seeded.port}\n", encoding="utf-8")
    (shared / ".env").chmod(0o600)
    (root / "repo").mkdir(parents=True, exist_ok=True)
    release_dir = root / "releases" / rid
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / "package.json").write_text(
        json.dumps({"name": "catalogo", "version": "1.8.0"}) + "\n", encoding="utf-8"
    )
    (release_dir / ".env").symlink_to("../../shared/.env")
    app = App(
        domain=NOTHING_NEW_APP,
        app_type="nextjs",
        source=NOTHING_NEW_SOURCE,
        branch="main",
        port=seeded.port,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
        layout="releases",
        keep_releases=5,
    )
    _tabs_register(sandbox, store, units, ports, domains, app, working_directory=root / "current")
    stored = store.get_app(NOTHING_NEW_APP)
    if stored is not None and stored.id is not None:
        store.record_release(
            ReleaseRecord(
                id=rid,
                app_id=stored.id,
                git_commit=commit,
                created_at=started.astimezone(timezone.utc).isoformat(),
                activated_at=(started + timedelta(seconds=31)).astimezone(timezone.utc).isoformat(),
                status="superseded",
                path=str(release_dir),
            )
        )
        (root / "current").symlink_to(Path("releases") / rid)
        store.mark_release_active(stored.id, rid)

    runner = get_runner()
    original = runner.run

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        answer = _nothing_new_ls_remote(args)
        if answer is None:
            return original(argv, **kwargs)
        runner.calls.append(args)
        return answer

    runner.run = run  # type: ignore[method-assign]

    # Only a Dockerfile: the inspection proposes the container type for it.
    container = sandbox.var / "www" / "src" / WIZARD_DOCKERFILE_SOURCE
    container.mkdir(parents=True, exist_ok=True)
    (container / "Dockerfile").write_text(
        "FROM node:22-alpine\nWORKDIR /app\nCOPY . .\nRUN npm ci\nEXPOSE 3000\n"
        'CMD ["node", "server.js"]\n',
        encoding="utf-8",
    )
    (container / "server.js").write_text(
        "require('http').createServer((q, s) => s.end('ok')).listen(3000);\n", encoding="utf-8"
    )

    # A Rust library: Noust deploys a Rust program, and this has none to run, nor a Dockerfile
    # to build one with, so it is refused.
    unsupported = sandbox.var / "www" / "src" / WIZARD_UNSUPPORTED_SOURCE
    (unsupported / "src").mkdir(parents=True, exist_ok=True)
    (unsupported / "Cargo.toml").write_text(
        '[package]\nname = "rust-service"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8"
    )
    (unsupported / "src" / "lib.rs").write_text(
        "pub fn answer() -> u32 {\n    42\n}\n", encoding="utf-8"
    )
