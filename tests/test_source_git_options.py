# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Submodules, Git LFS and shallow clones per application (checklist A-12).

The options live in the store (``noust.core.app_source_store``); the manager
reads them for the application it fetches for and turns them into git
commands. The commands are checked, not the network: what is asserted is the
argv git is given and the order it is given in, and that a repository's
submodules and LFS files reach the release rather than an empty directory and
a pointer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core import app_source_store
from noust.core.exceptions import SourceError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.managers.source_manager import SourceManager

DOMAIN = "app.example.com"
URL = "https://github.com/you/app.git"
COMMIT = "a" * 40
SAFE = ("git", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never")


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", source=URL, app_path=str(tmp_path / "app"))
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def manager(runner: FakeRunner, store: NoustStore) -> SourceManager:
    return SourceManager(domain=DOMAIN)


def git_calls(runner: FakeRunner, verb: str) -> list[tuple[str, ...]]:
    """The git calls whose subcommand is ``verb``, ignoring the ``-c`` pairs before it."""
    found = []
    for call in runner.calls_to("git"):
        rest = list(call[1:])
        while rest[:1] == ["-c"]:
            rest = rest[2:]
        if rest[:1] == [verb]:
            found.append(call)
    return found


class TestClone:
    def test_the_defaults_are_what_clones_always_were(
        self, manager: SourceManager, runner: FakeRunner
    ) -> None:
        manager.clone_git(URL, Path("/var/www/apps/x"), lfs=False)

        (clone,) = git_calls(runner, "clone")
        assert "--depth" in clone and clone[clone.index("--depth") + 1] == "1"
        assert "--recursive" in clone
        assert "--shallow-submodules" in clone

    def test_a_full_history_is_cloned_whole(
        self, manager: SourceManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        app_source_store.set_options(DOMAIN, shallow=False, store=store)

        manager.clone_git(URL, Path("/var/www/apps/x"), lfs=False)

        (clone,) = git_calls(runner, "clone")
        assert "--depth" not in clone
        assert "--shallow-submodules" not in clone

    def test_without_submodules_nothing_recursive_is_asked(
        self, manager: SourceManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        app_source_store.set_options(DOMAIN, submodules=False, store=store)

        manager.clone_git(URL, Path("/var/www/apps/x"), lfs=False)

        (clone,) = git_calls(runner, "clone")
        assert "--recursive" not in clone

    def test_what_a_caller_names_wins_over_the_options(
        self, manager: SourceManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        app_source_store.set_options(DOMAIN, shallow=False, submodules=True, store=store)

        manager.clone_git(URL, Path("/var/www/apps/x"), depth=1, recursive=False, lfs=False)

        (clone,) = git_calls(runner, "clone")
        assert "--depth" in clone
        assert "--recursive" not in clone

    def test_a_manager_with_no_application_uses_the_defaults(self, runner: FakeRunner) -> None:
        SourceManager().clone_git(URL, Path("/var/www/apps/x"), lfs=False)
        (clone,) = git_calls(runner, "clone")
        assert "--depth" in clone and "--recursive" in clone


class TestLfsOnClone:
    def script_lfs_repository(self, runner: FakeRunner) -> None:
        runner.script(
            [*SAFE, "grep", "-l", "-F", "filter=lfs"], stdout="HEAD:.gitattributes\n", exit_code=0
        )

    def test_a_repository_that_tracks_lfs_files_gets_them(
        self, manager: SourceManager, runner: FakeRunner
    ) -> None:
        self.script_lfs_repository(runner)

        manager.clone_git(URL, Path("/var/www/apps/x"))

        (pull,) = [call for call in git_calls(runner, "lfs") if "pull" in call]
        assert "filter.lfs.smudge=git-lfs smudge -- %f" in pull
        assert "filter.lfs.required=true" in pull

    def test_a_repository_without_lfs_runs_no_lfs_command(
        self, manager: SourceManager, runner: FakeRunner
    ) -> None:
        manager.clone_git(URL, Path("/var/www/apps/x"))

        assert [c for c in git_calls(runner, "lfs") if "pull" in c] == []

    def test_off_never_asks_even_when_the_repository_tracks_lfs(
        self, manager: SourceManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        self.script_lfs_repository(runner)
        app_source_store.set_options(DOMAIN, lfs="off", store=store)

        manager.clone_git(URL, Path("/var/www/apps/x"))

        assert git_calls(runner, "lfs") == []

    def test_on_always_asks(
        self, manager: SourceManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        app_source_store.set_options(DOMAIN, lfs="on", store=store)

        manager.clone_git(URL, Path("/var/www/apps/x"))

        assert [c for c in git_calls(runner, "lfs") if "pull" in c]

    def test_missing_git_lfs_is_said_with_how_to_install_it(
        self, manager: SourceManager, runner: FakeRunner
    ) -> None:
        self.script_lfs_repository(runner)
        runner.only_knows("git")

        with pytest.raises(SourceError) as caught:
            manager.clone_git(URL, Path("/var/www/apps/x"))

        assert "Git LFS" in caught.value.message
        assert "git-lfs" in (caught.value.details or "")
        assert "--lfs off" in (caught.value.details or "")

    def test_an_inspection_never_needs_the_files(
        self, manager: SourceManager, runner: FakeRunner
    ) -> None:
        self.script_lfs_repository(runner)
        runner.only_knows("git")

        assert manager.clone_git(URL, Path("/tmp/inspect"), lfs=False) is True


class TestDeepening:
    def test_a_clone_made_shallow_is_deepened_once_the_option_is_off(
        self, manager: SourceManager, runner: FakeRunner, store: NoustStore, tmp_path: Path
    ) -> None:
        cache = tmp_path / "repo"
        (cache / ".git").mkdir(parents=True)
        app_source_store.set_options(DOMAIN, shallow=False, store=store)
        runner.script([*SAFE, "rev-parse", "--is-shallow-repository"], stdout="true\n")
        runner.script([*SAFE, "rev-parse", "--abbrev-ref", "HEAD"], stdout="main\n")
        runner.script([*SAFE, "remote", "get-url", "origin"], stdout=f"{URL}\n")

        manager._update_cache(URL, cache, "main")

        deepen = git_calls(runner, "fetch")
        assert any("--unshallow" in call for call in deepen)

    def test_a_shallow_application_is_not_deepened(
        self, manager: SourceManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        cache = tmp_path / "repo"
        (cache / ".git").mkdir(parents=True)
        runner.script([*SAFE, "rev-parse", "--is-shallow-repository"], stdout="true\n")
        runner.script([*SAFE, "rev-parse", "--abbrev-ref", "HEAD"], stdout="main\n")
        runner.script([*SAFE, "remote", "get-url", "origin"], stdout=f"{URL}\n")

        manager._update_cache(URL, cache, "main")

        assert not any("--unshallow" in call for call in git_calls(runner, "fetch"))


class TestExport:
    """A release made of a commit that has submodules or LFS files is copied from a checkout."""

    def no_submodules(self, runner: FakeRunner) -> None:
        runner.script([*SAFE, "cat-file", "-e", f"{COMMIT}:.gitmodules"], exit_code=128)

    def test_a_plain_commit_is_still_archived(
        self, manager: SourceManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        self.no_submodules(runner)
        release = tmp_path / "release"
        release.mkdir()

        manager.export_commit(tmp_path / "repo", COMMIT, release)

        assert git_calls(runner, "archive")
        assert git_calls(runner, "checkout") == []

    def test_a_commit_with_submodules_is_exported_from_a_checkout_that_has_them(
        self, manager: SourceManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        runner.script(
            [*SAFE, "ls-files", "--cached", "--recurse-submodules", "-z"],
            stdout="package.json\0vendor/lib/index.js\0",
        )
        repository = tmp_path / "repo"
        (repository / "vendor" / "lib").mkdir(parents=True)
        (repository / "package.json").write_text("{}")
        (repository / ".gitmodules").write_text('[submodule "lib"]\n')
        (repository / "vendor" / "lib" / "index.js").write_text("x")
        release = tmp_path / "release"
        release.mkdir()

        manager.export_commit(repository, COMMIT, release)

        assert git_calls(runner, "archive") == []
        (checkout,) = git_calls(runner, "checkout")
        assert checkout[-3:] == ("--force", "--detach", COMMIT)
        update = git_calls(runner, "submodule")
        assert any(
            "update" in call and "--init" in call and "--recursive" in call for call in update
        )
        assert any("--depth" in call for call in update)
        tar = runner.calls_to("tar")
        assert tar and "--files-from=-" in tar[0]
        assert "vendor/lib/index.js\0" in (runner.inputs[runner.calls.index(tar[0])] or "")

    def test_a_submodule_that_cannot_be_fetched_shallow_is_fetched_whole(
        self, manager: SourceManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        runner.script(
            [*SAFE, "submodule", "update", "--init", "--recursive", "--force", "--depth", "1"],
            exit_code=1,
            stderr="fatal: Fetched in submodule path, but it did not contain abc",
        )
        repository = tmp_path / "repo"
        repository.mkdir()
        (repository / ".gitmodules").write_text('[submodule "lib"]\n')
        release = tmp_path / "release"
        release.mkdir()

        manager.export_commit(repository, COMMIT, release)

        updates = [c for c in git_calls(runner, "submodule") if "update" in c]
        assert len(updates) == 2
        assert "--depth" not in updates[1]

    def test_submodules_off_archives_even_when_the_commit_has_them(
        self, manager: SourceManager, runner: FakeRunner, store: NoustStore, tmp_path: Path
    ) -> None:
        app_source_store.set_options(DOMAIN, submodules=False, store=store)
        release = tmp_path / "release"
        release.mkdir()

        manager.export_commit(tmp_path / "repo", COMMIT, release)

        assert git_calls(runner, "archive")
        assert git_calls(runner, "submodule") == []

    def test_lfs_files_are_checked_out_with_the_lfs_filter(
        self, manager: SourceManager, runner: FakeRunner, store: NoustStore, tmp_path: Path
    ) -> None:
        self.no_submodules(runner)
        app_source_store.set_options(DOMAIN, lfs="on", store=store)
        runner.script([*SAFE, "ls-files", "--cached", "-z"], stdout="model.bin\0")
        repository = tmp_path / "repo"
        repository.mkdir()
        (repository / "model.bin").write_text("real bytes")
        release = tmp_path / "release"
        release.mkdir()

        manager.export_commit(repository, COMMIT, release)

        (checkout,) = git_calls(runner, "checkout")
        assert "filter.lfs.smudge=git-lfs smudge -- %f" in checkout
        assert git_calls(runner, "archive") == []
        assert runner.calls_to("tar")

    def test_lfs_without_git_lfs_is_refused_before_anything_is_moved(
        self, manager: SourceManager, runner: FakeRunner, store: NoustStore, tmp_path: Path
    ) -> None:
        self.no_submodules(runner)
        app_source_store.set_options(DOMAIN, lfs="on", store=store)
        runner.only_knows("git", "tar")
        release = tmp_path / "release"
        release.mkdir()

        with pytest.raises(SourceError) as caught:
            manager.export_commit(tmp_path / "repo", COMMIT, release)

        assert "git-lfs" in (caught.value.details or "")
        assert git_calls(runner, "checkout") == []

    def test_a_link_in_the_checkout_is_not_followed(
        self, manager: SourceManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        runner.script(
            [*SAFE, "ls-files", "--cached", "--recurse-submodules", "-z"],
            stdout="escape/secret\0ok.txt\0",
        )
        repository = tmp_path / "repo"
        repository.mkdir()
        (repository / "ok.txt").write_text("fine")
        (repository / "escape").symlink_to("/etc")
        release = tmp_path / "release"
        release.mkdir()

        manager.export_commit(repository, COMMIT, release)

        sent = runner.inputs[runner.calls.index(runner.calls_to("tar")[0])] or ""
        assert "ok.txt" in sent
        assert "escape/secret" not in sent


class TestInPlace:
    def test_a_pull_brings_the_submodules_along(
        self, manager: SourceManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        checkout = tmp_path / "app"
        (checkout / ".git").mkdir(parents=True)
        (checkout / ".gitmodules").write_text('[submodule "x"]\n')

        manager.pull(checkout, branch="main")

        assert any("update" in call for call in git_calls(runner, "submodule"))

    def test_a_pull_without_submodules_runs_no_submodule_command(
        self, manager: SourceManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        checkout = tmp_path / "app"
        (checkout / ".git").mkdir(parents=True)

        manager.pull(checkout, branch="main")

        assert git_calls(runner, "submodule") == []

    def test_a_pull_fetches_lfs_files_when_the_repository_tracks_them(
        self, manager: SourceManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        checkout = tmp_path / "app"
        (checkout / ".git").mkdir(parents=True)
        runner.script(
            [*SAFE, "grep", "-l", "-F", "filter=lfs"], stdout="HEAD:.gitattributes\n", exit_code=0
        )

        manager.pull(checkout, branch="main")

        assert [c for c in git_calls(runner, "lfs") if "pull" in c]
