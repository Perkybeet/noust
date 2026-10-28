# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Console 2.2, pull request previews."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .app_tabs import _tabs_register, _tabs_release_id, _tabs_serve_ok
from .machine import Unit
from .sandbox import Sandbox

# --- 2.2: pull request previews ----------------------------------------------


def seed_previews(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
) -> None:
    """
    Seed an application with pull request previews, and one preview deployed beside it.

    ``portal.example.org`` is deployed from GitHub on releases, with previews on
    under ``previews.example.org``: #42 ready (its own application, on releases
    too, marked as the portal's preview), #57 building and #61 failed. The
    sweep timer's units go to the sandboxed systemd directory, so saving the
    settings writes them there; removing a preview deletes its application
    through the fake runner and the sandbox filesystem.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
    """
    import noust.managers.previews as previews_module
    from tests.support.panel_factory import PREVIEWS_BASE_DOMAIN, seed_previews_records

    # Written when the settings are saved: into the sandbox, like every other unit.
    previews_module.SYSTEMD_DIR = sandbox.systemd_dir

    parent = PREVIEWS_APP
    _previews_release_app(
        sandbox, store, units, ports, domains, parent, commit="4d2a9c1", branch="main"
    )
    # Built from the pull request's branch, as a preview deploy records it.
    _previews_release_app(
        sandbox,
        store,
        units,
        ports,
        domains,
        previews_module.preview_domain_for(parent, 42, PREVIEWS_BASE_DOMAIN),
        commit="8c1f2e7",
        branch="feature/checkout-redesign",
    )
    seed_previews_records(store, parent)


#: The application with pull request previews.
PREVIEWS_APP = "portal.example.org"


def _previews_release_app(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    domain: str,
    *,
    commit: str,
    branch: str,
) -> Path:
    """
    Record a Next.js application on releases with one release on disk, serving.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        domain: The application.
        commit: The commit its one release was built from.
        branch: The branch it deploys.

    Returns:
        Its directory.
    """
    from noust.core.store import App, ReleaseRecord
    from noust.core.utils import domain_to_app_name

    root = sandbox.apps_dir / domain_to_app_name(domain)
    port = _tabs_serve_ok()
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / ".env").write_text(
        "NODE_ENV=production\n"
        f"PORT={port}\n"
        "DATABASE_URL=postgres://portal:Q3n8wLz5@127.0.0.1:5432/portal\n"
        f"NEXT_PUBLIC_SITE_URL=https://{domain}\n",
        encoding="utf-8",
    )
    (shared / ".env").chmod(0o600)
    (root / "repo").mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc) - timedelta(days=1, hours=3)
    release_id = _tabs_release_id(started, commit)
    release = root / "releases" / release_id
    (release / ".next").mkdir(parents=True, exist_ok=True)
    (release / "package.json").write_text(
        json.dumps({"name": "portal", "version": "0.9.0", "private": True}) + "\n",
        encoding="utf-8",
    )
    (release / ".next" / "BUILD_ID").write_text(commit + "\n", encoding="utf-8")
    (release / ".env").symlink_to("../../shared/.env")
    (root / "current").symlink_to(Path("releases") / release_id)

    app = App(
        domain=domain,
        app_type="nextjs",
        source="https://github.com/example-org/portal.git",
        branch=branch,
        port=port,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
        layout="releases",
        keep_releases=5,
    )
    _tabs_register(sandbox, store, units, ports, domains, app, working_directory=root / "current")
    stored = store.get_app(domain)
    if stored is not None and stored.id is not None:
        store.record_release(
            ReleaseRecord(
                id=release_id,
                app_id=stored.id,
                git_commit=commit,
                created_at=started.isoformat(),
                activated_at=(started + timedelta(minutes=2)).isoformat(),
                status="superseded",
                path=str(release),
            )
        )
        store.mark_release_active(stored.id, release_id)
    return root
