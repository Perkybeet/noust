# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An https credential for a private repository, kept as a secret (checklist B-27).

Bitbucket Cloud's private repositories are reached over https with an API token
or an access token (app passwords no longer work), and the token must not live
in the source URL. It is stored in the secret store, assigned to the repository,
and handed to git the way an ``https://user:token@host`` source always was:
as an ``Authorization`` header in git's environment, scoped to the host, never
in an argument or in a response.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core import audit
from noust.core.config import Config
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import set_runner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers.source_credentials import GitCredentialManager
from noust.managers.source_manager import SourceManager
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app as build_app
from noust.web.server import get_token_manager
from tests.deploy_key_support import GitScript

DOMAIN = "shop.example.com"
SOURCE = "https://bitbucket.org/acme/shop.git"
TOKEN = "ATBBxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
USER = "x-bitbucket-api-token-auth"
HEAD = "a" * 40


@pytest.fixture(autouse=True)
def _no_operator_ssh_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's own GIT_SSH_COMMAND out of these assertions."""
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of its own, the process-wide one."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(App(domain=DOMAIN, app_type="nodejs", source=SOURCE, branch="main"))
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()
        set_runner(None)


@pytest.fixture
def credentials(store: NoustStore, tmp_path: Path) -> GitCredentialManager:
    """The manager over the same secrets the source manager reads."""
    return GitCredentialManager(
        store=store, secrets=SecretStore(root=tmp_path / "state" / "secrets")
    )


def stdout_for(argv: Sequence[str]) -> str:
    """What ``git ls-remote`` prints for the branch asked for."""
    return f"{HEAD}\trefs/heads/main\n"


def asked_with(runner: GitScript) -> Mapping[str, str]:
    """The environment of the one git call that ran."""
    calls = [env for argv, env in zip(runner.calls, runner.envs, strict=True) if argv[0] == "git"]
    assert len(calls) == 1
    assert calls[0] is not None
    return calls[0]


class TestManager:
    """Keep, show and forget."""

    def test_nothing_is_stored_at_first(self, credentials: GitCredentialManager) -> None:
        view = credentials.view(DOMAIN)
        assert (view.configured, view.username) == (False, None)
        assert view.repository == "bitbucket.org/acme/shop"
        assert view.host == "bitbucket.org"
        assert view.source_is_https is True

    def test_a_credential_is_stored_as_a_secret_and_never_shown(
        self, credentials: GitCredentialManager, tmp_path: Path
    ) -> None:
        view = credentials.put(DOMAIN, USER, TOKEN)

        assert (view.configured, view.username) == (True, USER)
        assert TOKEN not in repr(view)
        path = credentials.secrets.path("git-credentials/bitbucket.org/acme/shop")
        assert path.stat().st_mode & 0o777 == 0o600
        assert TOKEN in path.read_text()

    def test_it_is_audited_without_the_token(self, credentials: GitCredentialManager) -> None:
        credentials.put(DOMAIN, USER, TOKEN)
        entries = [
            e for e in audit.get_log().read(limit=50) if e["action"] == "apps.git_credential.set"
        ]
        (entry,) = entries
        assert entry["details"]["username"] == USER
        assert TOKEN not in str(entry)

    def test_putting_again_replaces_it(self, credentials: GitCredentialManager) -> None:
        credentials.put(DOMAIN, USER, TOKEN)
        credentials.put(DOMAIN, "ada", "another")
        assert credentials.view(DOMAIN).username == "ada"

    def test_removing_forgets_it(self, credentials: GitCredentialManager) -> None:
        credentials.put(DOMAIN, USER, TOKEN)
        assert credentials.remove(DOMAIN) is True
        assert credentials.view(DOMAIN).configured is False
        assert credentials.remove(DOMAIN) is False
        assert not credentials.secrets.path("git-credentials/bitbucket.org/acme/shop").exists()

    @pytest.mark.parametrize(
        ("user", "secret"),
        [
            ("", TOKEN),
            ("a:b", TOKEN),
            ("bad\nuser", TOKEN),
            (USER, ""),
            (USER, "two\nlines"),
            (USER, "x" * 2000),
        ],
    )
    def test_values_that_would_change_the_header_are_refused(
        self, credentials: GitCredentialManager, user: str, secret: str
    ) -> None:
        with pytest.raises(ValidationError):
            credentials.put(DOMAIN, user, secret)
        assert credentials.view(DOMAIN).configured is False

    def test_an_application_that_is_not_a_repository_has_none(
        self, credentials: GitCredentialManager, store: NoustStore
    ) -> None:
        store.create_app(App(domain="static.example.com", app_type="static", source="/srv/site"))
        with pytest.raises(ValidationError, match="not built from a git repository"):
            credentials.put("static.example.com", USER, TOKEN)
        assert credentials.view("static.example.com").configured is False

    def test_a_repository_name_that_cannot_be_a_file_name_is_refused(
        self, credentials: GitCredentialManager, store: NoustStore
    ) -> None:
        store.create_app(
            App(domain="odd.example.com", app_type="nodejs", source="https://example.com/a/.hidden")
        )
        with pytest.raises(ValidationError, match="cannot hold a credential"):
            credentials.put("odd.example.com", USER, TOKEN)

    def test_any_stored_is_cheap_and_honest(self, credentials: GitCredentialManager) -> None:
        assert credentials.any_stored() is False
        credentials.put(DOMAIN, USER, TOKEN)
        assert credentials.any_stored() is True


class TestWhatGitIsGiven:
    """The token is in git's environment as a header, and nowhere else."""

    def test_an_https_repository_with_a_credential_gets_the_header(
        self, credentials: GitCredentialManager
    ) -> None:
        credentials.put(DOMAIN, USER, TOKEN)
        runner = GitScript(lambda argv, env: (0, stdout_for(argv), ""))

        SourceManager(runner=runner).remote_head(SOURCE, "main")

        env = asked_with(runner)
        assert env["GIT_CONFIG_KEY_0"] == "http.https://bitbucket.org/.extraHeader"
        expected = base64.b64encode(f"{USER}:{TOKEN}".encode()).decode()
        assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"

    def test_the_token_is_in_no_argument_and_not_in_the_error_text(
        self, credentials: GitCredentialManager
    ) -> None:
        credentials.put(DOMAIN, USER, TOKEN)
        runner = GitScript(lambda argv, env: (128, "", "fatal: Authentication failed"))
        with pytest.raises(NoustError) as caught:
            SourceManager(runner=runner).remote_head(SOURCE, "main")
        for argv in runner.calls:
            assert TOKEN not in " ".join(argv)
        assert TOKEN not in str(caught.value)

    def test_a_special_character_in_the_token_survives(
        self, credentials: GitCredentialManager
    ) -> None:
        credentials.put(DOMAIN, "user@mail", "p@ss:w/rd%")
        runner = GitScript(lambda argv, env: (0, stdout_for(argv), ""))
        SourceManager(runner=runner).remote_head(SOURCE, "main")
        value = asked_with(runner)["GIT_CONFIG_VALUE_0"].removeprefix("Authorization: Basic ")
        assert base64.b64decode(value).decode() == "user@mail:p@ss:w/rd%"

    def test_another_repository_is_not_given_it(self, credentials: GitCredentialManager) -> None:
        credentials.put(DOMAIN, USER, TOKEN)
        runner = GitScript(lambda argv, env: (0, stdout_for(argv), ""))
        SourceManager(runner=runner).remote_head("https://bitbucket.org/acme/other.git", "main")
        assert "GIT_CONFIG_KEY_0" not in asked_with(runner)

    def test_a_credential_in_the_url_wins(self, credentials: GitCredentialManager) -> None:
        credentials.put(DOMAIN, USER, TOKEN)
        runner = GitScript(lambda argv, env: (0, stdout_for(argv), ""))
        SourceManager(runner=runner).remote_head(
            "https://me:own@bitbucket.org/acme/shop.git", "main"
        )
        value = asked_with(runner)["GIT_CONFIG_VALUE_0"].removeprefix("Authorization: Basic ")
        assert base64.b64decode(value).decode() == "me:own"

    def test_an_ssh_url_never_gets_it(self, credentials: GitCredentialManager) -> None:
        credentials.put(DOMAIN, USER, TOKEN)
        runner = GitScript(lambda argv, env: (0, stdout_for(argv), ""))
        SourceManager(runner=runner).remote_head("git@bitbucket.org:acme/shop.git", "main")
        assert "GIT_CONFIG_KEY_0" not in asked_with(runner)

    def test_a_fetch_in_a_clone_finds_the_repository_through_its_origin(
        self, credentials: GitCredentialManager, tmp_path: Path
    ) -> None:
        credentials.put(DOMAIN, USER, TOKEN)
        clone = tmp_path / "clone"
        (clone / ".git").mkdir(parents=True)

        def respond(argv: Sequence[str], env: Mapping[str, str]) -> tuple[int, str, str]:
            return (0, SOURCE + "\n", "") if "get-url" in argv else (0, "", "")

        runner = GitScript(respond)
        SourceManager(runner=runner)._git(["fetch", "origin"], cwd=clone)
        fetch = next(
            env for argv, env in zip(runner.calls, runner.envs, strict=True) if "fetch" in argv
        )
        assert fetch is not None and "GIT_CONFIG_KEY_0" in fetch

    def test_nothing_is_read_while_no_credential_exists(self, store: NoustStore) -> None:
        runner = GitScript(lambda argv, env: (0, stdout_for(argv), ""))
        SourceManager(runner=runner).remote_head(SOURCE, "main")
        assert "GIT_CONFIG_KEY_0" not in asked_with(runner)


class TestCommandLine:
    """``noust app git-credential``: the token never comes from an argument."""

    @pytest.fixture(autouse=True)
    def _config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        monkeypatch.setattr(
            "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml"
        )
        Config.reset_instance()
        yield
        Config.reset_instance()

    @pytest.fixture
    def log(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        lines: list[str] = []
        monkeypatch.setattr(
            Logger, "_write", lambda self, message, newline=True: lines.append(message)
        )
        return lines

    def run(self, *args: str, input: str | None = None):
        return CliRunner().invoke(root_cli, ["app", "git-credential", *args], input=input)

    def test_set_from_standard_input(
        self, store: NoustStore, log: list[str], credentials: GitCredentialManager
    ) -> None:
        result = self.run(
            "set", DOMAIN, "--username", USER, "--secret-file", "-", input=TOKEN + "\n"
        )
        assert result.exit_code == 0, result.output
        view = GitCredentialManager().view(DOMAIN)
        assert (view.configured, view.username) == (True, USER)
        assert TOKEN not in result.output + "\n".join(log)

    def test_set_asks_for_the_token_without_echo(self, store: NoustStore) -> None:
        result = self.run("set", DOMAIN, "--username", USER, input=TOKEN + "\n")
        assert result.exit_code == 0, result.output
        assert GitCredentialManager().view(DOMAIN).configured is True
        assert TOKEN not in result.output

    def test_set_from_a_file(self, store: NoustStore, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text(TOKEN + "\n")
        assert (
            self.run("set", DOMAIN, "--username", USER, "--secret-file", str(token_file)).exit_code
            == 0
        )
        assert GitCredentialManager().view(DOMAIN).configured is True

    def test_show_says_who_it_signs_in_as_and_never_the_token(
        self, store: NoustStore, log: list[str]
    ) -> None:
        GitCredentialManager().put(DOMAIN, USER, TOKEN)
        result = self.run("show", DOMAIN)
        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert USER in text and TOKEN not in text + result.output
        body = self.run("show", DOMAIN, "--json").output
        assert TOKEN not in body and '"configured": true' in body

    def test_remove_asks_first(self, store: NoustStore) -> None:
        GitCredentialManager().put(DOMAIN, USER, TOKEN)
        assert self.run("remove", DOMAIN, input="n\n").exit_code != 0
        assert GitCredentialManager().view(DOMAIN).configured is True
        assert self.run("remove", DOMAIN, "--yes").exit_code == 0
        assert GitCredentialManager().view(DOMAIN).configured is False

    def test_a_dry_run_stores_nothing(self, store: NoustStore) -> None:
        result = self.run(
            "set", DOMAIN, "--username", USER, "--secret-file", "-", "--dry-run", input=TOKEN
        )
        assert result.exit_code == 0, result.output
        assert GitCredentialManager().view(DOMAIN).configured is False


class TestApi:
    """``/api/apps/{domain}/git-credential``: the token goes in and never comes out."""

    @pytest.fixture
    def app(self, tmp_path: Path, store: NoustStore) -> FastAPI:
        return build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))

    def signed_in(self, app: FastAPI, *, elevate: bool) -> TestClient:
        client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
        token = get_token_manager().generate_master_token()
        login = client.post("/api/auth/login", json={"token": token})
        client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
        if elevate:
            assert client.post("/api/auth/elevate", json={"token": token}).status_code == 200
        return client

    URL = f"/api/apps/{DOMAIN}/git-credential"

    def test_the_whole_cycle(self, app: FastAPI) -> None:
        admin = self.signed_in(app, elevate=True)
        assert admin.get(self.URL).json()["configured"] is False

        put = admin.put(self.URL, json={"username": USER, "secret": TOKEN})
        assert put.status_code == 200, put.text
        assert put.json()["configured"] is True and put.json()["username"] == USER
        assert TOKEN not in put.text
        assert TOKEN not in admin.get(self.URL).text

        deleted = admin.delete(self.URL)
        assert deleted.status_code == 200 and deleted.json()["configured"] is False

    def test_writing_and_removing_need_sudo_mode(self, app: FastAPI) -> None:
        plain = self.signed_in(app, elevate=False)
        assert plain.put(self.URL, json={"username": USER, "secret": TOKEN}).status_code == 403
        assert plain.delete(self.URL).status_code == 403
        assert plain.get(self.URL).status_code == 200

    def test_a_value_that_is_not_acceptable_is_refused_with_its_field(self, app: FastAPI) -> None:
        admin = self.signed_in(app, elevate=True)
        response = admin.put(self.URL, json={"username": "a:b", "secret": TOKEN})
        assert response.status_code == 400
        assert "username" in response.text

    def test_an_unknown_application_is_404(self, app: FastAPI) -> None:
        admin = self.signed_in(app, elevate=True)
        assert admin.get("/api/apps/nobody.example.com/git-credential").status_code == 404
