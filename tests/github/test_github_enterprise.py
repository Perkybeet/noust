# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
GitHub Enterprise Server and Cloud with data residency (checklist A-05).

``github.host`` points the integration at another GitHub: the API root, the address Apps are
created on, the repositories the App serves, the header an installation token is sent under and
the webhook's repository all follow it, and nothing else does: a source on github.com is not a
repository of an App that lives on a GitHub Enterprise Server.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import github_host
from noust.core.config import Config
from noust.core.exceptions import ConfigError, ValidationError
from noust.core.forge_events import Forge, PushEvent
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.integrations import webhook as hooks_info
from noust.integrations.github import app as github_app
from noust.integrations.github import client as github_client
from noust.integrations.github import service
from noust.integrations.github.webhooks import apps_following, parse_push
from noust.managers.source_manager import SourceManager
from noust.validators.source import github_repository
from tests.github.fakes import FAKE_SIGNATURE_HEX, FakeGitHub, token_route

HOST = "ghe.corp.example"
TOKEN = "ghs_enterprise_installation_token"


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    """
    Returns:
        The process-wide configuration, in a directory of its own.
    """
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield Config()
    Config.reset_instance()


@pytest.fixture
def enterprise(config: Config) -> str:
    """
    Returns:
        The host the integration is configured for.
    """
    config.set("github.host", HOST)
    return HOST


class TestTheHost:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("", ""),
            (None, ""),
            ("GHE.Corp.Example", "ghe.corp.example"),
            ("acme.ghe.com", "acme.ghe.com"),
            ("ghe.corp.example:8443", "ghe.corp.example:8443"),
            ("10.1.2.3", "10.1.2.3"),
        ],
    )
    def test_what_is_a_host(self, value: str | None, expected: str) -> None:
        assert github_host.validate_host(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "https://ghe.corp.example",
            "ghe.corp.example/api/v3",
            "user@ghe.corp.example",
            "ghe corp",
            "-bad.example",
            "ghe.corp.example:99999999",
            "ghe.corp.example;rm",
        ],
    )
    def test_what_is_not(self, value: str) -> None:
        with pytest.raises(ConfigError, match="host name"):
            github_host.validate_host(value)

    def test_the_default_is_github_com(self, config: Config) -> None:
        assert github_host.configured_host() == "github.com"
        assert not github_host.is_enterprise()
        assert github_host.api_url() == "https://api.github.com"
        assert github_host.web_url() == "https://github.com"
        assert github_host.source_for("you/app") == "github:you/app"

    def test_the_addresses_of_each_kind_of_host(self) -> None:
        assert github_host.api_url("ghe.corp.example") == "https://ghe.corp.example/api/v3"
        assert (
            github_host.api_url("ghe.corp.example:8443") == "https://ghe.corp.example:8443/api/v3"
        )
        assert github_host.api_url("acme.ghe.com") == "https://api.acme.ghe.com"
        assert github_host.web_url("acme.ghe.com") == "https://acme.ghe.com"
        assert github_host.source_for("you/app", "ghe.corp.example") == (
            "https://ghe.corp.example/you/app.git"
        )

    def test_the_configuration_decides(self, enterprise: str) -> None:
        assert github_host.configured_host() == HOST
        assert github_host.is_enterprise()
        assert github_host.repository_hosts() == (HOST,)

    def test_a_host_with_a_port_is_a_repository_host_by_name_and_by_authority(
        self, config: Config
    ) -> None:
        config.set("github.host", "ghe.corp.example:8443")

        assert github_host.repository_hosts() == ("ghe.corp.example:8443", "ghe.corp.example")
        assert github_host.integration_repository("https://ghe.corp.example:8443/you/app.git") == (
            "you/app"
        )

    def test_the_config_key_refuses_what_is_not_a_host(self, config: Config) -> None:
        with pytest.raises(ConfigError):
            config.set("github.host", "https://ghe.corp.example")


class TestWhichRepositoriesAreTheIntegrations:
    def test_a_repository_of_the_enterprise_host(self, enterprise: str) -> None:
        for source in (
            "https://ghe.corp.example/you/app.git",
            "https://ghe.corp.example/you/app",
            "git@ghe.corp.example:you/app.git",
            "ssh://git@ghe.corp.example/you/app.git",
        ):
            assert github_host.integration_repository(source) == "you/app", source

    def test_github_com_is_not_when_the_app_is_on_an_enterprise_host(self, enterprise: str) -> None:
        assert github_host.integration_repository("https://github.com/you/app.git") is None
        assert github_host.integration_repository("github:you/app") is None
        assert github_repository("https://github.com/you/app.git") == "you/app"

    def test_a_host_that_only_looks_like_it(self, enterprise: str) -> None:
        assert github_host.integration_repository("https://ghe.corp.example.evil.com/a/b") is None
        assert github_host.integration_repository("https://evil.com/ghe.corp.example/a") is None

    def test_by_default_it_is_github_com_only(self, config: Config) -> None:
        assert github_host.integration_repository("https://github.com/you/app.git") == "you/app"
        assert github_host.integration_repository("https://ghe.corp.example/you/app") is None


class TestTheClient:
    @pytest.fixture(autouse=True)
    def unpinned(self) -> Iterator[None]:
        github_client.set_client(None)
        yield
        github_client.set_client(None)

    @pytest.fixture
    def guard(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        asked: list[str] = []
        monkeypatch.setattr(
            "noust.core.notifier.require_public_destination",
            lambda url, purpose="server": asked.append(f"{purpose}: {url}"),
        )
        return asked

    def test_github_com_is_never_asked_about(self, config: Config, guard: list[str]) -> None:
        assert github_client.get_client().base_url == "https://api.github.com"
        assert guard == []

    def test_an_enterprise_host_is_asked_about_once(
        self, enterprise: str, guard: list[str]
    ) -> None:
        first = github_client.get_client()
        second = github_client.get_client()

        assert first is second
        assert first.base_url == "https://ghe.corp.example/api/v3"
        assert guard == ["the GitHub Enterprise server: https://ghe.corp.example/api/v3"]

    def test_the_client_follows_the_configuration(self, config: Config, guard: list[str]) -> None:
        assert github_client.get_client().base_url == "https://api.github.com"
        config.set("github.host", "acme.ghe.com")
        assert github_client.get_client().base_url == "https://api.acme.ghe.com"

    def test_a_host_inside_the_machines_networks_is_refused_with_the_way_out(
        self, enterprise: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(url: str, purpose: str = "server") -> None:
            raise ValueError(f"{purpose} resolves to a private address")

        monkeypatch.setattr("noust.core.notifier.require_public_destination", refuse)

        with pytest.raises(ConfigError) as caught:
            github_client.get_client()

        assert "private address" in str(caught.value)
        assert "notifications.allow_private_hosts" in (caught.value.details or "")
        assert HOST in (caught.value.details or "")

    def test_a_client_that_was_given_is_kept(self, enterprise: str, guard: list[str]) -> None:
        given = github_client.GitHubClient("http://127.0.0.1:1")
        github_client.set_client(given)

        assert github_client.get_client() is given
        assert guard == []


class TestTheAppAndTheClone:
    @pytest.fixture
    def git(
        self,
        enterprise: str,
        github_configured: NoustStore,
        fake_github: FakeGitHub,
        runner: FakeRunner,
    ) -> FakeRunner:
        runner.script(["openssl", "dgst"], stdout=f"SHA2-256(stdin)= {FAKE_SIGNATURE_HEX}\n")
        token_route(fake_github, TOKEN)
        return runner

    def test_a_clone_on_the_enterprise_host_carries_the_token_under_that_host(
        self, git: FakeRunner, tmp_path: Path
    ) -> None:
        SourceManager(runner=git).clone_git("https://ghe.corp.example/you/app.git", tmp_path / "c")

        env = next(dict(e or {}) for c, e in zip(git.calls, git.envs, strict=True) if "clone" in c)
        assert env["GIT_CONFIG_KEY_0"] == "http.https://ghe.corp.example/.extraheader"
        assert env["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic ")

    def test_github_com_gets_no_token_from_an_app_that_is_elsewhere(
        self, git: FakeRunner, tmp_path: Path
    ) -> None:
        SourceManager(runner=git).clone_git("https://github.com/you/app.git", tmp_path / "c")

        env = next(dict(e or {}) for c, e in zip(git.calls, git.envs, strict=True) if "clone" in c)
        assert "GIT_CONFIG_KEY_0" not in env

    def test_the_url_check(self, enterprise: str) -> None:
        assert github_app.is_github_https("https://ghe.corp.example/you/app.git")
        assert not github_app.is_github_https("https://github.com/you/app.git")
        assert not github_app.is_github_https("https://ghe.corp.example/")


class TestThePushAndTheHooks:
    def test_a_push_without_a_clone_url_is_on_the_enterprise_host(self, enterprise: str) -> None:
        event = parse_push(
            {
                "ref": "refs/heads/main",
                "after": "a" * 40,
                "repository": {"full_name": "you/app"},
            }
        )

        assert event is not None
        assert event.clone_url == "https://ghe.corp.example/you/app.git"

    def test_the_webhook_page_is_the_hosts(self, enterprise: str) -> None:
        info = hooks_info.forge_of("https://ghe.corp.example/you/app.git")

        assert (info.forge, info.host, info.repository) == ("github", HOST, "you/app")
        assert info.settings_url == "https://ghe.corp.example/you/app/settings/hooks/new"

    def test_a_repository_on_github_com_is_still_github(self, enterprise: str) -> None:
        info = hooks_info.forge_of("https://github.com/you/app.git")

        assert (info.forge, info.host) == ("github", "github.com")


class TestChoosingTheHost:
    @pytest.fixture
    def audited(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
        seen: list[tuple[Any, ...]] = []
        monkeypatch.setattr("noust.core.audit.record", lambda *a, **k: seen.append((a, k)) or None)
        monkeypatch.setattr("noust.core.notifier.require_public_destination", lambda *a, **k: None)
        return seen

    def test_set_and_reset(self, config: Config, store: NoustStore, audited: list[Any]) -> None:
        assert service.set_host("GHE.corp.example") == HOST
        assert github_host.configured_host() == HOST
        assert service.status().host == HOST

        assert service.set_host(None) == "github.com"
        assert github_host.configured_host() == "github.com"
        assert audited[0][0][0] == "integration.github.host"
        assert audited[0][1]["details"] == {"host": HOST}

    def test_it_is_saved(self, config: Config, store: NoustStore, audited: list[Any]) -> None:
        service.set_host(HOST)

        Config.reset_instance()
        assert Config().get("github.host") == HOST

    def test_a_server_with_an_app_keeps_its_host(
        self, config: Config, github_configured: NoustStore, audited: list[Any]
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            service.set_host(HOST)

        assert "remove it first" in (caught.value.details or "")
        assert github_host.configured_host() == "github.com"
        service.set_host(HOST, force=True)
        assert github_host.configured_host() == HOST

    def test_the_same_host_is_not_a_change(
        self, config: Config, github_configured: NoustStore, audited: list[Any]
    ) -> None:
        assert service.set_host("github.com") == "github.com"

    def test_a_private_host_must_be_allowed_first(
        self, config: Config, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(url: str, purpose: str = "server") -> None:
            raise ValueError("resolves to a private address")

        monkeypatch.setattr("noust.core.notifier.require_public_destination", refuse)

        with pytest.raises(ConfigError, match="private address"):
            service.set_host(HOST)
        assert github_host.configured_host() == "github.com"

    def test_the_repository_list_sources_follow_the_host(self, enterprise: str) -> None:
        assert github_host.source_for("you/app") == "https://ghe.corp.example/you/app.git"

    def test_the_commands(
        self, config: Config, store: NoustStore, audited: list[Any], tmp_path: Path
    ) -> None:
        from click.testing import CliRunner

        from noust.cli.app import cli

        done = CliRunner().invoke(cli, ["github", "host", "set", HOST])
        shown = CliRunner().invoke(cli, ["github", "host", "show", "--json"])
        reset = CliRunner().invoke(cli, ["github", "host", "reset"])

        assert done.exit_code == 0, done.output
        assert "talks to ghe.corp.example" in done.output
        body = json.loads(shown.output)
        assert body["host"] == HOST and body["enterprise"] is True
        assert body["api_url"] == "https://ghe.corp.example/api/v3"
        assert reset.exit_code == 0 and github_host.configured_host() == "github.com"

    def test_the_api(self, config: Config, store: NoustStore, audited: list[Any]) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from noust.web.api import integrations
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers, require_elevated
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_integrations import ROUTES

        assert ROUTES[("PUT", "/api/integrations/github/host")] == Permission.APPS_MANAGE
        server = FastAPI()
        install_error_handlers(server)
        server.include_router(integrations.router, prefix="/api/integrations")
        session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
        server.dependency_overrides[get_current_session] = lambda: session
        server.dependency_overrides[require_elevated] = lambda: session
        client = TestClient(server)

        got = client.put("/api/integrations/github/host", json={"host": HOST})
        bad = client.put("/api/integrations/github/host", json={"host": "https://x"})

        assert got.status_code == 200, got.text
        assert got.json()["host"] == HOST and got.json()["configured"] is False
        assert bad.status_code == 400
        assert client.get("/api/integrations/github").json()["host"] == HOST


def test_an_app_record_still_matches_apps_by_their_source(
    enterprise: str, store: NoustStore, tmp_path: Path
) -> None:
    store.create_app(
        App(
            domain="shop.example.com",
            app_type="nodejs",
            source="https://ghe.corp.example/you/app.git",
            app_path=str(tmp_path),
        )
    )
    push = PushEvent(
        forge=Forge.GITHUB,
        repository="you/app",
        clone_url="https://ghe.corp.example/you/app.git",
        branch="main",
        head_sha="b" * 40,
        installation_id=None,
    )

    assert [a.domain for a in apps_following(push, "main")] == ["shop.example.com"]


def test_the_app_is_created_and_installed_on_the_hosts_own_site(
    enterprise: str, store: NoustStore
) -> None:
    from noust.integrations.github import manifest

    started = manifest.start("https://console.example.com", hooks_url=None)
    in_an_organisation = manifest.start(
        "https://console.example.com", hooks_url=None, organization="acme"
    )

    assert started.post_url.startswith("https://ghe.corp.example/settings/apps/new?state=")
    assert in_an_organisation.post_url.startswith(
        "https://ghe.corp.example/organizations/acme/settings/apps/new?state="
    )
