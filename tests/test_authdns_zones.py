# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The zone editor of Noust DNS (4.1, item 104).

The store is the truth and Knot is told what it says. What is pinned: a zone is made with name
servers that can be found, a record is judged with the ones already at its name, every change
reaches the zone file and Knot, a change Knot does not take leaves the store, the file and the
audit trail as they were, and a signed zone is not unsigned while the registrar may still hold
its DS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.authdns_store import PRIMARY, SECONDARY
from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.authdns.engine import EngineError
from noust.managers.authdns.server import TransferKey
from noust.managers.authdns.zones import ZoneEditor
from tests.support.authdns_support import PUBLIC, SECRET, World, make_world

pytestmark = pytest.mark.real_listeners


@pytest.fixture
def world(
    tmp_path: Path,
    store: NoustStore,
    secrets: SecretStore,
    runner: FakeRunner,
    fs: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    return make_world(tmp_path, store, secrets, runner, fs, monkeypatch)


DS_LINE = (
    "example.com. DS 9431 13 2 EBE5D6007FDF87E3B70FBC2E1AFB0CFF87E66A0D8F70CAE380D4352472E83665\n"
)


@pytest.fixture
def editor(world: World) -> ZoneEditor:
    world.servers.setup(PRIMARY, hostname="ns1.example.net")
    world.servers.add_secondary("198.51.100.7", "ns2.example.net")
    world.runner.script(["systemctl", "is-active"], stdout="active\n")
    return ZoneEditor(world.store, world.servers, clock=lambda: 1793266825, sleep=lambda _: None)


def zone_file(world: World, name: str = "example.com") -> str:
    return world.engine.zone_file(name).read_text()


def configured_zones(world: World) -> list[str]:
    return [z["domain"] for z in yaml.safe_load(world.config.read_text())["zone"]]


class TestMakingAZone:
    def test_it_gets_this_server_and_its_secondaries_as_name_servers_and_is_served(
        self, editor: ZoneEditor, world: World, audited: list[dict[str, Any]]
    ) -> None:
        detail = editor.create_zone("Example.COM", actor="api:alice")

        assert detail.zone.name == "example.com"
        assert detail.zone.nameservers == ("ns1.example.net", "ns2.example.net")
        assert detail.zone.contact == "hostmaster@example.com"
        text = zone_file(world)
        assert "@ IN SOA ns1.example.net. hostmaster.example.com. 1793266825 7200" in text
        assert "@ IN NS ns2.example.net." in text
        assert configured_zones(world) == ["noust-catalog.invalid.", "example.com."]
        assert [
            "knotc",
            "-c",
            str(world.config),
            "-b",
            "zone-reload",
            "example.com.",
        ] in world.commands()
        assert audited[-1] == {
            "event": "dns.zone",
            "target": "dns-zone:example.com",
            "details": {"action": "create", "dnssec": False, "actor": "api:alice"},
        }

    def test_name_servers_inside_the_zone_get_the_addresses_the_server_knows(
        self, editor: ZoneEditor, world: World
    ) -> None:
        detail = editor.create_zone(
            "example.net", nameservers=["ns1.example.net", "ns2.example.net"]
        )

        assert [(r.name, r.type, r.content) for r in detail.records] == [
            ("ns1", "A", PUBLIC),
            ("ns2", "A", "198.51.100.7"),
        ]
        assert "ns1 IN A 203.0.113.10" in zone_file(world, "example.net")

    def test_a_name_server_inside_the_zone_with_no_known_address_is_asked_for_one(
        self, editor: ZoneEditor, world: World
    ) -> None:
        with pytest.raises(ValidationError) as raised:
            editor.create_zone("example.com", nameservers=["ns9.example.com"])

        assert raised.value.field == "glue"
        assert "--glue ns9.example.com=ADDRESS" in raised.value.details
        assert world.records.zones() == [] and not world.engine.zone_file("example.com").exists()

    def test_the_address_given_for_it_is_the_one_used(self, editor: ZoneEditor) -> None:
        detail = editor.create_zone(
            "example.com",
            nameservers=["ns9.example.com", "ns1.example.net"],
            glue={"ns9.example.com": ["2001:DB8::9"]},
        )

        assert [(r.name, r.type, r.content) for r in detail.records] == [
            ("ns9", "AAAA", "2001:db8::9")
        ]

    def test_glue_for_a_name_that_is_not_a_name_server_of_the_zone_is_refused(
        self, editor: ZoneEditor
    ) -> None:
        with pytest.raises(ValidationError):
            editor.create_zone("example.com", glue={"other.example.com": ["192.0.2.1"]})

    def test_without_a_name_for_this_server_the_zone_must_say_its_name_servers(
        self, world: World
    ) -> None:
        world.servers.setup(PRIMARY)
        editor = ZoneEditor(world.store, world.servers)

        with pytest.raises(ValidationError) as raised:
            editor.create_zone("example.com")

        assert raised.value.field == "nameservers"
        assert "--hostname" in raised.value.details

    def test_a_zone_is_made_once(self, editor: ZoneEditor) -> None:
        editor.create_zone("example.com")

        with pytest.raises(ValidationError) as raised:
            editor.create_zone("example.com")

        assert "already a zone" in raised.value.message

    def test_a_zone_knot_does_not_take_is_not_made(
        self, editor: ZoneEditor, world: World, audited: list[dict[str, Any]]
    ) -> None:
        audited.clear()
        world.runner.script(
            ["knotc", "-c", str(world.config), "-b", "reload"],
            stderr="error: (failed)",
            exit_code=1,
        )

        with pytest.raises(EngineError) as raised:
            editor.create_zone("example.com")

        assert "(failed)" in (raised.value.output or "")
        assert world.records.zones() == []
        assert not world.engine.zone_file("example.com").exists()
        assert "example.com." not in configured_zones(world)
        assert audited == []

    def test_a_secondary_cannot_edit_zones(self, world: World) -> None:
        world.servers.setup(
            SECONDARY,
            hostname="ns2.example.net",
            primary_address="198.51.100.9",
            transfer_key=TransferKey("noust-xfr", "hmac-sha256", SECRET),
        )
        editor = ZoneEditor(world.store, world.servers)

        with pytest.raises(ValidationError) as raised:
            editor.create_zone("example.com", nameservers=["ns1.example.net"])
        with pytest.raises(ValidationError):
            editor.get("example.com")

        assert raised.value.field == "role"

    def test_a_server_that_is_not_set_up_says_so(self, world: World) -> None:
        with pytest.raises(ValidationError) as raised:
            ZoneEditor(world.store, world.servers).create_zone("example.com")

        assert "not set up" in raised.value.message


class TestRecords:
    @pytest.fixture(autouse=True)
    def _zone(self, editor: ZoneEditor) -> None:
        editor.create_zone("example.com")

    def test_a_record_reaches_the_file_and_knot(self, editor: ZoneEditor, world: World) -> None:
        before = len(world.commands())

        record = editor.add_record(
            "example.com", "www", "a", "192.0.2.1", ttl=300, actor="cli:root"
        )

        assert (record.name, record.type, record.content, record.ttl) == (
            "www",
            "A",
            "192.0.2.1",
            300,
        )
        assert "www 300 IN A 192.0.2.1" in zone_file(world)
        assert [
            "knotc",
            "-c",
            str(world.config),
            "-b",
            "zone-reload",
            "example.com.",
        ] in world.commands()[before:]

    def test_a_record_is_changed_and_removed(self, editor: ZoneEditor, world: World) -> None:
        record = editor.add_record("example.com", "www", "A", "192.0.2.1")
        assert record.id is not None

        editor.update_record("example.com", record.id, content="192.0.2.9", ttl=900)
        assert "www 900 IN A 192.0.2.9" in zone_file(world)
        assert editor.delete_record("example.com", record.id) is True
        assert "www " not in zone_file(world).replace("www.example", "")
        assert editor.delete_record("example.com", record.id) is False

    def test_a_number_is_not_a_way_into_another_zone(self, editor: ZoneEditor) -> None:
        editor.create_zone("example.org", nameservers=["ns1.example.net"])
        other = editor.add_record("example.org", "www", "A", "192.0.2.1")
        assert other.id is not None

        with pytest.raises(ValidationError):
            editor.update_record("example.com", other.id, content="192.0.2.9")
        with pytest.raises(ValidationError):
            editor.delete_record("example.com", other.id)

        assert editor.get("example.org").records[0].content == "192.0.2.1"

    def test_a_cname_stands_alone_in_both_directions(self, editor: ZoneEditor) -> None:
        editor.add_record("example.com", "www", "A", "192.0.2.1")
        with pytest.raises(ValidationError) as over_a:
            editor.add_record("example.com", "www", "CNAME", "target.example.net")
        editor.add_record("example.com", "go", "CNAME", "target.example.net")
        with pytest.raises(ValidationError) as under_cname:
            editor.add_record("example.com", "go", "TXT", "hi")
        with pytest.raises(ValidationError) as at_apex:
            editor.add_record("example.com", "@", "CNAME", "target.example.net")

        assert "stands alone" in over_a.value.message
        assert "stands alone" in under_cname.value.message
        assert "the zone itself" in at_apex.value.message

    def test_the_zones_own_name_servers_and_ds_are_not_records_to_add(
        self, editor: ZoneEditor
    ) -> None:
        for kind, content in (("NS", "ns9.example.net"), ("DS", "1 13 2 ABCD")):
            with pytest.raises(ValidationError):
                editor.add_record("example.com", "@", kind, content)
        editor.add_record("example.com", "child", "NS", "ns.child.example.net")

    def test_what_is_not_a_record_is_refused_and_nothing_is_written(
        self, editor: ZoneEditor, world: World
    ) -> None:
        before = zone_file(world)

        for name, kind, content in (
            ("www", "A", "nope"),
            ("w w", "A", "192.0.2.1"),
            ("www", "SOA", "x"),
            ("www", "TXT", "a\nb"),
        ):
            with pytest.raises(ValidationError):
                editor.add_record("example.com", name, kind, content)

        assert zone_file(world) == before

    def test_a_record_knot_does_not_take_is_put_back_in_the_store_and_in_the_file(
        self, editor: ZoneEditor, world: World, audited: list[dict[str, Any]]
    ) -> None:
        editor.add_record("example.com", "keep", "A", "192.0.2.5")
        before = zone_file(world)
        audited.clear()
        world.runner.script(
            ["knotc", "-c", str(world.config), "-b", "zone-reload", "example.com."],
            stderr="error: [example.com.] (failed)",
            exit_code=1,
        )

        with pytest.raises(EngineError):
            editor.add_record("example.com", "www", "A", "192.0.2.1")

        assert [r.name for r in editor.get("example.com").records] == ["keep"]
        assert zone_file(world) == before
        assert audited == []

    def test_a_zone_that_is_not_there_has_no_records_to_add(self, editor: ZoneEditor) -> None:
        with pytest.raises(ValidationError):
            editor.add_record("nope.example", "www", "A", "192.0.2.1")


class TestChangingAZone:
    @pytest.fixture(autouse=True)
    def _zone(self, editor: ZoneEditor) -> None:
        editor.create_zone("example.com")

    def test_the_name_servers_contact_and_ttl_change_and_the_file_follows(
        self, editor: ZoneEditor, world: World
    ) -> None:
        detail = editor.update_zone(
            "example.com",
            nameservers=["ns1.example.net", "ns3.example.org"],
            contact="admin@example.com",
            default_ttl=600,
        )

        assert detail.zone.nameservers == ("ns1.example.net", "ns3.example.org")
        text = zone_file(world)
        assert "$TTL 600" in text and "admin.example.com." in text
        assert "@ IN NS ns3.example.org." in text and "ns2.example.net" not in text

    def test_a_name_server_inside_the_zone_is_given_its_address_when_it_joins(
        self, editor: ZoneEditor
    ) -> None:
        detail = editor.update_zone(
            "example.com",
            nameservers=["ns1.example.net", "ns5.example.com"],
            glue={"ns5.example.com": ["192.0.2.55"]},
        )

        assert [(r.name, r.content) for r in detail.records] == [("ns5", "192.0.2.55")]

    def test_a_change_knot_refuses_leaves_the_zone_as_it_was(
        self, editor: ZoneEditor, world: World
    ) -> None:
        before = zone_file(world)
        world.runner.script(
            ["knotc", "-c", str(world.config), "-b", "zone-reload", "example.com."],
            stderr="error: (failed)",
            exit_code=1,
        )

        with pytest.raises(EngineError):
            editor.update_zone("example.com", default_ttl=60)

        assert editor.get("example.com").zone.default_ttl == 3600
        assert zone_file(world) == before

    def test_a_zone_is_removed_with_its_file_and_its_place_in_the_configuration(
        self, editor: ZoneEditor, world: World
    ) -> None:
        editor.add_record("example.com", "www", "A", "192.0.2.1")

        assert editor.delete_zone("example.com", actor="cli:root") is True

        assert world.records.zones() == [] and world.records.records("example.com") == []
        assert not world.engine.zone_file("example.com").exists()
        assert configured_zones(world) == ["noust-catalog.invalid."]
        assert editor.delete_zone("example.com") is False

    def test_a_removal_knot_refuses_brings_the_zone_back_with_its_records(
        self, editor: ZoneEditor, world: World
    ) -> None:
        editor.add_record("example.com", "www", "A", "192.0.2.1")
        world.runner.script(
            ["knotc", "-c", str(world.config), "-b", "reload"], stderr="error: no", exit_code=1
        )

        with pytest.raises(EngineError):
            editor.delete_zone("example.com")

        assert [r.content for r in world.records.records("example.com")] == ["192.0.2.1"]


class TestWhatThePrimaryServes:
    def test_the_list_has_each_zone_with_its_records_and_the_serial_knot_serves(
        self, editor: ZoneEditor, world: World
    ) -> None:
        editor.create_zone("example.com", dnssec=True)
        editor.add_record("example.com", "www", "A", "192.0.2.1")
        world.runner.script(
            ["knotc", "-c", str(world.config), "zone-status"],
            stdout="[example.com.] role: master | serial: 1793266851 | catalog: @noust-catalog.invalid.\n",
        )

        (summary,) = editor.zones()

        assert summary.to_dict() == {
            "name": "example.com",
            "records": 1,
            "serial": 1793266851,
            "dnssec": True,
            "role": "master",
        }
        assert editor.get("example.com").serial == 1793266851

    def test_a_secondary_lists_what_it_copied_without_the_catalog(self, world: World) -> None:
        world.servers.setup(
            SECONDARY,
            hostname="ns2.example.net",
            primary_address="198.51.100.9",
            transfer_key=TransferKey("noust-xfr", "hmac-sha256", SECRET),
        )
        world.runner.script(["systemctl", "is-active"], stdout="active\n")
        world.runner.script(
            ["knotc", "-c", str(world.config), "zone-status"],
            stdout=(
                "[example.com.] role: slave | serial: 7 | catalog: noust-catalog.invalid.\n"
                "[noust-catalog.invalid.] role: slave | serial: 9 | catalog: interpret\n"
            ),
        )

        zones = ZoneEditor(world.store, world.servers).zones()

        assert [(z.name, z.serial, z.role, z.dnssec) for z in zones] == [
            ("example.com", 7, "slave", None)
        ]


class TestDnssec:
    @pytest.fixture(autouse=True)
    def _zone(self, editor: ZoneEditor) -> None:
        editor.create_zone("example.com")

    def test_an_unsigned_zone_has_no_ds(self, editor: ZoneEditor) -> None:
        info = editor.dnssec("example.com")

        assert info.to_dict() == {
            "zone": "example.com",
            "enabled": False,
            "algorithm": "ECDSAP256SHA256",
            "ds": [],
        }

    def test_signing_adds_the_zone_to_the_policy_and_returns_the_ds_to_give_the_registrar(
        self, editor: ZoneEditor, world: World, audited: list[dict[str, Any]]
    ) -> None:
        world.runner.script(["keymgr"], stdout=DS_LINE)

        info = editor.set_dnssec("example.com", True, actor="api:alice")

        assert info.enabled is True
        assert [r.text for r in info.ds] == [
            "9431 13 2 ebe5d6007fdf87e3b70fbc2e1afb0cff87e66a0d8f70cae380d4352472e83665"
        ]
        zones = {z["domain"]: z for z in yaml.safe_load(world.config.read_text())["zone"]}
        assert zones["example.com."]["dnssec-signing"] is True
        assert audited[-1]["details"] == {"action": "dnssec_on", "actor": "api:alice"}
        assert editor.dnssec("example.com").enabled is True

    def test_a_key_that_is_slow_to_exist_is_waited_for_and_then_reported_without(
        self, editor: ZoneEditor, world: World
    ) -> None:
        world.runner.script(["keymgr"], stderr="no keys", exit_code=1)

        info = editor.set_dnssec("example.com", True)

        assert info.enabled is True and info.ds == ()

    def test_unsigning_is_refused_until_the_ds_is_said_to_be_gone(
        self, editor: ZoneEditor, world: World
    ) -> None:
        world.runner.script(["keymgr"], stdout=DS_LINE)
        editor.set_dnssec("example.com", True)

        with pytest.raises(ValidationError) as raised:
            editor.set_dnssec("example.com", False)

        assert raised.value.field == "ds_removed"
        assert "validating resolver refuses the domain" in raised.value.details
        assert editor.dnssec("example.com").enabled is True
        info = editor.set_dnssec("example.com", False, ds_removed=True, actor="cli:root")
        assert info.enabled is False
        # The signatures stay in the zone until its file is read again.
        assert [
            "knotc",
            "-c",
            str(world.config),
            "-b",
            "zone-reload",
            "example.com.",
        ] in world.commands()

    def test_asking_for_what_is_already_so_changes_nothing(
        self, editor: ZoneEditor, world: World
    ) -> None:
        before = len(world.commands())

        editor.set_dnssec("example.com", False)

        assert len(world.commands()) == before

    def test_telling_knot_the_registrar_has_the_ds(
        self, editor: ZoneEditor, world: World, audited: list[dict[str, Any]]
    ) -> None:
        world.runner.script(["keymgr"], stdout=DS_LINE)
        editor.set_dnssec("example.com", True)

        editor.confirm_ds("example.com", actor="cli:root")

        assert [
            "knotc",
            "-c",
            str(world.config),
            "zone-ksk-submitted",
            "example.com.",
        ] in world.commands()
        assert audited[-1]["details"]["action"] == "ds_confirmed"

    def test_an_unsigned_zone_has_no_ds_to_confirm(self, editor: ZoneEditor) -> None:
        with pytest.raises(ValidationError):
            editor.confirm_ds("example.com")

    def test_signing_that_knot_does_not_take_leaves_the_zone_unsigned(
        self, editor: ZoneEditor, world: World
    ) -> None:
        world.runner.script(
            ["knotc", "-c", str(world.config), "-b", "reload"], stderr="error", exit_code=1
        )

        with pytest.raises(EngineError):
            editor.set_dnssec("example.com", True)

        assert editor.dnssec("example.com").enabled is False


def test_two_edits_do_not_interleave(editor: ZoneEditor, world: World, tmp_path: Path) -> None:
    editor.create_zone("example.com")
    runner: FakeRunner = world.runner
    seen: list[str] = []
    real = runner.run

    def run(argv: Any, **kwargs: Any) -> Any:
        if list(argv)[-2:] == ["zone-reload", "example.com."]:
            seen.append("reload")
        return real(argv, **kwargs)

    runner.run = run  # type: ignore[method-assign]
    editor.add_record("example.com", "a", "A", "192.0.2.1")
    editor.add_record("example.com", "b", "A", "192.0.2.2")

    assert seen == ["reload", "reload"]
