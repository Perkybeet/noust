# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the one deletion of an application.

``wasm delete`` and the console's delete job used to be two implementations.
The job never took a Docker Compose stack down: it stopped the unit, and a
unit that would not stop left its unit file behind, while the stack's
containers kept running from a directory the job then deleted. Both now go
through :func:`noust.deployers.lifecycle.delete_app`, which takes the stack
down (keeping its volumes unless told otherwise), attempts every step
whatever the one before it did, and holds the application's lock.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.applock import AppBusyError
from noust.core.config import Config
from noust.core.exceptions import NoustError, ServiceError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore, Service, Site
from noust.deployers import lifecycle
from noust.managers.webserver import SiteDeletion
from noust.web.jobs import Job, JobContext, JobType, delete_app_job
from tests.test_applock import Holder

DOMAIN = "shop.example.com"


@pytest.fixture(autouse=True)
def apps_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Noust's apps directory, in the test directory: what is outside it was adopted."""
    apps = tmp_path / "apps"
    monkeypatch.setattr(Config, "apps_directory", property(lambda _self: apps))
    return apps


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test directory, where every module looks."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


class FakeUnits:
    """Stands in for ServiceManager, recording what was removed."""

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.refuse: set[str] = set()

    def delete_service(self, name: str) -> None:
        if name in self.refuse:
            raise ServiceError(f"Failed to stop {name}", details="Job canceled")
        self.deleted.append(name)


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Any:
    """The units, the sites and docker, faked."""
    units = FakeUnits()
    sites: list[tuple[str, bool]] = []

    def delete_site(domain: str, **kwargs: Any) -> SiteDeletion:
        sites.append((domain, kwargs["delete_certificate"]))
        return SiteDeletion(domain=domain, nginx_removed=True, certificate_removed=True)

    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: units)
    monkeypatch.setattr(lifecycle, "delete_site_completely", delete_site)
    return type("Machine", (), {"units": units, "sites": sites, "runner": runner})


def compose_app(store: NoustStore, root: Path) -> App:
    """A Docker Compose application with a bind-mounted data directory."""
    root.mkdir(parents=True)
    (root / "docker-compose.prod.yml").write_text("services:\n  db:\n    image: postgres\n")
    (root / "data").mkdir()
    (root / "data" / "PG_VERSION").write_text("16")
    app = store.create_app(
        App(domain=DOMAIN, app_type="docker-compose", port=8080, app_path=str(root))
    )
    store.create_service(
        Service(app_id=app.id, name=root.name, command="docker compose up", working_directory="")
    )
    store.create_site(Site(app_id=app.id, domain=DOMAIN, proxy_port=8080))
    return app


def downs(runner: FakeRunner) -> list[tuple[str, ...]]:
    """Every ``docker compose ... down`` that ran."""
    return [call for call in runner.calls if call[:2] == ("docker", "compose") and "down" in call]


def job_context() -> JobContext:
    """A context for running a job function outside the job manager."""
    return JobContext(Job(id="job-1", type=JobType.DELETE, name="delete", description=""), print)


def test_a_compose_stack_is_taken_down_and_its_volumes_kept(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    root = tmp_path / "apps" / "shop-example-com"
    compose_app(store, root)

    outcome = lifecycle.delete_app(DOMAIN)

    (down,) = downs(machine.runner)
    assert str(root / "docker-compose.prod.yml") in down
    assert "--volumes" not in down
    assert outcome.containers_stopped and not outcome.volumes_removed
    assert machine.units.deleted == ["shop-example-com"]
    assert machine.sites == [(DOMAIN, True)]
    assert not root.exists() and outcome.files_removed
    assert store.get_app(DOMAIN) is None
    assert store.get_service("shop-example-com") is None


def test_volumes_go_only_when_asked_for(tmp_path: Path, store: NoustStore, machine: Any) -> None:
    compose_app(store, tmp_path / "apps" / "shop-example-com")

    outcome = lifecycle.delete_app(DOMAIN, remove_volumes=True)

    (down,) = downs(machine.runner)
    assert "--volumes" in down
    assert outcome.volumes_removed


def test_the_console_job_takes_the_same_path(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    """The job never ran compose down: its containers kept running."""
    root = tmp_path / "apps" / "shop-example-com"
    compose_app(store, root)

    result = delete_app_job(DOMAIN, remove_files=False, job_context=job_context())

    (down,) = downs(machine.runner)
    assert "--volumes" not in down
    assert machine.units.deleted == ["shop-example-com"]
    assert (root / "data" / "PG_VERSION").is_file(), "files kept, as asked"
    assert store.get_app(DOMAIN) is None
    assert result["status"] == "deleted"
    assert result["volumes_removed"] is False


def test_a_unit_that_will_not_stop_does_not_stop_the_rest(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    root = tmp_path / "apps" / "shop-example-com"
    compose_app(store, root)
    machine.units.refuse.add("shop-example-com")

    outcome = lifecycle.delete_app(DOMAIN)

    assert any("shop-example-com was not removed" in w for w in outcome.warnings)
    assert machine.sites == [(DOMAIN, True)]
    assert not root.exists()
    assert store.get_app(DOMAIN) is None


def test_every_unit_of_the_application_is_removed(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    """A monorepo runs one unit per workspace; deleting only the first left the others."""
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True)
    app = store.create_app(App(domain=DOMAIN, app_type="monorepo", app_path=str(root)))
    for name in ("shop-example-com-web", "shop-example-com-api"):
        store.create_service(Service(app_id=app.id, name=name, command="x", working_directory=""))

    lifecycle.delete_app(DOMAIN, remove_certificate=False)

    assert sorted(machine.units.deleted) == ["shop-example-com-api", "shop-example-com-web"]
    assert machine.sites == [(DOMAIN, False)]
    assert downs(machine.runner) == []


def test_a_deletion_is_refused_while_another_operation_runs(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    root = tmp_path / "apps" / "shop-example-com"
    compose_app(store, root)

    with Holder(DOMAIN, "update"), pytest.raises(AppBusyError, match="update started at"):
        lifecycle.delete_app(DOMAIN)

    assert downs(machine.runner) == []
    assert (root / "data" / "PG_VERSION").is_file()
    assert store.get_app(DOMAIN) is not None


def test_nothing_deployed_is_an_error(tmp_path: Path, store: NoustStore, machine: Any) -> None:
    with pytest.raises(NoustError, match="Application not found"):
        lifecycle.delete_app("other.example.com")


def test_the_images_an_update_kept_for_going_back_go_with_the_stack(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    """
    Each update tags what served as <project>-<service>:wasm-previous so that
    it survives a prune; deleting the stack must not leave them on disk for
    ever. Only this stack's are removed.
    """
    compose_app(store, tmp_path / "apps" / "shop-example-com")
    machine.runner.script(
        ["docker", "image", "ls"],
        stdout="shop-example-com-db:wasm-previous\nother-app-web:wasm-previous\n",
    )

    outcome = lifecycle.delete_app(DOMAIN)

    removed = [call for call in machine.runner.calls if call[:3] == ("docker", "image", "rm")]
    assert removed == [("docker", "image", "rm", "shop-example-com-db:wasm-previous")]
    assert outcome.warnings == ()
    rm_at = machine.runner.calls.index(removed[0])
    down_at = machine.runner.calls.index(downs(machine.runner)[0])
    assert down_at < rm_at, "the containers are gone before their images"


def test_a_kept_image_that_cannot_be_removed_is_a_warning(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    """The rest of the deletion goes on; the operator is told what is left."""
    compose_app(store, tmp_path / "apps" / "shop-example-com")
    machine.runner.script(["docker", "image", "ls"], stdout="shop-example-com-db:wasm-previous\n")
    machine.runner.script(
        ["docker", "image", "rm"], exit_code=1, stderr="Error: image is being used"
    )

    outcome = lifecycle.delete_app(DOMAIN)

    assert any("image is being used" in warning for warning in outcome.warnings)
    assert store.get_app(DOMAIN) is None


def test_an_operator_s_site_is_kept_and_said_so(
    tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    """Deleting an application is not ``noust site delete``: a site Noust did not write stays."""
    asked: list[dict[str, Any]] = []

    def delete_site(domain: str, **kwargs: Any) -> SiteDeletion:
        asked.append(kwargs)
        return SiteDeletion(domain=domain, kept_operator=("nginx",))

    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: FakeUnits())
    monkeypatch.setattr(lifecycle, "delete_site_completely", delete_site)
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True)
    store.create_app(App(domain=DOMAIN, app_type="nodejs", port=3000, app_path=str(root)))

    outcome = lifecycle.delete_app(DOMAIN)

    assert asked[0]["keep_operator_sites"] is True
    # Kept on purpose, so said but not a failure: `noust delete` of an adopted stack with a
    # hand-written site used to exit 1 for doing exactly what it should (found by X2).
    assert any("noust site delete" in note for note in outcome.kept)
    assert outcome.warnings == ()


# ---------------------------------------------------------------------------
# An adopted stack's directory is the operator's (owner's 3.2 integration)
# ---------------------------------------------------------------------------


def test_an_adopted_directory_is_kept_and_said_so(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    """
    ``noust app adopt`` registers a stack where it already ran (/opt/proggest);
    Noust never created that directory, and removing the files of the
    application must not remove it. The rest goes as usual.
    """
    root = tmp_path / "opt" / "proggest"
    compose_app(store, root)
    logged: list[str] = []
    log = lifecycle.Logger()
    log.info = logged.append  # type: ignore[method-assign]

    outcome = lifecycle.delete_app(DOMAIN, logger=log)

    assert (root / "data" / "PG_VERSION").is_file(), "the adopted directory stays, whole"
    assert not outcome.files_removed
    assert outcome.kept_directory == str(root)
    assert outcome.warnings == ()
    assert any(str(root) in line and "--remove-adopted-directory" in line for line in logged)
    # Containers, unit, Noust's site and the rows went as usual; volumes kept.
    (down,) = downs(machine.runner)
    assert "--volumes" not in down
    assert machine.units.deleted == ["proggest"]
    assert machine.sites == [(DOMAIN, True)]
    assert store.get_app(DOMAIN) is None


def test_naming_the_adopted_directory_removes_it(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    root = tmp_path / "opt" / "proggest"
    compose_app(store, root)

    outcome = lifecycle.delete_app(DOMAIN, remove_adopted_directory=str(root) + "/")

    assert not root.exists()
    assert outcome.files_removed and outcome.kept_directory is None


def test_naming_another_directory_is_refused_before_anything_changes(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    root = tmp_path / "opt" / "proggest"
    compose_app(store, root)

    with pytest.raises(ValidationError, match="not the directory of"):
        lifecycle.delete_app(DOMAIN, remove_adopted_directory=str(tmp_path / "opt"))

    assert downs(machine.runner) == []
    assert machine.units.deleted == []
    assert root.is_dir() and store.get_app(DOMAIN) is not None


def test_naming_the_directory_while_keeping_the_files_is_refused(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    root = tmp_path / "opt" / "proggest"
    compose_app(store, root)

    with pytest.raises(ValidationError, match="keep"):
        lifecycle.delete_app(DOMAIN, remove_files=False, remove_adopted_directory=str(root))

    assert downs(machine.runner) == [] and root.is_dir()


def test_a_link_from_the_apps_directory_to_elsewhere_is_not_followed(
    tmp_path: Path, store: NoustStore, machine: Any, apps_directory: Path
) -> None:
    """Where the tree really is decides, not how the store names it."""
    real = tmp_path / "srv" / "shop"
    real.mkdir(parents=True)
    (real / "index.js").write_text("x")
    apps_directory.mkdir()
    link = apps_directory / "shop-example-com"
    link.symlink_to(real)
    store.create_app(App(domain=DOMAIN, app_type="nodejs", port=3000, app_path=str(link)))

    outcome = lifecycle.delete_app(DOMAIN)

    assert (real / "index.js").is_file()
    assert outcome.kept_directory == str(link)


def test_the_console_job_keeps_an_adopted_directory_too(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    root = tmp_path / "opt" / "proggest"
    compose_app(store, root)

    result = delete_app_job(DOMAIN, remove_files=True, job_context=job_context())

    assert root.is_dir()
    assert result["files_removed"] is False
    assert result["kept_directory"] == str(root)


def test_the_console_job_removes_the_directory_it_is_given(
    tmp_path: Path, store: NoustStore, machine: Any
) -> None:
    root = tmp_path / "opt" / "proggest"
    compose_app(store, root)

    result = delete_app_job(
        DOMAIN, remove_files=True, remove_adopted_directory=str(root), job_context=job_context()
    )

    assert not root.exists()
    assert result["files_removed"] is True


# ---------------------------------------------------------------------------
# A stack updated through relays: its servers files go with it (C2)
# ---------------------------------------------------------------------------


@pytest.fixture
def upstreams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The servers files' directory, in the test directory."""
    from noust.managers import webserver

    base = tmp_path / "noust-upstreams"
    base.mkdir()
    monkeypatch.setattr(webserver, "NGINX_UPSTREAMS_DIR", base)
    return base


def relayed_app(store: NoustStore, root: Path, upstreams: Path) -> Path:
    """A Compose stack in zero-downtime mode, with its servers file."""
    compose_app(store, root)
    store.set_zero_downtime(DOMAIN, True)
    servers = upstreams / "shop-example-com"
    servers.mkdir()
    (servers / "web.servers").write_text("server 127.0.0.1:8080;\n")
    return servers


def test_a_relayed_stack_takes_its_servers_files_with_it(
    tmp_path: Path, store: NoustStore, machine: Any, upstreams: Path
) -> None:
    servers = relayed_app(store, tmp_path / "apps" / "shop-example-com", upstreams)

    outcome = lifecycle.delete_app(DOMAIN)

    assert not servers.exists()
    # A stack has no blue/green instances to tear down.
    assert not any("instance" in warning for warning in outcome.warnings), outcome.warnings
    assert machine.units.deleted == ["shop-example-com"]


def test_servers_files_an_operator_site_still_includes_stay(
    tmp_path: Path,
    store: NoustStore,
    machine: Any,
    upstreams: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nginx refuses a site that includes a file that is not there."""
    servers = relayed_app(store, tmp_path / "apps" / "shop-example-com", upstreams)
    monkeypatch.setattr(
        lifecycle,
        "delete_site_completely",
        lambda domain, **kwargs: SiteDeletion(domain=domain, kept_operator=("nginx",)),
    )

    outcome = lifecycle.delete_app(DOMAIN)

    assert (servers / "web.servers").is_file()
    assert any(str(servers) in note for note in outcome.kept), outcome.kept
    assert outcome.warnings == ()
