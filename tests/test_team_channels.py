# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Notification channels that belong to a team (checklist K, I-05).

A team brings its own Slack or ntfy and governs where its applications' notices go.
A channel of a team hears about the applications of that team and about nothing else,
whatever its route says; and a team's channel is invisible to every other team.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.exceptions import ValidationError
from noust.core.notification_channels import ChannelAdmin
from noust.core.notifications.instances import resolve_instances
from noust.core.notifications.model import Notification, State
from noust.core.notifications.routing import Placement, Routing, decide
from noust.core.notifications.team_channels import TeamChannels, channel_key
from noust.core.store import NoustStore
from noust.core.teams import TeamStore


@pytest.fixture
def teams(tmp_path: Path) -> TeamStore:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    found = TeamStore(store)
    found.create_team("Acme", slug="acme")
    found.create_team("Globex", slug="globex")
    return found


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        yield Config()
    finally:
        Config.reset_instance()


def _deploy_failed(domain: str | None) -> Notification:
    return Notification(
        kind="deploy_failed",
        code="deploy.failed",
        state=State.FAILED,
        locale="en",
        title="T",
        subject=domain or "",
        summary="",
        server="web-1",
        domain=domain,
    )


def _decide(routing: Routing, notification: Notification, placements: dict[str, Placement]) -> Any:
    return decide(
        routing,
        notification,
        now=datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
        server_events={},
        default_on=lambda kind: True,
        placement_of=lambda domain: placements.get(domain),
    )


class TestWhatATeamsChannelHears:
    def test_it_hears_the_applications_of_its_team(self) -> None:
        routing = Routing(team="acme")
        placements = {"shop.acme.example": Placement(team="acme", project="web")}

        assert _decide(routing, _deploy_failed("shop.acme.example"), placements).action == "send"

    def test_it_never_hears_another_teams_applications(self) -> None:
        routing = Routing(team="acme")
        placements = {"blog.globex.example": Placement(team="globex", project="web")}

        decision = _decide(routing, _deploy_failed("blog.globex.example"), placements)

        assert decision.action == "skip"

    def test_it_does_not_hear_the_server_itself(self) -> None:
        # A full disk is the instance's to tell, not a team's: there is no application in it.
        assert _decide(Routing(team="acme"), _deploy_failed(None), {}).action == "skip"

    def test_an_application_it_cannot_place_is_not_told(self) -> None:
        # Deleted meanwhile, or unreadable: what cannot be proven to be the team's is not sent.
        assert _decide(Routing(team="acme"), _deploy_failed("gone.example"), {}).action == "skip"

    def test_a_route_still_narrows_inside_the_team(self) -> None:
        from noust.core.notifications.routing import Route

        routing = Routing(team="acme", route=Route(projects=("web",)))
        placements = {
            "shop.acme.example": Placement(team="acme", project="web"),
            "api.acme.example": Placement(team="acme", project="api"),
        }

        assert _decide(routing, _deploy_failed("shop.acme.example"), placements).action == "send"
        assert _decide(routing, _deploy_failed("api.acme.example"), placements).action == "skip"

    def test_a_channel_with_no_team_is_unchanged(self) -> None:
        placements = {"blog.globex.example": Placement(team="globex")}

        assert (
            _decide(Routing(), _deploy_failed("blog.globex.example"), placements).action == "send"
        )


class TestTheBlock:
    def test_the_team_is_read_from_the_channel(self, config: Config) -> None:
        config.set(
            "notifications.channels.t1-slack",
            {"type": "slack", "webhook_url": "https://hooks.slack.test/x", "team": "acme"},
        )

        (instance,) = [
            i
            for i in resolve_instances(config.get("notifications.channels"))
            if i.name == "t1-slack"
        ]

        assert instance.routing.team == "acme"

    def test_a_team_that_is_not_a_slug_is_refused_when_written(self, config: Config) -> None:
        with pytest.raises(Exception, match="team"):
            config.set(
                "notifications.channels.t1-slack",
                {
                    "type": "slack",
                    "webhook_url": "https://hooks.slack.test/x",
                    "team": "Not A Slug",
                },
            )


class TestTheTeamsOwnChannels:
    def test_a_team_makes_a_channel_that_is_its_own(self, teams: TeamStore, config: Config) -> None:
        channels = TeamChannels("acme", ChannelAdmin(config), teams=teams)

        view = channels.save(
            "alerts", type="slack", settings={"webhook_url": "https://hooks.slack.test/a"}
        )

        assert view.name == "alerts"
        assert view.team == "acme"
        stored = ChannelAdmin(config).get(channel_key(teams.require_team("acme").id, "alerts"))
        assert stored is not None and stored.team == "acme"

    def test_each_team_sees_only_its_channels(self, teams: TeamStore, config: Config) -> None:
        acme = TeamChannels("acme", ChannelAdmin(config), teams=teams)
        globex = TeamChannels("globex", ChannelAdmin(config), teams=teams)
        acme.save("alerts", type="slack", settings={"webhook_url": "https://hooks.slack.test/a"})
        globex.save("alerts", type="ntfy", settings={"webhook_url": "https://ntfy.test/g"})

        assert [v.name for v in acme.list()] == ["alerts"]
        assert acme.list()[0].type == "slack"
        assert [v.type for v in globex.list()] == ["ntfy"]

    def test_the_same_name_in_two_teams_is_two_channels(
        self, teams: TeamStore, config: Config
    ) -> None:
        for slug in ("acme", "globex"):
            TeamChannels(slug, ChannelAdmin(config), teams=teams).save(
                "alerts", type="slack", settings={"webhook_url": f"https://hooks.slack.test/{slug}"}
            )

        keys = [
            i.name
            for i in resolve_instances(config.get("notifications.channels"))
            if i.routing.team
        ]
        assert len(keys) == 2 and len(set(keys)) == 2

    def test_the_instances_own_channels_are_not_a_teams(
        self, teams: TeamStore, config: Config
    ) -> None:
        ChannelAdmin(config).save(
            "ops", type="slack", settings={"webhook_url": "https://hooks.slack.test/o"}
        )

        assert TeamChannels("acme", ChannelAdmin(config), teams=teams).list() == []

    def test_a_team_cannot_reach_the_instances_channel_by_name(
        self, teams: TeamStore, config: Config
    ) -> None:
        ChannelAdmin(config).save(
            "ops", type="slack", settings={"webhook_url": "https://hooks.slack.test/o"}
        )
        channels = TeamChannels("acme", ChannelAdmin(config), teams=teams)

        assert channels.get("ops") is None
        assert channels.remove("ops") is False
        assert ChannelAdmin(config).get("ops") is not None

    def test_email_goes_through_the_servers_mail_account_and_is_not_a_teams(
        self, teams: TeamStore, config: Config
    ) -> None:
        channels = TeamChannels("acme", ChannelAdmin(config), teams=teams)

        with pytest.raises(ValidationError, match="mail"):
            channels.save("mail", type="email")

    def test_a_credential_is_never_read_back(self, teams: TeamStore, config: Config) -> None:
        channels = TeamChannels("acme", ChannelAdmin(config), teams=teams)

        channels.save(
            "alerts", type="slack", settings={"webhook_url": "https://hooks.slack.test/a"}
        )
        again = channels.get("alerts")

        assert again is not None
        assert again.settings["webhook_url"] == "***"

    def test_removing_a_channel_removes_only_that_one(
        self, teams: TeamStore, config: Config
    ) -> None:
        channels = TeamChannels("acme", ChannelAdmin(config), teams=teams)
        channels.save("a", type="slack", settings={"webhook_url": "https://hooks.slack.test/a"})
        channels.save("b", type="slack", settings={"webhook_url": "https://hooks.slack.test/b"})

        assert channels.remove("a") is True
        assert [v.name for v in channels.list()] == ["b"]
        assert channels.remove("a") is False

    def test_a_team_that_does_not_exist_has_no_channels(self, config: Config) -> None:
        from noust.core.teams import TeamNotFoundError

        with pytest.raises(TeamNotFoundError):
            TeamChannels("nope", ChannelAdmin(config)).list()

    def test_there_are_only_so_many_channels_in_a_team(
        self, teams: TeamStore, config: Config
    ) -> None:
        channels = TeamChannels("acme", ChannelAdmin(config), teams=teams)
        for index in range(8):
            channels.save(
                f"c{index}", type="slack", settings={"webhook_url": f"https://h.test/{index}"}
            )

        with pytest.raises(ValidationError, match="at most"):
            channels.save("c8", type="slack", settings={"webhook_url": "https://h.test/8"})
