# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the pre-flight checks.

The repository probe is the first thing a deploy does and the first thing an
operator sees fail, so its error message is the one that has to be true.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.deployers.helpers import preflight


class TestRepositoryProbe:
    """`git ls-remote` has to run somewhere that exists and report what git said."""

    def test_runs_from_a_directory_that_exists(self, runner: FakeRunner, monkeypatch):
        """
        After `noust delete` the operator's shell is often still inside the
        directory that was just removed. A child inheriting that cwd makes git
        abort with "Unable to read current working directory", which used to
        surface as an opaque "Repository not accessible" even when the
        credentials were fine.
        """
        seen: dict[str, Path | None] = {}

        def record(argv, **kwargs):
            seen["cwd"] = kwargs.get("cwd")
            return FakeRunner().run(argv)

        monkeypatch.setattr(runner, "run", record)

        preflight.repository_unreachable(runner, "git@github.com:user/repo.git")

        assert seen["cwd"] is not None, "the probe inherited the caller's working directory"
        assert seen["cwd"].exists()

    def test_reports_what_git_actually_said(self, runner: FakeRunner):
        runner.script(
            ["git", "ls-remote"],
            exit_code=128,
            stderr="ERROR: Repository not found.\nfatal: Could not read from remote repository.",
        )

        issues = preflight.repository_unreachable(runner, "git@github.com:user/gone.git")

        assert "ERROR: Repository not found." in issues[0], (
            "git's own first line beats a summary that fits half a dozen causes"
        )

    def test_points_at_the_ssh_check_when_that_is_the_problem(self, runner: FakeRunner):
        runner.script(
            ["git", "ls-remote"], exit_code=128, stderr="git@github.com: Permission denied"
        )

        issues = preflight.repository_unreachable(runner, "git@github.com:user/repo.git")

        assert any("noust setup ssh --test" in issue for issue in issues)

    def test_a_reachable_repository_reports_nothing(self, runner: FakeRunner):
        runner.script(["git", "ls-remote"], stdout="abc123\tHEAD")

        assert preflight.repository_unreachable(runner, "https://example.com/x.git") == []

    def test_a_private_repository_is_probed_with_the_token_its_central_leased(
        self, runner: FakeRunner, store
    ):
        """
        The first application of a repository is made by cloning it, and the probe that comes
        first is the clone's own credential's to carry: without it a repository only the token
        reaches is "not accessible" and the application is never made.
        """
        from noust.integrations.github.leases import SourceLeases

        SourceLeases().hold(
            central="nas",
            key="ghe",
            host="ghe.example.com",
            installation_id=5,
            repositories=["you/app"],
            token="ghs_leased_probe",
            expires_in=3600,
        )
        runner.script(["git", "ls-remote"], stdout="abc123\tHEAD")

        issues = preflight.repository_unreachable(runner, "https://ghe.example.com/you/app.git")

        assert issues == []
        (env,) = [
            dict(e or {})
            for c, e in zip(runner.calls, runner.envs, strict=True)
            if "ls-remote" in c
        ]
        assert env["GIT_CONFIG_KEY_0"] == "http.https://ghe.example.com/.extraheader"
        assert "AUTHORIZATION: basic" in env["GIT_CONFIG_VALUE_0"]
        for call in runner.calls:
            assert "ghs_leased_probe" not in " ".join(call)

    def test_a_repository_no_lease_covers_is_probed_as_before(self, runner: FakeRunner, store):
        runner.script(["git", "ls-remote"], stdout="abc123\tHEAD")

        preflight.repository_unreachable(runner, "https://example.com/x.git")

        (env,) = [
            dict(e or {})
            for c, e in zip(runner.calls, runner.envs, strict=True)
            if "ls-remote" in c
        ]
        assert "GIT_CONFIG_COUNT" not in env

    @pytest.mark.parametrize("source", ["/var/www/local", "./relative", "~/somewhere"])
    def test_a_local_path_is_not_probed(self, runner: FakeRunner, source: str):
        assert preflight.repository_unreachable(runner, source) == []
        assert runner.calls == []
