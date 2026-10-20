# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stack is told which commit and release it runs, and that follows it back (checklist B-46).

Over the harness of ``test_compose_release_retention.py`` (a Docker that remembers its images and
a tree on a commit): an update writes the new commit and the name its release will be given into
the ``.env`` the containers load, an update that does not pass the gate puts the file back as it
was, and going back to a release tells the containers that release's commit and name.
"""

# The release harness is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.store import NoustStore
from noust.deployers.helpers.env_manager import EnvManager
from tests.test_compose_release_retention import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    NEW,
    Docker,
    Probe,
    deployer,
    docker,
    no_waiting,
    probe,
    releases,
    root,
    store,
    update,
)


def told(root: Path) -> dict[str, str]:
    """What the stack's ``.env`` says about the deployment."""
    values = EnvManager().read_env_file(root / ".env")
    return {name: value for name, value in values.items() if name.startswith("NOUST_")}


def test_an_update_tells_the_containers_its_commit_and_the_release_it_becomes(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    update(root, docker, "2222222")

    (release,) = releases(store)
    facts = told(root)
    assert facts["NOUST_COMMIT"] == "2222222"
    assert facts["NOUST_RELEASE"] == release.id
    assert facts["NOUST_DOMAIN"] == DOMAIN


def test_the_next_update_replaces_them(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    update(root, docker, "2222222")
    update(root, docker, "3333333")

    newest = max(releases(store), key=lambda row: row.id)
    assert told(root)["NOUST_COMMIT"] == "3333333"
    assert told(root)["NOUST_RELEASE"] == newest.id
    assert newest.git_commit == "3333333"


def test_an_update_that_does_not_pass_the_gate_puts_the_file_back_as_it_was(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    update(root, docker, "2222222")
    before = told(root)
    probe.fail_next("http://127.0.0.1:8080/")
    docker.commit = "4444444"
    docker.after = list(NEW)

    with pytest.raises(DeploymentError):
        deployer(root, docker).update()

    assert told(root) == before


def test_going_back_to_a_release_tells_the_containers_that_releases_commit_and_name(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    update(root, docker, "2222222")
    first = releases(store)[0]
    update(root, docker, "3333333")
    assert told(root)["NOUST_COMMIT"] == "3333333"
    docker.kept.update(
        tag
        for tag in (
            f"stack-web:noust-{first.id}",
            f"stack-db:noust-{first.id}",
        )
    )

    deployer(root, docker).activate_release(first.id, went_back=True)

    assert told(root)["NOUST_COMMIT"] == "2222222"
    assert told(root)["NOUST_RELEASE"] == first.id
