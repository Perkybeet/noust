# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_monorepo_sandbox.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.monorepo import MonorepoDeployer

DOMAIN = "mono.example.com"


def workspace_tree(root: Path, scripts: dict[str, str] | None = None) -> Path:
    """A turbo/pnpm workspace with two deployable apps."""
    for name in ("web", "api"):
        (root / "apps" / name).mkdir(parents=True, exist_ok=True)
        (root / "apps" / name / "package.json").write_text(f'{{"name": "{name}"}}')
    body = {"name": "mono", "workspaces": ["apps/*"], "scripts": scripts or {}}
    import json

    (root / "package.json").write_text(json.dumps(body))
    (root / "pnpm-workspace.yaml").write_text("packages:\n  - apps/*\n")
    (root / "turbo.json").write_text("{}")
    return root


def registered(store: NoustStore, root: Path, *, sandboxed: bool) -> App:
    """A monorepo row; created from 3.1 when ``sandboxed``, from before otherwise."""
    app = store.create_app(App(domain=DOMAIN, app_type="monorepo", app_path=str(root)))
    if sandboxed:
        build_sandbox.adopt_new_app(DOMAIN, preview=False, store=store)
    return app


def deployer(root: Path, runner: FakeRunner) -> MonorepoDeployer:
    """A monorepo deployer over the tree."""
    instance = MonorepoDeployer(verbose=False, runner=runner)
    instance.configure(DOMAIN, str(root), app_path=root)
    return instance
