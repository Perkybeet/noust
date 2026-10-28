# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The typed calls that write one setting of the fleet on a node.

Never ``PATCH /api/config`` of loose keys: each kind goes through the node's own route for it, so
the node validates with its own validators and keeps its own audit trail. What is pinned: which
route each kind uses to find out whether the node already has the setting and to write it, that
a setting the node has is changed and one it lacks is made, that the body is the value and
nothing else, and that a destination's backend cannot be changed by a push.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.fleet.settings.push import find, push


class FakeNode:
    """A node that answers the lists and records what it is written."""

    def __init__(self, **lists: Any) -> None:
        self.lists = lists
        self.calls: list[tuple[str, str, Any]] = []

    def call(self, method: str, path: str, *, body: Any = None, params: Any = None) -> Any:
        self.calls.append((method, path, body))
        if method == "GET":
            return self.lists[path]
        return {}


CHANNELS = "/api/notifications/channels"


class TestFinding:
    def test_a_channel_is_found_by_the_list_of_channels(self) -> None:
        node = FakeNode(
            **{CHANNELS: {"items": [{"name": "ops", "default": False, "configured": True}]}}
        )

        assert find(node, "channel", "ops") == {
            "name": "ops",
            "default": False,
            "configured": True,
        }
        assert find(node, "channel", "other") is None
        assert node.calls[0][:2] == ("GET", CHANNELS)

    def test_the_empty_default_of_a_type_is_not_a_channel_the_operator_has(self) -> None:
        node = FakeNode(
            **{CHANNELS: {"items": [{"name": "slack", "default": True, "configured": False}]}}
        )

        assert find(node, "channel", "slack") is None

    def test_the_default_of_a_type_that_is_set_up_is(self) -> None:
        node = FakeNode(
            **{CHANNELS: {"items": [{"name": "slack", "default": True, "configured": True}]}}
        )

        assert find(node, "channel", "slack") is not None

    @pytest.mark.parametrize(
        ("resource", "path", "key"),
        [
            ("alert_rule", "/api/alerts/rules", "items"),
            ("backup_destination", "/api/backup-destinations", "destinations"),
            ("registry", "/api/registries", "logins"),
            ("variable", "/api/variables/server", "variables"),
        ],
    )
    def test_each_kind_is_found_in_its_own_list(self, resource: str, path: str, key: str) -> None:
        node = FakeNode(**{path: {key: [{"name": "x"}]}})

        assert find(node, resource, "x") == {"name": "x"}
        assert find(node, resource, "y") is None
        assert node.calls[0][:2] == ("GET", path)


class TestWriting:
    def test_a_channel_is_one_put_of_the_value(self) -> None:
        node = FakeNode()
        value = {"type": "slack", "settings": {"channel": "#ops"}}

        assert push(node, "channel", "ops", value, None) == "created"
        assert push(node, "channel", "ops", value, {"name": "ops"}) == "updated"

        assert node.calls == [
            ("PUT", "/api/notifications/channels/ops", value),
            ("PUT", "/api/notifications/channels/ops", value),
        ]

    def test_an_alert_rule_and_a_variable_are_one_put(self) -> None:
        node = FakeNode()

        push(node, "alert_rule", "disk-high", {"metric": "disk", "threshold": 90}, None)
        push(node, "variable", "REGION", {"value": "eu"}, {"name": "REGION"})

        assert [(m, p) for m, p, _ in node.calls] == [
            ("PUT", "/api/alerts/rules/disk-high"),
            ("PUT", "/api/variables/server/REGION"),
        ]

    def test_a_registry_login_is_made_with_a_post_and_changed_with_a_patch(self) -> None:
        node = FakeNode()
        value = {"kind": "ghcr", "username": "bot", "password": "tok", "server": "ghcr.io"}

        assert push(node, "registry", "ghcr", value, None) == "created"
        assert push(node, "registry", "ghcr", value, {"name": "ghcr"}) == "updated"

        assert node.calls == [
            ("POST", "/api/registries", {"name": "ghcr", **value}),
            ("PATCH", "/api/registries/ghcr", value),
        ]

    def test_a_backup_destination_is_made_with_a_post_and_changed_with_a_put(self) -> None:
        node = FakeNode()
        value = {
            "backend": "s3",
            "fields": {"bucket": "b", "secret_access_key": "shh"},
            "encrypted": True,
            "verify": False,
        }

        assert push(node, "backup_destination", "nas", value, None) == "created"
        assert (
            push(node, "backup_destination", "nas", value, {"name": "nas", "backend": "s3"})
            == "updated"
        )

        assert node.calls == [
            ("POST", "/api/backup-destinations", {"name": "nas", **value}),
            (
                "PUT",
                "/api/backup-destinations/nas",
                {"fields": value["fields"], "encrypted": True, "verify": False},
            ),
        ]

    def test_a_push_cannot_change_the_backend_of_a_destination_the_node_has(self) -> None:
        node = FakeNode()

        with pytest.raises(ValidationError) as caught:
            push(
                node,
                "backup_destination",
                "nas",
                {"backend": "sftp", "fields": {}},
                {"name": "nas", "backend": "s3"},
            )

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.backend_changed"
        assert node.calls == []

    def test_a_name_is_one_segment_of_the_path(self) -> None:
        node = FakeNode()

        push(node, "variable", "A/../B", {"value": "x"}, None)

        assert node.calls[0][1] == "/api/variables/server/A%2F..%2FB"

    def test_the_body_is_not_the_caller_s_to_change(self) -> None:
        node = FakeNode()
        value = {"kind": "ghcr", "username": "bot", "password": "tok"}

        push(node, "registry", "ghcr", value, None)

        assert value == {"kind": "ghcr", "username": "bot", "password": "tok"}
