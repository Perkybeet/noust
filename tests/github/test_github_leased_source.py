# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server with no GitHub App clones with the token its central leased to it.

What is pinned: the leased token reaches git at the one chokepoint (``SourceManager._git``), in
git's environment and nowhere else, scoped to the host the lease is for; it covers the repositories
it was given and no others; this server's own App, when it has one, and a credential the operator
put in the URL both come before it; and when it has ended because the central went quiet the
error says exactly that, with the time it ended and the last time the central called, instead of
"the repository is private".
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from noust.core.exceptions import IntegrationError, SourceError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet.central_calls import CentralCalls
from noust.integrations.github import app as github_app
from noust.integrations.github.leases import SourceLeases
from noust.managers.source_manager import SourceManager
from tests.github.fakes import FAKE_SIGNATURE_HEX, FakeGitHub, token_route

LEASED = "ghs_leased_by_the_central"
HEADER = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{LEASED}".encode()).decode()

#: What git says when a private repository is cloned with no credential that works.
REFUSED = "fatal: Authentication failed for 'https://github.com/you/app.git/'\n"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """One clock for every lease the test makes."""
    moving = Clock()
    monkeypatch.setattr("noust.integrations.github.leases._now", moving)
    return moving


@pytest.fixture
def leased(store: NoustStore, clock: Clock) -> SourceLeases:
    """A server with no App and a lease from the central ``nas``."""
    leases = SourceLeases()
    leases.hold(
        central="nas",
        key="github.com:77:0",
        host="github.com",
        installation_id=77,
        repositories=["you/app"],
        token=LEASED,
        expires_in=3600,
    )
    return leases


def git_calls(runner: FakeRunner) -> list[tuple[tuple[str, ...], dict[str, str]]]:
    return [
        (call, dict(env or {}))
        for call, env in zip(runner.calls, runner.envs, strict=True)
        if call[0] == "git"
    ]


def clone(runner: FakeRunner, url: str, tmp_path: Path) -> None:
    SourceManager(runner=runner).clone_git(url, tmp_path / "c")


class TestTheLeasedTokenReachesGit:
    def test_a_clone_of_a_covered_repository_carries_it_in_the_environment_only(
        self, leased: SourceLeases, runner: FakeRunner, tmp_path: Path
    ) -> None:
        clone(runner, "https://github.com/you/app.git", tmp_path)

        ((argv, env),) = [c for c in git_calls(runner) if "clone" in c[0]]
        assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
        assert env["GIT_CONFIG_VALUE_0"] == HEADER
        assert env["GIT_CONFIG_COUNT"] == "1"
        for call in runner.calls:
            assert LEASED not in " ".join(call)
            assert "x-access-token" not in " ".join(call)

    def test_the_shorthand_and_the_upstream_check_are_helped_too(
        self, leased: SourceLeases, runner: FakeRunner, tmp_path: Path
    ) -> None:
        runner.script(
            [
                "git",
                "-c",
                "protocol.ext.allow=never",
                "-c",
                "protocol.file.allow=never",
                "ls-remote",
            ],
            stdout="abc\trefs/heads/main\n",
        )

        SourceManager(runner=runner).remote_head("github:you/app", "main")

        ((_argv, env),) = [c for c in git_calls(runner) if "ls-remote" in c[0]]
        assert env["GIT_CONFIG_VALUE_0"] == HEADER

    def test_a_repository_the_lease_does_not_cover_gets_nothing(
        self, leased: SourceLeases, runner: FakeRunner, tmp_path: Path
    ) -> None:
        clone(runner, "https://github.com/you/other.git", tmp_path)

        ((_argv, env),) = [c for c in git_calls(runner) if "clone" in c[0]]
        assert "GIT_CONFIG_COUNT" not in env

    def test_another_host_gets_nothing(
        self, leased: SourceLeases, runner: FakeRunner, tmp_path: Path
    ) -> None:
        clone(runner, "https://gitlab.com/you/app.git", tmp_path)

        ((_argv, env),) = [c for c in git_calls(runner) if "clone" in c[0]]
        assert "GIT_CONFIG_COUNT" not in env

    def test_the_header_is_scoped_to_the_host_the_lease_is_for(
        self, store: NoustStore, clock: Clock, runner: FakeRunner, tmp_path: Path
    ) -> None:
        SourceLeases().hold(
            central="nas",
            key="ghe",
            host="ghe.example.com",
            installation_id=5,
            repositories=["you/app"],
            token=LEASED,
            expires_in=3600,
        )

        clone(runner, "https://ghe.example.com/you/app.git", tmp_path)
        clone(runner, "https://github.com/you/app.git", tmp_path)

        clones = [c for c in git_calls(runner) if "clone" in c[0]]
        assert clones[0][1]["GIT_CONFIG_KEY_0"] == "http.https://ghe.example.com/.extraheader"
        assert "GIT_CONFIG_KEY_0" not in clones[1][1]

    def test_the_environment_the_operator_already_passes_is_added_to(
        self, leased: SourceLeases, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = github_app.git_auth_environment("https://github.com/you/app.git", config_index=2)

        assert env["GIT_CONFIG_COUNT"] == "3"
        assert env["GIT_CONFIG_KEY_2"] == "http.https://github.com/.extraheader"
        assert env["GIT_CONFIG_VALUE_2"] == HEADER

    def test_a_credential_in_the_url_comes_first(
        self, leased: SourceLeases, runner: FakeRunner, tmp_path: Path
    ) -> None:
        clone(runner, "https://me:pat@github.com/you/app.git", tmp_path)

        ((_argv, env),) = [c for c in git_calls(runner) if "clone" in c[0]]
        assert env["GIT_CONFIG_VALUE_0"] != HEADER

    def test_this_servers_own_app_comes_before_the_lease(
        self,
        github_configured: NoustStore,
        leased: SourceLeases,
        fake_github: FakeGitHub,
        runner: FakeRunner,
        tmp_path: Path,
    ) -> None:
        runner.script(["openssl", "dgst"], stdout=f"SHA2-256(stdin)= {FAKE_SIGNATURE_HEX}\n")
        token_route(fake_github, "ghs_from_the_app")

        clone(runner, "https://github.com/you/app.git", tmp_path)

        ((_argv, env),) = [c for c in git_calls(runner) if "clone" in c[0]]
        assert (
            base64.b64encode(b"x-access-token:ghs_from_the_app").decode()
            in (env["GIT_CONFIG_VALUE_0"])
        )

    def test_a_server_with_no_lease_adds_nothing(self, store: NoustStore) -> None:
        assert github_app.git_auth_environment("https://github.com/you/app.git") == {}


class TestWhenTheCentralWentQuiet:
    def end(self, clock: Clock) -> None:
        clock.now += 3600 + 600

    def test_an_ended_lease_is_not_used(
        self, leased: SourceLeases, clock: Clock, runner: FakeRunner, tmp_path: Path
    ) -> None:
        self.end(clock)
        runner.script(["git"], exit_code=128, stderr=REFUSED)

        with pytest.raises(SourceError):
            clone(runner, "https://github.com/you/app.git", tmp_path)

        ((_argv, env),) = [c for c in git_calls(runner) if "clone" in c[0]]
        assert "GIT_CONFIG_COUNT" not in env

    def test_the_error_says_the_lease_ended_and_the_central_has_not_called(
        self, leased: SourceLeases, clock: Clock, runner: FakeRunner, tmp_path: Path
    ) -> None:
        CentralCalls(clock=lambda: clock.now - 3600 - 1200).note("nas", 30)
        self.end(clock)
        runner.script(["git"], exit_code=128, stderr=REFUSED)

        with pytest.raises(SourceError) as raised:
            clone(runner, "https://github.com/you/app.git", tmp_path)

        error = raised.value
        assert error.coded is not None and error.coded.code == "sources.lease_expired_quiet"
        assert error.coded.params["central"] == "nas"
        assert error.coded.params["repository"] == "you/app"
        assert "nas" in error.message and "you/app" in error.message
        assert "has not called" in error.message
        assert "deploy-key" in error.details
        # git's own words are still there, verbatim.
        assert "Authentication failed" in (error.output or "")

    def test_when_the_central_called_since_the_error_says_it_did_not_renew(
        self, leased: SourceLeases, clock: Clock, runner: FakeRunner, tmp_path: Path
    ) -> None:
        self.end(clock)
        CentralCalls(clock=lambda: clock.now - 30).note("nas", 30)
        runner.script(["git"], exit_code=128, stderr=REFUSED)

        with pytest.raises(SourceError) as raised:
            clone(runner, "https://github.com/you/app.git", tmp_path)

        assert raised.value.coded is not None
        assert raised.value.coded.code == "sources.lease_expired"
        assert "has not been renewed" in raised.value.message

    def test_a_central_never_heard_from_is_said_so(
        self, leased: SourceLeases, clock: Clock, runner: FakeRunner, tmp_path: Path
    ) -> None:
        self.end(clock)
        runner.script(["git"], exit_code=128, stderr=REFUSED)

        with pytest.raises(SourceError) as raised:
            clone(runner, "https://github.com/you/app.git", tmp_path)

        assert raised.value.coded is not None
        assert raised.value.coded.params["last_call"] == "never"

    def test_an_ordinary_refusal_of_a_repository_the_lease_never_covered_is_unchanged(
        self, leased: SourceLeases, clock: Clock, runner: FakeRunner, tmp_path: Path
    ) -> None:
        self.end(clock)
        runner.script(["git"], exit_code=128, stderr=REFUSED)

        with pytest.raises(SourceError) as raised:
            clone(runner, "https://github.com/you/other.git", tmp_path)

        assert raised.value.message == "The repository is private or the credentials were refused"
        assert raised.value.coded is None

    def test_asking_for_the_environment_of_an_ended_lease_raises_the_same_sentence(
        self, leased: SourceLeases, clock: Clock
    ) -> None:
        self.end(clock)

        with pytest.raises(IntegrationError) as raised:
            github_app.git_auth_environment("https://github.com/you/app.git")

        assert raised.value.coded is not None
        assert raised.value.coded.code.startswith("sources.lease_expired")

    def test_a_lease_renewed_in_time_is_used_and_nothing_is_said(
        self, leased: SourceLeases, clock: Clock, runner: FakeRunner, tmp_path: Path
    ) -> None:
        clock.now += 3000
        leased.hold(
            central="nas",
            key="github.com:77:0",
            host="github.com",
            installation_id=77,
            repositories=["you/app"],
            token="ghs_renewed",
            expires_in=3600,
        )
        clock.now += 1000

        clone(runner, "https://github.com/you/app.git", tmp_path)

        ((_argv, env),) = [c for c in git_calls(runner) if "clone" in c[0]]
        assert base64.b64encode(b"x-access-token:ghs_renewed").decode() in env["GIT_CONFIG_VALUE_0"]
