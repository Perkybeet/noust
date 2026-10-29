# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How the central's requests say they are a push of the fleet's settings.

The two headers are the same text the node parses (``fleet-sync on behalf of <person> (change
<id>)`` and ``change:<id>``), built from one :class:`SyncSignature`; a client refuses to send a
signature that is not exactly that, and a request that is not a push goes out as it always did.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import NodeError
from noust.core.store import NoustStore
from noust.fleet import client as client_module
from noust.fleet.aggregate import Asker
from noust.fleet.settings.signature import SyncSignature
from noust.web import auth
from tests.support.fleet_support import TOKEN, build_fleet

SIGNATURE = SyncSignature(person="ana", change="c0123456789ab")


@pytest.fixture
def fleet(tmp_path: Path):
    built = build_fleet(tmp_path)
    yield built
    NoustStore.reset_instance()


def test_a_signed_push_carries_the_signature_and_the_reference_of_the_change(fleet) -> None:
    fleet.added()
    client = fleet.manager.client("web-2")

    headers = client.auth_headers(
        SIGNATURE.actor, actor_scope="admin", elevated=SIGNATURE.elevation
    )

    assert headers == {
        "Authorization": f"Bearer {TOKEN}",
        "X-Noust-Actor": "fleet-sync on behalf of ana (change c0123456789ab)",
        "X-Noust-Actor-Scope": "admin",
        "X-Noust-Elevated": "change:c0123456789ab",
    }


def test_the_headers_are_the_ones_the_node_reads() -> None:
    assert client_module.ELEVATED_HEADER == auth.FLEET_ELEVATED_HEADER
    assert client_module.SYNC_ACTOR_PATTERN.pattern == (
        "fleet-sync on behalf of [A-Za-z0-9][A-Za-z0-9._:@+-]{0,63} \\(change c[0-9a-f]{12}\\)"
    )


@pytest.mark.parametrize(
    "actor",
    [
        "fleet-sync on behalf of ana",
        "fleet-sync on behalf of ana (change C0123456789AB)",
        "fleet-sync on behalf of ana (change c0123456789ab)\n",
        "fleet-sync on behalf of ana\n(change c0123456789ab)",
    ],
)
def test_a_signature_that_is_not_exactly_the_format_is_not_sent(fleet, actor: str) -> None:
    fleet.added()

    with pytest.raises(NodeError):
        fleet.manager.client("web-2").auth_headers(actor, actor_scope="admin")


@pytest.mark.parametrize("elevated", ["change:", "change:nope", "yes", "change:c0123456789ab\n"])
def test_a_reference_that_is_not_a_change_is_not_sent(fleet, elevated: str) -> None:
    fleet.added()

    with pytest.raises(NodeError):
        fleet.manager.client("web-2").auth_headers("ana", elevated=elevated)


def test_a_request_that_is_not_a_push_goes_out_as_it_always_did(fleet) -> None:
    fleet.added()

    headers = fleet.manager.client("web-2").auth_headers("ana", elevated=True)

    assert headers["X-Noust-Elevated"] == "1"


def test_an_asker_for_a_push_speaks_for_the_person_and_the_change() -> None:
    asker = Asker(actor="noust (fleet sync)", sync=SIGNATURE)

    assert asker.headers() == {
        "actor": SIGNATURE.actor,
        "actor_scope": "admin",
        "actor_role": None,
        "elevated": SIGNATURE.elevation,
    }


def test_an_asker_without_a_signature_speaks_as_before() -> None:
    assert Asker(actor="ana", scope="read", role="viewer").headers() == {
        "actor": "ana",
        "actor_scope": "read",
        "actor_role": "viewer",
        "elevated": False,
    }


def test_a_push_never_shares_a_cached_answer_with_a_person() -> None:
    assert Asker("a", sync=SIGNATURE).audience != Asker("a", scope="admin").audience
