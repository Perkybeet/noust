"""
Registry accounts: add, test, attach and use (checklist A-50).

Docker writes the credential it is given to a file in clear, so an account is
logged in to a configuration directory of the command's own and the directory is
removed with it. The password is on standard input, never in an argument, and
never in a row or a response; an Amazon ECR account trades its AWS keys for a
token that is good for twelve hours.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import NoustError, ValidationError
from noust.core.fs import RealFileSystem
from noust.core.registry_store import RegistryStore, check_login, check_name
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers.registry_logins import RegistryManager

PASSWORD = "ghp_tOpSeCrEtToKeN1234567890abcdef"
DOMAIN = "stack.example.com"


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(domain=DOMAIN, app_type="docker-compose", source="x", app_path=str(tmp_path / "stack"))
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "secrets", fs=RealFileSystem())


def manager(secrets: SecretStore, runner: FakeRunner, store: NoustStore) -> RegistryManager:
    return RegistryManager(
        store=RegistryStore(store), secrets=secrets, runner=runner, fs=RealFileSystem()
    )


def everything(runner: FakeRunner) -> str:
    """Every argv and environment the runner saw, as one text."""
    parts = [" ".join(call) for call in runner.calls]
    parts += [json.dumps(env) for env in runner.envs if env]
    return "\n".join(parts)


class TestChecks:
    @pytest.mark.parametrize("name", ["hub", "ghcr-acme", "a.b_c", "x" * 63])
    def test_names_that_are_fine(self, name: str) -> None:
        assert check_name(name) == name

    @pytest.mark.parametrize("name", ["", "Hub", "-x", "a b", "a/b", "x" * 64, ".x"])
    def test_names_that_are_not(self, name: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check_name(name)
        assert caught.value.field == "name"

    def test_a_kind_with_its_own_host_needs_none(self) -> None:
        assert check_login("ghcr", None, "me") == ("ghcr.io", "me")
        assert check_login("dockerhub", None, "me") == ("docker.io", "me")
        assert check_login("gitlab", None, "me") == ("registry.gitlab.com", "me")

    @pytest.mark.parametrize("kind", ["ecr", "acr", "gar", "custom"])
    def test_the_others_need_their_host(self, kind: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check_login(kind, None, "me")
        assert caught.value.field == "server"

    def test_an_ecr_host_names_its_region(self) -> None:
        host = "123456789012.dkr.ecr.eu-west-1.amazonaws.com"
        assert check_login("ecr", host, "AKIA")[0] == host
        with pytest.raises(ValidationError):
            check_login("ecr", "registry.example.com", "AKIA")

    @pytest.mark.parametrize("server", ["https://ghcr.io", "ghcr.io/org", "a b", "-x"])
    def test_a_host_is_a_host(self, server: str) -> None:
        with pytest.raises(ValidationError):
            check_login("custom", server, "me")

    @pytest.mark.parametrize("user", ["", "a b", "me\nyou", "x" * 300])
    def test_a_user_that_would_split_the_command_is_refused(self, user: str) -> None:
        with pytest.raises(ValidationError):
            check_login("ghcr", None, user)

    def test_an_unknown_kind(self) -> None:
        with pytest.raises(ValidationError) as caught:
            check_login("quay", "quay.io", "me")
        assert caught.value.field == "kind"


class TestAccounts:
    def test_add_keeps_the_password_in_the_secret_store_and_nowhere_else(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        runner = FakeRunner()
        registries = manager(secrets, runner, store)

        login = registries.add("ghcr-acme", "ghcr", "acme-bot", PASSWORD, actor="alice")

        assert (login.server, login.username, login.kind) == ("ghcr.io", "acme-bot", "ghcr")
        assert secrets.read("registry/ghcr-acme") == PASSWORD
        with store._transaction() as cursor:
            cursor.execute("SELECT * FROM registry_logins")
            assert PASSWORD not in json.dumps([tuple(row) for row in cursor.fetchall()])
        assert PASSWORD not in json.dumps(login.to_dict())

    def test_a_name_is_taken_once(self, store: NoustStore, secrets: SecretStore) -> None:
        registries = manager(secrets, FakeRunner(), store)
        registries.add("hub", "dockerhub", "me", PASSWORD)
        with pytest.raises(ValidationError) as caught:
            registries.add("hub", "dockerhub", "me", PASSWORD)
        assert caught.value.field == "name"
        assert secrets.read("registry/hub") == PASSWORD

    def test_an_empty_password_is_refused_before_anything_is_stored(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        registries = manager(secrets, FakeRunner(), store)
        with pytest.raises(ValidationError) as caught:
            registries.add("hub", "dockerhub", "me", "  ")
        assert caught.value.field == "password"
        assert registries.accounts.get("hub") is None

    def test_update_changes_what_it_is_given_and_forgets_the_last_test(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        registries = manager(secrets, FakeRunner(), store)
        registries.add("hub", "dockerhub", "me", PASSWORD)
        registries.accounts.record_test("hub", True, None)

        updated = registries.update("hub", username="you", password="new-token")

        assert updated.username == "you"
        assert updated.last_status is None
        assert secrets.read("registry/hub") == "new-token"

    def test_remove_takes_the_password_and_says_who_used_it(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        registries = manager(secrets, FakeRunner(), store)
        registries.add("hub", "dockerhub", "me", PASSWORD)
        registries.attach(DOMAIN, ["hub"])

        assert registries.remove("hub") == [DOMAIN]
        assert registries.accounts.get("hub") is None
        assert secrets.read("registry/hub") is None
        assert registries.accounts.assigned_to(DOMAIN) == []

    def test_an_unknown_application_or_account_cannot_be_attached(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        registries = manager(secrets, FakeRunner(), store)
        registries.add("hub", "dockerhub", "me", PASSWORD)
        with pytest.raises(NoustError):
            registries.attach("nobody.example.com", ["hub"])
        with pytest.raises(NoustError):
            registries.attach(DOMAIN, ["nope"])
        assert registries.accounts.assigned_to(DOMAIN) == []

    def test_attaching_replaces_and_empties(self, store: NoustStore, secrets: SecretStore) -> None:
        registries = manager(secrets, FakeRunner(), store)
        registries.add("a", "dockerhub", "me", PASSWORD)
        registries.add("b", "ghcr", "me", PASSWORD)
        registries.attach(DOMAIN, ["a", "b", "a"])
        assert [x.name for x in registries.accounts.assigned_to(DOMAIN)] == ["a", "b"]
        registries.attach(DOMAIN, ["b"])
        assert [x.name for x in registries.accounts.assigned_to(DOMAIN)] == ["b"]
        registries.attach(DOMAIN, [])
        assert registries.accounts.assigned_to(DOMAIN) == []

    def test_deleting_the_application_forgets_its_assignments(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        registries = manager(secrets, FakeRunner(), store)
        registries.add("hub", "dockerhub", "me", PASSWORD)
        registries.attach(DOMAIN, ["hub"])
        store.delete_app(DOMAIN)
        assert registries.accounts.get("hub").apps == ()


class TestALogin:
    def test_the_password_is_on_standard_input_and_the_login_is_private(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        runner = FakeRunner()
        registries = manager(secrets, runner, store)
        registries.add("ghcr-acme", "ghcr", "acme-bot", PASSWORD)
        registries.attach(DOMAIN, ["ghcr-acme"])

        with registries.session(DOMAIN) as env:
            config = Path(env["DOCKER_CONFIG"])
            assert config.is_dir()
            assert oct(config.stat().st_mode & 0o777) == "0o700"

        (login,) = runner.calls
        assert login == ("docker", "login", "ghcr.io", "--username", "acme-bot", "--password-stdin")
        assert runner.inputs == [PASSWORD]
        assert PASSWORD not in everything(runner)
        assert runner.envs[0] == {"DOCKER_CONFIG": str(config)}
        # Gone with the block, however it ended.
        assert not config.exists()

    def test_the_directory_is_removed_when_the_block_fails(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        registries = manager(secrets, FakeRunner(), store)
        registries.add("hub", "dockerhub", "me", PASSWORD)
        registries.attach(DOMAIN, ["hub"])
        seen: list[str] = []

        with pytest.raises(RuntimeError), registries.session(DOMAIN) as env:
            seen.append(env["DOCKER_CONFIG"])
            raise RuntimeError("the build failed")

        assert not Path(seen[0]).exists()

    def test_an_application_with_no_account_runs_docker_as_it_always_did(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        runner = FakeRunner()
        with manager(secrets, runner, store).session(DOMAIN) as env:
            assert env == {}
        assert runner.calls == []

    def test_every_attached_account_is_logged_in_to_the_same_directory(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        runner = FakeRunner()
        registries = manager(secrets, runner, store)
        registries.add("a", "dockerhub", "me", "pw-a")
        registries.add("b", "ghcr", "me", "pw-b")
        registries.attach(DOMAIN, ["a", "b"])

        with registries.session(DOMAIN):
            pass

        assert [call[2] for call in runner.calls] == ["docker.io", "ghcr.io"]
        assert runner.inputs == ["pw-a", "pw-b"]
        assert len({env["DOCKER_CONFIG"] for env in runner.envs}) == 1

    def test_a_registry_that_refuses_is_said_without_repeating_the_password(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        runner = FakeRunner().script(
            ["docker", "login"], exit_code=1, stderr=f"unauthorized: bad credentials {PASSWORD}"
        )
        registries = manager(secrets, runner, store)
        registries.add("hub", "dockerhub", "me", PASSWORD)
        registries.attach(DOMAIN, ["hub"])

        from noust.core.exceptions import DockerError

        with pytest.raises(DockerError) as caught, registries.session(DOMAIN):
            pass

        assert "docker.io did not accept the login hub" in caught.value.message
        assert PASSWORD not in caught.value.message + caught.value.details

    def test_a_password_that_went_missing_is_said_with_how_to_set_it_again(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        registries = manager(secrets, FakeRunner(), store)
        registries.add("hub", "dockerhub", "me", PASSWORD)
        registries.attach(DOMAIN, ["hub"])
        secrets.delete("registry/hub")

        from noust.core.exceptions import DeploymentError

        with pytest.raises(DeploymentError) as caught, registries.session(DOMAIN):
            pass
        assert "noust registry update hub --password-stdin" in caught.value.details


class TestAmazonEcr:
    HOST = "123456789012.dkr.ecr.eu-west-1.amazonaws.com"

    def test_the_keys_are_traded_for_a_token_in_the_environment(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        runner = FakeRunner().script(["aws", "ecr", "get-login-password"], stdout="ecr-token-xyz\n")
        registries = manager(secrets, runner, store)
        registries.add("ecr", "ecr", "AKIAEXAMPLE", "aws-secret-key", server=self.HOST)
        registries.attach(DOMAIN, ["ecr"])

        with registries.session(DOMAIN):
            pass

        aws, login = runner.calls
        assert aws == ("aws", "ecr", "get-login-password", "--region", "eu-west-1")
        assert runner.envs[0] == {
            "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "aws-secret-key",
        }
        assert login[:6] == ("docker", "login", self.HOST, "--username", "AWS", "--password-stdin")
        assert runner.inputs[1] == "ecr-token-xyz"
        assert "aws-secret-key" not in " ".join(c for call in runner.calls for c in call)

    def test_without_the_aws_cli_it_says_so(self, store: NoustStore, secrets: SecretStore) -> None:
        runner = FakeRunner().only_knows("docker")
        registries = manager(secrets, runner, store)
        registries.add("ecr", "ecr", "AKIAEXAMPLE", "aws-secret-key", server=self.HOST)

        tested = registries.test("ecr")

        assert tested.last_status == "failed"
        assert "AWS CLI" in (tested.last_error or "")


class TestATest:
    def test_a_good_account_is_recorded_ok_and_leaves_nothing(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        runner = FakeRunner()
        registries = manager(secrets, runner, store)
        registries.add("hub", "dockerhub", "me", PASSWORD)

        tested = registries.test("hub")

        assert tested.last_status == "ok" and tested.last_tested_at
        assert tested.last_error is None
        assert not list((secrets.root / "registry-sessions").glob("*"))

    def test_a_refused_account_is_a_result_not_an_error(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        runner = FakeRunner().script(["docker", "login"], exit_code=1, stderr="unauthorized")
        registries = manager(secrets, runner, store)
        registries.add("hub", "dockerhub", "me", PASSWORD)

        tested = registries.test("hub")

        assert tested.last_status == "failed"
        assert "unauthorized" in (tested.last_error or "")

    def test_an_unknown_account(self, store: NoustStore, secrets: SecretStore) -> None:
        with pytest.raises(NoustError):
            manager(secrets, FakeRunner(), store).test("nope")


class TestWhereItIsUsed:
    def test_a_compose_stack_runs_docker_with_the_login_during_a_deploy_and_not_after(
        self,
        store: NoustStore,
        secrets: SecretStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.core import secrets as secrets_module
        from noust.deployers.docker_compose import DockerComposeDeployer

        monkeypatch.setattr(secrets_module, "secrets_dir", lambda: tmp_path / "secrets")
        runner = FakeRunner()
        registries = RegistryManager(
            store=RegistryStore(store),
            secrets=SecretStore(root=tmp_path / "secrets", fs=RealFileSystem()),
            runner=runner,
            fs=RealFileSystem(),
        )
        registries.add("ghcr-acme", "ghcr", "bot", PASSWORD)
        registries.attach(DOMAIN, ["ghcr-acme"])
        deployer = DockerComposeDeployer(verbose=False, runner=runner)
        deployer.domain = DOMAIN
        deployer.app_path = tmp_path / "stack"
        deployer.app_path.mkdir()
        monkeypatch.setattr(
            "noust.managers.registry_logins.SecretStore",
            lambda: SecretStore(root=tmp_path / "secrets", fs=RealFileSystem()),
        )

        with deployer._registry_session():
            deployer._run(["docker", "compose", "pull"])
        deployer._run(["docker", "compose", "ps"])

        pull_env = runner.envs[runner.calls.index(("docker", "compose", "pull"))]
        ps_env = runner.envs[runner.calls.index(("docker", "compose", "ps"))]
        assert pull_env is not None and "DOCKER_CONFIG" in pull_env
        assert not ps_env

    def test_a_first_deployment_names_its_accounts_and_gets_them_attached(
        self,
        store: NoustStore,
        secrets: SecretStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.deployers.docker_compose import DockerComposeDeployer

        runner = FakeRunner()
        shared = SecretStore(root=tmp_path / "secrets", fs=RealFileSystem())
        monkeypatch.setattr("noust.managers.registry_logins.SecretStore", lambda: shared)
        registries = RegistryManager(
            store=RegistryStore(store), secrets=shared, runner=runner, fs=RealFileSystem()
        )
        registries.add("hub", "dockerhub", "me", PASSWORD)
        deployer = DockerComposeDeployer(verbose=False, runner=runner)
        deployer.domain = DOMAIN
        deployer.registry_logins = ["hub"]

        with deployer._registry_session():
            assert deployer._registry_env

        assert [x.name for x in registries.accounts.assigned_to(DOMAIN)] == ["hub"]
        assert not deployer._registry_env


def api_client() -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from noust.web.api import registries
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(registries.router, prefix="/api/registries")
    app.include_router(registries.apps_router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    return TestClient(app)


class TestTheApi:
    @pytest.fixture(autouse=True)
    def wired(
        self, store: NoustStore, secrets: SecretStore, monkeypatch: pytest.MonkeyPatch
    ) -> FakeRunner:
        runner = FakeRunner()
        monkeypatch.setattr(
            "noust.web.api.registries._manager", lambda: manager(secrets, runner, store)
        )
        return runner

    def test_the_permissions_and_the_scope(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_registries import ROUTES
        from noust.web.permissions.scopes import INSTANCE, rule_for

        assert ROUTES[("GET", "/api/registries")] == Permission.APPS_READ
        assert ROUTES[("POST", "/api/registries")] == Permission.APPS_MANAGE
        for method, template in ROUTES:
            if template.startswith("/api/registries"):
                assert rule_for(method, template).scoping == INSTANCE
        assert rule_for("PUT", "/api/apps/{domain}/registry-logins").scoping == INSTANCE

    def test_add_list_show_and_no_password_anywhere(self) -> None:
        client = api_client()

        created = client.post(
            "/api/registries",
            json={"name": "ghcr-acme", "kind": "ghcr", "username": "bot", "password": PASSWORD},
        )
        assert created.status_code == 201, created.text
        listed = client.get("/api/registries").json()
        shown = client.get("/api/registries/ghcr-acme")

        assert listed["kinds"] == ["dockerhub", "ghcr", "gitlab", "ecr", "acr", "gar", "custom"]
        assert listed["logins"][0]["server"] == "ghcr.io"
        assert shown.json()["name"] == "ghcr-acme"
        for response in (created, shown, client.get("/api/registries")):
            assert PASSWORD not in response.text

    def test_a_bad_value_is_a_400_naming_the_field(self) -> None:
        response = api_client().post(
            "/api/registries",
            json={"name": "Bad Name", "kind": "ghcr", "username": "bot", "password": "x"},
        )
        assert response.status_code == 400

    def test_patch_test_and_delete(self, wired: FakeRunner) -> None:
        client = api_client()
        client.post(
            "/api/registries",
            json={"name": "hub", "kind": "dockerhub", "username": "me", "password": PASSWORD},
        )

        changed = client.patch("/api/registries/hub", json={"username": "you"})
        tested = client.post("/api/registries/hub/test")
        removed = client.delete("/api/registries/hub")

        assert changed.json()["username"] == "you"
        assert tested.json()["last_status"] == "ok"
        assert removed.json() == {"name": "hub", "apps": []}
        assert client.get("/api/registries/hub").status_code == 404

    def test_the_accounts_of_an_application(self, store: NoustStore) -> None:
        client = api_client()
        client.post(
            "/api/registries",
            json={"name": "hub", "kind": "dockerhub", "username": "me", "password": PASSWORD},
        )

        put = client.put(f"/api/apps/{DOMAIN}/registry-logins", json={"logins": ["hub", "hub"]})
        got = client.get(f"/api/apps/{DOMAIN}/registry-logins")

        assert put.status_code == 200 and put.json()["logins"] == ["hub"]
        assert got.json() == {"domain": DOMAIN, "logins": ["hub"]}
        assert (
            client.put(
                "/api/apps/nobody.example.com/registry-logins", json={"logins": []}
            ).status_code
            == 404
        )


class TestTheCommands:
    @pytest.fixture(autouse=True)
    def wired(
        self,
        store: NoustStore,
        secrets: SecretStore,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> FakeRunner:
        from noust.core.config import Config

        monkeypatch.setattr(
            "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml"
        )
        Config.reset_instance()
        runner = FakeRunner()
        monkeypatch.setattr(
            "noust.cli.commands.registry.RegistryManager",
            lambda: manager(secrets, runner, store),
        )
        yield runner
        Config.reset_instance()

    def invoke(
        self, *args: str, stdin: str | None = None, env: dict[str, str] | None = None
    ) -> Any:
        from click.testing import CliRunner

        from noust.cli.app import cli

        return CliRunner().invoke(cli, list(args), input=stdin, env=env)

    def test_add_reads_the_password_from_standard_input(self, secrets: SecretStore) -> None:
        result = self.invoke(
            "registry",
            "add",
            "hub",
            "--kind",
            "dockerhub",
            "-u",
            "me",
            "--password-stdin",
            stdin=PASSWORD + "\n",
        )
        assert result.exit_code == 0, result.output
        assert secrets.read("registry/hub") == PASSWORD
        assert PASSWORD not in result.output

    def test_or_from_the_environment_and_never_an_argument(self, secrets: SecretStore) -> None:
        ok = self.invoke(
            "registry",
            "add",
            "hub",
            "--kind",
            "dockerhub",
            "-u",
            "me",
            env={"NOUST_REGISTRY_PASSWORD": PASSWORD},
        )
        assert ok.exit_code == 0, ok.output
        bad = self.invoke(
            "registry", "add", "x", "--kind", "dockerhub", "-u", "me", "--password", PASSWORD
        )
        assert bad.exit_code != 0 and "No such option" in bad.output

    def test_without_a_password_it_says_where_to_put_one(self) -> None:
        result = self.invoke("registry", "add", "hub", "--kind", "dockerhub", "-u", "me")
        assert result.exit_code != 0
        assert "--password-stdin" in result.exception.details

    def test_list_show_test_attach_remove(self, wired: FakeRunner) -> None:
        self.invoke(
            "registry",
            "add",
            "hub",
            "--kind",
            "dockerhub",
            "-u",
            "me",
            "--password-stdin",
            stdin=PASSWORD,
        )
        listed = self.invoke("registry", "list", "--json")
        assert [x["name"] for x in json.loads(listed.output)] == ["hub"]
        assert "hub" in self.invoke("registry", "show", "hub").output
        assert self.invoke("registry", "test", "hub").exit_code == 0
        assert self.invoke("registry", "attach", DOMAIN, "hub").exit_code == 0
        assert json.loads(self.invoke("registry", "show", "hub", "--json").output)["apps"] == [
            DOMAIN
        ]
        assert self.invoke("registry", "remove", "hub", "-y").exit_code == 0
        assert json.loads(self.invoke("registry", "list", "--json").output) == []

    def test_a_refused_account_fails_the_test_command(self, wired: FakeRunner) -> None:
        wired.script(["docker", "login"], exit_code=1, stderr="denied")
        self.invoke(
            "registry",
            "add",
            "hub",
            "--kind",
            "dockerhub",
            "-u",
            "me",
            "--password-stdin",
            stdin=PASSWORD,
        )
        result = self.invoke("registry", "test", "hub")
        assert result.exit_code == 1

    def test_a_dry_run_changes_nothing(self) -> None:
        result = self.invoke(
            "--dry-run",
            "registry",
            "add",
            "hub",
            "--kind",
            "dockerhub",
            "-u",
            "me",
            "--password-stdin",
            stdin=PASSWORD,
        )
        assert result.exit_code == 0 and "Would add" in result.output
        assert json.loads(self.invoke("registry", "list", "--json").output) == []
