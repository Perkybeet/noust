# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a central signs the pushes of the fleet's settings, and how a node reads the signature.

A push has no person in front of it (it is the reconciliation of a change somebody saved earlier),
so it carries who approved that change and which change it was. What is pinned: the exact text of
both headers, that the two have to agree for the node to take the confirmation as given, and that
nothing a person can type forges a second field of the signature.
"""

from __future__ import annotations

import pytest

from noust.fleet.settings.signature import (
    ACTOR_PREFIX,
    SyncSignature,
    SyncSignatureError,
    new_change_id,
)


def test_the_actor_header_names_the_person_and_the_change() -> None:
    signature = SyncSignature(person="ana", change="c0123456789ab")

    assert signature.actor == "fleet-sync on behalf of ana (change c0123456789ab)"
    assert signature.actor.startswith(ACTOR_PREFIX)


def test_the_elevation_header_carries_the_reference_of_the_change() -> None:
    assert SyncSignature("ana", "c0123456789ab").elevation == "change:c0123456789ab"


def test_a_signature_read_back_is_the_one_that_was_written() -> None:
    written = SyncSignature(person="ana@example.com", change=new_change_id())

    parsed = SyncSignature.parse(written.actor, written.elevation)

    assert parsed is not None
    assert parsed.signature == written
    assert parsed.attested is True


def test_an_actor_that_is_not_a_signature_is_not_one() -> None:
    assert SyncSignature.parse("ana", "1") is None
    assert SyncSignature.parse(None, None) is None
    assert SyncSignature.parse("master", "change:c0123456789ab") is None


def test_the_confirmation_counts_only_when_it_names_the_same_change() -> None:
    actor = SyncSignature("ana", "c0123456789ab").actor

    for elevated in (None, "", "1", "change:c000000000000", "change:c0123456789ab "):
        parsed = SyncSignature.parse(actor, elevated)
        assert parsed is not None
        assert parsed.attested is False, elevated


@pytest.mark.parametrize(
    "actor",
    [
        "fleet-sync on behalf of ana",
        "fleet-sync on behalf of ana (change nope)",
        "fleet-sync on behalf of ana (change c0123456789ab) extra",
        "fleet-sync on behalf of an a (change c0123456789ab)",
        "fleet-sync on behalf of ana (change c0123456789ab)\n",
        "fleet-sync on behalf of ana\n(change c0123456789ab)",
        "fleet-sync on behalf of  (change c0123456789ab)",
        "fleet-sync on behalf of ana (change C0123456789AB)",
        "fleet-sync on behalf of " + "a" * 65 + " (change c0123456789ab)",
    ],
)
def test_a_signature_that_is_not_exactly_the_format_is_refused(actor: str) -> None:
    with pytest.raises(SyncSignatureError):
        SyncSignature.parse(actor, "change:c0123456789ab")


def test_a_person_that_could_forge_a_field_cannot_be_signed_for() -> None:
    for person in ("ana (change c0123456789ab)", "ana\nroot", "a b", ""):
        with pytest.raises(SyncSignatureError):
            SyncSignature(person=person, change="c0123456789ab")


def test_the_change_ids_are_new_and_well_formed() -> None:
    ids = {new_change_id() for _ in range(50)}

    assert len(ids) == 50
    for change in ids:
        SyncSignature("ana", change)
