# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The configuration Noust DNS writes for Knot (4.1, item 104).

Knot reads a YAML-shaped file, so the tests read what is written back with a YAML parser and
assert on the structure: a primary lists its zones as members of a catalog it generates, signs
the ones that are signed and notifies its secondaries; a secondary interprets the catalog and
copies what it lists; both rate-limit UDP answers and neither can recurse. The text was also
checked by ``knotc conf-check`` and run against two real Knot servers (the network harness).
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from noust.core.authdns_store import PRIMARY, SECONDARY
from noust.managers.authdns.knotconf import CONFIG_MARKER, KnotSettings, ZoneEntry, render_config

SECRET = "ZmFrZWZha2VmYWtlZmFrZWZha2VmYWtlZmFrZWZha2U="


def parsed(settings: KnotSettings) -> dict[str, Any]:
    loaded = yaml.safe_load(render_config(settings))
    assert isinstance(loaded, dict)
    return loaded


def primary(**changes: Any) -> KnotSettings:
    values: dict[str, Any] = {
        "role": PRIMARY,
        "listen": ("203.0.113.10", "2001:db8::10"),
        "secret": SECRET,
        "zones": (ZoneEntry("example.com", True), ZoneEntry("example.net", False)),
        "secondaries": ("198.51.100.7", "2001:db8:2::7"),
    }
    values.update(changes)
    return KnotSettings(**values)


def secondary(**changes: Any) -> KnotSettings:
    values: dict[str, Any] = {
        "role": SECONDARY,
        "listen": ("198.51.100.7",),
        "secret": SECRET,
        "primary_address": "203.0.113.10",
    }
    values.update(changes)
    return KnotSettings(**values)


def by_id(section: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    return next(item for item in section if item["id"] == identifier)


class TestEveryServer:
    @pytest.mark.parametrize("settings", [primary(), secondary()])
    def test_it_is_marked_answers_only_where_told_and_hides_what_it_is(
        self, settings: KnotSettings
    ) -> None:
        text = render_config(settings)
        server = yaml.safe_load(text)["server"]

        assert text.startswith(f"# {CONFIG_MARKER}")
        assert server["listen"] == [f"{a}@53" for a in settings.listen]
        assert server["identity"] == "" and server["version"] == ""
        assert server["automatic-acl"] is True
        assert server["user"] == "knot:knot"

    @pytest.mark.parametrize("settings", [primary(), secondary()])
    def test_udp_answers_are_rate_limited_and_the_servers_it_talks_to_are_not(
        self, settings: KnotSettings
    ) -> None:
        loaded = parsed(settings)

        rrl = by_id(loaded["mod-rrl"], "noust-rrl")
        assert rrl["rate-limit"] == 100 and rrl["slip"] == 2
        assert loaded["template"][0]["global-module"] == "mod-rrl/noust-rrl"
        peers = settings.secondaries or (
            (settings.primary_address,) if settings.primary_address else ()
        )
        assert rrl["whitelist"] == list(peers)

    @pytest.mark.parametrize("settings", [primary(), secondary()])
    def test_the_transfer_key_is_one_hmac_sha256_key_with_the_secret_given(
        self, settings: KnotSettings
    ) -> None:
        key = by_id(parsed(settings)["key"], "noust-xfr")

        assert key == {"id": "noust-xfr", "algorithm": "hmac-sha256", "secret": SECRET}

    def test_nothing_in_it_turns_recursion_or_a_remote_update_on(self) -> None:
        for settings in (primary(), secondary()):
            text = render_config(settings)
            assert "recurs" not in text.lower() and "update" not in text


class TestAPrimary:
    def test_its_zones_are_members_of_the_catalog_it_generates(self) -> None:
        zones = parsed(primary())["zone"]

        catalog = zones[0]
        assert catalog["domain"] == "noust-catalog.invalid."
        assert catalog["catalog-role"] == "generate" and catalog["zonefile-load"] == "none"
        members = zones[1:]
        assert [z["domain"] for z in members] == ["example.com.", "example.net."]
        assert all(z["catalog-role"] == "member" for z in members)
        assert all(z["catalog-zone"] == "noust-catalog.invalid." for z in members)

    def test_only_a_signed_zone_signs(self) -> None:
        zones = {z["domain"]: z for z in parsed(primary())["zone"]}

        assert zones["example.com."]["dnssec-signing"] is True
        assert "dnssec-signing" not in zones["example.net."]

    def test_the_policy_is_one_ecdsa_key_that_publishes_what_a_registrar_reads(self) -> None:
        policy = by_id(parsed(primary())["policy"], "noust-signing")

        assert policy["algorithm"] == "ecdsap256sha256"
        assert policy["single-type-signing"] is True
        assert policy["zsk-lifetime"] == 0
        assert policy["cds-cdnskey-publish"] == "always"

    def test_the_engine_owns_the_serial_and_never_writes_the_file_back(self) -> None:
        template = parsed(primary())["template"][0]

        assert template["zonefile-load"] == "difference-no-serial"
        assert template["serial-policy"] == "unixtime"
        assert template["zonefile-sync"] == -1
        assert template["journal-content"] == "all"
        assert template["semantic-checks"] is True
        assert template["storage"] == "/var/lib/knot/zones"

    def test_it_notifies_each_secondary_with_the_key_and_lets_only_them_transfer(self) -> None:
        loaded = parsed(primary())

        remotes = loaded["remote"]
        assert [r["address"] for r in remotes] == ["198.51.100.7@53", "2001:db8:2::7@53"]
        assert all(r["key"] == "noust-xfr" for r in remotes)
        assert loaded["template"][0]["notify"] == [r["id"] for r in remotes]
        assert "acl" not in loaded  # the transfer rules follow the notify list (automatic-acl)

    def test_with_no_secondary_it_notifies_nobody(self) -> None:
        loaded = parsed(primary(secondaries=()))

        assert "remote" not in loaded and "notify" not in loaded["template"][0]

    def test_with_no_zone_there_is_still_a_catalog_for_the_first_to_join(self) -> None:
        zones = parsed(primary(zones=()))["zone"]

        assert [z["domain"] for z in zones] == ["noust-catalog.invalid."]


class TestASecondary:
    def test_it_interprets_the_catalog_and_copies_what_it_lists_from_the_primary(self) -> None:
        loaded = parsed(secondary())

        (catalog,) = loaded["zone"]
        assert catalog["domain"] == "noust-catalog.invalid."
        assert catalog["master"] == "noust-primary"
        assert catalog["catalog-role"] == "interpret"
        assert catalog["catalog-template"] == "noust-member"
        member = by_id(loaded["template"], "noust-member")
        assert member["master"] == "noust-primary"
        assert member["storage"] == "/var/lib/knot/zones"
        remote = by_id(loaded["remote"], "noust-primary")
        assert remote["address"] == "203.0.113.10@53" and remote["key"] == "noust-xfr"

    def test_it_signs_nothing_and_notifies_nobody(self) -> None:
        text = render_config(secondary())

        assert "dnssec-signing" not in text and "notify:" not in text and "policy" not in text

    def test_an_ipv6_primary_is_written_with_its_port(self) -> None:
        remote = by_id(parsed(secondary(primary_address="2001:db8::10"))["remote"], "noust-primary")

        assert remote["address"] == "2001:db8::10@53"


class TestWhatCannotBeWritten:
    @pytest.mark.parametrize(
        "settings",
        [
            lambda: primary(listen=()),
            lambda: secondary(primary_address=None),
            lambda: primary(secret=""),
            lambda: primary(secret='a"b'),
            lambda: primary(listen=("not an address",)),
            lambda: primary(secondaries=("x\ny",)),
            lambda: primary(zones=(ZoneEntry("bad zone", False),)),
            lambda: KnotSettings(role="boss", listen=("203.0.113.10",), secret=SECRET),
        ],
    )
    def test_the_config_is_written_from_values_it_checks(self, settings: Any) -> None:
        with pytest.raises(ValueError):
            render_config(settings())
