# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the registry last said of each domain Noust serves, kept in the store (item 173).

The schema is the dates lane's fragment of v18: ``domain_registrations`` (one row per registrable
domain: when its registration expires, how the last look ended, when to look again) and
``domain_registrables`` (which registrable domain each name Noust serves was found to belong to, so
the registry is not asked label by label every day).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core import schema_v18
from noust.core.domain_registrations import (
    ERROR,
    NO_RDAP,
    OK,
    Registration,
    RegistrationStore,
)
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, _run_script

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def registrations(store: NoustStore) -> RegistrationStore:
    return RegistrationStore(store)


def registration(domain: str = "example.com", **overrides: Any) -> Registration:
    values: dict[str, Any] = {
        "domain": domain,
        "status": OK,
        "expires_at": "2027-03-01T00:00:00+00:00",
        "rdap_base": "https://rdap.verisign.com/com/v1/",
        "checked_at": NOW.isoformat(timespec="seconds"),
        "next_check_at": (NOW + timedelta(days=1)).isoformat(timespec="seconds"),
        "detail": "",
    }
    values.update(overrides)
    return Registration(**values)


class TestTheSchema:
    def test_the_dates_lane_fragment_makes_the_two_tables(self, store: NoustStore) -> None:
        with store._transaction() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            tables = {row[0] for row in cursor.fetchall()}

        assert {"domain_registrations", "domain_registrables"} <= tables

    def test_a_store_that_lacks_them_is_incomplete_and_applying_completes_it(
        self, store: NoustStore
    ) -> None:
        with store._transaction() as cursor:
            cursor.execute("DROP TABLE domain_registrations")
            cursor.execute("DROP TABLE domain_registrables")
            assert schema_v18.incomplete(cursor) is True

            schema_v18.apply_v18(cursor, _run_script)

            assert schema_v18.incomplete(cursor) is False

    def test_applying_it_twice_changes_nothing(self, store: NoustStore) -> None:
        with store._transaction() as cursor:
            schema_v18.apply_v18(cursor, _run_script)
            schema_v18.apply_v18(cursor, _run_script)


class TestKeepingWhatTheRegistrySaid:
    def test_a_registration_is_kept_and_read_back(self, registrations: RegistrationStore) -> None:
        registrations.save(registration())

        assert registrations.get("example.com") == registration()

    def test_saving_again_replaces_it(self, registrations: RegistrationStore) -> None:
        registrations.save(registration())
        registrations.save(registration(expires_at="2028-03-01T00:00:00+00:00"))

        assert registrations.get("example.com").expires_at == "2028-03-01T00:00:00+00:00"  # type: ignore[union-attr]
        assert len(registrations.all()) == 1

    def test_what_is_not_there_is_none(self, registrations: RegistrationStore) -> None:
        assert registrations.get("nowhere.example") is None
        assert registrations.all() == []

    def test_they_are_listed_soonest_to_expire_first_and_the_unknown_last(
        self, registrations: RegistrationStore
    ) -> None:
        registrations.save(registration("late.example.com", expires_at="2028-01-01T00:00:00+00:00"))
        registrations.save(registration("soon.example.com", expires_at="2027-01-01T00:00:00+00:00"))
        registrations.save(
            registration("unknown.example.es", status=NO_RDAP, expires_at=None, rdap_base=None)
        )

        assert [r.domain for r in registrations.all()] == [
            "soon.example.com",
            "late.example.com",
            "unknown.example.es",
        ]

    def test_the_days_left_are_whole_days_and_negative_once_it_expired(self) -> None:
        soon = registration(expires_at="2026-11-20T00:00:00+00:00")
        gone = registration(expires_at="2026-11-08T00:00:00+00:00")

        assert soon.days_left(NOW) == 9
        assert gone.days_left(NOW) == -3
        assert registration(status=NO_RDAP, expires_at=None).days_left(NOW) is None

    def test_a_registration_is_due_when_its_time_has_come(self) -> None:
        item = registration(next_check_at=(NOW + timedelta(hours=1)).isoformat(timespec="seconds"))

        assert item.due(NOW) is False
        assert item.due(NOW + timedelta(hours=2)) is True


class TestWhatIsRefused:
    def test_a_status_that_is_not_one_is_refused(self, registrations: RegistrationStore) -> None:
        with pytest.raises(ValidationError):
            registrations.save(registration(status="whatever"))

    def test_a_domain_that_is_not_a_domain_is_refused(
        self, registrations: RegistrationStore
    ) -> None:
        for name in ("", "localhost", "../x", "10.0.0.1", "a b.example"):
            with pytest.raises(ValidationError):
                registrations.save(registration(name))

    def test_a_date_that_is_not_a_date_is_refused(self, registrations: RegistrationStore) -> None:
        with pytest.raises(ValidationError):
            registrations.save(registration(expires_at="next spring"))
        with pytest.raises(ValidationError):
            registrations.save(registration(next_check_at="soon"))

    def test_a_registry_address_that_is_not_https_is_refused(
        self, registrations: RegistrationStore
    ) -> None:
        with pytest.raises(ValidationError):
            registrations.save(registration(rdap_base="http://rdap.example/"))

    def test_what_a_registry_wrote_is_kept_short(self, registrations: RegistrationStore) -> None:
        registrations.save(registration(status=ERROR, expires_at=None, detail="x" * 5000))

        assert len(registrations.get("example.com").detail) <= 500  # type: ignore[union-attr]


class TestWhichNameBelongsToWhichDomain:
    def test_the_registrable_domain_found_for_a_name_is_kept(
        self, registrations: RegistrationStore
    ) -> None:
        registrations.remember_registrable("shop.example.co.uk", "example.co.uk", NOW)

        assert registrations.registrable_of("shop.example.co.uk") == "example.co.uk"
        assert registrations.registrable_of("blog.example.co.uk") is None

    def test_it_can_be_replaced_and_forgotten(self, registrations: RegistrationStore) -> None:
        registrations.remember_registrable("shop.example.co.uk", "example.co.uk", NOW)
        registrations.remember_registrable("shop.example.co.uk", "example.uk", NOW)
        assert registrations.registrable_of("shop.example.co.uk") == "example.uk"

        registrations.forget_registrable("shop.example.co.uk")

        assert registrations.registrable_of("shop.example.co.uk") is None

    def test_every_name_is_judged_by_the_name_it_was_asked_as(
        self, registrations: RegistrationStore
    ) -> None:
        registrations.remember_registrable("*.example.com", "example.com", NOW)

        assert registrations.registrable_of("*.Example.com") == "example.com"

    def test_the_names_that_belong_to_a_domain_are_listed(
        self, registrations: RegistrationStore
    ) -> None:
        registrations.remember_registrable("a.example.com", "example.com", NOW)
        registrations.remember_registrable("b.example.com", "example.com", NOW)
        registrations.remember_registrable("c.example.org", "example.org", NOW)

        assert registrations.names_of("example.com") == ["a.example.com", "b.example.com"]


class TestForgettingWhatIsNotServedAny:
    def test_what_no_served_name_leads_to_is_dropped(
        self, registrations: RegistrationStore
    ) -> None:
        registrations.save(registration("kept.example.com"))
        registrations.save(registration("gone.example.org"))
        registrations.remember_registrable("www.kept.example.com", "kept.example.com", NOW)
        registrations.remember_registrable("www.gone.example.org", "gone.example.org", NOW)

        registrations.keep_only(served={"www.kept.example.com"})

        assert [r.domain for r in registrations.all()] == ["kept.example.com"]
        assert registrations.registrable_of("www.gone.example.org") is None
        assert registrations.registrable_of("www.kept.example.com") == "kept.example.com"


class TestReadingWithoutAsking:
    def test_a_name_reads_the_registration_of_the_domain_it_belongs_to(
        self, registrations: RegistrationStore
    ) -> None:
        registrations.save(registration("example.com"))
        registrations.remember_registrable("shop.example.com", "example.com", NOW)

        assert registrations.for_name("shop.example.com") == registration("example.com")
        assert registrations.for_name("*.shop.example.com") == registration("example.com")

    def test_a_name_the_registry_could_not_be_asked_about_reads_its_own_look(
        self, registrations: RegistrationStore
    ) -> None:
        looked = registration("shop.example.com", status=ERROR, expires_at=None, rdap_base=None)
        registrations.save(looked)

        assert registrations.for_name("shop.example.com") == looked

    def test_a_name_never_looked_at_is_none(self, registrations: RegistrationStore) -> None:
        assert registrations.for_name("nowhere.example.com") is None
