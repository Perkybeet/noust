# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
git reaches a repository with the deploy key of that repository (checklist A-02).

The key is found by the repository's address, handed to ssh as a path in
``GIT_SSH_COMMAND``, and nothing else is offered. A repository without one is
reached exactly as it was before: with the server-wide key. While a rotation
waits, the new key is tried first, and the first answer it gets from the code
host makes it the key. A host key that changed is refused and says so.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest

from noust.core.exceptions import SourceError
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers.source_keys import DeployKeyManager, secret_name
from noust.managers.source_manager import (
    GIT_AUTH_FAILURE_MESSAGE,
    GIT_SSH_COMMAND,
    SourceManager,
)
from tests.deploy_key_support import DeployKeyRunner, GitScript

DOMAIN = "shop.example.com"
SSH_SOURCE = "git@github.com:acme/shop.git"
HEAD = "a" * 40

LS_REMOTE_HEAD = f"ref: refs/heads/main\tHEAD\n{HEAD}\tHEAD\n"
DENIED = (
    "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository."
)
CHANGED = (
    "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
    "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
    "Host key verification failed.\nfatal: Could not read from remote repository."
)


@pytest.fixture(autouse=True)
def _no_operator_ssh_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's own GIT_SSH_COMMAND out of these assertions."""
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """The process-wide store, which the source manager reads on its own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def keys(store: NoustStore, tmp_path: Path) -> DeployKeyManager:
    """A deploy key manager over the same store and secrets the source manager uses."""
    store.create_app(App(domain=DOMAIN, app_type="nodejs", source=SSH_SOURCE, branch="main"))
    return DeployKeyManager(
        store=store,
        secrets=SecretStore(root=tmp_path / "state" / "secrets"),
        runner=DeployKeyRunner(),
    )


def answer(argv: Sequence[str]) -> str:
    """
    What ``git ls-remote`` prints for the question it was asked.

    Args:
        argv: The git command.

    Returns:
        The branch's line, or the default branch's.
    """
    if "refs/heads/main" in argv:
        return f"{HEAD}\trefs/heads/main\n"
    return LS_REMOTE_HEAD


def anything(argv: Sequence[str], env: Mapping[str, str]) -> tuple[int, str, str]:
    """A remote that answers whatever key it is asked with."""
    return 0, answer(argv), ""


def accept_only(*names: str):
    """
    Build a responder: ssh keys named here are accepted, any other is refused.

    Args:
        *names: File names (``key``, ``next_key``) the code host knows.

    Returns:
        A responder for :class:`GitScript`.
    """

    def respond(argv: Sequence[str], env: Mapping[str, str]) -> tuple[int, str, str]:
        command = env.get("GIT_SSH_COMMAND", "")
        if any(f"/{name} " in f"{command} " for name in names):
            return 0, answer(argv), ""
        return 128, "", DENIED

    return respond


def git_calls(runner: GitScript) -> list[tuple[str, ...]]:
    """The git commands that ran."""
    return [call for call in runner.calls if call[0] == "git"]


def head(runner: GitScript) -> str:
    """Read the default branch of the repository, as an update does."""
    return SourceManager(runner=runner).remote_head(SSH_SOURCE).branch


class TestAKeyedRepository:
    """What git is given when the repository has a key."""

    def test_git_is_given_that_key_and_only_that_key(
        self, keys: DeployKeyManager, tmp_path: Path
    ) -> None:
        key = keys.generate(DOMAIN)
        runner = GitScript(accept_only("key"))

        assert head(runner) == "main"

        (call,) = git_calls(runner)
        env = runner.envs[runner.calls.index(call)]
        assert env is not None
        path = keys.secrets.path(secret_name(key.name, "key"))
        command = env["GIT_SSH_COMMAND"]
        assert f"-i {path}" in command
        assert "IdentitiesOnly=yes" in command
        assert "StrictHostKeyChecking=accept-new" in command
        assert f"UserKnownHostsFile={keys.known_hosts_path()}" in command

    def test_the_key_never_appears_in_what_git_is_run_with(self, keys: DeployKeyManager) -> None:
        key = keys.generate(DOMAIN)
        private = keys.secrets.read(secret_name(key.name, "key")) or ""
        runner = GitScript(accept_only("key"))
        head(runner)
        for argv, env in zip(runner.calls, runner.envs, strict=True):
            assert private.splitlines()[1] not in " ".join(argv)
            assert private.splitlines()[1] not in " ".join((env or {}).values())

    def test_it_is_recorded_as_used(self, keys: DeployKeyManager) -> None:
        key = keys.generate(DOMAIN)
        head(GitScript(accept_only("key")))
        used = keys.keystore.get(key.name)
        assert used is not None and used.last_used_at

    def test_a_repository_without_a_key_is_reached_as_before(self, keys: DeployKeyManager) -> None:
        keys.generate(DOMAIN)
        runner = GitScript(anything)
        SourceManager(runner=runner).remote_head("git@github.com:acme/other.git")
        env = runner.envs[runner.calls.index(git_calls(runner)[0])]
        assert env is not None and env["GIT_SSH_COMMAND"] == GIT_SSH_COMMAND

    def test_an_https_url_is_not_touched(self, keys: DeployKeyManager) -> None:
        keys.generate(DOMAIN)
        runner = GitScript(anything)
        SourceManager(runner=runner).remote_head("https://github.com/acme/shop.git")
        env = runner.envs[runner.calls.index(git_calls(runner)[0])]
        assert env is not None and env["GIT_SSH_COMMAND"] == GIT_SSH_COMMAND

    def test_nothing_changes_while_no_repository_has_a_key(self, store: NoustStore) -> None:
        runner = GitScript(anything)
        SourceManager(runner=runner).remote_head(SSH_SOURCE)
        env = runner.envs[runner.calls.index(git_calls(runner)[0])]
        assert env is not None and env["GIT_SSH_COMMAND"] == GIT_SSH_COMMAND

    def test_a_fetch_in_a_clone_finds_the_repository_through_its_origin(
        self, keys: DeployKeyManager, tmp_path: Path
    ) -> None:
        keys.generate(DOMAIN)
        clone = tmp_path / "clone"
        (clone / ".git").mkdir(parents=True)

        def respond(argv: Sequence[str], env: Mapping[str, str]) -> tuple[int, str, str]:
            if "get-url" in argv:
                return 0, SSH_SOURCE + "\n", ""
            return accept_only("key")(argv, env)

        runner = GitScript(respond)
        result = SourceManager(runner=runner)._git(["fetch", "origin"], cwd=clone)

        assert result.success
        fetch = next(
            env for argv, env in zip(runner.calls, runner.envs, strict=True) if "fetch" in argv
        )
        assert fetch is not None and "IdentitiesOnly=yes" in fetch["GIT_SSH_COMMAND"]

    def test_a_submodule_is_not_signed_in_with_the_key_of_its_parent(
        self, keys: DeployKeyManager, tmp_path: Path
    ) -> None:
        keys.generate(DOMAIN)
        runner = GitScript(accept_only("key"))
        SourceManager(runner=runner)._git(["submodule", "update", "--init"], cwd=tmp_path)
        env = runner.envs[runner.calls.index(git_calls(runner)[0])]
        assert env is not None and env["GIT_SSH_COMMAND"] == GIT_SSH_COMMAND

    def test_the_server_wide_ssh_check_is_not_asked_of_a_repository_with_a_key(
        self, keys: DeployKeyManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        keys.generate(DOMAIN)
        asked: list[str] = []
        monkeypatch.setattr(
            "noust.managers.source_manager.ensure_ssh_setup",
            lambda url, **kwargs: asked.append(url),
        )
        runner = GitScript(anything)
        SourceManager(runner=runner).clone_git(SSH_SOURCE, tmp_path / "out")
        assert asked == []
        SourceManager(runner=runner).clone_git("git@github.com:acme/other.git", tmp_path / "o2")
        assert asked == ["git@github.com:acme/other.git"]


class TestARotationInProgress:
    """The new key is tried first; the old one is never lost on the way."""

    def test_the_first_answer_to_the_new_key_replaces_the_old_one(
        self, keys: DeployKeyManager
    ) -> None:
        old = keys.generate(DOMAIN)
        waiting = keys.rotate_start(DOMAIN)
        runner = GitScript(accept_only("next_key", "key"))

        head(runner)

        assert len(git_calls(runner)) == 1  # it did not need the second attempt
        done = keys.keystore.get(old.name)
        assert done is not None and done.pending is None
        assert waiting.pending is not None and done.public_key == waiting.pending.public_key
        assert not keys.secrets.path(secret_name(old.name, "next_key")).exists()

    def test_while_the_code_host_does_not_know_it_yet_the_old_key_works(
        self, keys: DeployKeyManager
    ) -> None:
        old = keys.generate(DOMAIN)
        keys.rotate_start(DOMAIN)
        runner = GitScript(accept_only("key"))

        assert head(runner) == "main"

        assert len(git_calls(runner)) == 2  # the new key first, then the old
        waiting = keys.keystore.get(old.name)
        assert waiting is not None and waiting.pending is not None
        assert waiting.public_key == old.public_key

    def test_when_neither_works_the_error_says_how_to_grant_access(
        self, keys: DeployKeyManager
    ) -> None:
        keys.generate(DOMAIN)
        keys.rotate_start(DOMAIN)
        with pytest.raises(SourceError) as caught:
            head(GitScript(accept_only()))
        assert caught.value.message == GIT_AUTH_FAILURE_MESSAGE

    def test_a_failure_that_is_not_about_credentials_does_not_try_the_other_key(
        self, keys: DeployKeyManager
    ) -> None:
        keys.generate(DOMAIN)
        keys.rotate_start(DOMAIN)
        runner = GitScript(
            lambda argv, env: (128, "", "ssh: Could not resolve hostname github.com")
        )
        with pytest.raises(SourceError):
            head(runner)
        assert len(git_calls(runner)) == 1

    def test_verify_completes_the_rotation_and_says_so(self, keys: DeployKeyManager) -> None:
        keys.generate(DOMAIN)
        keys.rotate_start(DOMAIN)
        runner = GitScript(accept_only("next_key"))
        message = keys.verify(DOMAIN, source=SourceManager(runner=runner))
        assert "new key works" in message
        done = keys.keystore.get("shop-example-com")
        assert done is not None and done.pending is None

    def test_verify_says_when_only_the_old_key_answered(self, keys: DeployKeyManager) -> None:
        keys.generate(DOMAIN)
        keys.rotate_start(DOMAIN)
        runner = GitScript(accept_only("key"))
        message = keys.verify(DOMAIN, source=SourceManager(runner=runner))
        assert "add the new public key" in message

    def test_verify_without_a_rotation_just_checks_access(self, keys: DeployKeyManager) -> None:
        keys.generate(DOMAIN)
        runner = GitScript(accept_only("key"))
        message = keys.verify(DOMAIN, source=SourceManager(runner=runner))
        assert message.startswith("The repository answered")


class TestAHostKeyThatChanged:
    """A pinned host key is never replaced silently."""

    def test_it_is_refused_and_the_one_way_forward_is_named(self, keys: DeployKeyManager) -> None:
        keys.generate(DOMAIN)
        runner = GitScript(lambda argv, env: (255, "", CHANGED))
        with pytest.raises(SourceError) as caught:
            head(runner)
        assert "host key of github.com is not the one Noust pinned" in caught.value.message
        assert "noust app deploy-key forget-host github.com" in caught.value.details
        assert "HOST IDENTIFICATION HAS CHANGED" in (caught.value.output or "")
        assert len(git_calls(runner)) == 1

    def test_it_is_not_taken_for_a_refused_credential(self, keys: DeployKeyManager) -> None:
        keys.generate(DOMAIN)
        keys.rotate_start(DOMAIN)
        runner = GitScript(lambda argv, env: (255, "", CHANGED))
        with pytest.raises(SourceError):
            head(runner)
        assert len(git_calls(runner)) == 1  # a second key would be pointless
