# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A copy a central took away and holds is a copy kept apart, and the checks say where (item 195).

``backup_offsite`` (and ``ENS-BAK-01``) asked for a schedule that sends the backups to a
destination. A central that collects the server's copies through its tunnel keeps them somewhere
else too, and says which it holds: that counts, it is named (``central:<name>``) and, when the
copies it holds are not sealed, the check that asks backups to leave encrypted (``ENS-BAK-02``) says
so. What a central that has gone quiet said does not count.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from noust.core.backup_collection_store import FRESH_DAYS, CentralHeld, HeldCopy
from noust.core.compliance import check as compliance_check
from noust.core.ens import checks
from noust.core.ens.checks import Facts, Sources, evaluate, gather
from noust.core.store import App, NoustStore
from noust.managers.backup_manager import BackupManager, BackupMetadata
from tests.support.ens_checks import NOW

DOMAIN = "shop.example.com"
BACKUP_ID = "shop-example-com_20261110_020000"


class _Config:
    def get(self, key: str, default: Any = None) -> Any:
        return default


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain=DOMAIN, app_type="nodejs", port=3000))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One backup of the application, taken a day ago, in a file of this test's own."""
    path = tmp_path / f"{BACKUP_ID}.tar.gz"
    path.write_bytes(b"plain archive")
    backup = BackupMetadata(
        id=BACKUP_ID,
        domain=DOMAIN,
        app_name="shop-example-com",
        created_at=(NOW - timedelta(days=1)).replace(tzinfo=None).isoformat(),
        size_bytes=13,
        app_type="nodejs",
        version="4.3.0",
        description="",
        includes_env=True,
        includes_node_modules=False,
        verified_ok=True,
        last_verified_at=NOW.replace(tzinfo=None).isoformat(),
    )
    monkeypatch.setattr(BackupManager, "list_backups", lambda self, *a, **k: [backup])
    monkeypatch.setattr(BackupManager, "local_files", lambda self, b: (path, path))
    return path


def facts_of(store: NoustStore) -> Facts:
    return gather(Sources(store=store, config=_Config(), hardening=lambda: [], clock=lambda: NOW))


def hold(store: NoustStore, central: str = "nas") -> None:
    CentralHeld(store).replace(central, [HeldCopy("app", BACKUP_ID)])


class TestWhatGatheringSees:
    def test_a_copy_a_central_holds_is_a_place_it_is_kept(
        self, store: NoustStore, archive: Path
    ) -> None:
        hold(store)

        facts = facts_of(store)

        assert {"domain": DOMAIN, "destinations": ["central:nas"]} in facts.schedules
        assert checks.sent_away(facts) == {DOMAIN: ["central:nas"]}

    def test_with_no_central_nothing_is_added(self, store: NoustStore, archive: Path) -> None:
        facts = facts_of(store)

        assert facts.schedules == [] and facts.destinations == []

    def test_a_central_that_went_quiet_does_not_count(
        self, store: NoustStore, archive: Path
    ) -> None:
        hold(store)
        with store._transaction() as cursor:
            cursor.execute(
                "UPDATE backup_central_syncs SET synced_at = ?",
                ((NOW - timedelta(days=FRESH_DAYS + 30)).isoformat(),),
            )

        assert checks.sent_away(facts_of(store)) == {}

    def test_what_a_central_does_not_hold_is_not_a_place(
        self, store: NoustStore, archive: Path
    ) -> None:
        CentralHeld(store).replace("nas", [HeldCopy("app", "another_20260101_000000")])

        assert checks.sent_away(facts_of(store)) == {}

    def test_the_copies_it_holds_say_whether_they_are_sealed(
        self, store: NoustStore, archive: Path
    ) -> None:
        hold(store)

        (destination,) = facts_of(store).destinations

        assert destination == {"name": "central:nas", "backend": "central", "encrypted": False}


class TestWhatTheChecksSay:
    def test_it_passes_the_check_that_asks_for_a_copy_apart_and_names_where(
        self, store: NoustStore, archive: Path
    ) -> None:
        hold(store)
        facts = facts_of(store)

        finding = {f.id: f for f in evaluate(facts)}["ENS-BAK-01"]

        assert not any("no schedule sends it" in line for line in finding.evidence)

    def test_the_generic_check_says_where_every_application_is_kept(
        self, store: NoustStore, archive: Path
    ) -> None:
        hold(store)

        status, summary, evidence, _ = compliance_check._measure_offsite(facts_of(store), True) or (
            "",
            "",
            (),
            "",
        )

        assert status == "ok"
        assert evidence == (f"{DOMAIN}: kept at central:nas",)

    def test_without_it_the_generic_check_fails_as_it_did(
        self, store: NoustStore, archive: Path
    ) -> None:
        status, _, evidence, _ = compliance_check._measure_offsite(facts_of(store), True) or (
            "",
            "",
            (),
            "",
        )

        assert status == "fail"
        assert evidence == (f"{DOMAIN}: no schedule sends it away",)

    def test_a_central_that_holds_plain_copies_is_a_destination_that_is_not_encrypted(
        self, store: NoustStore, archive: Path
    ) -> None:
        hold(store)

        finding = {f.id: f for f in evaluate(facts_of(store))}["ENS-BAK-02"]

        assert finding.status in ("fail", "warning")
        assert any("central:nas (central): NOT encrypted" in line for line in finding.evidence)
