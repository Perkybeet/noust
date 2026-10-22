# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A policy that keeps no copy on this server (checklist H-13).

For a server with a small disk: once a scheduled dump is on every destination of its policy,
and each upload was checked when it arrived, the local file goes. What is defended is the
direction of the risk: a dump that did not reach every destination, or never had one to
reach, is never deleted, and a dump taken by hand is never this rule's.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import Context
from noust.cli.commands import db_backups
from noust.core.exceptions import BackupError, ValidationError
from noust.managers.database.backups import DatabaseBackups
from tests.test_database_backups import (  # noqa: F401
    FakeScheduler,
    RcloneRunner,
    _reset_store,
    add_destination,
    backups,
    dumps_dir,
    engine,
    runner,
    service,
    set_up_history,
)


@pytest.fixture(autouse=True)
def _nas(_reset_store: None, backups: DatabaseBackups, runner: RcloneRunner) -> None:
    """The destination every test sends to, made once the store is this test's own."""
    add_destination(runner)


class TestThePolicy:
    def test_it_is_stored_and_shown(self, backups: DatabaseBackups) -> None:
        view = backups.set_policy(
            "postgresql", "shop", destinations=[{"name": "nas"}], remote_only=True
        )

        assert view.to_dict()["remote_only"] is True
        assert backups.records.policy("postgresql", "shop").remote_only is True  # type: ignore[union-attr]

    def test_it_is_off_unless_asked_for_and_saving_again_does_not_keep_it(
        self, backups: DatabaseBackups
    ) -> None:
        assert backups.set_policy("postgresql", "shop").to_dict()["remote_only"] is False
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}], remote_only=True)
        assert backups.set_policy("postgresql", "shop").to_dict()["remote_only"] is False

    def test_without_a_destination_it_is_refused_before_a_timer_exists(
        self, backups: DatabaseBackups
    ) -> None:
        with pytest.raises(ValidationError, match="needs a destination") as caught:
            backups.set_policy("postgresql", "shop", remote_only=True)

        assert caught.value.field == "remote_only"
        assert backups.get_policy("postgresql", "shop").policy is None


class TestRunningIt:
    def test_the_dump_goes_to_every_destination_and_then_leaves_this_server(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}], remote_only=True)

        result = backups.run_policy("postgresql", "shop")

        assert result["destinations"]["nas"]["ok"] is True
        assert result["kept_locally"] is False
        assert result["local_deleted"] == [result["dump"]["name"]]
        assert list(dumps_dir.glob("postgresql-shop-*")) == []
        assert backups.list_dumps("postgresql", "shop") == []

    def test_it_is_still_on_the_destination_afterwards(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}], remote_only=True)

        backups.run_policy("postgresql", "shop")

        remote = backups.remote_dumps("postgresql", "shop", "nas")
        assert len(remote) == 1

    def test_a_dump_that_could_not_be_sent_stays(
        self, backups: DatabaseBackups, dumps_dir: Path, runner: RcloneRunner
    ) -> None:
        runner.refusing["nas"] = "Failed to copyto: connection refused"
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}], remote_only=True)

        with pytest.raises(BackupError, match="could not be sent"):
            backups.run_policy("postgresql", "shop")

        assert len(list(dumps_dir.glob("postgresql-shop-*"))) == 1

    def test_with_two_destinations_one_failing_keeps_the_dump(
        self, backups: DatabaseBackups, dumps_dir: Path, runner: RcloneRunner
    ) -> None:
        add_destination(runner, "offsite")
        runner.refusing["offsite"] = "Failed to copyto: no route to host"
        backups.set_policy(
            "postgresql",
            "shop",
            destinations=[{"name": "nas"}, {"name": "offsite"}],
            remote_only=True,
        )

        with pytest.raises(BackupError):
            backups.run_policy("postgresql", "shop")

        assert len(list(dumps_dir.glob("postgresql-shop-*"))) == 1

    def test_dumps_taken_by_hand_and_safety_copies_are_not_this_rules(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}], remote_only=True)

        backups.run_policy("postgresql", "shop")

        assert files["manual"].exists() and files["safety"].exists()

    def test_the_restore_test_runs_before_the_local_file_goes(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        backups.set_policy(
            "postgresql",
            "shop",
            destinations=[{"name": "nas"}],
            remote_only=True,
            verify_restore=True,
        )

        result = backups.run_policy("postgresql", "shop")

        assert result["restore_test"] == "ok"
        assert list(dumps_dir.glob("postgresql-shop-*")) == []


class TestTheCommandLine:
    def test_the_flag_reaches_the_policy_and_the_listing_says_so(
        self, backups: DatabaseBackups, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(db_backups, "_backups", lambda logger: backups)

        result = CliRunner().invoke(
            db_backups.backup_schedule,
            ["set", "shop", "-e", "postgresql", "--to", "nas", "--remote-only"],
            obj=Context(),
        )

        assert result.exit_code == 0, result.output
        assert backups.records.policy("postgresql", "shop").remote_only is True  # type: ignore[union-attr]
        assert "nothing once every destination holds the dump" in result.output
