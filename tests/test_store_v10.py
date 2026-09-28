# SPDX-License-Identifier: AGPL-3.0-or-later
"""What schema v10 keeps: blue/green, secret marks, previews, backups, GitHub."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from wasm.core.exceptions import ValidationError
from wasm.core.store import (
    App,
    BackupDestinationRecord,
    BackupScheduleRecord,
    GitHubAppRecord,
    GitHubInstallationRecord,
    PreviewRecord,
    PreviewSettings,
    WASMStore,
)

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WASMStore]:
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "state" / "wasm.db")
    instance.create_app(App(domain=DOMAIN, app_path=str(tmp_path / "app"), port=3000))
    yield instance
    WASMStore.reset_instance()


def app(store: WASMStore) -> App:
    record = store.get_app(DOMAIN)
    assert record is not None
    return record


class TestBlueGreenColumns:
    def test_turning_it_on_and_off(self, store: WASMStore) -> None:
        assert store.set_zero_downtime(DOMAIN, True, drain_seconds=5)
        assert store.set_active_color(DOMAIN, "green")
        assert (app(store).zero_downtime, app(store).drain_seconds) == (True, 5)
        assert app(store).active_color == "green"

        store.set_zero_downtime(DOMAIN, False)
        assert (app(store).zero_downtime, app(store).active_color) == (False, None)

    @pytest.mark.parametrize("drain", [-1, 301, True, "10"])
    def test_a_drain_out_of_range_is_refused(self, store: WASMStore, drain: object) -> None:
        with pytest.raises(ValidationError):
            store.set_zero_downtime(DOMAIN, True, drain_seconds=drain)  # type: ignore[arg-type]

    def test_an_unknown_color_is_refused(self, store: WASMStore) -> None:
        with pytest.raises(ValidationError):
            store.set_active_color(DOMAIN, "red")

    def test_a_redeploy_rewriting_the_row_keeps_them(self, store: WASMStore) -> None:
        """update_app writes back the row a deploy read before; it must not undo a setter."""
        stale = app(store)
        store.set_zero_downtime(DOMAIN, True)
        store.set_active_color(DOMAIN, "blue")
        store.set_env_secret_marks(DOMAIN, {"PUBLIC_KEY": False})
        store.set_github_installation(DOMAIN, 42)

        store.update_app(stale)

        fresh = app(store)
        assert (fresh.zero_downtime, fresh.active_color) == (True, "blue")
        assert fresh.env_secret_marks == {"PUBLIC_KEY": False}
        assert fresh.github_installation_id == 42


class TestSecretMarks:
    def test_marks_round_trip(self, store: WASMStore) -> None:
        store.set_env_secret_marks(DOMAIN, {"STRIPE_SK": True, "NEXT_PUBLIC_API_KEY": False})
        assert app(store).env_secret_marks == {"STRIPE_SK": True, "NEXT_PUBLIC_API_KEY": False}

    @pytest.mark.parametrize("marks", [{"1BAD": True}, {"A B": True}, {"OK": "yes"}])
    def test_invalid_marks_are_refused(self, store: WASMStore, marks: dict) -> None:
        with pytest.raises(ValidationError):
            store.set_env_secret_marks(DOMAIN, marks)


class TestPreviews:
    def test_settings_and_previews(self, store: WASMStore) -> None:
        store.save_preview_settings(
            PreviewSettings(app_domain=DOMAIN, base_domain="previews.example.com", max_previews=2)
        )
        settings = store.get_preview_settings(DOMAIN)
        assert settings is not None and settings.max_previews == 2

        first = store.save_preview(
            PreviewRecord(
                parent_domain=DOMAIN,
                domain="pr-7.shop-example-com.previews.example.com",
                number=7,
                branch="feature",
                provider="github",
                expires_at="2026-10-01T00:00:00+00:00",
            )
        )
        assert first.id is not None and first.status == "pending"

        first.head_sha, first.status = "abc1234", "ready"
        again = store.save_preview(first)
        assert (again.id, again.head_sha, again.status) == (first.id, "abc1234", "ready")
        assert store.get_preview_by_domain(first.domain) == again
        assert [p.number for p in store.list_previews(DOMAIN)] == [7]
        assert store.list_expired_previews("2026-09-30T00:00:00+00:00") == []
        assert store.list_expired_previews("2026-10-01T00:00:00+00:00") == [again]

        assert store.delete_preview(first.domain)
        assert store.delete_preview_settings(DOMAIN)
        assert store.get_preview_settings(DOMAIN) is None


class TestBackups:
    def test_destinations(self, store: WASMStore) -> None:
        store.save_backup_destination(
            BackupDestinationRecord(name="nas", backend="sftp", settings={"host": "nas.lan"})
        )
        store.save_backup_destination(
            BackupDestinationRecord(
                name="nas", backend="sftp", settings={"host": "n2"}, encrypted=True
            )
        )
        stored = store.get_backup_destination("nas")
        assert stored is not None
        assert (stored.settings, stored.encrypted) == ({"host": "n2"}, True)
        assert [d.name for d in store.list_backup_destinations()] == ["nas"]
        assert store.delete_backup_destination("nas")
        assert store.get_backup_destination("nas") is None

    def test_schedules(self, store: WASMStore) -> None:
        store.save_backup_schedule(
            BackupScheduleRecord(
                app_domain=DOMAIN,
                schedule="daily",
                retention_count=7,
                destinations=[{"name": "nas", "retention_count": 30}],
            )
        )
        stored = store.get_backup_schedule(DOMAIN)
        assert stored is not None
        assert stored.destinations == [{"name": "nas", "retention_count": 30}]
        assert stored.include_databases is True
        assert [s.app_domain for s in store.list_backup_schedules()] == [DOMAIN]
        assert store.delete_backup_schedule(DOMAIN)


class TestGitHub:
    def test_app_and_installations(self, store: WASMStore) -> None:
        store.save_github_app(GitHubAppRecord(app_id=1, slug="wasm-host", owner="acme"))
        store.save_github_installation(GitHubInstallationRecord(installation_id=9, account="acme"))
        store.set_github_installation(DOMAIN, 9)

        github_app = store.get_github_app()
        assert github_app is not None and github_app.slug == "wasm-host"
        assert [i.account for i in store.list_github_installations()] == ["acme"]

        assert store.delete_github_installation(9)
        assert app(store).github_installation_id is None

    def test_deleting_the_app_forgets_everything_linked(self, store: WASMStore) -> None:
        store.save_github_app(GitHubAppRecord(app_id=1, slug="wasm-host"))
        store.save_github_installation(GitHubInstallationRecord(installation_id=9, account="acme"))
        store.set_github_installation(DOMAIN, 9)

        assert store.delete_github_app()
        assert store.get_github_app() is None
        assert store.list_github_installations() == []
        assert app(store).github_installation_id is None
