# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central wants every server to have: ``fleet_desired`` and the secrets beside it.

What is pinned: a version is one counter for every setting and scope that is never reused (so that
what a node applied says which row it was, also after an exception comes and goes); the secrets of
a value live in the secret store and never in the table or in what is shown; a person needs the
permission of the thing they set (the guard is the manager's, not the route's); an exception
belongs to a server that exists; and which value a server gets is decided in one place - its own
exception, else the fleet's, else nothing when it is independent.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from noust.core.exceptions import PermissionError as NoustPermissionError
from noust.core.exceptions import ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import NodeRecord, NoustStore
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.settings.kinds import REDACTED

MANAGE = frozenset({"settings.manage", "server.manage", "backups.manage", "apps.manage"})

CHANNEL: dict[str, Any] = {
    "type": "telegram",
    "enabled": True,
    "settings": {"chat_id": "-100", "token": "bot:s3cret"},
}


@pytest.fixture
def settings(store: NoustStore, secrets: SecretStore) -> FleetSettings:
    for name in ("web-1", "web-2", "web-3"):
        store.save_node(
            NodeRecord(
                name=name, ssh_host=name, ssh_port=22, ssh_user="u", host_key="k", console_port=1
            )
        )
    return FleetSettings(store, secrets)


def save(settings: FleetSettings, name: str = "ops", **kw: Any) -> Any:
    return settings.save(
        kw.pop("resource", "channel"),
        name,
        kw.pop("value", CHANNEL),
        scope=kw.pop("scope", "fleet"),
        person=kw.pop("person", "ana"),
        held=kw.pop("held", MANAGE),
    )


class TestSaving:
    def test_a_saved_setting_is_the_fleets_with_a_version_and_the_change_that_made_it(
        self, settings: FleetSettings
    ) -> None:
        saved = save(settings)

        assert (saved.resource, saved.name, saved.scope) == ("channel", "ops", "fleet")
        assert saved.version >= 1
        assert saved.changed_by == "ana"
        assert saved.change_id.startswith("c") and len(saved.change_id) == 13
        assert settings.get("channel", "ops") == saved

    def test_the_version_is_one_counter_for_everything_and_never_goes_back(
        self, settings: FleetSettings
    ) -> None:
        first = save(settings, "ops")
        second = save(settings, "alerts")
        again = save(settings, "ops")
        settings.remove("channel", "alerts", person="ana", held=MANAGE)
        third = save(settings, "alerts")

        versions = [first.version, second.version, again.version, third.version]
        assert versions == sorted(set(versions)), "a version was reused or went back"

    def test_saving_again_replaces_the_value_and_keeps_one_row(
        self, settings: FleetSettings
    ) -> None:
        save(settings)
        save(settings, value={**CHANNEL, "enabled": False})

        listed = settings.list("channel")
        assert [(s.name, s.value["enabled"]) for s in listed] == [("ops", False)]

    def test_the_secrets_are_in_the_secret_store_and_never_in_the_table(
        self, settings: FleetSettings, store: NoustStore, secrets: SecretStore
    ) -> None:
        saved = save(settings)

        rows = store._get_connection().execute("SELECT * FROM fleet_desired").fetchall()
        assert "s3cret" not in repr([tuple(row) for row in rows])
        assert saved.secret_paths == ("settings.token",)
        assert settings.whole_value(saved) == CHANNEL
        assert secrets.read(f"fleet-settings/v{saved.version}") is not None

    def test_what_is_shown_marks_the_secret_and_never_says_it(
        self, settings: FleetSettings
    ) -> None:
        saved = save(settings)

        shown = settings.shown(saved)

        assert shown["value"]["settings"]["token"] == REDACTED
        assert "s3cret" not in repr(shown)
        assert shown["secrets"] == ["settings.token"]

    def test_the_secret_of_the_row_that_was_replaced_is_gone_with_it(
        self, settings: FleetSettings, secrets: SecretStore
    ) -> None:
        old = save(settings)
        new = save(settings, value={**CHANNEL, "settings": {"chat_id": "-1", "token": "other"}})

        assert secrets.read(f"fleet-settings/v{old.version}") is None
        assert settings.whole_value(new)["settings"]["token"] == "other"

    def test_sending_the_redacted_mark_back_keeps_the_stored_secret(
        self, settings: FleetSettings
    ) -> None:
        save(settings)

        kept = save(settings, value={**CHANNEL, "settings": {"chat_id": "-2", "token": REDACTED}})

        assert settings.whole_value(kept)["settings"] == {"chat_id": "-2", "token": "bot:s3cret"}

    def test_a_failure_writing_the_secret_leaves_nothing_behind(
        self, settings: FleetSettings, secrets: SecretStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(name: str, value: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(secrets, "write", broken)

        with pytest.raises(OSError):
            save(settings)

        assert settings.list() == []


class TestWhoMaySet:
    def test_a_person_needs_the_permission_of_the_thing_they_set(
        self, settings: FleetSettings
    ) -> None:
        with pytest.raises(NoustPermissionError) as caught:
            save(settings, held=frozenset({"fleet.manage", "server.manage"}))

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.permission_missing"
        assert settings.list() == []

    def test_each_kind_asks_the_permission_of_its_own_route(self, settings: FleetSettings) -> None:
        rule = {"metric": "cpu", "threshold": 90}

        save(
            settings,
            "cpu-high",
            resource="alert_rule",
            value=rule,
            held=frozenset({"server.manage"}),
        )
        with pytest.raises(NoustPermissionError):
            save(
                settings,
                "cpu-low",
                resource="alert_rule",
                value=rule,
                held=frozenset({"settings.manage"}),
            )

    def test_removing_one_asks_it_too(self, settings: FleetSettings) -> None:
        save(settings)

        with pytest.raises(NoustPermissionError):
            settings.remove("channel", "ops", person="ana", held=frozenset())

        assert settings.get("channel", "ops") is not None


class TestExceptions:
    def test_a_server_can_have_a_value_of_its_own_beside_the_fleets(
        self, settings: FleetSettings
    ) -> None:
        fleet = save(settings)
        own = save(settings, scope="node:web-2", value={**CHANNEL, "enabled": False})

        assert own.version != fleet.version
        assert settings.get("channel", "ops", "node:web-2") == own
        assert settings.get("channel", "ops") == fleet

    def test_an_exception_for_a_server_that_does_not_exist_is_refused(
        self, settings: FleetSettings
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            save(settings, scope="node:web-9")

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.unknown_node"

    def test_an_exception_that_sends_the_mark_back_keeps_the_fleets_secret(
        self, settings: FleetSettings
    ) -> None:
        save(settings)

        own = save(
            settings,
            scope="node:web-2",
            value={**CHANNEL, "settings": {"chat_id": "-7", "token": REDACTED}},
        )

        assert settings.whole_value(own)["settings"]["token"] == "bot:s3cret"

    def test_taking_the_exception_away_leaves_the_fleets_value_for_that_server(
        self, settings: FleetSettings
    ) -> None:
        fleet = save(settings)
        save(settings, scope="node:web-2", value={**CHANNEL, "enabled": False})

        assert (
            settings.remove("channel", "ops", scope="node:web-2", person="ana", held=MANAGE) is True
        )

        assert [s.version for s in settings.effective_for("web-2")] == [fleet.version]


class TestWhatAServerGets:
    def test_its_own_exception_wins_over_the_fleets_value(self, settings: FleetSettings) -> None:
        save(settings, "ops")
        save(settings, "alerts")
        own = save(settings, "ops", scope="node:web-2", value={**CHANNEL, "enabled": False})

        web2 = {s.name: s for s in settings.effective_for("web-2")}
        web1 = {s.name: s for s in settings.effective_for("web-1")}

        assert web2["ops"] == own
        assert web1["ops"].scope == "fleet"
        assert set(web2) == set(web1) == {"ops", "alerts"}

    def test_an_exception_of_another_server_is_not_seen(self, settings: FleetSettings) -> None:
        save(settings, "ops")
        save(settings, "ops", scope="node:web-2", value={**CHANNEL, "enabled": False})

        assert [s.scope for s in settings.effective_for("web-3")] == ["fleet"]

    def test_an_independent_server_gets_nothing(self, settings: FleetSettings) -> None:
        save(settings, "ops")
        settings.set_mode("web-1", "independent", person="ana")

        assert settings.effective_for("web-1") == []
        assert settings.mode("web-1") == "independent"
        assert settings.mode("web-2") == "managed"

    def test_going_back_to_managed_gives_it_the_fleet_again(self, settings: FleetSettings) -> None:
        save(settings, "ops")
        settings.set_mode("web-1", "independent", person="ana")
        settings.set_mode("web-1", "managed", person="ana")

        assert [s.name for s in settings.effective_for("web-1")] == ["ops"]

    def test_the_mode_is_one_of_two(self, settings: FleetSettings) -> None:
        with pytest.raises(ValidationError):
            settings.set_mode("web-1", "paused", person="ana")
        with pytest.raises(ValidationError):
            settings.set_mode("web-9", "independent", person="ana")


class TestComingBack:
    def test_a_server_that_came_back_is_asked_for_a_resync_until_it_is_done(
        self, settings: FleetSettings
    ) -> None:
        settings.request_resync("web-2")
        settings.request_resync("web-3")

        assert settings.pending_resync() == ["web-2", "web-3"]

        settings.clear_resync("web-2")
        assert settings.pending_resync() == ["web-3"]

    def test_asking_twice_is_asking_once(self, settings: FleetSettings) -> None:
        settings.request_resync("web-2")
        settings.request_resync("web-2")

        assert settings.pending_resync() == ["web-2"]

    def test_a_resync_is_not_lost_when_the_mode_changes(self, settings: FleetSettings) -> None:
        settings.request_resync("web-2")
        settings.set_mode("web-2", "independent", person="ana")

        assert settings.pending_resync() == ["web-2"]


class TestTheSchema:
    def test_a_store_stamped_v16_before_the_tables_existed_gets_them_on_opening(
        self, tmp_path: Any
    ) -> None:
        NoustStore.reset_instance()
        path = tmp_path / "old.db"
        built = NoustStore(path)
        connection = built._get_connection()
        for table in ("fleet_desired", "fleet_managed", "fleet_node_settings"):
            connection.execute(f"DROP TABLE {table}")
        connection.commit()
        NoustStore.reset_instance()

        reopened = NoustStore(path)

        names = {
            row[0]
            for row in reopened._get_connection().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"fleet_desired", "fleet_managed", "fleet_node_settings"} <= names
        NoustStore.reset_instance()

    def test_the_version_is_the_rows_own_never_reused_id(self, store: NoustStore) -> None:
        connection = store._get_connection()
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'fleet_desired'"
        ).fetchone()[0]

        assert "AUTOINCREMENT" in sql
        with pytest.raises(sqlite3.IntegrityError):
            for _ in range(2):
                connection.execute(
                    "INSERT INTO fleet_desired (resource, name, scope, value, change_id, "
                    "changed_by, changed_at) VALUES ('channel', 'x', 'fleet', '{}', 'c', 'a', 't')"
                )
        connection.rollback()
