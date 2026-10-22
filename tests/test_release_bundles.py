# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Building a release on one server and running it on another (checklist A-49).

The trees are real directories and links, built and activated through the deployers' own
pipeline; only the machine is faked (git, systemd, nginx, the health probe). Two applications on
two roots stand for two servers: the one that builds and the one that serves.
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import audit, deploy_freeze
from noust.core.exceptions import (
    DeploymentError,
    DeploysFrozenError,
    NoustError,
    SchemaChangedError,
    SourceError,
    ValidationError,
)
from noust.core.runner import CommandResult
from noust.core.store import App, NoustStore
from noust.deployers import base as base_module
from noust.deployers import bundle as bundles
from noust.deployers import promote as promote_module
from noust.deployers import release_bundles
from noust.deployers import staged as staged_module
from noust.deployers.base import BaseDeployer
from noust.deployers.lifecycle import list_releases
from noust.deployers.nodejs import NodeJSDeployer
from noust.deployers.release_bundles import build_and_pack, receive
from noust.deployers.releases import ReleaseManager
from tests.test_promote import SearchingRunner
from tests.test_release_bundle import RELEASE, raw_bundle
from tests.test_release_pipeline import (
    DOMAIN,
    GIT_URL,
    PORT,
    FakeServices,
    FakeWeb,
    TreeRunner,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    wire,
)

FIXTURES = (git, machine, root, store)

TARGET = "serve.example.com"


@pytest.fixture(autouse=True)
def wired(
    store: NoustStore, machine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """
    Both ends see the same store and the same fakes, and the health probe is a switch.

    The builder builds with the fixture's machine; the receiver's deployers are wired to a
    machine of their own, so each one's service restarts read its own ``current``.
    """
    state = SimpleNamespace(healthy=True, receiver=None, built_with=machine)
    monkeypatch.setattr(release_bundles, "get_store", lambda: store)
    monkeypatch.setattr(base_module, "wait_until_healthy", lambda url, **kwargs: state.healthy)

    def make(app_type: str, verbose: bool = False) -> Any:
        use = state.receiver if state.receiver is not None else machine
        return wire(NodeJSDeployer(verbose=False, runner=use.runner), use)

    monkeypatch.setattr(release_bundles, "get_deployer", make)
    monkeypatch.setattr(staged_module, "get_deployer", make)
    # The search for the builder's directory in a received tree is a real search of the real tree.
    monkeypatch.setattr(promote_module, "get_runner", lambda: SearchingRunner())
    return state


def other_machine(root_b: Path, git: Any) -> SimpleNamespace:
    return SimpleNamespace(
        services=FakeServices(root_b), web=FakeWeb(), runner=TreeRunner(), git=git, probes=[]
    )


def register_receiver(
    store: NoustStore, tmp_path: Path, state: SimpleNamespace, git: Any, **fields: Any
) -> Path:
    """The application that serves, on a root of its own, with a release already active."""
    root_b = tmp_path / "apps" / "serve-example-com"
    old = root_b / "releases" / "20261012-090000-1111111"
    old.mkdir(parents=True)
    (old / "server.js").write_text("old")
    (root_b / "shared").mkdir()
    (root_b / "shared" / ".env").write_text("RECEIVER=1\n")
    os.symlink("../../shared/.env", old / ".env")
    os.symlink("releases/20261012-090000-1111111", root_b / "current")
    values: dict[str, Any] = {
        "domain": TARGET,
        "app_type": "nodejs",
        "source": GIT_URL,
        "port": PORT + 1,
        "app_path": str(root_b),
        "layout": "releases",
        "branch": "main",
    }
    values.update(fields)
    store.create_app(App(**values))
    state.receiver = other_machine(root_b, git)
    return root_b


def built_app(
    root: Path, machine: SimpleNamespace, git: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    """The builder: deployed once, then a newer commit waiting on the branch."""
    git.publish(node_tree(tmp_path / "src-1"))
    deploy_new(root, machine)
    return git.publish(node_tree(tmp_path / "src-2", server="v2\n"))


def current_id(root: Path) -> str:
    return Path(os.readlink(root / "current")).name


class TestBuilding:
    def test_it_builds_without_activating_recording_or_keeping_anything(
        self,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        git: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        built_app(root, machine, git, tmp_path, monkeypatch)
        before = current_id(root)
        deployments = len(store.list_deployments(DOMAIN))
        restarts = list(machine.services.restarts)
        out = tmp_path / "out" / f"shop{bundles.BUNDLE_SUFFIX}"
        out.parent.mkdir()

        packed = build_and_pack(DOMAIN, out)

        # What serves is untouched: no switch, no restart, no history, no release left to switch to.
        assert current_id(root) == before and machine.services.restarts == restarts
        assert len(store.list_deployments(DOMAIN)) == deployments
        assert [r.id for r in ReleaseManager(root).list()] == [before]
        app = store.get_app(DOMAIN)
        assert app is not None
        assert [r.id for r in store.list_releases(app.id)] == [before]
        # And what was packed is the new build, with the install and the build run in it.
        with tarfile.open(out, "r:gz") as archive:
            names = archive.getnames()
            server = archive.extractfile("release/server.js")
            assert server is not None and server.read().decode() == "v2\n"
        assert "release/node_modules/.installed-by" in names
        assert not any(name in ("release/.env",) for name in names)
        assert packed.manifest.app_type == "nodejs" and packed.manifest.domain == DOMAIN
        assert packed.manifest.app_root == str(root)
        assert packed.manifest.release_id != before and packed.manifest.commit

    def test_the_release_can_be_kept_when_asked(
        self,
        root: Path,
        machine: SimpleNamespace,
        git: Any,
        store: NoustStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        built_app(root, machine, git, tmp_path, monkeypatch)

        packed = build_and_pack(DOMAIN, tmp_path / "keep.tar.gz", keep=True)

        assert (root / "releases" / packed.manifest.release_id / "server.js").is_file()
        # Kept, it is a release that was built and is waiting, as a staged build is.
        waiting = [info.id for info in list_releases(DOMAIN) if info.waiting]
        assert waiting == [packed.manifest.release_id]

    def test_a_release_already_built_is_packed_without_building_again(
        self,
        root: Path,
        machine: SimpleNamespace,
        git: Any,
        store: NoustStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        built_app(root, machine, git, tmp_path, monkeypatch)
        active = current_id(root)
        commands = len(machine.runner.where)

        packed = build_and_pack(DOMAIN, tmp_path / "again.tar.gz", release_id=active)

        assert packed.manifest.release_id == active
        assert len(machine.runner.where) == commands
        # Packing what is on disk removes nothing: the release is the application's.
        assert (root / "releases" / active).is_dir()

    def test_a_release_that_does_not_exist_lists_the_ones_that_do(
        self,
        root: Path,
        machine: SimpleNamespace,
        git: Any,
        store: NoustStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        built_app(root, machine, git, tmp_path, monkeypatch)

        with pytest.raises(ValidationError, match="has no release") as caught:
            build_and_pack(DOMAIN, tmp_path / "x.tar.gz", release_id="20990101-000000-0000000")

        assert current_id(root) in caught.value.details

    def test_a_commit_is_built_instead_of_the_head(
        self,
        root: Path,
        machine: SimpleNamespace,
        git: Any,
        store: NoustStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        built_app(root, machine, git, tmp_path, monkeypatch)
        first = next(iter(git.commits))
        monkeypatch.setattr(staged_module, "_resolve_in_cache", lambda *args: first)

        packed = build_and_pack(DOMAIN, tmp_path / "first.tar.gz", commit=first[:7])

        assert packed.manifest.commit == first[:7]
        with tarfile.open(packed.path, "r:gz") as archive:
            server = archive.extractfile("release/server.js")
            assert server is not None and "createServer" in server.read().decode()

    def test_a_failed_build_leaves_nothing_and_says_why(
        self,
        root: Path,
        machine: SimpleNamespace,
        git: Any,
        store: NoustStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        built_app(root, machine, git, tmp_path, monkeypatch)
        before = [r.id for r in ReleaseManager(root).list()]
        failing = node_tree(tmp_path / "src-3")
        (failing / "package.json").write_text(
            '{"name": "app", "scripts": {"build": "node build.js", "start": "node server.js"}}\n'
        )
        git.publish(failing)
        machine.runner.script(["npm", "run", "build"], exit_code=1, stderr="npm ERR! build failed")

        with pytest.raises(NoustError):
            build_and_pack(DOMAIN, tmp_path / "x.tar.gz")

        assert [r.id for r in ReleaseManager(root).list()] == before
        assert not (tmp_path / "x.tar.gz").exists()

    def test_a_tree_with_a_link_that_points_out_is_refused_and_is_not_removed(
        self,
        root: Path,
        machine: SimpleNamespace,
        git: Any,
        store: NoustStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        built_app(root, machine, git, tmp_path, monkeypatch)
        active = current_id(root)
        (root / "releases" / active / ".venv" / "bin").mkdir(parents=True)
        os.symlink("/usr/bin/python3", root / "releases" / active / ".venv" / "bin" / "python")

        with pytest.raises(ValidationError, match="points outside the release"):
            build_and_pack(DOMAIN, tmp_path / "x.tar.gz", release_id=active)

        assert (root / "releases" / active).is_dir()

    def test_the_commit_of_a_source_that_is_not_git_is_refused(
        self, root: Path, store: NoustStore, tmp_path: Path
    ) -> None:
        store.create_app(
            App(
                domain=DOMAIN,
                app_type="nodejs",
                source=str(tmp_path),
                app_path=str(root),
                layout="releases",
            )
        )

        with pytest.raises(SourceError, match="not deployed from git"):
            build_and_pack(DOMAIN, tmp_path / "x.tar.gz", commit="abc1234")

    def test_a_release_and_a_commit_together_are_a_contradiction(
        self, root: Path, store: NoustStore, tmp_path: Path
    ) -> None:
        store.create_app(
            App(
                domain=DOMAIN,
                app_type="nodejs",
                source=GIT_URL,
                app_path=str(root),
                layout="releases",
            )
        )

        with pytest.raises(ValidationError, match="a release or a commit"):
            build_and_pack(DOMAIN, tmp_path / "x.tar.gz", commit="abc1234", release_id=RELEASE)

    @pytest.mark.parametrize(
        ("fields", "message"),
        [
            ({"preview_parent": "other.example.com"}, "is a preview"),
            ({"app_type": "docker-compose"}, "builds images"),
            ({"layout": "inplace"}, "not on the release layout"),
        ],
    )
    def test_an_application_that_cannot_send_a_release(
        self,
        root: Path,
        store: NoustStore,
        tmp_path: Path,
        fields: dict[str, Any],
        message: str,
    ) -> None:
        values: dict[str, Any] = {
            "domain": DOMAIN,
            "app_type": "nodejs",
            "source": GIT_URL,
            "app_path": str(root),
            "layout": "releases",
        }
        values.update(fields)
        store.create_app(App(**values))

        with pytest.raises(ValidationError, match=message):
            build_and_pack(DOMAIN, tmp_path / "x.tar.gz")

    def test_an_application_that_is_not_there(self, store: NoustStore, tmp_path: Path) -> None:
        with pytest.raises(NoustError, match="Application not found"):
            build_and_pack("ghost.example.com", tmp_path / "x.tar.gz")


class TestReceiving:
    @pytest.fixture
    def sent(
        self,
        root: Path,
        machine: SimpleNamespace,
        git: Any,
        store: NoustStore,
        wired: SimpleNamespace,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> SimpleNamespace:
        """A bundle built on the builder, and the application that will run it."""
        built_app(root, machine, git, tmp_path, monkeypatch)
        packed = build_and_pack(DOMAIN, tmp_path / f"shop{bundles.BUNDLE_SUFFIX}")
        root_b = register_receiver(store, tmp_path, wired, git)
        wired.healthy = True
        return SimpleNamespace(packed=packed, root_b=root_b, release=packed.manifest.release_id)

    def test_it_is_activated_behind_the_health_gate_with_the_receivers_own_environment(
        self, sent: SimpleNamespace, store: NoustStore, wired: SimpleNamespace
    ) -> None:
        reception = receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

        assert reception.release == sent.release and reception.domain == TARGET
        assert current_id(sent.root_b) == sent.release
        assert (sent.root_b / "releases" / sent.release / "server.js").read_text() == "v2\n"
        assert (
            sent.root_b / "releases" / sent.release / "node_modules" / ".installed-by"
        ).is_file()
        # Its own environment: the link goes to this application's shared/, not the builder's.
        link = sent.root_b / "releases" / sent.release / ".env"
        assert link.resolve() == (sent.root_b / "shared" / ".env").resolve()
        assert wired.receiver.services.restarts[-1] == f"releases/{sent.release}"
        # The release that served is still there to go back to.
        assert (sent.root_b / "releases" / "20261012-090000-1111111").is_dir()

    def test_it_is_a_deployment_of_the_receiver(
        self, sent: SimpleNamespace, store: NoustStore
    ) -> None:
        reception = receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

        rows = store.list_deployments(TARGET)
        assert rows[0].id == reception.deployment_id and rows[0].status == "success"
        assert rows[0].release_id == sent.release
        assert rows[0].git_commit == sent.packed.manifest.commit

    def test_both_ends_are_audited_without_the_path_of_anything_secret(
        self, sent: SimpleNamespace
    ) -> None:
        receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

        events = {e["action"]: e for e in audit.get_log().read(limit=200)}
        assert events["releases.bundle"]["details"]["sha256"] == sent.packed.sha256
        assert events["releases.receive"]["details"]["release"] == sent.release
        assert events["releases.receive"]["details"]["built_on"] == sent.packed.manifest.builder

    def test_the_builders_directory_is_rewritten_to_the_receivers(
        self,
        sent: SimpleNamespace,
        root: Path,
        store: NoustStore,
        tmp_path: Path,
        wired: SimpleNamespace,
    ) -> None:
        hand = root / "releases" / "20261014-080000-def5678"
        hand.mkdir()
        (hand / "server.js").write_text("hand")
        (hand / "paths.txt").write_text(f"{root}/shared and {root}-old/shared and {root}\n")
        packed = build_and_pack(DOMAIN, tmp_path / "hand.tar.gz", release_id=hand.name)

        reception = receive(TARGET, packed.path, sha256=packed.sha256)

        assert reception.rewritten == 1
        written = sent.root_b / "releases" / hand.name / "paths.txt"
        assert (
            written.read_text() == f"{sent.root_b}/shared and {root}-old/shared and {sent.root_b}\n"
        )

    def test_building_is_not_held_by_a_freeze_but_serving_is(
        self,
        sent: SimpleNamespace,
        root: Path,
        tmp_path: Path,
    ) -> None:
        deploy_freeze.create("incident 412")

        built = build_and_pack(DOMAIN, tmp_path / "frozen.tar.gz")
        with pytest.raises(DeploysFrozenError):
            receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)
        assert not (sent.root_b / "releases" / sent.release).exists()

        receive(TARGET, sent.packed.path, sha256=sent.packed.sha256, frozen_ok=True)
        assert built.manifest.release_id and current_id(sent.root_b) == sent.release

    def test_the_release_meets_the_receivers_database_like_a_staged_one(
        self, sent: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[bool] = []
        original = BaseDeployer.deploy_prebuilt

        def spy(self: BaseDeployer, staged: Any, on_step: Any = None, **kwargs: Any) -> Any:
            asked.append(kwargs.get("apply_migrations", False))
            return original(self, staged, on_step, **kwargs)

        monkeypatch.setattr(BaseDeployer, "deploy_prebuilt", spy)

        receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

        assert asked == [True]

    def test_a_checksum_that_is_not_the_files_extracts_nothing(self, sent: SimpleNamespace) -> None:
        before = current_id(sent.root_b)

        with pytest.raises(ValidationError, match="not the file the checksum names"):
            receive(TARGET, sent.packed.path, sha256="0" * 64)

        assert current_id(sent.root_b) == before
        assert not (sent.root_b / "releases" / sent.release).exists()
        assert not any(p.name.startswith(".bundle-") for p in (sent.root_b / "releases").iterdir())

    def test_a_release_for_another_type_is_refused_before_it_is_written(
        self, sent: SimpleNamespace, store: NoustStore
    ) -> None:
        target = store.get_app(TARGET)
        assert target is not None
        target.app_type = "python"
        store.update_app(target)

        with pytest.raises(
            ValidationError, match=r"built as nodejs and serve\.example\.com is python"
        ):
            receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

        assert not (sent.root_b / "releases" / sent.release).exists()

    def test_what_already_runs_is_not_received_again(self, sent: SimpleNamespace) -> None:
        receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

        with pytest.raises(ValidationError, match="already runs"):
            receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

    def test_a_release_the_receiver_already_has_on_disk_is_not_replaced(
        self, sent: SimpleNamespace
    ) -> None:
        (sent.root_b / "releases" / sent.release).mkdir()
        (sent.root_b / "releases" / sent.release / "mine").write_text("kept")

        with pytest.raises(DeploymentError, match="already has a release"):
            receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

        assert (sent.root_b / "releases" / sent.release / "mine").read_text() == "kept"

    def test_going_back_past_a_schema_change_is_asked_first(
        self, sent: SimpleNamespace, store: NoustStore
    ) -> None:
        commit = sent.packed.manifest.commit
        first = store.record_deployment_start(TARGET, "cli", git_commit=commit)
        later = store.record_deployment_start(TARGET, "cli", git_commit="fff0000")
        store.annotate_deployment(later, git_commit="fff0000")
        store._get_connection().execute(
            "UPDATE deployments SET schema_changed = 1 WHERE id = ?", (later,)
        )
        store._get_connection().commit()
        assert first < later

        with pytest.raises(SchemaChangedError):
            receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)
        assert not (sent.root_b / "releases" / sent.release).exists()
        receive(TARGET, sent.packed.path, sha256=sent.packed.sha256, schema_changed_ok=True)

    def test_a_release_that_does_not_answer_is_put_back_and_removed(
        self, sent: SimpleNamespace, wired: SimpleNamespace
    ) -> None:
        wired.healthy = False
        before = current_id(sent.root_b)

        with pytest.raises(DeploymentError, match="health check"):
            receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

        assert current_id(sent.root_b) == before
        assert not (sent.root_b / "releases" / sent.release).exists()

    def test_a_bundle_with_a_member_that_leaves_the_release_is_refused_with_nothing_left(
        self, sent: SimpleNamespace, tmp_path: Path
    ) -> None:
        document = {
            **sent.packed.manifest.to_dict(),
            "release_id": "20261015-120000-fedcba9",
        }
        path = raw_bundle(
            tmp_path / "evil.tar.gz", [("release/../../escaped", "x", 0o644)], doc=document
        )

        with pytest.raises(SourceError):
            receive(TARGET, path, sha256=bundles.sha256_of(path))

        assert not (sent.root_b / "releases" / "20261015-120000-fedcba9").exists()
        assert not (sent.root_b.parent / "escaped").exists()
        assert current_id(sent.root_b) == "20261012-090000-1111111"

    def test_the_receivers_own_system_packages_are_installed_before_it_serves(
        self, sent: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order: list[str] = []
        monkeypatch.setattr(
            BaseDeployer, "_ensure_system_packages", lambda self, report: order.append("packages")
        )
        original = BaseDeployer._activate_release

        def activate(self: BaseDeployer) -> None:
            order.append("activate")
            original(self)

        monkeypatch.setattr(BaseDeployer, "_activate_release", activate)

        receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

        assert order == ["packages", "activate"]

    @pytest.mark.parametrize(
        ("fields", "message"),
        [
            ({"app_type": "container"}, "builds images"),
            ({"layout": "inplace"}, "not on the release layout"),
        ],
    )
    def test_an_application_that_cannot_receive_one(
        self, sent: SimpleNamespace, store: NoustStore, fields: dict[str, Any], message: str
    ) -> None:
        target = store.get_app(TARGET)
        assert target is not None
        for key, value in fields.items():
            setattr(target, key, value)
        store.update_app(target)

        with pytest.raises(ValidationError, match=message):
            receive(TARGET, sent.packed.path, sha256=sent.packed.sha256)

    def test_a_preview_takes_its_parents_releases_not_a_bundle(
        self, sent: SimpleNamespace, store: NoustStore, tmp_path: Path
    ) -> None:
        store.create_app(
            App(
                domain="pr-1.serve.example.com",
                app_type="nodejs",
                source=GIT_URL,
                app_path=str(tmp_path / "apps" / "preview"),
                layout="releases",
                preview_parent=TARGET,
            )
        )

        with pytest.raises(ValidationError, match="is a preview"):
            receive("pr-1.serve.example.com", sent.packed.path, sha256=sent.packed.sha256)

    def test_an_application_that_is_not_there(self, sent: SimpleNamespace) -> None:
        with pytest.raises(NoustError, match="Application not found"):
            receive("ghost.example.com", sent.packed.path, sha256=sent.packed.sha256)


def test_the_result_type_carries_what_the_cli_prints() -> None:
    assert {"domain", "release", "commit", "deployment_id", "manifest", "rewritten"} == set(
        release_bundles.Reception.__dataclass_fields__
    )
    assert CommandResult is not None
