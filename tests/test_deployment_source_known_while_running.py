# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a deployment's row says while it is still running.

The commit, its branch, its subject and the release being built were written when the
deployment ended, so for the minutes a build takes the page of the deployment had nothing
to show and said "Not a git checkout", which reads like an error and was not true. They are
known the moment the source is fetched, and that is when the row is told.
"""

# The pipeline's fixtures are imported rather than replicated, so there stays one fake machine.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from noust.core.appdir.layout import RELEASES
from noust.core.store import NoustStore
from noust.deployers.nodejs import NodeJSDeployer
from tests.support.release_pipeline import (  # noqa: F401 - fixtures used by name
    DOMAIN,
    GIT_URL,
    PORT,
    git,
    machine,
    node_tree,
    root,
    store,
    wire,
)


def row_seen_while_installing(deployer: Any, store: NoustStore) -> list[tuple[Any, ...]]:
    """Deploy, and read the deployment's row from inside the step after the fetch."""
    seen: list[tuple[Any, ...]] = []
    original = deployer._install_release_dependencies

    def peek(*args: Any, **kwargs: Any) -> Any:
        row = store.list_deployments(DOMAIN)[0]
        seen.append((row.status, row.git_commit, row.release_id))
        return original(*args, **kwargs)

    deployer._install_release_dependencies = peek
    assert deployer.deploy() is True
    return seen


def test_a_release_names_its_commit_and_release_as_soon_as_it_is_fetched(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    commit = machine.git.publish(node_tree(tmp_path / "v1"))
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

    seen = row_seen_while_installing(deployer, store)

    finished = store.list_deployments(DOMAIN)[0]
    assert finished.release_id
    assert seen == [("running", commit[:7], finished.release_id)]
