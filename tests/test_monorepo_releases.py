# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A monorepo on the release layout goes back instantly (checklist B-16 and A-53).

A monorepo used to rebuild in place: an update that broke a workspace was undone by checking out
the previous commit and building it again. Moved onto releases by ``noust app migrate``, every
update builds a release of its own, every workspace is switched to it behind the health gate, and
going back is the swap of one link.

What is pinned here, because each is a way this goes wrong without anybody noticing: the workspaces'
environment files travel to the next release by copy (so a release keeps the configuration it ran
with); the build runs as the build account in the new release with ``shared/`` visible and the
rest of the machine not; every unit is restarted and probed, and one that does not answer puts the
release that served back, units included; the migration stops, rewrites and restarts every unit,
not the first; an in-place monorepo is never converted by an update or a deploy.
"""

# The sandbox fixture is imported rather than replicated; pytest resolves it by name.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, RolledBackError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore, ReleaseRecord, Service
from noust.deployers import lifecycle, migrate, monorepo, monorepo_releases
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers import sandbox_trial
from noust.deployers.helpers.release_build import StagedRelease
from noust.deployers.helpers.workspace_gates import WorkspaceGates
from noust.deployers.monorepo import MonorepoDeployer
from noust.deployers.monorepo_releases import workspace_paths
from noust.deployers.releases import ReleaseManager
from tests.test_build_sandbox import as_root  # noqa: F401 - fixture used by name

DOMAIN = "mono.example.com"
APP = "mono-example-com"
FIRST = "20200101-000000-aaaaaaa"
COMMIT = "b" * 40
WORKSPACES = (("web", 3001), ("api", 3002))


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test directory, where every module that reads one looks."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for module in (monorepo, build_sandbox, sandbox_trial, lifecycle, migrate):
        monkeypatch.setattr(module, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing here needs real time to pass."""
    monkeypatch.setattr(monorepo.time, "sleep", lambda seconds: None)


class Probe:
    """The HTTP probe, answering from a script instead of the network."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        #: Decides, for a URL, whether it does not answer; a test replaces it.
        self.fails: Callable[[str], bool] = lambda url: False

    def __call__(self, url: str, *, on_attempt: Any = None, **kwargs: Any) -> bool:
        self.urls.append(url)
        if self.fails(url):
            if on_attempt is not None:
                on_attempt("Health check attempt 1 failed: HTTP 502")
            return False
        return True


class Units:
    """The service manager, as the workspaces' units answer it: restarts are recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        #: What ``current`` pointed at for each restart, which is the release the unit started on.
        self.on: list[str] = []
        self.root: Path | None = None

    def restart(self, name: str) -> None:
        self.calls.append(("restart", name))
        if self.root is not None:
            self.on.append(os.readlink(self.root / "current"))

    def logs(self, name: str, lines: int = 50) -> str:
        return f"{name}: Error: listen EADDRINUSE"

    def get_status(self, name: str) -> dict[str, Any]:
        return {"exists": True, "active": True}


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Probe:
    fake = Probe()
    monkeypatch.setattr(monorepo, "wait_until_healthy", fake)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", fake)
    return fake


def write_release(path: Path, *, generation: str = "old") -> Path:
    """A release of the monorepo: the workspaces, each with the file its unit loads."""
    for name, port in WORKSPACES:
        workspace = path / "apps" / name
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "package.json").write_text(f'{{"name": "{name}"}}')
        env = workspace / ".env.production"
        env.write_text(f"PORT={port}\nSECRET={generation}-{name}\n")
        env.chmod(0o600)
    (path / "package.json").write_text(json.dumps({"name": "mono", "scripts": {}}))
    return path


def released_monorepo(
    tmp_path: Path, store: NoustStore, *, layout: str = "releases", sandboxed: bool = False
) -> tuple[Path, App]:
    """A monorepo with one release serving, two workspace units, and its ``shared/.env``."""
    root = tmp_path / "apps" / APP
    first = write_release(root / "releases" / FIRST)
    (root / "shared").mkdir()
    (root / "shared" / ".env").write_text("DATABASE_URL=postgresql://x\n")
    (root / "shared" / ".env").chmod(0o600)
    (first / ".env").symlink_to("../../shared/.env")
    (root / "current").symlink_to(Path("releases") / FIRST)
    app = store.create_app(
        App(
            domain=DOMAIN,
            app_type="monorepo",
            app_path=str(root),
            port=3001,
            layout=layout,
            source="https://example.com/mono.git",
            branch="main",
            env_vars={
                "_workspaces": json.dumps(
                    [
                        {"name": name, "path": f"apps/{name}", "port": port}
                        for name, port in WORKSPACES
                    ]
                )
            },
        )
    )
    for name, port in WORKSPACES:
        store.create_service(
            Service(
                name=f"{APP}-{name}",
                app_id=app.id,
                unit_file=f"/etc/systemd/system/{APP}-{name}.service",
                working_directory=str(root / "current" / "apps" / name),
                port=port,
            )
        )
    assert app.id is not None
    store.record_release(
        ReleaseRecord(
            id=FIRST,
            app_id=app.id,
            git_commit="aaaaaaa",
            created_at="2020-01-01T00:00:00+00:00",
            status="active",
            path=str(first),
        )
    )
    store.mark_release_active(app.id, FIRST)
    if sandboxed:
        build_sandbox.adopt_new_app(DOMAIN, preview=False, store=store)
    return root, app


@pytest.fixture
def staging(monkeypatch: pytest.MonkeyPatch) -> list[StagedRelease]:
    """Replace the fetch of a release with an export of a tree that has the workspaces."""
    staged: list[StagedRelease] = []

    def fake(
        source: str,
        branch: str | None,
        *,
        releases: ReleaseManager,
        source_manager: Any,
        logger: Any,
        commit: str | None = None,
    ) -> StagedRelease:
        path = releases.new_release_dir(COMMIT)
        write_release(path, generation="fresh")
        for name, _port in WORKSPACES:
            # What a fresh checkout has: no environment file, which the deployment wrote.
            (path / "apps" / name / ".env.production").unlink()
        release = StagedRelease(path=path, commit=COMMIT, manager=releases)
        staged.append(release)
        return release

    monkeypatch.setattr(monorepo_releases, "stage_release", fake)
    return staged


def deployer(root: Path, runner: FakeRunner, units: Units) -> MonorepoDeployer:
    """The deployer lifecycle._update_release builds."""
    instance = MonorepoDeployer(runner=runner)
    instance.configure(
        DOMAIN, "https://example.com/mono.git", app_path=root, branch="main", port=3001
    )
    instance.service_manager = units  # type: ignore[assignment]
    units.root = root
    return instance


def serving(root: Path) -> str:
    """The release ``current`` points at."""
    return os.readlink(root / "current")


# ---------------------------------------------------------------------------
# Where the workspaces are
# ---------------------------------------------------------------------------


class TestWorkspacePaths:
    def test_they_come_from_the_record_and_from_the_units(self, tmp_path: Path) -> None:
        root = tmp_path / "app"
        app = App(
            domain=DOMAIN,
            app_type="monorepo",
            app_path=str(root),
            env_vars={"_workspaces": json.dumps([{"path": "apps/web"}, {"path": "apps/web"}])},
        )
        units = [
            Service(name="a", working_directory=str(root / "current" / "apps" / "api")),
            Service(name="b", working_directory=str(root / "packages" / "jobs")),
        ]

        assert workspace_paths(app, units) == ["apps/web", "apps/api", "packages/jobs"]

    def test_a_path_that_leaves_the_tree_is_not_a_workspace(self, tmp_path: Path) -> None:
        app = App(
            domain=DOMAIN,
            app_type="monorepo",
            app_path=str(tmp_path),
            env_vars={
                "_workspaces": json.dumps(
                    [{"path": "../outside"}, {"path": "/etc"}, {"path": "."}, {"path": "apps/ok"}]
                )
            },
        )
        assert workspace_paths(app) == ["apps/ok"]

    def test_a_record_that_is_not_json_is_ignored(self, tmp_path: Path) -> None:
        app = App(domain=DOMAIN, app_type="monorepo", env_vars={"_workspaces": "{not json"})
        assert workspace_paths(app) == []


# ---------------------------------------------------------------------------
# An update builds a release and switches every workspace to it
# ---------------------------------------------------------------------------


def test_an_update_builds_a_new_release_and_switches_every_workspace(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    probe: Probe,
    staging: list[StagedRelease],
) -> None:
    root, _app = released_monorepo(tmp_path, store)
    units = Units()

    result = deployer(root, runner, units).update()

    new = staging[0]
    assert serving(root) == f"releases/{new.id}"
    # The old release stays on disk to go back to.
    assert (root / "releases" / FIRST).is_dir()
    assert result.restarted == (f"{APP}-api", f"{APP}-web")
    assert set(probe.urls) == {"http://127.0.0.1:3001/", "http://127.0.0.1:3002/"}
    # Every unit was restarted on the new release, and nobody built in the live tree.
    assert units.on == [f"releases/{new.id}"] * 2
    installs = [call for call in runner.calls if call[:2] == ("pnpm", "install")]
    assert installs and all(
        str(root / "releases" / FIRST) not in str(call) for call in runner.calls
    )
    assert store.list_deployments(DOMAIN)[0].status == "success"
    assert store.list_deployments(DOMAIN)[0].release_id == new.id
    rows = {row.id: row.status for row in store.list_releases(_app.id)}
    assert rows[new.id] == "active"


def test_the_environment_of_every_workspace_is_carried_to_the_new_release(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    probe: Probe,
    staging: list[StagedRelease],
) -> None:
    root, _app = released_monorepo(tmp_path, store)

    deployer(root, runner, Units()).update()

    new = staging[0].path
    for name, port in WORKSPACES:
        carried = new / "apps" / name / ".env.production"
        assert carried.read_text() == f"PORT={port}\nSECRET=old-{name}\n"
        assert carried.stat().st_mode & 0o777 == 0o600
        # A copy, not a link: a release keeps the configuration it ran with.
        assert not carried.is_symlink()
    # The application's own .env is the usual link into shared/.
    assert os.readlink(new / ".env") == "../../shared/.env"


def test_a_link_in_the_active_release_is_not_followed_when_carrying_the_environment(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    probe: Probe,
    staging: list[StagedRelease],
) -> None:
    root, _app = released_monorepo(tmp_path, store)
    secret = tmp_path / "somebody-elses-secret"
    secret.write_text("TOKEN=1\n")
    env = root / "releases" / FIRST / "apps" / "web" / ".env.production"
    env.unlink()
    env.symlink_to(secret)

    deployer(root, runner, Units()).update()

    assert not (staging[0].path / "apps" / "web" / ".env.production").exists()
    assert (staging[0].path / "apps" / "api" / ".env.production").is_file()


def test_a_workspace_that_does_not_answer_puts_the_serving_release_back(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    probe: Probe,
    staging: list[StagedRelease],
) -> None:
    root, app = released_monorepo(tmp_path, store)
    units = Units()
    # Only the new build is broken: the release that served answers once it is back.
    probe.fails = lambda url: url.endswith(":3002/") and serving(root) != f"releases/{FIRST}"

    with pytest.raises(RolledBackError) as caught:
        deployer(root, runner, units).update()

    new = staging[0]
    error = caught.value
    assert f"release {FIRST} is active again" in error.message
    assert "HTTP 502" in (error.details or "")
    assert serving(root) == f"releases/{FIRST}"
    # Every unit was restarted twice: on the new release, then back on the one that served.
    assert units.on == [f"releases/{new.id}"] * 2 + [f"releases/{FIRST}"] * 2
    # Going back rebuilt nothing.
    assert len([c for c in runner.calls if c[:2] == ("pnpm", "build")]) == 1
    # The release that failed is gone from disk and recorded as failed.
    assert not new.path.exists()
    rows = {row.id: row.status for row in store.list_releases(app.id)}
    assert rows[new.id] == "failed"
    assert rows[FIRST] == "active"
    assert store.list_deployments(DOMAIN)[0].status == "failed"


def test_a_failed_build_leaves_what_serves_untouched(
    tmp_path: Path, store: NoustStore, probe: Probe, staging: list[StagedRelease]
) -> None:
    root, _app = released_monorepo(tmp_path, store)
    runner = FakeRunner()
    runner.script(["pnpm", "build"], exit_code=1, stderr="Type error: 'x' is not assignable")
    units = Units()

    with pytest.raises(DeploymentError):
        deployer(root, runner, units).update()

    assert serving(root) == f"releases/{FIRST}"
    assert units.calls == []
    assert not staging[0].path.exists()


def test_a_dry_run_builds_and_switches_nothing(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    probe: Probe,
    staging: list[StagedRelease],
) -> None:
    from noust.core.fs import DryRunFileSystem

    root, _app = released_monorepo(tmp_path, store)
    instance = MonorepoDeployer(runner=runner, fs=DryRunFileSystem())
    instance.configure(DOMAIN, "https://example.com/mono.git", app_path=root, branch="main")

    result = instance.update()

    assert staging == []
    assert result.restarted == ()
    assert serving(root) == f"releases/{FIRST}"


def test_a_monorepo_on_releases_is_updated_not_deployed_over(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    root, _app = released_monorepo(tmp_path, store)

    with pytest.raises(DeploymentError, match="release layout") as caught:
        deployer(root, runner, Units())._deploy()

    assert f"noust update {DOMAIN}" in (caught.value.details or "")
    assert (root / "releases" / FIRST).is_dir()


# ---------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------


def test_a_release_is_built_by_the_build_account_with_shared_visible(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    probe: Probe,
    staging: list[StagedRelease],
    as_root: Any,
) -> None:
    root, _app = released_monorepo(tmp_path, store, sandboxed=True)

    deployer(root, runner, Units()).update()

    new = staging[0].path
    installs = [spec for argv, spec in runner.sandboxed if argv[:2] == ("pnpm", "install")]
    builds = [spec for argv, spec in runner.sandboxed if argv[:2] == ("pnpm", "build")]
    assert installs and builds
    for spec in (*installs, *builds):
        assert spec.user == build_sandbox.BUILD_USER
        # The release being built is the one thing it may write (and its cache).
        assert new in spec.writable_paths
        assert root / "releases" / FIRST not in spec.writable_paths
        assert root / "current" not in spec.writable_paths
        # shared/ is visible, read-only: the .env is a link into it.
        assert root / "shared" in spec.read_only_paths
        assert spec.env_files == (root / "shared" / ".env",)
    # After the build the release goes to the units' account, and shared/ with it.
    owners = [call for call in runner.calls if call[:2] == ("chown", "-R")]
    assert any(str(new) in call for call in owners)
    assert any(str(root / "shared") in call for call in owners)


# ---------------------------------------------------------------------------
# Going back and the other callers of the gate
# ---------------------------------------------------------------------------


def test_the_gate_of_a_monorepo_is_every_workspace_at_once(
    tmp_path: Path, store: NoustStore, probe: Probe
) -> None:
    _root, app = released_monorepo(tmp_path, store)

    gate = lifecycle.health_gate_for(app, store, lifecycle.Logger())

    assert isinstance(gate, WorkspaceGates)
    assert [unit.name for unit in gate.units] == [f"{APP}-api", f"{APP}-web"]


def test_an_instant_rollback_swaps_the_link_and_restarts_every_workspace(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    probe: Probe,
    staging: list[StagedRelease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _app = released_monorepo(tmp_path, store)
    units = Units()
    deployer(root, runner, units).update()
    assert serving(root) == f"releases/{staging[0].id}"
    units.calls.clear()
    units.on.clear()
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kw: units)
    monkeypatch.setattr(lifecycle, "_confirm_release_schema", lambda *a, **k: None)
    builds_before = len([c for c in runner.calls if c[:2] == ("pnpm", "build")])

    activation = lifecycle.activate_release(DOMAIN)

    assert activation.went_back is True
    assert serving(root) == f"releases/{FIRST}"
    assert units.on == [f"releases/{FIRST}"] * 2
    assert sorted(name for _kind, name in units.calls) == [f"{APP}-api", f"{APP}-web"]
    # Nothing was built: it is a swap of what is already there.
    assert len([c for c in runner.calls if c[:2] == ("pnpm", "build")]) == builds_before


def test_the_monorepo_is_offered_the_instant_rollback_once_it_is_on_releases(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    probe: Probe,
    staging: list[StagedRelease],
) -> None:
    root, _app = released_monorepo(tmp_path, store)
    # The deployment that built the release now serving, as its migration or its update left it.
    first = store.record_deployment_start(DOMAIN, "cli", git_commit="aaaaaaa")
    store.annotate_deployment(first, release_id=FIRST)
    store.finish_deployment(first, "success")
    deployer(root, runner, Units()).update()
    records = {record.release_id: record for record in store.list_deployments(DOMAIN)}

    availability = lifecycle.rollback_availability(list(records.values()))
    methods = lifecycle.rollback_methods(list(records.values()), availability)

    # The release that serves is live; the earlier one is a swap away, and nothing is rebuilt.
    assert availability[records[staging[0].id].id or 0] is not None
    assert availability[records[FIRST].id or 0] is None
    assert methods == {records[FIRST].id: "instant"}


def test_a_monorepo_can_be_migrated_and_updated_on_releases_but_not_asked_for_new() -> None:
    from noust.deployers.helpers.layout import supports_release_layout

    assert supports_release_layout(MonorepoDeployer)
    # Not the BaseDeployer pipeline: previews, promotion and release bundles stay out of reach.
    assert getattr(MonorepoDeployer, "SUPPORTS_RELEASES", False) is False
    assert SimpleNamespace  # keep the import used by the migration tests below


# ---------------------------------------------------------------------------
# The migration onto releases moves every workspace, not the first
# ---------------------------------------------------------------------------


class UnitFiles:
    """The service manager for the migration: every unit's body in memory, restarts recorded."""

    def __init__(self, root: Path, names: list[str]) -> None:
        self.root = root
        self.names = names
        self.bodies: dict[str, str] = {}
        self.events: list[tuple[str, str]] = []

    def app_units(self, app: App) -> list[str]:
        return list(self.names)

    def serving_units(self, app: App) -> list[str]:
        return list(self.names)

    def stop(self, name: str) -> bool:
        self.events.append(("stop", name))
        return True

    def restart(self, name: str) -> None:
        link = self.root / "current"
        self.events.append(("restart", os.readlink(link) if link.is_symlink() else "in place"))

    def get_service_config(self, name: str) -> str | None:
        return self.bodies.get(name)

    def update_config(self, name: str, content: str) -> str:
        previous = self.bodies[name]
        self.bodies[name] = content
        return previous

    def logs(self, name: str, lines: int = 50) -> str:
        return f"{name}: Error: cannot start"

    def get_status(self, name: str) -> dict[str, Any]:
        return {"exists": True, "active": True}


def unit_body(root: Path, name: str, port: int) -> str:
    """A workspace's unit as a monorepo deployment wrote it."""
    return (
        "# Generated by Noust\n[Service]\n"
        f"WorkingDirectory={root}/apps/{name}\n"
        "ExecStart=/usr/bin/pnpm run start\n"
        f"EnvironmentFile={root}/apps/{name}/.env.production\n"
        f'Environment="PORT={port}"\n'
        f'Environment="HOME={root}"\n'
    )


@pytest.fixture
def in_place(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """An in-place monorepo with two units, and the machine faked around it."""
    root = tmp_path / "apps" / APP
    write_release(root)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / ".env").write_text("DATABASE_URL=postgresql://x\n")
    (root / ".env").chmod(0o600)
    (root / "node_modules").mkdir()
    (root / "node_modules" / "left-pad.js").write_text("module.exports = 1")
    app = store.create_app(
        App(
            domain=DOMAIN,
            app_type="monorepo",
            app_path=str(root),
            port=3001,
            source="https://example.com/mono.git",
            branch="main",
            env_vars={
                "_workspaces": json.dumps(
                    [{"name": n, "path": f"apps/{n}", "port": p} for n, p in WORKSPACES]
                )
            },
        )
    )
    names = [f"{APP}-{name}" for name, _port in WORKSPACES]
    units = UnitFiles(root, names)
    for (name, port), unit in zip(WORKSPACES, names, strict=True):
        units.bodies[unit] = unit_body(root, name, port)
        store.create_service(
            Service(
                name=unit,
                app_id=app.id,
                working_directory=f"{root}/apps/{name}",
                command="/usr/bin/pnpm run start",
                environment={"HOME": str(root), "PORT": str(port)},
                port=port,
            )
        )
    sites = SimpleNamespace(
        get_site_config=lambda domain: "server { proxy_pass http://127.0.0.1:3001; }",
        replace_site_config=lambda domain, text: None,
        reload=lambda: True,
    )
    probes: list[str] = []
    ports = {port: name for name, port in WORKSPACES}

    def probe(url: str, **kwargs: Any) -> bool:
        probes.append(url)
        # The workspaces answer when current has their directory: a correct migration gives it.
        name = ports[int(url.rsplit(":", 1)[1].rstrip("/"))]
        answers = (root / "current" / "apps" / name).is_dir()
        if not answers:
            kwargs["on_attempt"]("Health check attempt 1 failed: Connection refused")
        return answers

    monkeypatch.setattr(migrate, "ServiceManager", lambda **kw: units)
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kw: units)
    monkeypatch.setattr(migrate, "_webserver_for", lambda app: sites)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", probe)
    prefix = ["git", "-c", f"safe.directory={root}", "--no-optional-locks"]
    runner.script([*prefix, "rev-parse"], stdout=f"{COMMIT}\n")
    runner.script(
        [*prefix, "status"],
        stdout="\0".join(
            [
                "!! node_modules/",
                "!! .env",
                "?? apps/web/.env.production",
                "?? apps/api/.env.production",
                "",
            ]
        ),
    )
    return SimpleNamespace(root=root, app=app, units=units, probes=probes, runner=runner)


def test_the_plan_names_every_workspace_unit_and_keeps_their_environment_in_the_release(
    in_place: SimpleNamespace,
) -> None:
    plan = migrate.plan_migration(DOMAIN)

    assert plan.units == (f"{APP}-web", f"{APP}-api")
    assert plan.unit == f"{APP}-web"
    assert plan.unit_rewrite is True
    # The workspaces' env files are not "left behind": every release is given a copy.
    assert plan.untracked_files == ()
    assert any(".env.production" in warning and "copy" in warning for warning in plan.warnings)
    assert any(f"{APP}-api" in warning and f"{APP}-web" in warning for warning in plan.warnings)


def test_migrating_stops_rewrites_and_restarts_every_unit(in_place: SimpleNamespace) -> None:
    plan = migrate.plan_migration(DOMAIN)

    result = migrate.migrate(DOMAIN, plan)

    root = in_place.root
    assert result.unit_rewritten is True
    stops = [name for kind, name in in_place.units.events if kind == "stop"]
    assert stops == [f"{APP}-web", f"{APP}-api"]
    for name, _port in WORKSPACES:
        body = in_place.units.bodies[f"{APP}-{name}"]
        assert f"WorkingDirectory={root}/current/apps/{name}\n" in body
        assert f"EnvironmentFile={root}/current/apps/{name}/.env.production\n" in body
        assert f'"HOME={root}/current"' in body
    # The rows the store keeps say the same, workspace by workspace.
    rows = {s.name: s for s in migrate.get_store().list_services()}
    for unit in in_place.units.names:
        assert "/current/apps/" in rows[unit].working_directory
        assert rows[unit].environment["HOME"] == f"{root}/current"
    assert os.readlink(root / "current") == f"releases/{result.release_id}"
    release = root / "releases" / result.release_id
    assert (release / "apps" / "web" / ".env.production").read_text().startswith("PORT=3001")
    assert os.readlink(release / ".env") == "../../shared/.env"
    stored = migrate.get_store().get_app(DOMAIN)
    assert stored is not None and stored.layout == "releases"
    # Both workspaces were probed on the new layout.
    assert set(in_place.probes) == {"http://127.0.0.1:3001/", "http://127.0.0.1:3002/"}


def test_a_migration_whose_workspace_does_not_answer_puts_every_unit_back(
    in_place: SimpleNamespace,
) -> None:
    plan = migrate.plan_migration(DOMAIN)
    # The api's directory is not in the release: it never answers on the layout.
    api = in_place.root / "apps" / "api"
    for leaf in api.iterdir():
        leaf.unlink()
    api.rmdir()

    with pytest.raises(DeploymentError, match="in-place layout was put back"):
        migrate.migrate(DOMAIN, plan)

    root = in_place.root
    assert not (root / "current").exists() and not (root / "releases").exists()
    restarts = [what for kind, what in in_place.units.events if kind == "restart"]
    # Each unit was started again in place after the undo.
    assert restarts.count("in place") >= 2
    for name, _port in WORKSPACES:
        assert f"WorkingDirectory={root}/apps/{name}\n" in in_place.units.bodies[f"{APP}-{name}"]
    stored = migrate.get_store().get_app(DOMAIN)
    assert stored is not None and stored.layout != "releases"
