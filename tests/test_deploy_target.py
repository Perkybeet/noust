# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests that a deploy never destroys a directory it did not create.

``wasm create`` over a domain whose directory already held an application
used to wipe it: the in-place fetch empties the target before cloning, and a
deploy that then failed ran the fetch's undo, which deleted the whole
directory - ``.env``, uploads, a Compose project's bind-mounted data. The
same happened when the store had moved and WASM no longer knew the
application existed. A deploy now refuses a directory that exists and is not
empty unless it is told to replace it, and even then never deletes what it
found there when it fails.

The deploys run through the real pipeline over temporary trees, with the
machine faked by the fixtures of ``tests/test_release_pipeline.py``.
"""

# ruff: noqa: F811

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.auto import AutoDeployer
from noust.deployers.helpers.layout import INPLACE, RELEASES
from noust.deployers.helpers.target import claim_deploy_target
from noust.deployers.monorepo import MonorepoDeployer
from noust.deployers.nodejs import NodeJSDeployer
from noust.deployers.releases import ReleaseManager
from noust.managers.source_manager import SourceManager
from tests.test_release_pipeline import (  # noqa: F401  (pytest resolves fixtures by name)
    BROKEN_SERVER,
    DOMAIN,
    GIT_URL,
    PORT,
    active_id,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    wire,
)


def operator_tree(root: Path) -> dict[str, str]:
    """
    Fill a directory the way a live in-place application fills it.

    Returns:
        Relative path to content, to compare against afterwards.
    """
    files = {
        "package.json": '{"name": "live"}',
        "server.js": "// the running build",
        ".env": "DATABASE_URL=postgres://live\n",
        "public/uploads/photo.jpg": "a user's photo",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return files


def assert_untouched(root: Path, files: dict[str, str]) -> None:
    """Every file the operator had is there, with its content."""
    for relative, content in files.items():
        assert (root / relative).read_text() == content, relative


def in_place_deployer(machine: SimpleNamespace, source: Path, **options: object) -> NodeJSDeployer:
    """A Node deployer, in place, fetching from a local directory."""
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    deployer.source_manager = SourceManager(runner=FakeRunner())
    deployer.configure(
        DOMAIN, str(source), port=PORT, ssl=False, app_path=machine.root, layout=INPLACE, **options
    )
    return deployer


@pytest.fixture
def at(root: Path, machine: SimpleNamespace) -> SimpleNamespace:
    """The machine, told where the application lives."""
    machine.root = root
    return machine


def test_a_deploy_over_an_in_place_application_is_refused(
    tmp_path: Path, root: Path, store: NoustStore, at: SimpleNamespace
) -> None:
    files = operator_tree(root)
    store.create_app(
        App(domain=DOMAIN, app_type="nodejs", source=GIT_URL, port=PORT, app_path=str(root))
    )

    deployer = in_place_deployer(at, node_tree(tmp_path / "v2"))
    with pytest.raises(DeploymentError, match="already deployed") as refused:
        deployer.deploy()

    assert "noust update rel.example.com" in refused.value.details
    assert "--force" in refused.value.details
    assert_untouched(root, files)


def test_a_directory_wasm_has_no_record_of_is_refused(
    tmp_path: Path, root: Path, store: NoustStore, at: SimpleNamespace
) -> None:
    """The store moved, or someone put files there: neither is Noust's to delete."""
    files = operator_tree(root)
    at.git.publish(node_tree(tmp_path / "v1"))

    with pytest.raises(DeploymentError, match="not empty") as refused:
        deploy_new(root, at)

    assert "noust store path" in refused.value.details
    assert_untouched(root, files)
    assert store.get_app(DOMAIN) is None
    assert not (root / "releases").exists()


def test_auto_detection_refuses_before_fetching_anything(
    tmp_path: Path, root: Path, store: NoustStore, at: SimpleNamespace
) -> None:
    files = operator_tree(root)
    auto = AutoDeployer()
    auto.configure(DOMAIN, str(node_tree(tmp_path / "v1")), app_path=root, layout=INPLACE)
    auto.source_manager = SourceManager(runner=FakeRunner())

    with pytest.raises(DeploymentError, match="not empty"):
        auto.deploy()

    assert_untouched(root, files)


def test_a_monorepo_deploy_refuses_a_directory_that_is_not_empty(
    tmp_path: Path, root: Path, store: NoustStore
) -> None:
    files = operator_tree(root)
    deployer = MonorepoDeployer(verbose=False)
    deployer.configure(DOMAIN, str(tmp_path / "mono"), app_path=root)

    with pytest.raises(DeploymentError, match="not empty"):
        deployer.deploy()

    assert_untouched(root, files)


def test_forced_in_place_deploy_that_fails_leaves_the_directory(
    tmp_path: Path, root: Path, store: NoustStore, at: SimpleNamespace
) -> None:
    """Asked to replace what is not a checkout, it refuses; it never deletes the directory."""
    operator_tree(root)
    at.runner.script(["npm", "ci"], stderr="npm ERR! network ETIMEDOUT", exit_code=1)

    deployer = in_place_deployer(at, node_tree(tmp_path / "v2"), replace_existing=True)
    with pytest.raises(DeploymentError):
        deployer.deploy()

    assert root.is_dir(), "the directory the operator had is never removed"
    assert (root / "server.js").is_file()


def test_forced_release_deploy_that_fails_keeps_what_was_there(
    tmp_path: Path, root: Path, store: NoustStore, at: SimpleNamespace
) -> None:
    """Only the release this deploy staged is thrown away."""
    files = operator_tree(root)
    at.git.publish(node_tree(tmp_path / "v1", server=BROKEN_SERVER))
    deployer = wire(NodeJSDeployer(verbose=False, runner=at.runner), at)
    deployer.configure(
        DOMAIN,
        GIT_URL,
        port=PORT,
        ssl=False,
        app_path=root,
        layout=RELEASES,
        replace_existing=True,
    )

    with pytest.raises(DeploymentError, match="did not pass its health check"):
        deployer.deploy()

    assert_untouched(root, files)
    assert ReleaseManager(root).list() == []
    assert not os.path.lexists(root / "current")


def test_an_empty_directory_is_deployed_into_and_kept_on_failure(
    tmp_path: Path, root: Path, store: NoustStore, at: SimpleNamespace
) -> None:
    """A directory made ahead of time (a mount point) is used, and never removed."""
    root.mkdir(parents=True)
    at.git.publish(node_tree(tmp_path / "v1", server=BROKEN_SERVER))
    deployer = wire(NodeJSDeployer(verbose=False, runner=at.runner), at)
    deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

    with pytest.raises(DeploymentError, match="did not pass its health check"):
        deployer.deploy()

    assert root.is_dir()
    assert os.listdir(root) == []


def test_a_new_directory_is_still_removed_when_the_first_deploy_fails(
    tmp_path: Path, root: Path, store: NoustStore, at: SimpleNamespace
) -> None:
    at.git.publish(node_tree(tmp_path / "v1", server=BROKEN_SERVER))
    deployer = wire(NodeJSDeployer(verbose=False, runner=at.runner), at)
    deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

    with pytest.raises(DeploymentError):
        deployer.deploy()

    assert not root.exists()


def test_redeploying_an_application_on_releases_needs_no_force(
    tmp_path: Path, root: Path, store: NoustStore, at: SimpleNamespace
) -> None:
    """It builds a release beside the ones there; nothing the application has is replaced."""
    at.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, at)
    first = active_id(root)
    (root / "shared" / ".env").write_text("KEPT=1\n")
    at.git.publish(node_tree(tmp_path / "v2"))

    deploy_new(root, at)

    assert active_id(root) != first
    assert (root / "shared" / ".env").read_text() == "KEPT=1\n"


def test_a_compose_deploy_refuses_a_directory_that_is_not_empty(
    tmp_path: Path, root: Path, store: NoustStore
) -> None:
    """A Compose project's bind-mounted data lives in its directory; the fetch would empty it."""
    from noust.deployers.docker_compose import DockerComposeDeployer

    files = operator_tree(root)
    (root / "data" / "postgres").mkdir(parents=True)
    (root / "data" / "postgres" / "PG_VERSION").write_text("16")
    files["data/postgres/PG_VERSION"] = "16"
    deployer = DockerComposeDeployer(runner=FakeRunner())
    deployer.configure(DOMAIN, str(tmp_path / "stack"), app_path=root)

    with pytest.raises(DeploymentError, match="not empty"):
        deployer.deploy()

    assert_untouched(root, files)


def test_a_forced_compose_deploy_that_fails_keeps_the_directory(
    tmp_path: Path, root: Path, store: NoustStore, runner: FakeRunner
) -> None:
    from noust.deployers.docker_compose import DockerComposeDeployer

    operator_tree(root)
    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(
        DOMAIN, str(tmp_path / "missing-source"), app_path=root, replace_existing=True
    )
    deployer.source_already_fetched = True  # the fetch "happened"; discovery then fails

    with pytest.raises(DeploymentError):
        deployer.deploy()

    assert root.is_dir(), "never removed: it held files before this deploy"


# -- --force never empties a directory (review E2, item 4) ----------------------------


class RecordingSource:
    """A source manager that records each fetch and fetches nothing: the tree is there."""

    def __init__(self) -> None:
        self.fetches: list[dict[str, object]] = []

    def fetch(self, source: str, destination: Path, **kwargs: object) -> bool:
        self.fetches.append({"source": source, "destination": destination, **kwargs})
        return True


def compose_checkout(root: Path, *, git: bool = True) -> dict[str, str]:
    """A Compose project's directory, its database's data bind-mounted from data/."""
    files = {
        "docker-compose.yml": "services:\n  db:\n    image: postgres:16\n"
        "    volumes: ['./data:/var/lib/postgresql/data']\n",
        "data/PG_VERSION": "16",
        ".env": "POSTGRES_PASSWORD=dummy\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    if git:
        (root / ".git").mkdir()
    return files


def test_a_forced_auto_detected_deploy_updates_a_checkout_in_place(
    root: Path, store: NoustStore
) -> None:
    """Detection fetched with clean=True before the Compose deployer was chosen."""
    files = compose_checkout(root)
    auto = AutoDeployer()
    auto.configure(DOMAIN, GIT_URL, app_path=root, layout=INPLACE, replace_existing=True)
    source = RecordingSource()
    auto.source_manager = source  # type: ignore[assignment]

    delegate = auto.resolve()

    (fetch,) = source.fetches
    assert fetch["force"] is True and fetch["clean"] is False
    assert delegate.APP_TYPE == "docker-compose"
    assert_untouched(root, files)


def test_a_forced_auto_detected_deploy_refuses_a_directory_that_is_not_a_checkout(
    root: Path, store: NoustStore
) -> None:
    files = compose_checkout(root, git=False)
    auto = AutoDeployer()
    auto.configure(DOMAIN, GIT_URL, app_path=root, layout=INPLACE, replace_existing=True)
    source = RecordingSource()
    auto.source_manager = source  # type: ignore[assignment]

    with pytest.raises(DeploymentError, match="not a git checkout") as refused:
        auto.resolve()

    assert "noust backup create" in (refused.value.details or "")
    assert source.fetches == []
    assert_untouched(root, files)


def test_a_forced_in_place_deploy_refuses_a_directory_that_is_not_a_checkout(
    tmp_path: Path, root: Path, store: NoustStore, at: SimpleNamespace
) -> None:
    files = operator_tree(root)
    deployer = in_place_deployer(at, node_tree(tmp_path / "v2"), replace_existing=True)

    with pytest.raises(DeploymentError, match="not a git checkout"):
        deployer.deploy()

    assert_untouched(root, files)


def test_a_forced_monorepo_deploy_updates_a_checkout_in_place(
    tmp_path: Path, root: Path, store: NoustStore
) -> None:
    files = compose_checkout(root)
    deployer = MonorepoDeployer(verbose=False)
    deployer.configure(DOMAIN, GIT_URL, app_path=root, replace_existing=True)
    source = RecordingSource()
    deployer.source_manager = source  # type: ignore[assignment]
    deployer.deploy_target = claim_deploy_target(root, domain=DOMAIN, existing=None, replace=True)

    deployer._fetch_source()

    (fetch,) = source.fetches
    assert fetch["force"] is True and fetch["clean"] is False
    assert_untouched(root, files)
