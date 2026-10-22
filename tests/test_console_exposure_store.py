# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the exposure records, in the access lane's fragment of schema v14.

The record is what lets ``unexpose`` remove exactly what ``expose`` made, so what
is pinned is that it survives: a fresh store has the table, a store stamped v14
before the fragment existed gets it added, a value written comes back whole, and
nothing but a state the code knows is stored.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import schema_v14
from noust.core.console_exposure_store import (
    STATE_ACTIVE,
    STATE_PENDING,
    ConsoleExposureStore,
    ExposureState,
)
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def run_script(cursor: sqlite3.Cursor, script: str) -> None:
    for statement in script.split(";"):
        if statement.strip():
            cursor.execute(statement)


def test_the_fragment_creates_one_table_and_applies_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    # This is about the access fragment alone, over a database with the one older table it
    # may alter: another area's tables, columns and triggers are tested by that area, and
    # they alter tables (`deployments`, `apps`...) this database does not have. Every other
    # area is emptied by name, so a fragment added later does not have to be listed here.
    for name in list(vars(schema_v14)):
        if name.endswith("_SQL") and name != "ACCESS_SQL":
            monkeypatch.setattr(schema_v14, name, "")
        elif name.endswith("_COLUMNS") and name != "ACCESS_COLUMNS":
            monkeypatch.setattr(schema_v14, name, [])
    connection = sqlite3.connect(":memory:")
    cursor = connection.cursor()
    cursor.execute("CREATE TABLE apps (id INTEGER PRIMARY KEY, domain TEXT)")
    assert schema_v14.incomplete(cursor) is True

    schema_v14.apply_v14(cursor, run_script)
    schema_v14.apply_v14(cursor, run_script)

    assert schema_v14._existing_columns(cursor, "console_exposure") >= {
        "domain",
        "port",
        "allow_ips",
        "site_created",
        "certificate_created",
        "proxy_added",
        "previous_public_url",
        "previous_allowed_hosts",
        "state",
    }
    connection.close()


def test_a_fresh_store_has_the_table_and_nothing_in_it(store: NoustStore) -> None:
    assert ConsoleExposureStore(store).get() is None


def test_a_store_stamped_v14_before_the_fragment_existed_gets_the_table(
    tmp_path: Path, store: NoustStore
) -> None:
    with store._transaction() as cursor:
        cursor.execute("DROP TABLE console_exposure")
    NoustStore.reset_instance()

    reopened = NoustStore(tmp_path / "noust.db")

    assert ConsoleExposureStore(reopened).get() is None
    ConsoleExposureStore(reopened).save(ExposureState(domain="console.example.com", port=8080))
    assert ConsoleExposureStore(reopened).get() is not None


def test_what_is_written_comes_back_whole(store: NoustStore) -> None:
    exposures = ConsoleExposureStore(store)
    state = ExposureState(
        domain="console.example.com",
        port=8443,
        scheme="https",
        allow_ips=["203.0.113.0/24", "2001:db8::/32"],
        site_created=True,
        certificate_created=True,
        proxy_added="127.0.0.1",
        previous_public_url="https://old.example.org",
        previous_allowed_hosts=["old.example.org"],
        state=STATE_ACTIVE,
        created_by="cli:maria",
    )

    exposures.save(state)
    again = exposures.get()

    assert again is not None
    assert again.domain == "console.example.com" and again.port == 8443
    assert again.scheme == "https"
    assert again.allow_ips == ["203.0.113.0/24", "2001:db8::/32"]
    assert again.site_created and again.certificate_created
    assert again.proxy_added == "127.0.0.1"
    assert again.previous_public_url == "https://old.example.org"
    assert again.previous_allowed_hosts == ["old.example.org"]
    assert again.state == STATE_ACTIVE and again.created_by == "cli:maria"
    assert again.created_at, "the moment is recorded when the caller gives none"


def test_unset_settings_stay_unset(store: NoustStore) -> None:
    exposures = ConsoleExposureStore(store)

    exposures.save(ExposureState(domain="console.example.com", port=8080))

    again = exposures.get()
    assert again is not None
    assert again.previous_public_url is None
    assert again.previous_allowed_hosts is None, "unset is not the same as an empty list"
    assert again.proxy_added is None
    assert again.state == STATE_PENDING


def test_there_is_one_exposure_and_saving_replaces_it(store: NoustStore) -> None:
    exposures = ConsoleExposureStore(store)
    exposures.save(ExposureState(domain="one.example.com", port=8080))

    exposures.save(ExposureState(domain="two.example.com", port=8081, state=STATE_ACTIVE))

    again = exposures.get()
    assert again is not None and again.domain == "two.example.com"
    count = store._get_connection().execute("SELECT COUNT(*) FROM console_exposure").fetchone()[0]
    assert count == 1


def test_clearing_forgets_it(store: NoustStore) -> None:
    exposures = ConsoleExposureStore(store)
    exposures.save(ExposureState(domain="console.example.com", port=8080))

    exposures.clear()

    assert exposures.get() is None


def test_only_a_known_state_is_stored(store: NoustStore) -> None:
    with pytest.raises(ValidationError):
        ConsoleExposureStore(store).save(
            ExposureState(domain="console.example.com", port=8080, state="sort-of-active")
        )


def test_the_api_shape_names_the_url_and_leaves_the_internals_out() -> None:
    shown = ExposureState(
        domain="console.example.com", port=8080, allow_ips=["203.0.113.0/24"], state=STATE_ACTIVE
    ).to_dict()

    assert shown["url"] == "https://console.example.com"
    assert shown["allow_ips"] == ["203.0.113.0/24"]
    assert "previous_public_url" not in shown and "previous_allowed_hosts" not in shown
