# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving a server from one central to another with what the first knew about it (checklist F-21).

A server is not a thing a central owns: it trusts a central because the server's operator ran
``noust fleet authorize`` there, and it can trust two at once. So moving it is: authorize the new
central on the server, add the server there, and carry what the old central knew that the server
does not: its labels, the applications it runs for the fleet's sake (placements), the machine it is
at a provider, whether its monitoring is paused. The document carries no secret of any kind: not
its token, not the central's key for it, not an account's API token.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.fleet.cloud.servers import READY, CloudServers
from noust.fleet.inventory import FORMAT, apply_inventory, export_inventory
from noust.fleet.labels import NodeLabels
from noust.fleet.liveness import Liveness
from noust.fleet.placements import Placements
from noust.fleet.ssh_options import Bastion, NodeSSHOptions
from tests.fleet_support import TOKEN, build_fleet, ed25519_line

BASTION_KEY = ed25519_line(7)


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


@pytest.fixture
def fleet(tmp_path: Path):
    built = build_fleet(tmp_path)
    built.added("web-2")
    return built


def full(fleet) -> None:
    NodeLabels(fleet.store).change("web-2", set_labels={"env": "prod", "role": "web"})
    Placements(fleet.store).save(
        "shop.example.com", nodes=["web-2"], serial=2, canary="web-2", rollback_on_failure=False
    )
    NodeSSHOptions(fleet.store).configure(
        "web-2",
        connect_timeout=30,
        bastion=Bastion(host="bastion.example.com", user="hop", host_key=BASTION_KEY, port=2200),
    )
    Liveness(fleet.store).pause("web-2", reason="moving racks", actor="cli:yago@nas")
    cloud = CloudServers(fleet.store)
    cloud.record(
        node="web-2",
        account="hetzner-prod",
        provider="hetzner",
        provider_id="4242",
        location="fsn1",
        plan="cx22",
        image="ubuntu-24.04",
        address="203.0.113.10",
    )
    cloud.update("web-2", state=READY)


def test_the_document_has_what_the_central_knew_and_nothing_secret(fleet) -> None:
    full(fleet)

    document = export_inventory(fleet.manager, "web-2")

    assert document["format"] == FORMAT and document["version"] == 1
    assert document["node"] == {"name": "web-2", "address": "web2.example.com", "port": 22}
    assert document["labels"] == {"env": "prod", "role": "web"}
    assert document["placements"] == [
        {
            "domain": "shop.example.com",
            "serial": 2,
            "max_failures": None,
            "canary": True,
            "rollback_on_failure": False,
        }
    ]
    assert document["ssh"]["connect_timeout"] == 30
    assert document["ssh"]["bastion"]["address"] == "hop@bastion.example.com:2200"
    assert document["monitoring"] == {"paused": True, "reason": "moving racks"}
    assert document["cloud"] == {
        "account": "hetzner-prod",
        "provider": "hetzner",
        "provider_id": "4242",
        "location": "fsn1",
        "plan": "cx22",
        "image": "ubuntu-24.04",
        "address": "203.0.113.10",
    }
    text = json.dumps(document)
    assert TOKEN not in text
    assert "id_ed25519" not in text and "PRIVATE" not in text
    assert fleet.keys.public_key("web-2").blob not in text, (
        "the old central's key is not part of it"
    )


def test_a_server_with_nothing_recorded_exports_an_empty_inventory(fleet) -> None:
    document = export_inventory(fleet.manager, "web-2")

    assert document["labels"] == {}
    assert document["placements"] == []
    assert document["cloud"] is None
    assert document["monitoring"] == {"paused": False, "reason": None}
    assert document["ssh"] == {"connect_timeout": None, "bastion": None}


def test_the_document_is_applied_on_another_central(tmp_path: Path, fleet) -> None:
    full(fleet)
    document = export_inventory(fleet.manager, "web-2")
    other = build_fleet(tmp_path / "other")
    other.added("web-2")

    report = apply_inventory(other.manager, "web-2", document, actor="cli:yago@other")

    assert NodeLabels(other.store).of("web-2") == {"env": "prod", "role": "web"}
    placement = Placements(other.store).get("shop.example.com")
    assert placement is not None
    assert placement.nodes == ("web-2",) and placement.serial == 2
    assert placement.canary == "web-2" and placement.rollback_on_failure is False
    assert Liveness(other.store).get("web-2").paused is True
    assert {item["part"] for item in report["applied"]} >= {"labels", "placements", "monitoring"}


def test_an_existing_placement_gains_the_server_and_keeps_its_own_strategy(
    tmp_path: Path, fleet
) -> None:
    full(fleet)
    document = export_inventory(fleet.manager, "web-2")
    other = build_fleet(tmp_path / "other")
    other.added("web-2")
    Placements(other.store).save("shop.example.com", nodes=["web-2"], serial=5)

    apply_inventory(other.manager, "web-2", document, actor="x")

    saved = Placements(other.store).get("shop.example.com")
    assert saved is not None and saved.serial == 5, "this central's own strategy stays"


def test_the_ssh_options_are_left_out_unless_asked_and_said(tmp_path: Path, fleet) -> None:
    full(fleet)
    document = export_inventory(fleet.manager, "web-2")
    other = build_fleet(tmp_path / "other")
    other.added("web-2")

    report = apply_inventory(other.manager, "web-2", document, actor="x")

    assert NodeSSHOptions(other.store).get("web-2").bastion is None
    skipped = {item["part"]: item["reason"] for item in report["skipped"]}
    assert "bastion" in skipped["ssh"]


def test_the_ssh_options_come_along_when_asked(tmp_path: Path, fleet) -> None:
    full(fleet)
    document = export_inventory(fleet.manager, "web-2")
    other = build_fleet(tmp_path / "other")
    other.added("web-2")

    apply_inventory(other.manager, "web-2", document, actor="x", include_ssh=True)

    options = NodeSSHOptions(other.store).get("web-2")
    assert options.connect_timeout == 30 and options.bastion is not None
    assert options.bastion.host_key == BASTION_KEY


def test_the_machine_is_linked_only_where_the_account_exists(tmp_path: Path, fleet) -> None:
    full(fleet)
    document = export_inventory(fleet.manager, "web-2")
    other = build_fleet(tmp_path / "other")
    other.added("web-2")

    report = apply_inventory(other.manager, "web-2", document, actor="x")

    assert CloudServers(other.store).find("web-2") is None
    skipped = {item["part"]: item["reason"] for item in report["skipped"]}
    assert "hetzner-prod" in skipped["cloud"]
    assert "noust fleet cloud server link" in skipped["cloud"]


def test_the_machine_is_linked_when_this_central_has_the_account(tmp_path: Path, fleet) -> None:
    from noust.fleet.cloud.accounts import CloudAccounts

    full(fleet)
    document = export_inventory(fleet.manager, "web-2")
    other = build_fleet(tmp_path / "other")
    other.added("web-2")
    CloudAccounts(other.store, other.secrets).add(
        "hetzner-prod", "hetzner", "t" * 40, verify=False, actor="x"
    )

    report = apply_inventory(other.manager, "web-2", document, actor="x")

    machine = CloudServers(other.store).get("web-2")
    assert (machine.provider, machine.provider_id, machine.state) == ("hetzner", "4242", READY)
    assert machine.address == "203.0.113.10"
    assert "cloud" in {item["part"] for item in report["applied"]}
    assert "t" * 40 not in json.dumps(document)


def test_a_machine_id_a_provider_would_never_give_is_not_linked(tmp_path: Path, fleet) -> None:
    from noust.fleet.cloud.accounts import CloudAccounts

    full(fleet)
    document = export_inventory(fleet.manager, "web-2")
    document["cloud"]["provider_id"] = "42; rm -rf /"
    other = build_fleet(tmp_path / "other")
    other.added("web-2")
    CloudAccounts(other.store, other.secrets).add(
        "hetzner-prod", "hetzner", "t" * 40, verify=False, actor="x"
    )

    report = apply_inventory(other.manager, "web-2", document, actor="x")

    assert CloudServers(other.store).find("web-2") is None
    assert (
        "not one a provider gives" in {i["part"]: i["reason"] for i in report["skipped"]}["cloud"]
    )


def test_applying_twice_changes_nothing_more(tmp_path: Path, fleet) -> None:
    full(fleet)
    document = export_inventory(fleet.manager, "web-2")
    other = build_fleet(tmp_path / "other")
    other.added("web-2")

    apply_inventory(other.manager, "web-2", document, actor="x")
    apply_inventory(other.manager, "web-2", document, actor="x")

    assert NodeLabels(other.store).of("web-2") == {"env": "prod", "role": "web"}
    assert len(Placements(other.store).list()) == 1


def test_it_can_be_applied_under_another_name(tmp_path: Path, fleet) -> None:
    full(fleet)
    document = export_inventory(fleet.manager, "web-2")
    other = build_fleet(tmp_path / "other")
    other.added("web-7")

    apply_inventory(other.manager, "web-7", document, actor="x")

    assert NodeLabels(other.store).of("web-7") == {"env": "prod", "role": "web"}
    placement = Placements(other.store).get("shop.example.com")
    assert placement is not None and placement.nodes == ("web-7",)
    assert placement.canary == "web-7", "the canary was this server; it still is"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(format="something-else"),
        lambda d: d.update(version=99),
        lambda d: d.update(labels={"Env": "prod"}),
        lambda d: d.update(labels={"env": "has space"}),
        lambda d: d.update(labels="env=prod"),
        lambda d: d.update(placements=[{"domain": "https://x.example.com"}]),
        lambda d: d.update(placements=[{"domain": "a.example.com", "serial": "many"}]),
        lambda d: d.update(placements="x"),
        lambda d: d.update(monitoring={"paused": "yes"}),
        lambda d: d.pop("node"),
    ],
)
def test_a_document_that_is_not_one_is_refused_before_anything_is_written(
    tmp_path: Path, fleet, mutate
) -> None:
    document = export_inventory(fleet.manager, "web-2")
    mutate(document)
    other = build_fleet(tmp_path / "other")
    other.added("web-2")

    with pytest.raises(ValidationError):
        apply_inventory(other.manager, "web-2", document, actor="x")

    assert NodeLabels(other.store).of("web-2") == {}
    assert Placements(other.store).list() == []


def test_a_server_that_is_not_registered_here_cannot_take_an_inventory(fleet) -> None:
    document = export_inventory(fleet.manager, "web-2")

    with pytest.raises(Exception, match="No node named ghost"):
        apply_inventory(fleet.manager, "ghost", document, actor="x")


def test_a_document_that_is_far_too_large_is_refused(fleet) -> None:
    document = export_inventory(fleet.manager, "web-2")
    document["placements"] = [
        {"domain": f"app{i}.example.com", "serial": None} for i in range(2000)
    ]

    with pytest.raises(ValidationError):
        apply_inventory(fleet.manager, "web-2", document, actor="x")
