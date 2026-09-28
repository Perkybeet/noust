# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for GitHub sources: the ``github:`` shorthand, and the installation
token reaching git at the one chokepoint, ``SourceManager._git``.

The token must be in git's environment and nowhere else: not in argv (every
local user reads it in ``ps``), not in the URL, not in ``.git/config``.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from tests.github.fakes import (
    FAKE_SIGNATURE_HEX,
    FakeGitHub,
    token_route,
)
from wasm.core.runner import FakeRunner
from wasm.core.store import WASMStore
from wasm.managers.source_manager import SourceManager
from wasm.validators.source import (
    expand_github_shorthand,
    github_repository,
    is_git_url,
    parse_git_url,
    validate_source,
)

TOKEN = "ghs_secret_installation_token"
HEADER = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()


# -- Shorthand ---------------------------------------------------------------


def test_the_shorthand_is_a_github_https_url() -> None:
    """github:owner/repo becomes the https URL git clones."""
    assert validate_source("github:you/app") == ("git", "https://github.com/you/app.git")
    assert expand_github_shorthand("github:you/app.git") == "https://github.com/you/app.git"
    assert is_git_url("github:you/app")
    parsed = parse_git_url("github:you/app")
    assert (parsed["host"], parsed["owner"], parsed["repo"]) == ("github.com", "you", "app")
    assert parsed["original"] == "github:you/app"


@pytest.mark.parametrize(
    "source",
    [
        "github:you",
        "github:-bad/app",
        "github:you/app/extra",
        "github:you/../etc",
        "github:you/app#dev",
    ],
)
def test_malformed_shorthand_is_not_a_github_repository(source: str) -> None:
    """Only a GitHub owner and repository name pass."""
    assert expand_github_shorthand(source) == source
    assert not is_git_url(source) or github_repository(source) is None


@pytest.mark.parametrize(
    "source",
    [
        "github:you/app",
        "https://github.com/you/app",
        "https://github.com/you/app.git",
        "https://user:token@github.com/you/app.git",
        "git@github.com:you/app.git",
        "ssh://git@github.com/you/app",
        "you/app",
        "https://github.com/you/app.git#main",
    ],
)
def test_every_spelling_names_one_repository(source: str) -> None:
    """A push matches an application however its source was written."""
    assert github_repository(source) == "you/app"


@pytest.mark.parametrize(
    "source",
    [
        "https://gitlab.com/you/app",
        "https://github.com/you",
        "https://github.com/you/app/tree/x",
        "",
        None,
    ],
)
def test_other_sources_name_no_github_repository(source: str | None) -> None:
    """Another host, or not a repository URL."""
    assert github_repository(source) is None


# -- The chokepoint -----------------------------------------------------------


@pytest.fixture
def git(github_configured: WASMStore, fake_github: FakeGitHub, runner: FakeRunner) -> FakeRunner:
    """
    A runner answering openssl, with GitHub handing out TOKEN.

    Args:
        github_configured: A store with an App installed on ``you``.
        fake_github: The fake GitHub.
        runner: The process-wide fake runner.

    Returns:
        The runner.
    """
    runner.script(["openssl", "dgst"], stdout=f"SHA2-256(stdin)= {FAKE_SIGNATURE_HEX}\n")
    token_route(fake_github, TOKEN)
    return runner


def git_calls(runner: FakeRunner) -> list[tuple[tuple[str, ...], dict[str, str]]]:
    """
    Pair every git call with its environment.

    Args:
        runner: The fake runner.

    Returns:
        ``(argv, env)`` of each git call.
    """
    return [
        (call, dict(env or {}))
        for call, env in zip(runner.calls, runner.envs, strict=True)
        if call[0] == "git"
    ]


def assert_token_only_in_env(runner: FakeRunner) -> None:
    """
    The token appears in no argv and no stdin.

    Args:
        runner: The fake runner.
    """
    for call in runner.calls:
        assert TOKEN not in " ".join(call)
        assert "x-access-token" not in " ".join(call)
    for given in runner.inputs:
        assert given is None or TOKEN not in given


def test_a_clone_of_a_covered_repository_carries_the_token_in_env(
    git: FakeRunner, tmp_path: Path
) -> None:
    """The clone gets the extra header; the URL and argv stay clean."""
    SourceManager(runner=git).clone_git("https://github.com/you/app.git", tmp_path / "c")
    ((argv, env),) = [c for c in git_calls(git) if "clone" in c[0]]
    assert "https://github.com/you/app.git" in argv
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_0"] == HEADER
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert_token_only_in_env(git)


def test_the_shorthand_clones_through_fetch(git: FakeRunner, tmp_path: Path) -> None:
    """A github: source is fetched as its https URL, with the token."""
    SourceManager(runner=git).fetch("github:you/app", tmp_path / "c")
    clones = [c for c in git_calls(git) if "clone" in c[0]]
    assert clones and "https://github.com/you/app.git" in clones[0][0]
    assert clones[0][1]["GIT_CONFIG_VALUE_0"] == HEADER


def test_ls_remote_for_the_upstream_check_carries_the_token(git: FakeRunner) -> None:
    """The remote head is asked with the token, so a private repository answers."""
    git.script(
        ["git", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never", "ls-remote"],
        stdout="abc\trefs/heads/main\n",
    )
    head = SourceManager(runner=git).remote_head("github:you/app", "main")
    assert head.commit == "abc"
    ((argv, env),) = [c for c in git_calls(git) if "ls-remote" in c[0]]
    assert env["GIT_CONFIG_VALUE_0"] == HEADER
    assert_token_only_in_env(git)


def test_the_blobless_checkout_of_an_inspection_carries_the_token(
    git: FakeRunner, tmp_path: Path
) -> None:
    """The checkout fetches blobs, so it is a network command too."""
    SourceManager(runner=git).sparse_clone(
        "https://github.com/you/app.git", tmp_path / "s", patterns=["/package.json"]
    )
    by_verb = {c[0][5]: c[1] for c in git_calls(git)}
    assert by_verb["clone"]["GIT_CONFIG_VALUE_0"] == HEADER
    assert by_verb["checkout"]["GIT_CONFIG_VALUE_0"] == HEADER
    assert "GIT_CONFIG_VALUE_0" not in by_verb["sparse-checkout"]


def test_a_fetch_in_the_cache_carries_the_token(git: FakeRunner, tmp_path: Path) -> None:
    """The cache's fetch knows its remote from the URL it was synced from."""
    cache = tmp_path / "repo"
    (cache / ".git").mkdir(parents=True)
    git.script(
        ["git", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never", "rev-parse"],
        stdout="main\n",
    )
    SourceManager(runner=git).sync_cache("github:you/app", cache, branch="main")
    fetches = [c for c in git_calls(git) if c[0][5] == "fetch"]
    assert fetches and fetches[0][1]["GIT_CONFIG_VALUE_0"] == HEADER
    local = [c for c in git_calls(git) if c[0][5] in ("rev-parse", "reset", "remote")]
    assert all("GIT_CONFIG_VALUE_0" not in env for _, env in local)
    assert_token_only_in_env(git)


def test_an_existing_clone_is_asked_its_origin_before_a_fetch(
    git: FakeRunner, tmp_path: Path
) -> None:
    """A fetch in a clone this manager did not make finds its host through origin."""
    git.script(
        [
            "git",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "protocol.file.allow=never",
            "remote",
            "get-url",
        ],
        stdout="https://github.com/you/app.git\n",
    )
    SourceManager(runner=git)._git(["fetch", "origin"], cwd=tmp_path)
    fetch = next(c for c in git_calls(git) if c[0][5] == "fetch")
    assert fetch[1]["GIT_CONFIG_VALUE_0"] == HEADER


def test_a_credential_in_the_url_wins(git: FakeRunner, tmp_path: Path) -> None:
    """The operator's own credential is used, and no token is asked for."""
    SourceManager(runner=git).clone_git("https://me:pat@github.com/you/app.git", tmp_path / "c")
    ((argv, env),) = [c for c in git_calls(git) if "clone" in c[0]]
    assert "x-access-token" not in env.get("GIT_CONFIG_VALUE_0", "")
    assert not git.calls_to("openssl")


def test_an_uncovered_repository_clones_without_a_token(git: FakeRunner, tmp_path: Path) -> None:
    """A repository of an account without an installation gets nothing."""
    SourceManager(runner=git).clone_git("https://github.com/stranger/app.git", tmp_path / "c")
    ((argv, env),) = [c for c in git_calls(git) if "clone" in c[0]]
    assert "GIT_CONFIG_COUNT" not in env
    assert not git.calls_to("openssl")


def test_a_refused_token_leaves_git_to_answer(
    github_configured: WASMStore, fake_github: FakeGitHub, runner: FakeRunner, tmp_path: Path
) -> None:
    """GitHub refusing the token is a warning; the clone runs without it."""
    runner.script(["openssl", "dgst"], stdout=f"SHA2-256(stdin)= {FAKE_SIGNATURE_HEX}\n")
    fake_github.on(
        "POST", r"/app/installations/\d+/access_tokens", {"message": "Bad credentials"}, 401
    )
    SourceManager(runner=runner).clone_git("https://github.com/you/app.git", tmp_path / "c")
    ((argv, env),) = [c for c in git_calls(runner) if "clone" in c[0]]
    assert "GIT_CONFIG_COUNT" not in env


def test_no_app_means_no_change(store: WASMStore, runner: FakeRunner, tmp_path: Path) -> None:
    """Without a GitHub App, git runs exactly as before."""
    SourceManager(runner=runner).clone_git("https://github.com/you/app.git", tmp_path / "c")
    ((argv, env),) = [c for c in git_calls(runner) if "clone" in c[0]]
    assert "GIT_CONFIG_COUNT" not in env
    assert [c[0][5] for c in git_calls(runner)] == ["clone"]


def test_the_explicit_installation_is_used(
    github_configured: WASMStore, fake_github: FakeGitHub, runner: FakeRunner, tmp_path: Path
) -> None:
    """An application's own installation id is the one asked for a token."""
    runner.script(["openssl", "dgst"], stdout=f"SHA2-256(stdin)= {FAKE_SIGNATURE_HEX}\n")
    token_route(fake_github, TOKEN)
    SourceManager(runner=runner, github_installation_id=31337).clone_git(
        "https://github.com/stranger/app.git", tmp_path / "c"
    )
    assert fake_github.paths("POST") == ["/app/installations/31337/access_tokens"]
