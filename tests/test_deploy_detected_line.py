# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The line a deployment's log says what it understood in.

Railway and Render open a build log with what they detected; Noust's began with "Registering
application", and a person whose first deployment failed could not tell whether Noust had read
their project as they meant it. Once the source is fetched the type knows the commands and the
port, so the log says them in one line before any of them runs.
"""

# The pipeline's fixtures are imported rather than replicated, so there stays one fake machine.
# ruff: noqa: F811

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from noust.core.store import NoustStore
from noust.deployers.nodejs import NodeJSDeployer
from noust.deployers.recorder import CapturingLogger
from noust.deployers.static import StaticDeployer
from tests.test_release_pipeline import (  # noqa: F401 - fixtures used by name
    DOMAIN,
    GIT_URL,
    PORT,
    RELEASES,
    git,
    machine,
    node_tree,
    root,
    store,
    wire,
    write_tree,
)


def deploy_and_listen(deployer: Any, machine: SimpleNamespace) -> list[str]:
    """Deploy, and return every line the deployment's logger emitted, in order."""
    console = io.StringIO()
    deployer.logger = CapturingLogger(verbose=True, no_color=True, stream=console)
    assert deployer.deploy() is True
    return console.getvalue().splitlines()


def test_a_node_project_is_read_aloud_with_what_will_run(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

    lines = deploy_and_listen(deployer, machine)

    (detected,) = [line for line in lines if "Detected" in line]
    assert detected.strip() == (
        f"i Detected Node.js (package.json). Install: npm ci. Start: npm run start. Port {PORT}."
    )


def test_the_line_comes_before_anything_it_describes_runs(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

    lines = deploy_and_listen(deployer, machine)

    detected = next(i for i, line in enumerate(lines) if "Detected" in line)
    installing = next(i for i, line in enumerate(lines) if "Installing" in line)
    assert detected < installing


def test_a_static_site_says_it_is_served_as_files_and_names_no_command(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    machine.git.publish(write_tree(tmp_path / "v1", {"index.html": "<h1>hi</h1>\n"}))
    deployer = wire(StaticDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, GIT_URL, ssl=False, app_path=root, layout=RELEASES)

    lines = deploy_and_listen(deployer, machine)

    (detected,) = [line for line in lines if "Detected" in line]
    assert "Detected Static Site (index.html)." in detected
    assert "served as static files" in detected.lower()
    assert "Install:" not in detected and "Start:" not in detected and "Port" not in detected
