# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What can be a setting of the fleet: the closed list, how each is named and which fields are secrets.

The central checks the shape of a value (which fields it has, that the ones a server needs to
make it are there) and nothing a server checks itself: a threshold, a webhook address or a
registry kind is judged by the server that receives it, with its own validators (rule 3). What is
pinned: the names reuse the validators of the objects themselves, an unknown field is a typo found
when the setting is saved and not on every server, and the secrets of a value are told apart from
the rest in exactly one place so that they can be kept apart (the secret store) and handed back
whole.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.fleet.settings.kinds import (
    AVAILABLE,
    KINDS,
    REDACTED,
    PlannedKind,
    check_scope,
    join_secrets,
    kind_of,
    split_secrets,
)

CHANNEL: dict[str, Any] = {
    "type": "slack",
    "enabled": True,
    "settings": {"webhook_url": "https://hooks.example.com/T/B/x", "channel": "#ops"},
    "events": {"deploy_failed": True},
}


class TestTheClosedList:
    def test_the_seven_of_this_release_are_available(self) -> None:
        assert set(AVAILABLE) == {
            "channel",
            "alert_rule",
            "backup_destination",
            "registry",
            "variable",
            "console_link",
            "policy",
        }

    def test_the_ones_that_wait_say_so_instead_of_being_unknown(self) -> None:
        for key in ("secret_manager", "dns_provider", "source_integration"):
            with pytest.raises(PlannedKind) as caught:
                kind_of(key)
            assert caught.value.coded is not None
            assert caught.value.coded.code == "fleet_settings.kind_not_yet"

    def test_something_else_is_unknown_and_the_error_lists_what_is_not(self) -> None:
        with pytest.raises(ValidationError) as caught:
            kind_of("firewall_rule")

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.unknown_kind"
        assert "channel" in (caught.value.details or "")

    def test_the_machines_own_settings_are_never_on_the_list(self) -> None:
        assert not set(KINDS) & {"webserver", "firewall", "ssh", "ports", "runtime", "swap"}


class TestNames:
    def test_each_kind_uses_the_validator_of_the_object_it_names(self) -> None:
        assert kind_of("channel").check_name("ops-slack") == "ops-slack"
        assert kind_of("alert_rule").check_name("disk-high") == "disk-high"
        assert kind_of("registry").check_name("ghcr-main") == "ghcr-main"
        assert kind_of("backup_destination").check_name("nas-1") == "nas-1"
        assert kind_of("variable").check_name("REGION") == "REGION"

    @pytest.mark.parametrize(
        ("key", "name"),
        [
            ("channel", "Ops"),
            ("channel", "ops\n"),
            ("channel", "x" * 40),
            ("alert_rule", "no spaces"),
            ("registry", "../etc"),
            ("backup_destination", "a/b"),
            ("variable", "1ST"),
            ("variable", "NAME WITH SPACE"),
            ("variable", "NAME\n"),
        ],
    )
    def test_a_name_the_object_would_not_have_is_refused(self, key: str, name: str) -> None:
        with pytest.raises(ValidationError):
            kind_of(key).check_name(name)


class TestTheShapeOfAValue:
    def test_a_value_is_an_object_with_only_the_fields_the_servers_route_takes(self) -> None:
        channel = kind_of("channel")

        assert channel.check_value(CHANNEL) == CHANNEL
        with pytest.raises(ValidationError) as caught:
            channel.check_value({**CHANNEL, "colour": "red"})
        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.unknown_field"
        assert caught.value.field == "colour"

    def test_what_a_server_needs_to_make_one_has_to_be_there(self) -> None:
        with pytest.raises(ValidationError) as caught:
            kind_of("channel").check_value({"enabled": True})
        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.missing_field"
        assert caught.value.field == "type"

        with pytest.raises(ValidationError):
            kind_of("alert_rule").check_value({"metric": "cpu"})  # a limit is needed too
        with pytest.raises(ValidationError):
            kind_of("registry").check_value({"kind": "ghcr", "username": "bot"})  # a password
        with pytest.raises(ValidationError):
            kind_of("variable").check_value({})

    def test_a_value_that_is_not_an_object_is_refused(self) -> None:
        for bad in ("text", 3, None, ["a"]):
            with pytest.raises(ValidationError) as caught:
                kind_of("channel").check_value(bad)
            assert caught.value.coded is not None
            assert caught.value.coded.code == "fleet_settings.value_not_object"

    def test_a_server_is_the_judge_of_what_the_fields_hold(self) -> None:
        # The central does not know what a valid threshold or channel type is.
        rule = {"metric": "cpu", "threshold": "ninety percent please"}

        assert kind_of("alert_rule").check_value(rule) == rule

    def test_a_value_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            kind_of("variable").check_value({"value": "x" * 100_000})


class TestTheSecretsOfAValue:
    def test_a_channel_credentials_are_found_by_the_name_the_configuration_seals_by(self) -> None:
        public, secrets = split_secrets(
            kind_of("channel"),
            "ops",
            {**CHANNEL, "settings": {"channel": "#ops", "token": "s3cret"}},
        )

        assert secrets == {"settings.token": "s3cret"}
        assert public["settings"] == {"channel": "#ops"}

    def test_a_webhook_address_is_one_because_the_configuration_says_so(self) -> None:
        _, secrets = split_secrets(kind_of("channel"), "ops", CHANNEL)

        assert secrets == {"settings.webhook_url": "https://hooks.example.com/T/B/x"}

    def test_the_secrets_join_back_into_the_value_they_were_split_from(self) -> None:
        value = {**CHANNEL, "settings": {"channel": "#ops", "token": "s3cret"}}
        kind = kind_of("channel")

        public, secrets = split_secrets(kind, "ops", value)

        assert join_secrets(kind, public, secrets) == value

    def test_a_registry_password_and_a_destination_key_are_secrets(self) -> None:
        _, registry = split_secrets(
            kind_of("registry"),
            "ghcr",
            {"kind": "ghcr", "username": "bot", "password": "tok"},
        )
        _, destination = split_secrets(
            kind_of("backup_destination"),
            "nas",
            {
                "backend": "s3",
                "fields": {"access_key_id": "AKIA", "secret_access_key": "shh", "bucket": "b"},
                "encryption_key": {"password": "p1", "password2": "p2"},
            },
        )

        assert registry == {"password": "tok"}
        assert destination["fields.secret_access_key"] == "shh"
        assert destination["encryption_key.password"] == "p1"
        assert destination["encryption_key.password2"] == "p2"
        assert "fields.bucket" not in destination

    def test_a_variable_is_a_secret_when_it_says_so_or_its_name_does(self) -> None:
        kind = kind_of("variable")

        assert split_secrets(kind, "REGION", {"value": "eu"})[1] == {}
        assert split_secrets(kind, "REGION", {"value": "eu", "secret": True})[1] == {"value": "eu"}
        assert split_secrets(kind, "API_TOKEN", {"value": "x"})[1] == {"value": "x"}
        assert split_secrets(kind, "API_TOKEN", {"value": "x", "secret": False})[1] == {}

    def test_an_alert_rule_has_none(self) -> None:
        assert (
            split_secrets(kind_of("alert_rule"), "disk", {"metric": "disk", "threshold": 90})[1]
            == {}
        )

    def test_what_is_shown_marks_where_a_secret_is_and_never_what_it_is(self) -> None:
        kind = kind_of("channel")
        public, secrets = split_secrets(
            kind, "ops", {**CHANNEL, "settings": {"token": "s3cret", "channel": "#ops"}}
        )

        shown = kind.shown(public, set(secrets))

        assert shown["settings"]["token"] == REDACTED
        assert "s3cret" not in repr(shown)

    def test_a_redacted_marker_sent_back_means_keep_what_is_stored(self) -> None:
        kind = kind_of("channel")
        sent = {**CHANNEL, "settings": {"token": REDACTED, "channel": "#new"}}

        public, secrets = split_secrets(kind, "ops", sent, keep={"settings.token": "kept"})

        assert secrets == {"settings.token": "kept"}
        assert public["settings"] == {"channel": "#new"}

    def test_a_redacted_marker_with_nothing_stored_is_refused_as_a_missing_secret(self) -> None:
        with pytest.raises(ValidationError) as caught:
            split_secrets(kind_of("channel"), "ops", {**CHANNEL, "settings": {"token": REDACTED}})

        assert caught.value.field == "settings.token"


POLICY: dict[str, Any] = {
    "updates_check": False,
    "ssl_email": "ops@example.com",
    "backup_max_per_app": 5,
    "delivery_max_minutes": 30,
}


class TestThePolicyOfAServer:
    """The four settings of "General" that are about how a server behaves, as one setting."""

    def test_there_is_one_and_it_is_called_general(self) -> None:
        kind = kind_of("policy")

        assert kind.check_name("general") == "general"
        for bad in ("Ops", "general2", "general\n", "", "default"):
            with pytest.raises(ValidationError) as caught:
                kind.check_name(bad)
            assert caught.value.coded is not None
            assert caught.value.coded.code == "fleet_settings.single_name"
            assert caught.value.field == "name"

    def test_its_value_is_the_four_fields_of_the_servers_own_route(self) -> None:
        kind = kind_of("policy")

        assert kind.check_value(POLICY) == POLICY
        assert kind.fields == {
            "updates_check",
            "ssl_email",
            "backup_max_per_app",
            "delivery_max_minutes",
        }

    @pytest.mark.parametrize("missing", sorted(POLICY))
    def test_a_server_needs_all_four_to_make_one(self, missing: str) -> None:
        partial = {k: v for k, v in POLICY.items() if k != missing}

        with pytest.raises(ValidationError) as caught:
            kind_of("policy").check_value(partial)

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.missing_field"
        assert caught.value.field == missing

    def test_a_field_it_does_not_have_is_a_typo_found_when_it_is_saved(self) -> None:
        with pytest.raises(ValidationError) as caught:
            kind_of("policy").check_value({**POLICY, "apps_directory": "/srv"})

        assert caught.value.field == "apps_directory"

    def test_the_machines_own_fields_are_not_in_it(self) -> None:
        # The folders, the web server and the console's bind are the machine's (Server).
        assert not kind_of("policy").fields & {
            "directory",
            "backup_directory",
            "apps_directory",
            "webserver",
            "max_concurrent_builds",
            "queue_limit",
        }

    def test_a_server_is_the_judge_of_what_the_fields_hold(self) -> None:
        value = {**POLICY, "backup_max_per_app": "a lot", "ssl_email": "not an address"}

        assert kind_of("policy").check_value(value) == value

    def test_it_holds_no_secret(self) -> None:
        kind = kind_of("policy")

        public, secrets = split_secrets(kind, "general", POLICY)

        assert secrets == {}
        assert public == POLICY

    def test_it_asks_the_permission_of_the_routes_that_write_it(self) -> None:
        from noust.web.permissions.registry import permission_for_route

        kind = kind_of("policy")

        assert [method for method, _path in kind.operations] == ["GET", "PUT"]
        assert {path for _method, path in kind.operations} == {"/api/config/policy"}
        assert permission_for_route("PUT", "/api/config/policy") == kind.permission
        assert permission_for_route("GET", "/api/config/policy") == "settings.read"

    def test_the_server_it_is_sent_to_is_given_the_value_as_it_is(self) -> None:
        assert kind_of("policy").for_node(POLICY, "web-2") == POLICY


class TestScope:
    def test_a_setting_is_the_fleets_or_a_servers(self) -> None:
        assert check_scope("fleet") == "fleet"
        assert check_scope("node:web-2") == "node:web-2"

    @pytest.mark.parametrize("scope", ["", "node:", "node:Web 2", "all", "fleet\n", "node:x\n"])
    def test_anything_else_is_not_a_place_to_set_one(self, scope: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check_scope(scope)
        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.bad_scope"
