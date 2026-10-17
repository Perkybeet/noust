"""
The ways an operator declares the system packages an application needs (checklist A-44).

``noust app packages`` and ``/api/apps/{d}/packages`` are clients of
:mod:`noust.managers.app_packages`, which ``tests/test_app_packages.py`` covers; what is pinned
here is what each adds: what the command prints and asks, the permission the routes declare (the
root-equivalent one, so a team's role never holds it), sudo mode for the write, the job that
installs, and that nothing in the answers hides what the package manager printed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_packages_store
from noust.core.store import App, NoustStore
from noust.managers.app_packages import AppPackages
from noust.web.permissions import Permission
from noust.web.permissions.roles import ROLE_PERMISSIONS
from noust.web.permissions.routes_app_packages import ROUTES
from tests.test_app_packages import APT, DOMAIN, Config, HostRunner

OTHER = "api.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for domain in (DOMAIN, OTHER):
        instance.create_app(
            App(
                domain=domain,
                app_type="nodejs",
                source="x",
                app_path=f"/tmp/{domain}",
                layout="releases",
            )
        )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> HostRunner:
    """The host this process runs the package manager on: a Debian whose runner we hold."""
    from noust.core.runner import set_runner

    runner = HostRunner()
    set_runner(runner)
    monkeypatch.setattr("noust.managers.app_packages.detect_platform", lambda *_a, **_k: APT)
    monkeypatch.setattr("noust.managers.app_packages.Config", lambda: Config())
    yield runner
    set_runner(None)


def test_the_routes_declare_their_permission_and_no_team_role_holds_it() -> None:
    assert ROUTES[("GET", "/api/apps/{domain}/packages")] == Permission.APPS_READ
    assert ROUTES[("PUT", "/api/apps/{domain}/packages")] == Permission.ROOT_EQUIVALENT
    assert ROUTES[("POST", "/api/apps/{domain}/packages/install")] == Permission.ROOT_EQUIVALENT
    from noust.web.permissions.team_roles import TENANT_PERMISSIONS

    assert Permission.ROOT_EQUIVALENT not in TENANT_PERMISSIONS
    holders = {
        name for name, held in ROLE_PERMISSIONS.items() if Permission.ROOT_EQUIVALENT in held
    }
    assert "viewer" not in holders and "operator" not in holders


@pytest.fixture
def cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from noust.core.config import Config as RealConfig

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    RealConfig.reset_instance()
    yield
    RealConfig.reset_instance()


def invoke(*args: str) -> Any:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli

    return CliRunner().invoke(root_cli, list(args))


class TestTheCommand:
    def test_set_declares_installs_and_says_what_it_did(
        self, store: NoustStore, host: HostRunner, cli_config: None
    ) -> None:
        result = invoke("app", "packages", "set", DOMAIN, "libvips-dev", "ffmpeg")

        assert result.exit_code == 0, result.output
        assert f"{DOMAIN} now needs: libvips-dev, ffmpeg" in result.output
        assert "Installed on this server: libvips-dev, ffmpeg" in result.output
        assert host.installed == {"libvips-dev", "ffmpeg"}
        assert app_packages_store.get(DOMAIN, store=store).packages == ("libvips-dev", "ffmpeg")  # type: ignore[union-attr]

    def test_set_with_no_install_only_records(
        self, store: NoustStore, host: HostRunner, cli_config: None
    ) -> None:
        result = invoke("app", "packages", "set", DOMAIN, "ffmpeg", "--no-install")

        assert result.exit_code == 0, result.output
        assert host.installs() == [] and "ffmpeg" in result.output and "missing" in result.output
        assert f"noust app packages install {DOMAIN}" in result.output

    def test_add_and_remove_keep_the_others(
        self, store: NoustStore, host: HostRunner, cli_config: None
    ) -> None:
        invoke("app", "packages", "set", DOMAIN, "a-lib", "b-lib", "--no-install")

        added = invoke("app", "packages", "add", DOMAIN, "c-lib", "--no-install")
        removed = invoke("app", "packages", "remove", DOMAIN, "a-lib")

        assert added.exit_code == 0 and removed.exit_code == 0, removed.output
        assert app_packages_store.get(DOMAIN, store=store).packages == ("b-lib", "c-lib")  # type: ignore[union-attr]
        # Removing from the list removes nothing from the host.
        assert not any("remove" in call for call in host.calls)

    def test_remove_of_a_package_not_declared(
        self, store: NoustStore, host: HostRunner, cli_config: None
    ) -> None:
        result = invoke("app", "packages", "remove", DOMAIN, "nothing")

        assert result.exit_code != 0 and "does not declare: nothing" in result.output

    def test_show_in_words_and_in_json(
        self, store: NoustStore, host: HostRunner, cli_config: None
    ) -> None:
        host.installed.add("ffmpeg")
        invoke("app", "packages", "set", DOMAIN, "ffmpeg", "libvips-dev", "--no-install")

        words = invoke("app", "packages", "show", DOMAIN).output
        shown = json.loads(invoke("app", "packages", "show", DOMAIN, "--json").output)

        assert "ffmpeg" in words and "installed" in words and "missing" in words
        assert shown["declared"] == ["ffmpeg", "libvips-dev"]
        assert shown["installed"] == ["ffmpeg"] and shown["missing"] == ["libvips-dev"]
        assert shown["manager"] == "apt" and shown["supported"] is True and shown["policy"] == []

    def test_install_brings_what_is_missing(
        self, store: NoustStore, host: HostRunner, cli_config: None
    ) -> None:
        invoke("app", "packages", "set", DOMAIN, "libvips-dev", "--no-install")

        result = invoke("app", "packages", "install", DOMAIN, "--json")

        assert json.loads(result.output)["installed_now"] == ["libvips-dev"]
        assert host.installed == {"libvips-dev"}

    def test_a_name_that_is_not_one_is_refused_before_anything_runs(
        self, store: NoustStore, host: HostRunner, cli_config: None
    ) -> None:
        result = invoke("app", "packages", "set", DOMAIN, "-y", "--no-install")

        assert result.exit_code != 0
        assert host.calls == [] or all(call[0] == "dpkg-query" for call in host.calls)
        assert app_packages_store.get(DOMAIN, store=store) is None

    def test_a_rehearsal_writes_nothing(
        self, store: NoustStore, host: HostRunner, cli_config: None
    ) -> None:
        result = invoke("--dry-run", "app", "packages", "set", DOMAIN, "ffmpeg")

        assert result.exit_code == 0, result.output
        assert "Would declare ffmpeg" in result.output
        assert app_packages_store.get(DOMAIN, store=store) is None and host.installs() == []

    def test_an_unknown_application(
        self, store: NoustStore, host: HostRunner, cli_config: None
    ) -> None:
        result = invoke("app", "packages", "show", "nope.example.com")

        assert result.exit_code != 0 and "Application not found" in str(result.exception)


def client_for(*, elevated: bool = True) -> Any:
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from noust.web.api import app_packages as api
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(api.router, prefix="/api/apps")
    session = {
        "type": "session",
        "sid": "s",
        "scope": "admin",
        "account_id": 1,
        "username": "alice",
    }
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


class TestTheApi:
    def test_it_says_what_an_application_declares_and_the_host_has(
        self, store: NoustStore, host: HostRunner
    ) -> None:
        host.installed.add("ffmpeg")
        AppPackages(store=store, runner=host, config=Config(), platform=APT).set(
            DOMAIN, ["ffmpeg", "libvips-dev"], actor="ana", install=False
        )

        body = client_for().get(f"/api/apps/{DOMAIN}/packages").json()

        assert body["declared"] == ["ffmpeg", "libvips-dev"]
        assert body["installed"] == ["ffmpeg"] and body["missing"] == ["libvips-dev"]
        assert body["manager"] == "apt" and body["supported"] is True
        assert body["updated_by"] == "ana" and body["refused"] == []

    def test_the_put_records_the_list_and_installs_nothing(
        self, store: NoustStore, host: HostRunner
    ) -> None:
        response = client_for().put(
            f"/api/apps/{DOMAIN}/packages", json={"packages": ["libvips-dev"]}
        )

        assert response.status_code == 200, response.text
        assert response.json()["declared"] == ["libvips-dev"]
        assert response.json()["updated_by"] == "alice"
        assert host.installs() == []

    def test_the_put_needs_sudo_mode_and_the_get_does_not(
        self, store: NoustStore, host: HostRunner
    ) -> None:
        client = client_for(elevated=False)

        refused = client.put(f"/api/apps/{DOMAIN}/packages", json={"packages": ["ffmpeg"]})
        read = client.get(f"/api/apps/{DOMAIN}/packages")

        assert refused.status_code == 403 and read.status_code == 200
        assert app_packages_store.get(DOMAIN, store=store) is None

    @pytest.mark.parametrize("name", ["-y", "foo bar", "foo=1", "../x", "a;b"])
    def test_a_name_that_is_not_one_is_a_400_naming_the_field(
        self, store: NoustStore, host: HostRunner, name: str
    ) -> None:
        response = client_for().put(f"/api/apps/{DOMAIN}/packages", json={"packages": [name]})

        assert response.status_code == 400
        assert response.json()["fields"]["packages"]
        assert app_packages_store.get(DOMAIN, store=store) is None

    def test_a_list_past_the_limit_is_refused(self, store: NoustStore, host: HostRunner) -> None:
        names = [f"pkg{index}" for index in range(51)]

        response = client_for().put(f"/api/apps/{DOMAIN}/packages", json={"packages": names})

        assert response.status_code == 422 or response.status_code == 400

    def test_the_policy_refuses_what_is_outside_it(
        self, store: NoustStore, host: HostRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.managers.app_packages.Config", lambda: Config(["libvips*"]))

        refused = client_for().put(f"/api/apps/{DOMAIN}/packages", json={"packages": ["nginx"]})
        allowed = client_for().put(
            f"/api/apps/{DOMAIN}/packages", json={"packages": ["libvips-dev"]}
        )

        assert refused.status_code == 400 and "nginx" in refused.text
        assert allowed.status_code == 200 and allowed.json()["policy"] == ["libvips*"]

    def test_an_unknown_application_is_a_404(self, store: NoustStore, host: HostRunner) -> None:
        client = client_for()

        assert client.get("/api/apps/nope.example.com/packages").status_code == 404
        assert (
            client.put("/api/apps/nope.example.com/packages", json={"packages": ["a"]}).status_code
            == 404
        )

    def test_the_install_is_a_job_with_the_package_managers_output(
        self, store: NoustStore, host: HostRunner
    ) -> None:
        from noust.web.api.app_packages import install_packages_job

        AppPackages(store=store, runner=host, config=Config(), platform=APT).set(
            DOMAIN, ["libvips-dev"], actor="ana", install=False
        )

        done = install_packages_job(DOMAIN, "alice")

        assert done == {"domain": DOMAIN, "installed": ["libvips-dev"], "missing": []}
        assert host.installed == {"libvips-dev"}

    def test_the_install_route_queues_the_job(
        self, store: NoustStore, host: HostRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queued: list[dict[str, Any]] = []

        class Job:
            id = "job1"

            class status:
                value = "pending"

            def to_dict(self) -> dict[str, Any]:
                return {"id": "job1", "status": "pending"}

        class Manager:
            def create_job(self, **kwargs: Any) -> Job:
                queued.append(kwargs)
                return Job()

        monkeypatch.setattr("noust.web.api.app_packages.get_job_manager", lambda: Manager())

        response = client_for().post(f"/api/apps/{DOMAIN}/packages/install")

        assert response.status_code == 202, response.text
        assert queued[0]["kwargs"] == {"domain": DOMAIN, "actor": "alice"}
        assert queued[0]["metadata"] == {"domain": DOMAIN}
        assert response.json()["job_id"] == "job1"
        assert (
            client_for(elevated=False).post(f"/api/apps/{DOMAIN}/packages/install").status_code
            == 403
        )
