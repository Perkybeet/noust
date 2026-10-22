# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application that runs on several servers: where it runs (checklist F-18).

A placement is the central's record of "shop.example.com runs on web-1, web-2 and every
server labelled role=web". It holds no copy of the application: each server keeps its own
(its releases, its variables, its database), and the central asks them. What the record owns is
the membership, the way an update is rolled out over it, and nothing else.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import NodeError, ValidationError
from noust.fleet.labels import NodeLabels
from noust.fleet.placements import Placement, Placements
from tests.fleet_views_support import Central, build_central

DOMAIN = "shop.example.com"


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Central:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    return build_central(tmp_path, names=("web-1", "web-2", "web-3"))


@pytest.fixture
def placements(central: Central) -> Placements:
    return Placements(central.store)


def test_a_placement_is_named_servers_or_a_label_or_both(placements: Placements) -> None:
    saved = placements.save(
        DOMAIN, nodes=["web-1", "web-2"], selector={"role": "web"}, actor="cli:yago@nas"
    )

    assert isinstance(saved, Placement)
    assert saved.domain == DOMAIN
    assert saved.nodes == ("web-1", "web-2")
    assert saved.selector == {"role": "web"}
    assert saved.created_by == "cli:yago@nas"
    assert placements.get(DOMAIN) == saved
    assert [p.domain for p in placements.list()] == [DOMAIN]


def test_a_placement_with_no_servers_and_no_label_is_refused(placements: Placements) -> None:
    with pytest.raises(ValidationError) as caught:
        placements.save(DOMAIN, nodes=[], selector={})

    assert "server" in (caught.value.details or "").lower()


def test_a_server_that_is_not_registered_is_refused_by_name(placements: Placements) -> None:
    with pytest.raises(NodeError) as caught:
        placements.save(DOMAIN, nodes=["web-1", "ghost"])

    assert "ghost" in caught.value.message


def test_the_domain_is_the_bare_name(placements: Placements) -> None:
    for bad in ("https://shop.example.com", "shop.example.com/x", "evil/../..", ""):
        with pytest.raises(ValidationError):
            placements.save(bad, nodes=["web-1"])


def test_saving_again_changes_what_is_named_and_keeps_the_rest(placements: Placements) -> None:
    placements.save(DOMAIN, nodes=["web-1"], serial=2, canary="web-1")

    saved = placements.save(DOMAIN, nodes=["web-1", "web-2"])

    assert saved.nodes == ("web-1", "web-2")
    assert saved.serial == 2 and saved.canary == "web-1"
    assert len(placements.list()) == 1


def test_the_way_an_update_goes_out_is_part_of_it(placements: Placements) -> None:
    saved = placements.save(
        DOMAIN,
        nodes=["web-1", "web-2", "web-3"],
        serial="50%",
        max_failures=1,
        canary="web-2",
        rollback_on_failure=False,
    )

    assert (saved.serial, saved.max_failures, saved.canary) == ("50%", 1, "web-2")
    assert saved.rollback_on_failure is False


@pytest.mark.parametrize("serial", [0, -1, "0%", "101%", "many", 2.5, True])
def test_a_serial_that_is_not_a_count_or_a_percentage_is_refused(placements, serial) -> None:
    with pytest.raises(ValidationError):
        placements.save(DOMAIN, nodes=["web-1"], serial=serial)


def test_the_canary_must_be_one_of_the_servers(placements: Placements, central) -> None:
    with pytest.raises(ValidationError) as caught:
        placements.save(DOMAIN, nodes=["web-1", "web-2"], canary="web-3")

    assert "web-3" in caught.value.message


def test_a_canary_chosen_by_label_is_checked_when_it_is_used_not_when_it_is_saved(
    placements: Placements, central
) -> None:
    NodeLabels(central.store).change("web-3", set_labels={"role": "web"})

    saved = placements.save(DOMAIN, nodes=["web-1"], selector={"role": "web"}, canary="web-3")

    assert saved.canary == "web-3"


def test_members_are_the_named_servers_and_those_that_carry_the_label(
    placements: Placements, central
) -> None:
    NodeLabels(central.store).change("web-3", set_labels={"role": "web"})
    saved = placements.save(DOMAIN, nodes=["web-1"], selector={"role": "web"})

    names = placements.members(saved, central.manager.list())

    assert names == ["web-1", "web-3"]


def test_a_server_that_gets_the_label_later_becomes_a_member(
    placements: Placements, central
) -> None:
    saved = placements.save(DOMAIN, selector={"role": "web"})
    assert placements.members(saved, central.manager.list()) == []

    NodeLabels(central.store).change("web-2", set_labels={"role": "web"})

    assert placements.members(saved, central.manager.list()) == ["web-2"]


def test_removing_a_placement_forgets_where_it_runs_and_nothing_else(
    placements: Placements, central
) -> None:
    placements.save(DOMAIN, nodes=["web-1"])

    assert placements.remove(DOMAIN) is True
    assert placements.remove(DOMAIN) is False

    assert placements.get(DOMAIN) is None
    assert central.store.get_node("web-1") is not None


def test_removing_a_server_takes_it_out_of_the_placements_that_named_it(
    placements: Placements, central
) -> None:
    placements.save(DOMAIN, nodes=["web-1", "web-2"], canary="web-2")

    placements.forget_node("web-2")

    saved = placements.get(DOMAIN)
    assert saved is not None and saved.nodes == ("web-1",) and saved.canary is None


def test_a_placement_left_with_no_server_and_no_label_is_dropped_with_its_last_one(
    placements: Placements,
) -> None:
    placements.save(DOMAIN, nodes=["web-1"])

    placements.forget_node("web-1")

    assert placements.get(DOMAIN) is None


def test_the_table_is_in_the_fleet_fragment_and_not_owned_by_a_team() -> None:
    from noust.core import schema_v14

    assert "fleet_placements" in schema_v14.FLEET_SQL
