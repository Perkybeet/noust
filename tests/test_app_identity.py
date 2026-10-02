"""
Tests for each application's own system account (Noust 3.2, spec section 4.2).

A new application runs as ``noust-app-<name>``: its tree, its ``.env`` and its
caches are that account's, its unit says ``User=`` it and a PHP pool runs as
it. An application from before keeps the shared account through any redeploy;
``noust app identity migrate`` moves it, behind its health gate, and puts back
exactly what it changed (files' owners, the unit or the pool, the store) when
it does not answer.
"""

# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import paths
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore, Service
from noust.managers import app_identity
from noust.managers.service_manager import ServiceManager, with_account
from tests.test_release_pipeline import (  # noqa: F401 - fixtures used by name
    DOMAIN,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    wire,
)
from tests.test_service_manager import FakeStore, unit_dirs  # noqa: F401

ACCOUNT = "noust-app-rel-example-com"


@pytest.fixture
def as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend to be root, where accounts can be created."""
    monkeypatch.setattr(app_identity, "running_as_root", lambda: True)


@pytest.fixture
def own_store(tmp_path: Path) -> Any:
    """A store of the test's own (the store is a process-wide instance)."""
    NoustStore.reset_instance()
    yield NoustStore(tmp_path / "noust.db")
    NoustStore.reset_instance()


# ---------------------------------------------------------------------------
# Names and the one answer to "as whom"
# ---------------------------------------------------------------------------


def test_an_account_is_named_after_the_application() -> None:
    assert app_identity.account_name("shop-example-com") == "noust-app-shop-example-com"


def test_a_long_name_is_shortened_uniquely_within_32_characters() -> None:
    first = app_identity.account_name("a-very-long-subdomain-of-the-shop-example-com")
    second = app_identity.account_name("a-very-long-subdomain-of-the-shop-example-org")

    assert len(first) <= 32 and len(second) <= 32
    assert first != second
    assert first.startswith("noust-app-a-very-long")
    # The store's own check of a system account name accepts it.
    import re

    assert re.match(r"^[a-z_][a-z0-9_-]{0,31}$", first)


def test_an_application_without_an_account_runs_as_the_shared_one() -> None:
    config = SimpleNamespace(service_user="www-data", service_group="www-data")

    assert app_identity.service_account(App(domain="a.example.com"), config) == (  # type: ignore[arg-type]
        "www-data",
        "www-data",
    )
    assert app_identity.service_account(None, config) == ("www-data", "www-data")  # type: ignore[arg-type]
    assert app_identity.service_account(
        App(domain="a.example.com", identity="noust-app-a"),
        config,  # type: ignore[arg-type]
    ) == ("noust-app-a", "noust-app-a")


# ---------------------------------------------------------------------------
# A new application gets its own account
# ---------------------------------------------------------------------------


def test_a_new_application_runs_as_its_own_account_and_owns_its_tree_and_env(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, as_root: None
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))

    deploy_new(root, machine)

    [useradd] = [c for c in machine.runner.calls_to("useradd") if c[-1] == ACCOUNT]
    assert useradd[:5] == ("useradd", "--system", "--user-group", "--no-create-home", "--home-dir")
    assert useradd[5] == "/nonexistent"
    assert useradd[7].endswith("nologin")
    app = store.get_app(DOMAIN)
    assert app is not None and app.identity == ACCOUNT
    release = str(next((root / "releases").iterdir()))
    owner = f"{ACCOUNT}:{ACCOUNT}"
    assert ("chown", "-R", owner, release) in machine.runner.calls
    assert ("chown", "-R", owner, str(root / "shared")) in machine.runner.calls
    assert not [c for c in machine.runner.calls if c[:3] == ("chown", "-R", "www-data:www-data")]
    service = store.get_service("rel-example-com")
    assert service is not None and (service.user, service.group) == (ACCOUNT, ACCOUNT)


def test_an_application_from_before_keeps_the_shared_account_on_a_redeploy(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, as_root: None
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    store.set_app_identity(DOMAIN, None)
    machine.git.publish(node_tree(tmp_path / "v2"))
    accounts = len(machine.runner.calls_to("useradd"))

    deploy_new(root, machine)

    app = store.get_app(DOMAIN)
    assert app is not None and app.identity is None
    assert len(machine.runner.calls_to("useradd")) == accounts
    release = (root / "current").resolve()
    assert ("chown", "-R", "www-data:www-data", str(release)) in machine.runner.calls


def test_a_redeploy_makes_sure_the_account_exists(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, as_root: None
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    machine.git.publish(node_tree(tmp_path / "v2"))
    probes = len(machine.runner.calls_to("getent"))

    deploy_new(root, machine)

    assert ("getent", "passwd", ACCOUNT) in machine.runner.calls_to("getent")[probes:]
    app = store.get_app(DOMAIN)
    assert app is not None and app.identity == ACCOUNT


def test_without_root_nothing_changes(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))

    deploy_new(root, machine)

    app = store.get_app(DOMAIN)
    assert app is not None and app.identity is None
    assert not machine.runner.calls_to("useradd")


@pytest.mark.parametrize(
    ("app_type", "is_static"), [("docker-compose", False), ("monorepo", False), ("vite", True)]
)
def test_stacks_monorepos_and_sites_with_nothing_running_get_no_account(
    tmp_path: Path, as_root: None, own_store: NoustStore, app_type: str, is_static: bool
) -> None:
    store = own_store
    store.create_app(App(domain=DOMAIN, app_type=app_type, app_path=str(tmp_path / "x")))
    runner = FakeRunner()

    account = app_identity.adopt_new_app(
        DOMAIN,
        app_name="rel-example-com",
        app_type=app_type,
        is_static=is_static,
        runner=runner,
        store=store,
        new=True,
    )

    assert account is None
    assert not runner.calls_to("useradd")


def test_an_account_that_cannot_be_created_stops_the_deploy_with_useradds_words(
    tmp_path: Path, as_root: None, own_store: NoustStore
) -> None:
    store = own_store
    store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path=str(tmp_path / "x")))
    runner = FakeRunner().script(["useradd"], exit_code=1, stderr="useradd: cannot lock")

    with pytest.raises(DeploymentError) as raised:
        app_identity.adopt_new_app(
            DOMAIN,
            app_name="rel-example-com",
            app_type="nodejs",
            is_static=False,
            runner=runner,
            store=store,
            new=True,
        )

    assert "useradd: cannot lock" in (raised.value.details or "")
    app = store.get_app(DOMAIN)
    assert app is not None and app.identity is None


# ---------------------------------------------------------------------------
# The unit and the pool
# ---------------------------------------------------------------------------


UNIT = (
    "# Generated by Noust\n[Unit]\nDescription=x\n\n[Service]\nType=simple\n"
    "User=www-data\nGroup=www-data\nExecStart=/usr/bin/node server.js\n\n"
    "[Install]\nWantedBy=multi-user.target\n"
)


def test_with_account_replaces_user_and_group_and_nothing_else() -> None:
    moved = with_account(UNIT, ACCOUNT, ACCOUNT)

    assert moved == UNIT.replace("User=www-data", f"User={ACCOUNT}").replace(
        "Group=www-data", f"Group={ACCOUNT}"
    )
    assert with_account(moved, "www-data", "www-data") == UNIT


def test_with_account_adds_what_a_unit_lacks() -> None:
    bare = "[Service]\nExecStart=/bin/true\n"

    assert with_account(bare, ACCOUNT, ACCOUNT) == (
        f"[Service]\nUser={ACCOUNT}\nGroup={ACCOUNT}\nExecStart=/bin/true\n"
    )


def test_with_account_refuses_a_name_that_would_inject_a_directive() -> None:
    with pytest.raises(ValidationError):
        with_account(UNIT, "x\nExecStartPre=/bin/sh", "x")


def test_the_unit_of_an_application_with_an_account_runs_as_it(
    runner: FakeRunner, unit_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeStore()
    fake.apps = [
        App(
            domain="shop.example.com",
            app_path="/var/www/apps/shop-example-com",
            identity="noust-app-shop-example-com",
        ),
        App(domain="old.example.com", app_path="/var/www/apps/old-example-com"),
    ]
    monkeypatch.setattr("noust.managers.service_manager.get_store", lambda: fake)
    manager = ServiceManager()

    manager.create_service(
        name="shop-example-com", command="/usr/bin/true", working_directory="/srv"
    )
    manager.create_service(
        name="old-example-com", command="/usr/bin/true", working_directory="/srv"
    )

    own = (unit_dirs["managed"] / "shop-example-com.service").read_text()
    shared = (unit_dirs["managed"] / "old-example-com.service").read_text()
    assert "User=noust-app-shop-example-com\n" in own
    assert "Group=noust-app-shop-example-com\n" in own
    assert "User=www-data\n" in shared


def test_set_unit_account_rewrites_a_managed_unit_and_returns_the_old_body(
    runner: FakeRunner, unit_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeStore()
    monkeypatch.setattr("noust.managers.service_manager.get_store", lambda: fake)
    manager = ServiceManager()
    manager.create_service(
        name="shop-example-com", command="/usr/bin/true", working_directory="/srv"
    )
    path = unit_dirs["managed"] / "shop-example-com.service"
    before = path.read_text()

    previous = manager.set_unit_account("shop-example-com", ACCOUNT, ACCOUNT)

    assert previous == before
    assert f"User={ACCOUNT}\n" in path.read_text()
    assert runner.ran("systemctl", "daemon-reload")


def test_a_php_pool_runs_as_the_applications_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, own_store: NoustStore
) -> None:
    from noust.core.config import Config
    from noust.deployers import php_fpm

    store = own_store
    store.create_app(App(domain=DOMAIN, app_type="php-fpm", app_path=str(tmp_path / "app")))
    store.set_app_identity(DOMAIN, ACCOUNT)
    monkeypatch.setattr("noust.managers.app_identity.get_store", lambda: store)
    installation = SimpleNamespace(socket=lambda name: Path(f"/run/php/{name}.sock"))

    spec = php_fpm.pool_spec_for(
        app_name="rel-example-com",
        domain=DOMAIN,
        app_path=tmp_path / "app",
        env={},
        installation=installation,  # type: ignore[arg-type]
        config=Config(),
        max_upload="64m",
        memory_max_mb=None,
        tasks_max=None,
    )

    assert (spec.user, spec.group) == (ACCOUNT, ACCOUNT)
    assert spec.listen_group != ACCOUNT, "the web server still connects to the socket"


# ---------------------------------------------------------------------------
# Moving an existing application
# ---------------------------------------------------------------------------


class Units:
    """Stands in for ServiceManager: the unit bodies, rewritten as the real one does."""

    def __init__(self, unit: str) -> None:
        self.bodies = {unit: UNIT}
        self.unit = unit

    def serving_units(self, app: App) -> list[str]:
        return [self.unit]

    def set_unit_account(self, name: str, user: str, group: str) -> str:
        previous = self.bodies[name]
        self.bodies[name] = with_account(previous, user, group)
        return previous

    def update_config(self, name: str, content: str) -> str:
        previous = self.bodies[name]
        self.bodies[name] = content
        return previous


class Gate:
    """A health gate whose answers are scripted, one per restart."""

    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.restarts = 0

    def restart_and_probe(self) -> tuple[bool, str]:
        self.restarts += 1
        healthy = self.answers.pop(0) if self.answers else True
        return healthy, "" if healthy else "Error: EACCES: permission denied, open '.env'"


@pytest.fixture
def existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_root: None) -> SimpleNamespace:
    """An in-place application from before 3.2, its unit and its store rows."""
    app_path = tmp_path / "apps" / "shop-example-com"
    app_path.mkdir(parents=True)
    (app_path / ".env").write_text("SECRET=1\n")
    cache = tmp_path / "cache" / "build"
    (cache / "shop-example-com").mkdir(parents=True)
    monkeypatch.setattr(paths, "BUILD_CACHE_DIR", cache)
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    store.create_app(
        App(domain="shop.example.com", app_type="nodejs", app_path=str(app_path), port=3000)
    )
    store.create_service(Service(name="shop-example-com", user="www-data", group="www-data"))
    yield SimpleNamespace(
        store=store,
        app_path=app_path,
        cache=cache / "shop-example-com",
        units=Units("shop-example-com"),
        runner=FakeRunner(),
    )
    NoustStore.reset_instance()


def migrate(world: SimpleNamespace, gate: Gate) -> app_identity.IdentityMigration:
    return app_identity.migrate(
        "shop.example.com",
        actor="alice",
        logger=Logger(verbose=False),
        runner=world.runner,
        store=world.store,
        services=world.units,  # type: ignore[arg-type]
        gate_for=lambda app, store, log: gate,  # type: ignore[arg-type,return-value]
    )


def chowns(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[0] == "chown"]


def test_a_migration_hands_over_what_the_shared_account_owned_and_rewrites_the_unit(
    existing: SimpleNamespace,
) -> None:
    gate = Gate(True)
    old = app_identity._owner_spec("www-data", "www-data")
    account = "noust-app-shop-example-com"

    done = migrate(existing, gate)

    assert done.account == account
    assert done.previous == ("www-data", "www-data")
    assert existing.runner.calls_to("useradd")[0][-1] == account
    assert chowns(existing.runner) == [
        ("chown", "-R", f"--from={old}", f"{account}:{account}", str(existing.app_path)),
        ("chown", "-R", f"--from={old}", f"{account}:{account}", str(existing.cache)),
    ]
    assert f"User={account}\n" in existing.units.bodies["shop-example-com"]
    app = existing.store.get_app("shop.example.com")
    assert app is not None and app.identity == account
    service = existing.store.get_service("shop-example-com")
    assert service is not None and (service.user, service.group) == (account, account)
    assert gate.restarts == 1


def test_a_migration_that_does_not_answer_puts_back_exactly_what_it_changed(
    existing: SimpleNamespace,
) -> None:
    gate = Gate(False, True)
    account = "noust-app-shop-example-com"
    new = app_identity._owner_spec(account, account)

    with pytest.raises(DeploymentError) as raised:
        migrate(existing, gate)

    assert "was not moved to its own account" in raised.value.message
    assert "answering again" in raised.value.message
    assert "EACCES" in (raised.value.details or "")
    assert "Everything was put back" in (raised.value.details or "")
    back = chowns(existing.runner)[2:]
    assert back == [
        ("chown", "-R", f"--from={new}", "www-data:www-data", str(existing.app_path)),
        ("chown", "-R", f"--from={new}", "www-data:www-data", str(existing.cache)),
    ]
    assert existing.units.bodies["shop-example-com"] == UNIT
    app = existing.store.get_app("shop.example.com")
    assert app is not None and app.identity is None
    service = existing.store.get_service("shop-example-com")
    assert service is not None and (service.user, service.group) == ("www-data", "www-data")
    assert gate.restarts == 2, "restarted again as it was"


def test_a_migration_whose_files_cannot_be_handed_over_changes_nothing_else(
    existing: SimpleNamespace,
) -> None:
    existing.runner.script(["chown"], exit_code=1, stderr="chown: changing ownership: Read-only")
    gate = Gate()

    with pytest.raises(DeploymentError) as raised:
        migrate(existing, gate)

    assert "Read-only" in (raised.value.details or "")
    assert "was not restarted" in raised.value.message
    assert gate.restarts == 0, "nothing it runs was touched"
    assert existing.units.bodies["shop-example-com"] == UNIT
    app = existing.store.get_app("shop.example.com")
    assert app is not None and app.identity is None


def test_a_migrated_application_is_not_migrated_again(existing: SimpleNamespace) -> None:
    migrate(existing, Gate(True))

    with pytest.raises(ValidationError, match="already runs as its own account"):
        migrate(existing, Gate(True))


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"app_type": "docker-compose"}, "containers"),
        ({"app_type": "monorepo"}, "several units"),
        ({"app_type": "static"}, "nothing runs"),
        ({"zero_downtime": True}, "zero-downtime"),
    ],
)
def test_what_cannot_move_is_refused_before_anything_changes(
    existing: SimpleNamespace, changes: dict[str, Any], match: str
) -> None:
    app = existing.store.get_app("shop.example.com")
    for name, value in changes.items():
        setattr(app, name, value)
    existing.store.update_app(app)
    if changes.get("zero_downtime"):
        existing.store.set_zero_downtime("shop.example.com", True)

    with pytest.raises(ValidationError, match=match):
        migrate(existing, Gate())

    assert existing.runner.calls == []


def test_a_migration_needs_root(existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_identity, "running_as_root", lambda: False)

    with pytest.raises(ValidationError, match="needs root"):
        migrate(existing, Gate())


def test_the_status_says_who_it_runs_as_and_what_it_would_become(existing: SimpleNamespace) -> None:
    before = app_identity.status("shop.example.com", store=existing.store)
    migrate(existing, Gate(True))
    after = app_identity.status("shop.example.com", store=existing.store)

    assert (before["account"], before["own"], before["proposed"]) == (
        "www-data",
        False,
        "noust-app-shop-example-com",
    )
    assert (after["account"], after["own"], after["proposed"]) == (
        "noust-app-shop-example-com",
        True,
        None,
    )


# ---------------------------------------------------------------------------
# The API: a client of the module
# ---------------------------------------------------------------------------


def identity_client(*, elevated: bool) -> Any:
    """A client for the identity router alone, authenticated, elevated or not."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from noust.web.api import identity as identity_api
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(identity_api.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


@pytest.fixture
def api_app(own_store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> NoustStore:
    """The process-wide store with one in-place application from before 3.2."""
    monkeypatch.setattr("noust.web.api.identity.get_store", lambda: own_store)
    monkeypatch.setattr("noust.managers.app_identity.get_store", lambda: own_store)
    own_store.create_app(
        App(domain="shop.example.com", app_type="nodejs", app_path="/var/www/apps/shop-example-com")
    )
    return own_store


def test_the_api_reads_the_account_and_what_a_migration_would_give(api_app: NoustStore) -> None:
    body = identity_client(elevated=False).get("/api/apps/shop.example.com/identity").json()

    assert body["own"] is False
    assert body["eligible"] is True
    assert body["proposed"] == "noust-app-shop-example-com"
    assert (
        identity_client(elevated=False).get("/api/apps/nope.example.com/identity").status_code
        == 404
    )


def test_the_api_migration_needs_sudo_mode_and_is_a_job(
    api_app: NoustStore, monkeypatch: pytest.MonkeyPatch, as_root: None
) -> None:
    from noust.web.api import identity as identity_api

    queued: list[dict[str, Any]] = []

    class Jobs:
        def create_job(self, **kwargs: Any) -> Any:
            queued.append(kwargs)
            return SimpleNamespace(
                id="j1", status=SimpleNamespace(value="pending"), to_dict=lambda: {"id": "j1"}
            )

    monkeypatch.setattr(identity_api, "get_job_manager", lambda: Jobs())

    refused = identity_client(elevated=False).post("/api/apps/shop.example.com/identity/migrate")
    accepted = identity_client(elevated=True).post("/api/apps/shop.example.com/identity/migrate")

    assert refused.status_code == 403
    assert accepted.status_code == 202
    [job] = queued
    assert job["job_type"].value == "identity_migrate"
    assert job["kwargs"]["domain"] == "shop.example.com"
    assert job["kwargs"]["actor"] == job["actor"], "the job audits who asked"
    assert job["func"] is identity_api.identity_migrate_job


def test_the_api_refuses_a_stack_before_queueing(
    api_app: NoustStore, monkeypatch: pytest.MonkeyPatch, as_root: None
) -> None:
    api_app.create_app(
        App(domain="stack.example.com", app_type="docker-compose", app_path="/srv/s")
    )

    response = identity_client(elevated=True).post("/api/apps/stack.example.com/identity/migrate")

    assert response.status_code == 400
    assert "containers" in response.text


def test_the_migration_route_is_root_equivalent() -> None:
    from noust.web.permissions.routes_identity import ROUTES

    assert ROUTES[("POST", "/api/apps/{domain}/identity/migrate")] == "root_equivalent"
    assert ROUTES[("GET", "/api/apps/{domain}/identity")] == "apps.read"


def test_only_an_application_account_is_ever_removed() -> None:
    runner = FakeRunner()
    logger = Logger(verbose=False)

    assert app_identity.remove_account("www-data", runner, logger) is False
    assert app_identity.remove_account(None, runner, logger) is False
    assert runner.calls == []
    assert app_identity.remove_account(ACCOUNT, runner, logger) is True
    assert runner.calls == [("userdel", ACCOUNT)]


def test_deleting_an_application_and_its_files_removes_its_account(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: None,
    monkeypatch: pytest.MonkeyPatch,
    runner: FakeRunner,
) -> None:
    from noust.deployers import lifecycle

    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: machine.services)
    monkeypatch.setattr(
        lifecycle,
        "delete_site_completely",
        lambda *a, **k: SimpleNamespace(certificate_removed=False, kept_operator=()),
    )

    lifecycle.delete_app(DOMAIN, remove_files=True)

    assert ("userdel", ACCOUNT) in runner.calls
