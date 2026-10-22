# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The record of which names of an application hold a certificate of their own.

Pinned here: the vocabulary and the lineage a name's own certificate is kept in
(:mod:`noust.core.domain_certificates`), the store's validated setter for the choice, and that the
choice outlives the full-row write of an application a redeploy makes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import domain_certificates as dc
from noust.core.exceptions import DomainError, ValidationError
from noust.core.store import App, DomainKind, DomainRecord, NoustStore


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store of the test's own, installed as the process-wide one."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def make_app(store: NoustStore, domain: str = "example.com", app_type: str = "nodejs") -> App:
    """Create an application with a few names."""
    app = store.create_app(App(domain=domain, app_type=app_type, port=3000))
    store.add_domain(domain, f"shop.{domain}", DomainKind.ALIAS.value)
    store.add_domain(domain, f"admin.{domain}", DomainKind.ALIAS.value)
    store.add_domain(domain, f"old.{domain}", DomainKind.REDIRECT.value)
    return app


class TestVocabulary:
    def test_the_modes_are_shared_and_own(self) -> None:
        assert dc.MODES == ("shared", "own")
        assert dc.SHARED == "shared"
        assert dc.OWN == "own"

    @pytest.mark.parametrize("mode", ["shared", "own"])
    def test_a_mode_is_accepted(self, mode: str) -> None:
        assert dc.check_mode(mode) == mode

    @pytest.mark.parametrize("mode", ["", "Own", "private", "none", "own ", "shared\n"])
    def test_anything_else_is_refused_naming_the_field(self, mode: str) -> None:
        with pytest.raises(ValidationError) as caught:
            dc.check_mode(mode)
        assert caught.value.field == "certificate"


class TestLineageName:
    def test_a_name_keeps_its_certificate_under_its_own_name(self) -> None:
        assert dc.lineage_name("admin.example.com") == "admin.example.com"

    def test_it_is_lowercased_and_trimmed(self) -> None:
        assert dc.lineage_name("  Admin.Example.COM ") == "admin.example.com"

    def test_a_wildcard_cannot_be_a_directory_name_so_it_gets_one_no_host_can_have(self) -> None:
        assert dc.lineage_name("*.example.com") == "_wildcard.example.com"

    def test_a_name_that_is_not_a_domain_is_refused(self) -> None:
        for bad in ("", "a/b", "../etc", "a;b", "exa mple.com", "*", "*.", "-x.com"):
            with pytest.raises(ValidationError):
                dc.lineage_name(bad)

    def test_a_lineage_name_never_collides_with_a_host_name(self) -> None:
        # A host cannot start with an underscore, so the wildcard's lineage cannot be a host's.
        assert dc.lineage_name("*.example.com").startswith("_")

    def test_is_own_lineage_tells_the_two_apart(self) -> None:
        assert dc.is_wildcard_lineage("_wildcard.example.com")
        assert not dc.is_wildcard_lineage("example.com")


class TestSplittingNames:
    def records(self) -> list[DomainRecord]:
        return [
            DomainRecord(domain="example.com", kind="primary"),
            DomainRecord(domain="shop.example.com", kind="alias"),
            DomainRecord(domain="admin.example.com", kind="alias", certificate="own"),
            DomainRecord(domain="old.example.com", kind="redirect", certificate="own"),
        ]

    def test_the_shared_certificate_covers_everyone_but_the_names_with_their_own(self) -> None:
        assert dc.shared_names(self.records()) == ["example.com", "shop.example.com"]

    def test_the_own_names_are_the_others(self) -> None:
        assert dc.own_names(self.records()) == ["admin.example.com", "old.example.com"]

    def test_a_record_without_the_field_is_shared(self) -> None:
        assert DomainRecord(domain="x.example.com").certificate == "shared"


class TestTheStoreSetter:
    def test_a_name_starts_shared(self, store: NoustStore) -> None:
        make_app(store)

        assert {r.domain: r.certificate for r in store.list_domains("example.com")} == {
            "example.com": "shared",
            "shop.example.com": "shared",
            "admin.example.com": "shared",
            "old.example.com": "shared",
        }

    def test_an_alias_can_take_a_certificate_of_its_own_and_give_it_back(
        self, store: NoustStore
    ) -> None:
        make_app(store)

        assert store.set_domain_certificate("example.com", "admin.example.com", "own") is True
        by_name = {r.domain: r.certificate for r in store.list_domains("example.com")}
        assert by_name["admin.example.com"] == "own"
        assert by_name["shop.example.com"] == "shared"

        assert store.set_domain_certificate("example.com", "admin.example.com", "shared") is True
        assert {r.domain: r.certificate for r in store.list_domains("example.com")}[
            "admin.example.com"
        ] == "shared"

    def test_a_redirect_can_too_because_it_is_served_over_tls_as_well(
        self, store: NoustStore
    ) -> None:
        make_app(store)

        assert store.set_domain_certificate("example.com", "old.example.com", "own") is True

    def test_the_name_is_normalised(self, store: NoustStore) -> None:
        make_app(store)

        assert store.set_domain_certificate("example.com", " Admin.Example.com ", "own") is True

    def test_a_name_the_application_does_not_have_is_reported_not_invented(
        self, store: NoustStore
    ) -> None:
        make_app(store)

        assert store.set_domain_certificate("example.com", "ghost.example.com", "own") is False
        assert store.set_domain_certificate("nowhere.example.com", "x.example.com", "own") is False

    def test_the_primary_is_the_lineage_everything_else_hangs_from_so_it_cannot_leave_it(
        self, store: NoustStore
    ) -> None:
        make_app(store)

        with pytest.raises(DomainError) as caught:
            store.set_domain_certificate("example.com", "example.com", "own")
        assert "primary" in caught.value.message

    def test_the_primary_can_be_set_back_to_shared_harmlessly(self, store: NoustStore) -> None:
        make_app(store)

        assert store.set_domain_certificate("example.com", "example.com", "shared") is True

    def test_a_value_that_is_not_a_mode_is_refused_before_anything_is_written(
        self, store: NoustStore
    ) -> None:
        make_app(store)

        with pytest.raises(ValidationError):
            store.set_domain_certificate("example.com", "admin.example.com", "Own")
        assert {r.domain: r.certificate for r in store.list_domains("example.com")}[
            "admin.example.com"
        ] == "shared"

    def test_a_monorepo_has_one_certificate_for_its_workspaces_and_cannot_split_it(
        self, store: NoustStore
    ) -> None:
        make_app(store, "mono.example.com", app_type="monorepo")

        with pytest.raises(ValidationError) as caught:
            store.set_domain_certificate("mono.example.com", "admin.mono.example.com", "own")
        assert "monorepo" in caught.value.message

    def test_it_survives_the_full_row_write_of_a_redeploy(self, store: NoustStore) -> None:
        app = make_app(store)
        store.set_domain_certificate("example.com", "admin.example.com", "own")

        app.status = "running"
        store.update_app(app)
        store.create_app(App(domain="other.example.org", app_type="static"))

        assert {r.domain: r.certificate for r in store.list_domains("example.com")}[
            "admin.example.com"
        ] == "own"

    def test_it_goes_with_the_name(self, store: NoustStore) -> None:
        make_app(store)
        store.set_domain_certificate("example.com", "admin.example.com", "own")
        store.remove_domain("example.com", "admin.example.com")
        store.add_domain("example.com", "admin.example.com", DomainKind.ALIAS.value)

        assert {r.domain: r.certificate for r in store.list_domains("example.com")}[
            "admin.example.com"
        ] == "shared"


class TestTheSchema:
    def test_a_fresh_store_has_the_column_with_the_default_every_name_had_before(
        self, store: NoustStore
    ) -> None:
        columns = {
            row[1]: row
            for row in store._get_connection().execute("PRAGMA table_info(domains)").fetchall()
        }
        assert "certificate" in columns
        assert columns["certificate"][3] == 1  # NOT NULL
        assert columns["certificate"][4] == "'shared'"

    def test_the_column_lives_in_its_own_fragment_of_v14(self) -> None:
        from noust.core import schema_v14

        assert (
            "domains",
            "certificate",
            "TEXT NOT NULL DEFAULT 'shared'",
        ) in schema_v14.CERTS_COLUMNS
