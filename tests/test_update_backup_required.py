# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A backup that must exist before an update (checklist D-17): when it fails, nothing changes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli
from noust.core.exceptions import BackupError, NoustError
from noust.core.store import Database, NoustStore
from noust.core.update_policy_store import get_policy, set_required
from noust.deployers import lifecycle
from tests.test_lifecycle import DOMAIN, FakeDeployer, Recorder, make_app
from tests.test_lifecycle import store as store


@pytest.fixture
def backups(monkeypatch: pytest.MonkeyPatch, store: NoustStore) -> SimpleNamespace:
    """
    Every collaborator of an update faked, and the pre-update backup scripted.

    ``failure`` is raised by the backup when set; ``taken`` records what it was asked.
    """
    rec = Recorder()
    state = SimpleNamespace(recorder=rec, failure=None, taken=[], release_calls=[])

    def take(**kwargs: Any) -> None:
        state.taken.append(kwargs)
        rec.calls.append(("backup", kwargs["domain"]))
        if state.failure is not None:
            raise state.failure
        return SimpleNamespace(id="backup-1")

    monkeypatch.setattr(
        lifecycle,
        "RollbackManager",
        lambda verbose=False: SimpleNamespace(create_pre_deploy_backup=take),
    )
    monkeypatch.setattr(
        lifecycle,
        "SourceManager",
        lambda verbose=False: SimpleNamespace(
            get_repo_info=lambda path: {"commit": "abc1234", "branch": None},
            pull=lambda path, branch=None: rec.calls.append(("pull",)),
        ),
    )
    monkeypatch.setattr(
        lifecycle,
        "ServiceManager",
        lambda verbose=False: SimpleNamespace(
            get_status=lambda name: {"exists": True, "active": True},
            restart=lambda name: rec.calls.append(("restart", name)) or True,
            get_service_config=lambda name: None,
        ),
    )
    monkeypatch.setattr(lifecycle.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        lifecycle, "get_deployer", lambda app_type, verbose=False: FakeDeployer(rec)
    )

    def update_release(*args: Any, **kwargs: Any) -> str:
        rec.calls.append(("update_release",))
        return "released"

    monkeypatch.setattr(lifecycle, "_update_release", update_release)
    return state


def with_database(store: NoustStore, domain: str = DOMAIN) -> None:
    """Link a database to the application."""
    app = store.get_app(domain)
    assert app is not None and app.id is not None
    store.create_database(Database(name="shop", engine="postgresql", app_id=app.id))


class TestThePolicy:
    def test_an_application_without_a_database_does_not_require_it(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        make_app(store, tmp_path / "app")

        policy = get_policy(DOMAIN, store=store)

        assert policy is not None
        assert (policy.required, policy.explicit, policy.default_required) == (False, False, False)

    def test_an_application_with_a_database_requires_it_by_default(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        make_app(store, tmp_path / "app")
        with_database(store)

        policy = get_policy(DOMAIN, store=store)

        assert policy is not None and policy.required is True and policy.explicit is False
        assert policy.databases == ("postgresql/shop",)

    def test_the_operators_choice_wins_and_default_restores_the_rule(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        make_app(store, tmp_path / "app")
        with_database(store)

        off = set_required(DOMAIN, False, updated_by="alice", store=store)
        assert off is not None and (off.required, off.explicit) == (False, True)
        again = set_required(DOMAIN, None, updated_by="alice", store=store)
        assert again is not None and (again.required, again.explicit) == (True, False)

    def test_an_unknown_application_has_no_policy(self, store: NoustStore) -> None:
        assert get_policy("nobody.example.com", store=store) is None
        assert set_required("nobody.example.com", True, updated_by=None, store=store) is None

    def test_a_deploys_full_row_write_does_not_put_the_choice_back(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        app = make_app(store, tmp_path / "app")
        set_required(DOMAIN, True, updated_by="alice", store=store)

        app.branch = "other"
        store.update_app(app)

        policy = get_policy(DOMAIN, store=store)
        assert policy is not None and policy.required is True and policy.explicit is True

    def test_the_choice_goes_with_the_application(self, store: NoustStore, tmp_path: Path) -> None:
        make_app(store, tmp_path / "app")
        set_required(DOMAIN, True, updated_by="alice", store=store)

        store.delete_app(DOMAIN)

        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM app_update_policy")
            assert cursor.fetchone()[0] == 0


class TestInPlace:
    def test_a_failed_backup_stops_an_application_with_a_database(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        make_app(store, tmp_path / "app")
        with_database(store)
        backups.failure = BackupError("disk full")

        with pytest.raises(BackupError) as refused:
            lifecycle.update_app(DOMAIN)

        assert "not applied" in refused.value.message
        assert "disk full" in (refused.value.details or "")
        assert f"noust app backup-required {DOMAIN} off" in (refused.value.details or "")
        kinds = [call[0] for call in backups.recorder.calls]
        assert kinds == ["backup"]  # nothing was pulled, built or restarted

    def test_the_backup_carries_the_databases_when_it_is_required(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        make_app(store, tmp_path / "app")
        with_database(store)

        lifecycle.update_app(DOMAIN)

        assert backups.taken[0]["include_databases"] is True
        assert [call[0] for call in backups.recorder.calls][-2:] == ["update", "restart"]

    def test_without_a_database_a_failed_backup_is_a_warning(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        make_app(store, tmp_path / "app")
        backups.failure = BackupError("disk full")

        lifecycle.update_app(DOMAIN)

        assert [call[0] for call in backups.recorder.calls][-2:] == ["update", "restart"]
        assert backups.taken[0]["include_databases"] is False

    def test_turning_it_off_lets_the_update_go_on(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        make_app(store, tmp_path / "app")
        with_database(store)
        set_required(DOMAIN, False, updated_by="alice", store=store)
        backups.failure = OSError("no space left")

        lifecycle.update_app(DOMAIN)

        assert [call[0] for call in backups.recorder.calls][-2:] == ["update", "restart"]

    def test_turning_it_on_stops_an_application_without_a_database(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        make_app(store, tmp_path / "app")
        set_required(DOMAIN, True, updated_by="alice", store=store)
        backups.failure = NoustError("cannot write")

        with pytest.raises(BackupError):
            lifecycle.update_app(DOMAIN)

        assert "pull" not in [call[0] for call in backups.recorder.calls]

    def test_a_rollbacks_own_update_is_never_held_to_it(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        """The way back from a bad update must not wait for a backup of the bad state."""
        make_app(store, tmp_path / "app")
        with_database(store)
        backups.failure = BackupError("the database is down")

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
        )

        assert backups.taken[0]["include_databases"] is False
        assert [call[0] for call in backups.recorder.calls][-2:] == ["update", "restart"]


class TestOnReleases:
    def releases(self, store: NoustStore, tmp_path: Path) -> None:
        app = make_app(store, tmp_path / "app")
        app.layout = "releases"
        store.update_app(app)

    def test_an_application_with_a_database_is_backed_up_before_the_release_is_built(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        self.releases(store, tmp_path)
        with_database(store)

        lifecycle.update_app(DOMAIN)

        assert [call[0] for call in backups.recorder.calls] == ["backup", "update_release"]
        assert backups.taken[0]["include_databases"] is True

    def test_a_failed_backup_stops_it_before_a_release_exists(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        self.releases(store, tmp_path)
        with_database(store)
        backups.failure = BackupError("the dump failed")

        with pytest.raises(BackupError):
            lifecycle.update_app(DOMAIN)

        assert [call[0] for call in backups.recorder.calls] == ["backup"]

    def test_without_a_database_a_release_update_takes_no_backup_as_before(
        self, store: NoustStore, tmp_path: Path, backups: SimpleNamespace
    ) -> None:
        self.releases(store, tmp_path)

        lifecycle.update_app(DOMAIN)

        assert [call[0] for call in backups.recorder.calls] == ["update_release"]


class TestTheCommand:
    def run(self, *args: str) -> Any:
        return CliRunner().invoke(cli, ["app", "backup-required", *args])

    def test_it_shows_and_changes_the_requirement(
        self, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        NoustStore._instance = store
        make_app(store, tmp_path / "app")
        with_database(store)

        shown = self.run(DOMAIN, "--json")
        off = self.run(DOMAIN, "off")
        shown_off = self.run(DOMAIN, "--json")
        default = self.run(DOMAIN, "default")

        import json

        assert json.loads(shown.output)["required"] is True
        assert off.exit_code == 0, off.output
        assert json.loads(shown_off.output)["required"] is False
        assert json.loads(shown_off.output)["explicit"] is True
        assert default.exit_code == 0
        assert get_policy(DOMAIN, store=store).explicit is False  # type: ignore[union-attr]

    def test_an_unknown_application_is_an_error(self, store: NoustStore) -> None:
        NoustStore._instance = store

        result = self.run("nobody.example.com", "on")

        assert result.exit_code != 0
