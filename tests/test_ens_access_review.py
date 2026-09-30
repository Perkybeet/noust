# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The periodic access review (ENS op.acc.4.4): the list, its digest, the attestation.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli import audit_policy
from noust.cli.app import cli
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.audit import Actor, get_log
from noust.core.ens import access_review
from noust.core.store import NoustStore, get_store

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cheap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)
    monkeypatch.setattr(
        audit_policy.audit, "cli_actor", lambda: Actor(kind="cli", id="0", name="root")
    )
    monkeypatch.setattr(audit_policy, "security_profile", lambda: "standard")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = get_store()
    yield instance
    NoustStore.reset_instance()


def accounts(store: NoustStore) -> AccountManager:
    manager = AccountManager(store, policy=AuthPolicy())
    manager.create("sofia", "security", password=PASSWORD, person_ref="sofia@example.com")
    manager.create("juan", "admin", password=PASSWORD, person_ref="juan@example.com")
    return manager


class TestTheReview:
    def test_the_list_has_every_account_and_a_digest(self, store) -> None:
        accounts(store)

        review = access_review.build_review(
            store, tokens=[{"owner_account_id": 2, "revoked_at": None}]
        )

        assert [a["username"] for a in review["accounts"]] == ["juan", "sofia"] or [
            a["username"] for a in review["accounts"]
        ] == ["sofia", "juan"]
        assert len(review["digest"]) == 64
        juan = next(a for a in review["accounts"] if a["username"] == "juan")
        assert juan["tokens"] == 1 and juan["mfa"] is False

    def test_the_digest_changes_when_the_list_does(self, store) -> None:
        manager = accounts(store)
        before = access_review.build_review(store)["digest"]

        manager.set_role("juan", "operator")

        assert access_review.build_review(store)["digest"] != before

    def test_an_attestation_is_on_record_with_the_digest(self, store) -> None:
        accounts(store)
        review = access_review.build_review(store)

        access_review.attest(review, actor=Actor(kind="user", id="1", name="sofia"), notes="ok")

        (event,) = access_review.last_reviews()
        assert event["action"] == "access.review"
        assert event["details"]["digest"] == review["digest"]
        assert event["details"]["accounts"] == 2


class TestTheCommands:
    def test_list_then_attest(self, store) -> None:
        accounts(store)

        listed = CliRunner().invoke(cli, ["ens", "access-review", "list", "--json"])
        attested = CliRunner().invoke(
            cli, ["ens", "access-review", "attest", "--notes", "Q3 review, nothing to change"]
        )

        assert listed.exit_code == 0, listed.output
        assert attested.exit_code == 0, attested.output
        events = get_log().read(action="access.review")
        assert events and events[0]["details"]["notes"] == "Q3 review, nothing to change"
