# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ledger of what Noust installed on the server (``installed_software``, schema v17, item 118).

Nothing recorded "Noust installed this" before: an installer skipped what was on the path and the
audit trail, which can be purged, was the only trace. The ledger is what lets a removal take the
repository and the key Noust added with the package, and what tells software Noust put there from
software that was already there. It starts empty on a server that exists: what was there before is
recognised by the marks Noust leaves, not by a row nobody wrote.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.software_ledger import SoftwareLedger
from noust.core.store import NoustStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def ledger(store: NoustStore) -> SoftwareLedger:
    return SoftwareLedger(store)


class TestWhatIsKept:
    def test_a_server_that_exists_starts_with_an_empty_ledger(self, ledger: SoftwareLedger) -> None:
        assert ledger.all() == {}
        assert ledger.get("nginx") is None

    def test_an_install_is_kept_with_what_it_brought_who_asked_and_how(
        self, ledger: SoftwareLedger
    ) -> None:
        ledger.record(
            "cloudflared",
            packages=["cloudflared"],
            repository_files=[
                "/etc/apt/sources.list.d/noust-cloudflared.list",
                "/usr/share/keyrings/noust-cloudflared.gpg",
            ],
            via="cloudflared",
            actor="cli:root",
        )

        found = ledger.get("cloudflared")

        assert found is not None
        assert found.packages == ("cloudflared",)
        assert found.repository_files == (
            "/etc/apt/sources.list.d/noust-cloudflared.list",
            "/usr/share/keyrings/noust-cloudflared.gpg",
        )
        assert found.via == "cloudflared"
        assert found.actor == "cli:root"
        assert found.installed_at.endswith("+00:00")

    def test_a_second_record_of_the_same_component_adds_to_the_first(
        self, ledger: SoftwareLedger
    ) -> None:
        # The date, the actor and the way are those of the install that put it there; what a later
        # install brought along is added, so a removal knows all of it.
        ledger.record("nginx", packages=["nginx"], via="package", actor="cli:root")
        first = ledger.get("nginx")
        assert first is not None

        ledger.record(
            "nginx",
            packages=["nginx", "nginx-core"],
            repository_files=["/etc/apt/sources.list.d/noust-nginx.list"],
            via="database_engine",
            actor="token:ci",
        )

        found = ledger.get("nginx")
        assert found is not None
        assert found.packages == ("nginx", "nginx-core")
        assert found.repository_files == ("/etc/apt/sources.list.d/noust-nginx.list",)
        assert (found.installed_at, found.actor, found.via) == (
            first.installed_at,
            "cli:root",
            "package",
        )

    def test_it_lists_every_component_by_name(self, ledger: SoftwareLedger) -> None:
        ledger.record("nginx", packages=["nginx"], via="package")
        ledger.record("fail2ban", packages=["fail2ban"], via="fail2ban")

        assert list(ledger.all()) == ["fail2ban", "nginx"]

    def test_forgetting_a_component_says_whether_it_was_there(self, ledger: SoftwareLedger) -> None:
        ledger.record("nginx", packages=["nginx"], via="package")

        assert ledger.forget("nginx") is True
        assert ledger.forget("nginx") is False
        assert ledger.get("nginx") is None

    def test_it_survives_the_store_being_opened_again(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        SoftwareLedger(store).record("rclone", packages=["rclone"], via="package", actor="cli:root")
        store.close()
        NoustStore.reset_instance()

        reopened = NoustStore(tmp_path / "noust.db")
        try:
            found = SoftwareLedger(reopened).get("rclone")
        finally:
            reopened.close()
            NoustStore.reset_instance()

        assert found is not None and found.packages == ("rclone",)

    def test_the_actor_may_be_unknown(self, ledger: SoftwareLedger) -> None:
        ledger.record("git", packages=["git"], via="package")

        found = ledger.get("git")
        assert found is not None and found.actor is None


class TestOnlyWhatIsValidIsKept:
    @pytest.mark.parametrize(
        "component", ["", "Nginx", "nginx server", "../nginx", "nginx\n", "1nginx", "a" * 41, "x-y"]
    )
    def test_a_component_is_an_identifier(self, ledger: SoftwareLedger, component: str) -> None:
        with pytest.raises(ValidationError, match="component"):
            ledger.record(component, packages=["nginx"], via="package")

        assert ledger.all() == {}

    @pytest.mark.parametrize("package", ["", "-y", "a b", "$(id)", "x*", "../x", "a\nb"])
    def test_a_package_is_a_package_name(self, ledger: SoftwareLedger, package: str) -> None:
        with pytest.raises(ValidationError, match="package"):
            ledger.record("nginx", packages=[package], via="package")

        assert ledger.all() == {}

    @pytest.mark.parametrize(
        "path",
        [
            "etc/apt/sources.list.d/x.list",
            "/etc/apt/../shadow",
            "/etc/apt/x\n.list",
            "/etc/x\x00.list",
            "",
            "/etc/apt/./x.list",
        ],
    )
    def test_a_repository_file_is_a_normal_absolute_path(
        self, ledger: SoftwareLedger, path: str
    ) -> None:
        with pytest.raises(ValidationError, match="repository file"):
            ledger.record("nginx", packages=["nginx"], repository_files=[path], via="package")

        assert ledger.all() == {}

    @pytest.mark.parametrize("via", ["", "Package", "a b", "x" * 41, "a\nb"])
    def test_the_way_is_an_identifier(self, ledger: SoftwareLedger, via: str) -> None:
        with pytest.raises(ValidationError, match="via"):
            ledger.record("nginx", packages=["nginx"], via=via)

    def test_an_actor_is_one_line(self, ledger: SoftwareLedger) -> None:
        with pytest.raises(ValidationError, match="actor"):
            ledger.record("nginx", packages=["nginx"], via="package", actor="cli:root\nforged")

    def test_a_repeated_package_is_kept_once_in_the_order_given(
        self, ledger: SoftwareLedger
    ) -> None:
        ledger.record("php", packages=["php-fpm", "php-cli", "php-fpm"], via="package")

        found = ledger.get("php")
        assert found is not None and found.packages == ("php-fpm", "php-cli")


class TestTheTableIsWhatTheLedgerSays:
    def test_the_rows_are_json_lists_so_another_reader_does_not_parse_a_format(
        self, ledger: SoftwareLedger, store: NoustStore
    ) -> None:
        ledger.record("nginx", packages=["nginx", "nginx-core"], via="package")

        row = (
            store._get_connection()
            .execute("SELECT packages, repository_files FROM installed_software")
            .fetchone()
        )

        assert row["packages"] == '["nginx", "nginx-core"]'
        assert row["repository_files"] == "[]"

    def test_a_row_cannot_be_made_without_its_way_or_its_date(self, store: NoustStore) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            store._get_connection().execute(
                "INSERT INTO installed_software (component, installed_at) VALUES ('x', 't')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            store._get_connection().execute(
                "INSERT INTO installed_software (component, via) VALUES ('x', 'package')"
            )
