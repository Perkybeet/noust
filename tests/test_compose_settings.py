"""
Choosing a Compose stack's file, profiles and extra arguments (checklist A-33).

A stack kept only what its unit recorded when it was created, and an update did
not even read the profiles back, so a service under a profile was left out of
every rebuild. Now the file and profiles can be changed, and the stack can be
given a few flags of ``docker compose build`` and ``up`` and the services they
address. Everything ends up in the argv of a command run as root, so the flags
are a closed list and anything else is refused with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_compose_store
from noust.core.app_compose_store import ComposeOptions, check_args, check_options
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.docker_compose import (
    DockerComposeDeployer,
    stack_deployer,
)
from noust.deployers.helpers.compose_settings import change_compose, describe_compose, reset_compose

DOMAIN = "stack.example.com"
COMPOSE = "services:\n  web:\n    image: nginx\n    ports: ['8080:80']\n  worker:\n    image: busybox\n    profiles: [jobs]\n"


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    root = tmp_path / "apps" / "stack-example-com"
    root.mkdir(parents=True)
    (root / "docker-compose.yml").write_text(COMPOSE)
    (root / "deploy").mkdir()
    (root / "deploy" / "compose.yml").write_text(COMPOSE)
    (root / "deploy" / "compose.prod.yml").write_text(COMPOSE)
    instance.create_app(
        App(domain=DOMAIN, app_type="docker-compose", source="x", app_path=str(root))
    )
    yield instance
    NoustStore.reset_instance()


UNIT = (
    "[Service]\n"
    'Environment="COMPOSE_FILE=deploy/compose.yml"\n'
    'Environment="COMPOSE_PROFILES=web,jobs"\n'
    "ExecStart=/usr/bin/docker compose up -d --remove-orphans\n"
)


@pytest.fixture
def unit(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A service manager that holds one unit and records every unit written."""
    state: dict[str, Any] = {"text": UNIT, "written": []}

    class FakeServices:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def get_service_config(self, name: str) -> str | None:
            return state["text"]

        def create_service(self, **kwargs: Any) -> None:
            state["written"].append(kwargs)

    monkeypatch.setattr("noust.deployers.docker_compose.ServiceManager", FakeServices)
    return state


class TestTheAllowlist:
    @pytest.mark.parametrize(
        "line",
        [
            "--pull",
            "--no-cache --pull",
            "--build-arg VERSION=1.2 --build-arg=CHANNEL=beta",
            "--progress plain",
            "--with-dependencies -q",
        ],
    )
    def test_build_arguments_that_are_fine(self, line: str) -> None:
        from noust.core.app_compose_store import BUILD_SWITCHES, BUILD_VALUED

        assert check_args(line, switches=BUILD_SWITCHES, valued=BUILD_VALUED, field="build_args")

    @pytest.mark.parametrize(
        "line",
        [
            "--privileged",
            "--build-arg",
            "--build-arg NOEQUALS",
            "--build-arg 1BAD=x",
            "--progress rainbow",
            "--ssh default",
            "--push",
            "web",
            "--pull; rm -rf /",
            "--file /etc/shadow",
        ],
    )
    def test_build_arguments_that_are_not(self, line: str) -> None:
        from noust.core.app_compose_store import BUILD_SWITCHES, BUILD_VALUED

        with pytest.raises(ValidationError) as caught:
            check_args(line, switches=BUILD_SWITCHES, valued=BUILD_VALUED, field="build_args")
        assert caught.value.field == "build_args"

    def test_a_value_is_one_literal_word_never_a_shell_line(self) -> None:
        from noust.core.app_compose_store import BUILD_SWITCHES, BUILD_VALUED

        checked = check_args(
            "--build-arg 'NOTE=$(id) `id`; x'",
            switches=BUILD_SWITCHES,
            valued=BUILD_VALUED,
            field="build_args",
        )

        assert checked == ("--build-arg", "NOTE=$(id) `id`; x")

    @pytest.mark.parametrize(
        "line",
        [
            "--wait",
            "--wait-timeout 90",
            "--timeout=30",
            "-t 10",
            "--scale worker=3",
            "--force-recreate --no-deps",
        ],
    )
    def test_up_arguments_that_are_fine(self, line: str) -> None:
        options = check_options(up_args=line)
        assert options["up_args"]

    @pytest.mark.parametrize(
        "line", ["--build", "--no-build", "-d", "--detach", "--abort-on-container-exit"]
    )
    def test_up_arguments_that_are_not(self, line: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check_options(up_args=line)
        assert caught.value.field == "up_args"
        assert "Allowed:" in (caught.value.details or "")

    @pytest.mark.parametrize(
        "line", ["--wait-timeout 0", "--wait-timeout x", "--scale worker=99", "--scale =3"]
    )
    def test_up_arguments_with_a_value_out_of_range(self, line: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check_options(up_args=line)
        assert caught.value.field == "up_args"
        assert "needs a value" in str(caught.value)

    def test_a_list_is_as_good_as_a_line(self) -> None:
        assert check_options(build_args=["--pull", "--build-arg", "A=b"])["build_args"] == (
            "--pull",
            "--build-arg",
            "A=b",
        )

    def test_names(self) -> None:
        assert check_options(profiles="web, jobs  web")["profiles"] == ("web", "jobs")
        assert check_options(services=["web", "worker"])["services"] == ("web", "worker")
        for bad in ("-x", "a b;", "../x"):
            with pytest.raises(ValidationError):
                check_options(profiles=[bad])

    @pytest.mark.parametrize("path", ["/etc/passwd", "../x.yml", "a/../../x.yml", "x\x00.yml"])
    def test_a_file_outside_the_application_is_refused(self, path: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check_options(compose_file=path)
        assert caught.value.field == "compose_file"

    def test_an_empty_file_means_the_default_names(self) -> None:
        assert check_options(compose_file="  ")["compose_file"] is None


class TestTheStore:
    def test_no_row_is_plain_compose(self, store: NoustStore) -> None:
        assert app_compose_store.get_options(DOMAIN, store=store) == ComposeOptions()

    def test_set_keeps_what_is_not_named_and_survives_a_redeploy(self, store: NoustStore) -> None:
        app_compose_store.set_options(
            DOMAIN, check_options(profiles=["jobs"], up_args="--wait"), actor="t", store=store
        )
        app_compose_store.set_options(
            DOMAIN, check_options(build_args="--pull"), actor="t", store=store
        )
        app = store.get_app(DOMAIN)
        assert app is not None
        store.update_app(app)

        options = app_compose_store.get_options(DOMAIN, store=store)
        assert options.profiles == ("jobs",)
        assert options.up_args == ("--wait",)
        assert options.build_args == ("--pull",)
        assert options.customised

    def test_reset_and_delete(self, store: NoustStore) -> None:
        app_compose_store.set_options(DOMAIN, check_options(profiles=["a"]), actor="t", store=store)
        assert app_compose_store.reset_options(DOMAIN, store=store) is True
        assert app_compose_store.reset_options(DOMAIN, store=store) is False
        app_compose_store.set_options(DOMAIN, check_options(profiles=["a"]), actor="t", store=store)
        store.delete_app(DOMAIN)
        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM app_compose")
            assert cursor.fetchone()[0] == 0

    def test_an_unknown_application(self, store: NoustStore) -> None:
        with pytest.raises(NoustError):
            app_compose_store.set_options("nobody.example.com", {}, actor="t", store=store)


class TestTheDeployer:
    def test_an_update_reads_the_profiles_the_unit_activates(
        self, store: NoustStore, unit: dict[str, Any]
    ) -> None:
        app = store.get_app(DOMAIN)
        assert app is not None

        deployer = stack_deployer(app)

        assert deployer.compose_file == "deploy/compose.yml"
        assert deployer.compose_profiles == ["web", "jobs"]

    def test_what_the_operator_chose_wins_over_the_unit(
        self, store: NoustStore, unit: dict[str, Any]
    ) -> None:
        app_compose_store.set_options(
            DOMAIN,
            check_options(
                compose_file="deploy/compose.prod.yml",
                profiles=["jobs"],
                build_args="--pull",
                up_args="--wait",
                services=["web"],
            ),
            actor="t",
            store=store,
        )
        app = store.get_app(DOMAIN)
        assert app is not None

        deployer = stack_deployer(app)

        assert deployer.compose_file == "deploy/compose.prod.yml"
        assert deployer.compose_profiles == ["jobs"]
        assert deployer.compose_build_args == ("--pull",)
        assert deployer.compose_up_args == ("--wait",)
        assert deployer.compose_services == ("web",)

    def deployer(self, tmp_path: Path, runner: FakeRunner) -> DockerComposeDeployer:
        deployer = DockerComposeDeployer(runner=runner)
        deployer.configure(DOMAIN, "x", app_path=tmp_path)
        (tmp_path / "docker-compose.yml").write_text(COMPOSE)
        deployer._discover_compose_file()
        deployer.compose_profiles = ["jobs"]
        return deployer

    def test_the_build_gets_its_arguments_and_services_after_the_profiles(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner()
        deployer = self.deployer(tmp_path, runner)
        deployer.compose_build_args = ("--pull", "--build-arg", "V=1")
        deployer.compose_services = ("web",)

        deployer._build_images()

        build = next(call for call in runner.calls if "build" in call)
        assert list(build[build.index("build") :]) == [
            "build",
            "--pull",
            "--build-arg",
            "V=1",
            "web",
        ]
        assert build.index("--profile") < build.index("build")
        assert build[build.index("--profile") + 1] == "jobs"

    def test_no_cache_is_not_given_twice(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = self.deployer(tmp_path, runner)
        deployer.compose_build_args = ("--no-cache", "--pull")

        deployer._build_images(no_cache=True)

        build = next(call for call in runner.calls if "build" in call)
        assert list(build[build.index("build") :]) == ["build", "--no-cache", "--pull"]

    def test_the_recreate_gets_its_arguments_and_services(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = self.deployer(tmp_path, runner)
        deployer.compose_up_args = ("--wait", "--wait-timeout", "90")
        deployer.compose_services = ("web", "worker")

        deployer._recreate()

        up = next(call for call in runner.calls if "up" in call)
        assert list(up[up.index("up") :]) == [
            "up",
            "-d",
            "--remove-orphans",
            "--wait",
            "--wait-timeout",
            "90",
            "web",
            "worker",
        ]

    def test_a_plain_stack_is_run_as_it_always_was(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        deployer = self.deployer(tmp_path, runner)

        deployer._build_images()
        deployer._recreate()

        assert any(call[-1] == "build" for call in runner.calls)
        up = next(call for call in runner.calls if "up" in call)
        assert up[-2:] == ("-d", "--remove-orphans")


class TestChangingIt:
    def test_a_new_file_is_proved_stored_and_written_to_the_unit(
        self, store: NoustStore, unit: dict[str, Any]
    ) -> None:
        options = change_compose(
            DOMAIN,
            {"compose_file": "deploy/compose.prod.yml", "profiles": ["jobs"]},
            actor="alice",
            store=store,
        )

        assert options.compose_file == "deploy/compose.prod.yml"
        (written,) = unit["written"]
        assert written["compose_file"] == "deploy/compose.prod.yml"
        assert written["environment"] == {"COMPOSE_PROFILES": "jobs"}

    def test_a_file_that_is_not_there_changes_nothing(
        self, store: NoustStore, unit: dict[str, Any]
    ) -> None:
        with pytest.raises(DeploymentError):
            change_compose(DOMAIN, {"compose_file": "deploy/nope.yml"}, actor="a", store=store)

        assert app_compose_store.get_options(DOMAIN, store=store) == ComposeOptions()
        assert unit["written"] == []

    def test_only_arguments_leave_the_unit_alone(
        self, store: NoustStore, unit: dict[str, Any]
    ) -> None:
        change_compose(DOMAIN, {"up_args": "--wait", "services": ["web"]}, actor="a", store=store)
        assert unit["written"] == []
        assert describe_compose(DOMAIN, store=store).services == ("web",)

    def test_a_bad_value_changes_nothing_at_all(
        self, store: NoustStore, unit: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError):
            change_compose(
                DOMAIN, {"profiles": ["ok"], "up_args": "--privileged"}, actor="a", store=store
            )
        assert app_compose_store.get_options(DOMAIN, store=store) == ComposeOptions()

    def test_only_a_stack_has_them(self, store: NoustStore, tmp_path: Path) -> None:
        store.create_app(
            App(domain="node.example.com", app_type="nodejs", source="x", app_path=str(tmp_path))
        )
        for call in (
            lambda: describe_compose("node.example.com", store=store),
            lambda: change_compose("node.example.com", {"profiles": ["a"]}, actor="a", store=store),
            lambda: reset_compose("node.example.com", store=store),
        ):
            with pytest.raises(NoustError, match="not a Docker Compose"):
                call()

    def test_reset(self, store: NoustStore, unit: dict[str, Any]) -> None:
        change_compose(DOMAIN, {"up_args": "--wait"}, actor="a", store=store)
        assert reset_compose(DOMAIN, store=store) is True
        assert describe_compose(DOMAIN, store=store) == ComposeOptions()


class TestSurfaces:
    @pytest.fixture(autouse=True)
    def cli_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from noust.core.config import Config
        from noust.core.runner import set_runner

        monkeypatch.setattr(
            "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml"
        )
        Config.reset_instance()
        yield
        Config.reset_instance()
        set_runner(None)

    def invoke(self, *args: str) -> Any:
        from click.testing import CliRunner

        from noust.cli.app import cli

        return CliRunner().invoke(cli, list(args))

    def test_the_commands(self, store: NoustStore, unit: dict[str, Any]) -> None:
        shown = self.invoke("app", "compose", "show", DOMAIN, "--json")
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.output)["customised"] is False

        done = self.invoke(
            "app",
            "compose",
            "set",
            DOMAIN,
            "--profile",
            "jobs",
            "--up-args",
            "--wait --wait-timeout 60",
            "--service",
            "web",
            "--json",
        )
        assert done.exit_code == 0, done.output
        body = json.loads(done.output)
        assert body["profiles"] == ["jobs"]
        assert body["up_args"] == ["--wait", "--wait-timeout", "60"]
        assert body["services"] == ["web"]

        cleared = self.invoke(
            "app", "compose", "set", DOMAIN, "--no-profiles", "--all-services", "--json"
        )
        assert json.loads(cleared.output)["profiles"] == []
        assert json.loads(cleared.output)["services"] == []

        assert self.invoke("app", "compose", "reset", DOMAIN).exit_code == 0

    def test_a_bad_argument_names_the_field_and_the_list(
        self, store: NoustStore, unit: dict
    ) -> None:
        result = self.invoke("app", "compose", "set", DOMAIN, "--up-args", "--privileged")
        assert result.exit_code != 0
        assert result.exception.field == "up_args"

    def test_nothing_to_set_and_exclusive_options(self, store: NoustStore, unit: dict) -> None:
        assert "at least one setting" in self.invoke("app", "compose", "set", DOMAIN).output
        both = self.invoke("app", "compose", "set", DOMAIN, "--profile", "a", "--no-profiles")
        assert both.exit_code != 0 and "not both" in both.output

    def test_a_dry_run(self, store: NoustStore, unit: dict) -> None:
        result = self.invoke("--dry-run", "app", "compose", "set", DOMAIN, "--up-args", "--wait")
        assert result.exit_code == 0 and "Would set" in result.output
        assert app_compose_store.get_options(DOMAIN, store=store) == ComposeOptions()

    def test_the_api(self, store: NoustStore, unit: dict[str, Any]) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from noust.web.api import app_compose
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers, require_elevated
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_app_compose import ROUTES

        assert ROUTES[("GET", "/api/apps/{domain}/compose")] == Permission.APPS_READ
        assert ROUTES[("PUT", "/api/apps/{domain}/compose")] == Permission.APPS_MANAGE

        server = FastAPI()
        install_error_handlers(server)
        server.include_router(app_compose.router, prefix="/api/apps")
        session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
        server.dependency_overrides[get_current_session] = lambda: session
        server.dependency_overrides[require_elevated] = lambda: session
        client = TestClient(server)

        got = client.get(f"/api/apps/{DOMAIN}/compose").json()
        assert got["customised"] is False and "--wait" in got["allowed_up_args"]
        put = client.put(
            f"/api/apps/{DOMAIN}/compose", json={"profiles": ["jobs"], "build_args": ["--pull"]}
        )
        assert put.status_code == 200, put.text
        assert put.json()["profiles"] == ["jobs"]
        assert (
            client.put(f"/api/apps/{DOMAIN}/compose", json={"up_args": ["--build"]}).status_code
            == 400
        )
        assert client.put(f"/api/apps/{DOMAIN}/compose", json={}).status_code == 400
        assert client.delete(f"/api/apps/{DOMAIN}/compose").json()["customised"] is False
        assert client.get("/api/apps/nope.example.com/compose").status_code == 404
