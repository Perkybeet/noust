# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where a channel of the fleet can be heard: the routes the fleet filters by, and the servers they reach.

A fleet channel filters by what means the same on every server: the application's domain (with
patterns), its tags, the severity, the event and the server. Projects, environments and teams are
each server's own and are refused when the channel is saved. A channel is pushed only to a server
where its route can match something, so that a credential is not handed to a machine that can
never use it. What is pinned: the shape is judged once, with the server's own route validator and
with the one list the fleet adds (``servers``, which the central uses and no server ever sees);
the decision is pure; and a server whose applications cannot be read is neither said to reach nor
not to reach.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.fleet.settings.kinds import kind_of
from noust.fleet.settings.reach import (
    Reach,
    check_route,
    explain,
    needs_apps,
    node_value,
    servers_of,
)

SHOP = {"domain": "shop.example.com", "tags": ["prod", "eu"]}
BLOG = {"domain": "blog.example.org", "tags": ["staging"]}


def channel(**route: Any) -> dict[str, Any]:
    return {"type": "slack", "settings": {"channel": "#ops"}, "route": route}


class TestWhatARouteMayHold:
    def test_the_routes_a_server_has_and_the_list_of_servers_are_accepted(self) -> None:
        check_route(
            channel(
                apps=["*.example.com"],
                tags=["prod"],
                min_severity="failed",
                servers=["web-*", "db-1"],
                server_events=True,
            )
        )

    def test_a_channel_with_no_route_is_accepted(self) -> None:
        check_route({"type": "slack"})
        check_route({"type": "slack", "route": {}})
        check_route({"type": "slack", "route": None})

    @pytest.mark.parametrize("lists", [{"projects": ["shop"]}, {"environments": ["production"]}])
    def test_projects_and_environments_are_each_servers_own_and_are_refused(
        self, lists: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            check_route(channel(**lists))

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.route_local"
        assert caught.value.field == f"route.{next(iter(lists))}"

    def test_the_servers_own_validator_judges_the_rest_of_the_route(self) -> None:
        with pytest.raises(ValidationError) as caught:
            check_route(channel(min_severity="loud"))

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.route_invalid"
        assert "min_severity" in caught.value.coded.params["problem"]

    def test_a_route_that_is_not_an_object_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            check_route({"type": "slack", "route": ["shop.example.com"]})

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.route_invalid"

    @pytest.mark.parametrize(
        "servers", ["web 1", ["web 1"], ["web;1"], [f"s{n}" for n in range(65)]]
    )
    def test_a_list_of_servers_is_judged_like_every_other_list_of_a_route(
        self, servers: Any
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            check_route(channel(servers=servers))

        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.route_invalid"
        assert caught.value.field == "route.servers"

    def test_the_channel_kind_runs_the_check_when_a_value_is_saved(self) -> None:
        with pytest.raises(ValidationError) as caught:
            kind_of("channel").check_value(channel(projects=["shop"]))
        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.route_local"

    def test_only_a_channel_has_a_route(self) -> None:
        rule = {"metric": "cpu", "threshold": 90}
        assert kind_of("alert_rule").check_value(rule) == rule


class TestWhatTheServerIsSent:
    def test_the_list_of_servers_stays_on_the_central(self) -> None:
        value = channel(servers=["web-*"], apps=["*.example.com"], min_severity="failed")

        sent = node_value(value)

        assert sent["route"] == {"apps": ["*.example.com"], "min_severity": "failed"}
        assert value["route"]["servers"] == ["web-*"]  # the stored value is not changed

    def test_a_route_that_only_named_servers_is_not_sent_at_all(self) -> None:
        sent = node_value(channel(servers=["web-1"]))

        assert "route" not in sent
        assert sent["settings"] == {"channel": "#ops"}

    def test_a_value_with_no_route_is_sent_as_it_is(self) -> None:
        value = {"type": "slack"}
        assert node_value(value) == value

    def test_the_servers_are_read_trimmed_and_without_repeats(self) -> None:
        assert servers_of(channel(servers="web-1, web-1 ,db-*")) == ("web-1", "db-*")
        assert servers_of({"type": "slack"}) == ()


class TestWhereAChannelReaches:
    def test_a_channel_with_no_route_reaches_every_server(self) -> None:
        for node in ("web-1", "db-1"):
            assert explain("channel", {"type": "slack"}, node, []) == Reach(True)

    def test_a_list_of_servers_reaches_the_servers_it_names_and_patterns_match_names(self) -> None:
        value = channel(servers=["web-*", "db-1"])

        assert explain("channel", value, "web-3", None) == Reach(True)
        assert explain("channel", value, "db-1", None) == Reach(True)
        assert explain("channel", value, "db-2", None) == Reach(False, "server")

    def test_a_server_that_is_not_listed_is_decided_without_asking_for_its_applications(
        self,
    ) -> None:
        value = channel(servers=["web-1"], apps=["*.example.com"])

        assert explain("channel", value, "db-1", None) == Reach(False, "server")

    def test_domains_reach_the_servers_where_an_application_matches(self) -> None:
        value = channel(apps=["*.example.com"])

        assert explain("channel", value, "web-1", [SHOP, BLOG]) == Reach(True)
        assert explain("channel", value, "web-2", [BLOG]) == Reach(False, "domain")
        assert explain("channel", value, "web-3", []) == Reach(False, "domain")

    def test_a_domain_is_matched_as_the_servers_own_route_matches_it(self) -> None:
        # fnmatch, case sensitive, ? for one character: the same pattern means the same.
        value = channel(apps=["shop.example.co?"])

        assert explain("channel", value, "web-1", [SHOP]) == Reach(True)
        assert explain("channel", value, "web-1", [{"domain": "shop.example.comm"}]) == Reach(
            False, "domain"
        )
        assert explain("channel", channel(apps=["SHOP.example.com"]), "web-1", [SHOP]) == Reach(
            False, "domain"
        )

    def test_tags_reach_the_servers_where_an_application_carries_one(self) -> None:
        value = channel(tags=["prod"])

        assert explain("channel", value, "web-1", [SHOP, BLOG]) == Reach(True)
        assert explain("channel", value, "web-2", [BLOG]) == Reach(False, "tag")

    def test_a_domain_and_a_tag_have_to_be_on_the_same_application(self) -> None:
        value = channel(apps=["blog.*"], tags=["prod"])

        # One application is the blog and another is in prod: the route needs both of one.
        assert explain("channel", value, "web-1", [SHOP, BLOG]) == Reach(False, "tag")

    def test_what_is_about_the_server_itself_is_heard_on_every_server_when_the_route_asks(
        self,
    ) -> None:
        value = channel(apps=["*.nowhere.example"], server_events=True)

        assert explain("channel", value, "web-1", []) == Reach(True)

    def test_a_severity_or_an_event_does_not_depend_on_the_server(self) -> None:
        value = {**channel(min_severity="failed"), "events": {"deploy_failed": True}}

        assert explain("channel", value, "web-1", []) == Reach(True)

    def test_when_the_applications_cannot_be_read_it_is_not_known(self) -> None:
        value = channel(apps=["*.example.com"])

        assert explain("channel", value, "web-1", None) == Reach(None)

    def test_only_a_channel_has_a_reach(self) -> None:
        assert explain("alert_rule", {"metric": "cpu", "threshold": 9}, "web-1", []) == Reach(True)
        assert explain("variable", {"value": "x"}, "web-1", None) == Reach(True)


class TestWhenTheApplicationsAreNeeded:
    def test_only_a_route_by_domain_or_tag_needs_them(self) -> None:
        assert needs_apps("channel", channel(apps=["*.example.com"]))
        assert needs_apps("channel", channel(tags=["prod"]))
        assert not needs_apps("channel", channel(servers=["web-1"]))
        assert not needs_apps("channel", channel(min_severity="failed"))
        assert not needs_apps("channel", {"type": "slack"})
        assert not needs_apps("channel", channel(apps=["*"], server_events=True))
        assert not needs_apps("registry", {"kind": "ghcr"})
