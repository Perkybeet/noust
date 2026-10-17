# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A scheduled update takes its backup as a condition (checklist B-23).

Nobody is there to read a warning at three in the morning, so a run asks ``update_app`` to refuse
the update when its backup does not happen, whatever the application's own policy says. The
fakes are ``test_update_backup_required.py``'s.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.exceptions import BackupError
from noust.core.store import NoustStore
from noust.core.update_policy_store import set_required
from noust.deployers import lifecycle
from tests.test_lifecycle import DOMAIN, make_app
from tests.test_lifecycle import store as store
from tests.test_update_backup_required import backups, with_database

FIXTURES = (backups, store)


class TestTheUpdateTakesItsBackupRequired:
    """A scheduled update has nobody to read a warning: its backup is a condition."""

    def test_without_the_flag_a_failed_backup_of_an_app_with_no_database_is_a_warning(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        make_app(store, tmp_path / "app")
        backups.failure = BackupError("disk full")

        lifecycle.update_app(DOMAIN)

        assert [call[0] for call in backups.recorder.calls][-2:] == ["update", "restart"]

    def test_with_it_the_same_failure_refuses_the_update_before_anything_changes(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        make_app(store, tmp_path / "app")
        backups.failure = BackupError("disk full")

        with pytest.raises(BackupError) as refused:
            lifecycle.update_app(DOMAIN, automatic=True, backup_required=True)

        assert "not applied" in refused.value.message
        assert [call[0] for call in backups.recorder.calls] == ["backup"]

    def test_on_releases_it_overrides_a_policy_that_turned_the_backup_off(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        app = make_app(store, tmp_path / "app")
        app.layout = "releases"
        store.update_app(app)
        with_database(store)
        set_required(DOMAIN, False, updated_by="ana", store=store)
        backups.failure = BackupError("the dump failed")

        lifecycle.update_app(DOMAIN)  # a person's update goes on, as the policy says
        assert [call[0] for call in backups.recorder.calls] == ["update_release"]
        backups.recorder.calls.clear()

        with pytest.raises(BackupError, match="not applied"):
            lifecycle.update_app(DOMAIN, automatic=True, backup_required=True)

        assert [call[0] for call in backups.recorder.calls] == ["backup"]

    def test_a_rollbacks_own_update_is_never_held_to_it(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        make_app(store, tmp_path / "app")
        backups.failure = BackupError("disk full")

        lifecycle._update_app(
            DOMAIN,
            source=None,
            branch=None,
            commit=None,
            tag=None,
            package_manager="auto",
            trigger="cli",
            job_id=None,
            on_phase=None,
            on_step=None,
            logger=None,
            verbose=False,
            gated=False,
            going_back=True,
            backup_required=True,
        )

        assert [call[0] for call in backups.recorder.calls][-2:] == ["update", "restart"]
