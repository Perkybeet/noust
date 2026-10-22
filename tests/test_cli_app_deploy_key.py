# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app deploy-key show|list|generate|import|assign|rotate|test|remove|hosts|forget-host``.

The commands are clients of :class:`noust.managers.source_keys.DeployKeyManager`,
like the console's API. What matters at this level is what an operator sees and
what never appears: the private key is read from a file or standard input and
printed nowhere.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.runner import set_runner
from noust.core.store import App, NoustStore
from noust.managers.source_keys import DeployKeyManager
from tests.deploy_key_support import DeployKeyRunner, GitScript, private_key_text

DOMAIN = "shop.example.com"
SOURCE = "git@github.com:acme/shop.git"


@pytest.fixture(autouse=True)
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """An empty configuration."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    try:
        yield
    finally:
        Config.reset_instance()
        set_runner(None)


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the commands report to the operator."""
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of its own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def keygen() -> DeployKeyRunner:
    """The runner every command runs ssh-keygen through."""
    runner = DeployKeyRunner()
    set_runner(runner)
    return runner


@pytest.fixture
def app(store: NoustStore, keygen: DeployKeyRunner) -> App:
    """An application built from a private repository."""
    return store.create_app(
        App(domain=DOMAIN, app_type="nodejs", source=SOURCE, branch="main", port=3000)
    )


def run(*args: str, input: str | None = None) -> Result:
    """Run ``noust app deploy-key ...``."""
    return CliRunner().invoke(root_cli, ["app", "deploy-key", *args], input=input)


def test_the_group_offers_its_commands() -> None:
    result = CliRunner().invoke(root_cli, ["app", "deploy-key", "--help"])
    assert result.exit_code == 0, result.output
    for name in (
        "show",
        "list",
        "generate",
        "import",
        "assign",
        "rotate",
        "test",
        "remove",
        "prune",
        "hosts",
        "forget-host",
    ):
        assert name in result.output


def test_it_is_a_subgroup_of_app() -> None:
    assert "deploy-key" in CliRunner().invoke(root_cli, ["app", "--help"]).output


def test_an_unknown_application_is_named(store: NoustStore, keygen: DeployKeyRunner) -> None:
    for command in ("show", "generate", "remove", "test"):
        result = run(command, "nothing.example.com", *(["--yes"] if command == "remove" else []))
        assert isinstance(result.exception, NoustError), command
        assert "Application not found: nothing.example.com" in str(result.exception)


class TestShow:
    """Without a key, the repository uses the server-wide one; the command says so."""

    def test_no_key_says_how_to_make_one(self, app: App, log: list[str]) -> None:
        assert run("show", DOMAIN).exit_code == 0
        text = "\n".join(log)
        assert "github.com/acme/shop" in text
        assert "server-wide key" in text
        assert f"noust app deploy-key generate {DOMAIN}" in text

    def test_a_key_is_shown_as_its_public_half(self, app: App, log: list[str]) -> None:
        key = DeployKeyManager().generate(DOMAIN)
        assert run("show", DOMAIN).exit_code == 0
        text = "\n".join(log)
        assert key.public_key in text
        assert key.fingerprint in text
        assert "PRIVATE" not in text

    def test_json_never_carries_the_private_half(self, app: App) -> None:
        key = DeployKeyManager().generate(DOMAIN)
        private = DeployKeyManager().secrets.read(f"deploy-keys/{key.name}/key") or ""
        result = run("show", DOMAIN, "--json")
        body = json.loads(result.output)
        assert body["key"]["public_key"] == key.public_key
        assert body["key"]["applications"] == [DOMAIN]
        assert private.splitlines()[1] not in result.output
        assert "PRIVATE" not in result.output

    def test_a_waiting_rotation_is_announced_with_what_to_do(
        self, app: App, log: list[str]
    ) -> None:
        manager = DeployKeyManager()
        manager.generate(DOMAIN)
        waiting = manager.rotate_start(DOMAIN)
        run("show", DOMAIN)
        text = "\n".join(log)
        assert waiting.pending is not None and waiting.pending.public_key in text
        assert f"rotate {DOMAIN} --finish" in text


class TestGenerate:
    """The public key is what the command prints; where to put it too."""

    def test_it_prints_the_public_key_and_where_it_goes(self, app: App, log: list[str]) -> None:
        result = run("generate", DOMAIN)
        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "ssh-ed25519 " in text
        assert "Settings > Deploy keys" in text
        assert DeployKeyManager().view(DOMAIN).key is not None

    def test_json(self, app: App) -> None:
        body = json.loads(run("generate", DOMAIN, "--json", "--name", "mine").output)
        assert body["name"] == "mine"
        assert body["origin"] == "generated"

    def test_an_https_application_is_told_to_deploy_from_ssh(
        self, store: NoustStore, keygen: DeployKeyRunner, log: list[str]
    ) -> None:
        store.create_app(
            App(domain="web.example.com", app_type="nodejs", source="https://github.com/acme/web")
        )
        run("generate", "web.example.com")
        assert "git@github.com:acme/web.git" in "\n".join(log)

    def test_a_dry_run_makes_nothing(self, app: App, log: list[str]) -> None:
        result = run("generate", DOMAIN, "--dry-run")
        assert result.exit_code == 0, result.output
        assert "Would generate" in "\n".join(log)
        assert DeployKeyManager().keys() == []


class TestImport:
    """The private key comes from a file or standard input, and is printed nowhere."""

    def test_from_a_file(self, app: App, tmp_path: Path, log: list[str]) -> None:
        key_file = tmp_path / "id_deploy"
        key_file.write_text(private_key_text(3))
        result = run("import", DOMAIN, "--private-key-file", str(key_file))
        assert result.exit_code == 0, result.output
        assert DeployKeyManager().view(DOMAIN).key is not None
        assert "seed=3" not in result.output + "\n".join(log)

    def test_from_standard_input(self, app: App) -> None:
        result = run("import", DOMAIN, "--private-key-file", "-", input=private_key_text(3))
        assert result.exit_code == 0, result.output
        key = DeployKeyManager().view(DOMAIN).key
        assert key is not None and key.origin == "imported"

    def test_a_key_that_is_not_one_is_refused(self, app: App) -> None:
        result = run("import", DOMAIN, "--private-key-file", "-", input="hello")
        assert isinstance(result.exception, NoustError)
        assert DeployKeyManager().keys() == []

    def test_a_missing_file_is_named(self, app: App, tmp_path: Path) -> None:
        result = run("import", DOMAIN, "--private-key-file", str(tmp_path / "nope"))
        assert "Cannot read the private key" in str(result.exception)


class TestRotate:
    """Start, finish, cancel."""

    def test_start_prints_the_new_key_and_the_next_step(self, app: App, log: list[str]) -> None:
        DeployKeyManager().generate(DOMAIN)
        result = run("rotate", DOMAIN)
        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "the old one still works" in text
        assert f"rotate {DOMAIN} --finish" in text

    def test_finish_asks_the_repository_with_the_new_key(
        self, app: App, log: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        DeployKeyManager().generate(DOMAIN)
        run("rotate", DOMAIN)

        def respond(argv, env):
            return (
                (0, f"{'a' * 40}\trefs/heads/main\n", "")
                if "/next_key " in (env.get("GIT_SSH_COMMAND", "") + " ")
                else (128, "", "Permission denied (publickey).")
            )

        set_runner(GitScript(respond))
        result = run("rotate", DOMAIN, "--finish")
        assert result.exit_code == 0, result.output
        assert "new key works" in "\n".join(log)
        key = DeployKeyManager().view(DOMAIN).key
        assert key is not None and key.pending is None

    def test_cancel_forgets_the_new_key(self, app: App, log: list[str]) -> None:
        DeployKeyManager().generate(DOMAIN)
        run("rotate", DOMAIN)
        assert run("rotate", DOMAIN, "--cancel").exit_code == 0
        key = DeployKeyManager().view(DOMAIN).key
        assert key is not None and key.pending is None

    def test_finish_and_cancel_are_exclusive(self, app: App) -> None:
        result = run("rotate", DOMAIN, "--finish", "--cancel")
        assert "not both" in str(result.exception)


class TestRemoveAndPrune:
    """Removing is asked about, and says what is left to do at the code host."""

    def test_remove_asks_first(self, app: App) -> None:
        DeployKeyManager().generate(DOMAIN)
        result = run("remove", DOMAIN, input="n\n")
        assert result.exit_code != 0
        assert DeployKeyManager().view(DOMAIN).key is not None

    def test_remove_with_yes_deletes_and_reminds_about_the_host(
        self, app: App, log: list[str]
    ) -> None:
        key = DeployKeyManager().generate(DOMAIN)
        result = run("remove", DOMAIN, "--yes")
        assert result.exit_code == 0, result.output
        assert DeployKeyManager().keys() == []
        assert key.fingerprint in "\n".join(log)

    def test_prune_deletes_the_orphans(self, app: App, store: NoustStore, log: list[str]) -> None:
        key = DeployKeyManager().generate(DOMAIN)
        store.delete_app(DOMAIN)
        assert run("prune").exit_code == 0
        assert key.name in "\n".join(log)
        assert DeployKeyManager().keys() == []


class TestList:
    """Every key and what it reaches."""

    def test_empty(self, store: NoustStore, log: list[str]) -> None:
        assert run("list").exit_code == 0
        assert "server-wide key" in "\n".join(log)

    def test_json(self, app: App) -> None:
        key = DeployKeyManager().generate(DOMAIN)
        body = json.loads(run("list", "--json").output)
        assert [entry["name"] for entry in body["keys"]] == [key.name]
        assert body["keys"][0]["applications"] == [DOMAIN]


class TestHosts:
    """Pinned host keys: shown, and forgotten only on purpose."""

    def test_none_pinned(self, store: NoustStore, keygen: DeployKeyRunner, log: list[str]) -> None:
        assert run("hosts").exit_code == 0
        assert "No host key is pinned yet" in "\n".join(log)

    def test_forget_asks_first(self, store: NoustStore, keygen: DeployKeyRunner) -> None:
        result = run("forget-host", "github.com", input="n\n")
        assert result.exit_code != 0

    def test_forget_with_yes(
        self, store: NoustStore, keygen: DeployKeyRunner, log: list[str]
    ) -> None:
        manager = DeployKeyManager()
        path = manager.known_hosts_path()
        path.parent.mkdir(parents=True)
        path.write_text("github.com ssh-ed25519 AAAA\n")
        result = run("forget-host", "github.com", "--yes")
        assert result.exit_code == 0, result.output
        assert "Forgot the pinned host key of github.com" in "\n".join(log)
