# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A team's notification channels over HTTP: who may see them, change them and use them.

Two teams share the server. Each keeps channels of its own under its path; the other
team's channels read as absent (a 404, the body of a team that does not exist), they are
not in the configuration a member reads, and they cannot be tried by guessing their key.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.config import Config
from noust.web.api import team_notifications
from tests.teams_support import World, build_world, make_person

SLACK = {"type": "slack", "settings": {"webhook_url": "https://hooks.slack.test/services/a"}}


@pytest.fixture
def world(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    built = build_world(tmp_path, monkeypatch)
    yield built
    Config.reset_instance()


def _put(world: World, who: str, team: str, name: str, body: dict[str, Any]) -> Any:
    return world.client.put(
        f"/api/teams/{team}/notifications/channels/{name}", headers=world.as_(who), json=body
    )


class TestTheTeamsOwnChannels:
    def test_a_team_admin_makes_a_channel_and_the_team_reads_it(self, world: World) -> None:
        made = _put(world, "alice", "acme", "alerts", SLACK)
        listed = world.client.get(
            "/api/teams/acme/notifications/channels", headers=world.as_("dana")
        )

        assert made.status_code == 200, made.text
        assert made.json()["name"] == "alerts" and made.json()["configured"] is True
        assert [item["name"] for item in listed.json()["items"]] == ["alerts"]
        assert "email" not in [t["name"] for t in listed.json()["types"]]

    def test_a_credential_is_not_in_any_answer(self, world: World) -> None:
        made = _put(world, "alice", "acme", "alerts", SLACK)
        listed = world.client.get(
            "/api/teams/acme/notifications/channels", headers=world.as_("dana")
        )

        assert "services/a" not in made.text
        assert "services/a" not in listed.text
        assert made.json()["settings"]["webhook_url"] == "***"

    def test_a_viewer_reads_but_does_not_change(self, world: World) -> None:
        assert _put(world, "dana", "acme", "alerts", SLACK).status_code == 403

    def test_another_teams_channels_do_not_exist_for_a_member(self, world: World) -> None:
        _put(world, "alice", "acme", "alerts", SLACK)

        read = world.client.get("/api/teams/acme/notifications/channels", headers=world.as_("bob"))
        write = _put(world, "bob", "acme", "x", SLACK)
        ghost = world.client.get(
            "/api/teams/ghost/notifications/channels", headers=world.as_("bob")
        )

        assert read.status_code == write.status_code == 404
        assert read.json()["detail"].replace("acme", "X") == ghost.json()["detail"].replace(
            "ghost", "X"
        )

    def test_email_is_the_servers_and_is_refused_with_the_reason(self, world: World) -> None:
        response = _put(world, "alice", "acme", "mail", {"type": "email"})

        assert response.status_code == 400
        assert "mail account" in response.text

    def test_removing_one_removes_it_and_a_second_time_is_a_404(self, world: World) -> None:
        _put(world, "alice", "acme", "alerts", SLACK)

        first = world.client.delete(
            "/api/teams/acme/notifications/channels/alerts", headers=world.as_("alice")
        )
        second = world.client.delete(
            "/api/teams/acme/notifications/channels/alerts", headers=world.as_("alice")
        )

        assert (first.status_code, second.status_code) == (204, 404)

    def test_a_team_cannot_remove_the_servers_own_channel_by_its_name(self, world: World) -> None:
        world.client.put(
            "/api/notifications/channels/ops",
            headers=world.as_("admin"),
            json={"type": "slack", "settings": {"webhook_url": "https://hooks.slack.test/o"}},
        )

        response = world.client.delete(
            "/api/teams/acme/notifications/channels/ops", headers=world.as_("alice")
        )

        assert response.status_code == 404
        names = [
            item["name"]
            for item in world.client.get(
                "/api/notifications/channels", headers=world.as_("admin")
            ).json()["items"]
        ]
        assert "ops" in names

    def test_sending_a_test_goes_through_the_teams_channel(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[str] = []

        class Fake:
            def __init__(self, config: Any) -> None: ...

            def test_channel(self, channel: str) -> None:
                sent.append(channel)

        monkeypatch.setattr(team_notifications, "Notifier", Fake)
        _put(world, "alice", "acme", "alerts", SLACK)

        response = world.client.post(
            "/api/teams/acme/notifications/channels/alerts/test", headers=world.as_("omar")
        )

        assert response.json()["ok"] is True
        assert len(sent) == 1 and sent[0].endswith("-alerts") and sent[0].startswith("t")


class TestWhatTheOthersCanSee:
    def test_the_instance_lists_every_teams_channels_with_their_team(self, world: World) -> None:
        _put(world, "alice", "acme", "alerts", SLACK)
        _put(world, "bob", "globex", "alerts", SLACK)

        items = world.client.get("/api/notifications/channels", headers=world.as_("admin")).json()[
            "items"
        ]

        assert sorted(i["team"] for i in items if i["team"]) == ["acme", "globex"]

    def test_a_member_does_not_read_another_teams_channels_in_the_configuration(
        self, world: World
    ) -> None:
        _put(world, "alice", "acme", "alerts", SLACK)

        seen_by_bob = world.client.get("/api/config", headers=world.as_("bob")).json()
        seen_by_alice = world.client.get("/api/config", headers=world.as_("alice")).json()
        seen_by_admin = world.client.get("/api/config", headers=world.as_("admin")).json()

        def teams(payload: dict[str, Any]) -> list[str]:
            channels = payload["config"]["notifications"]["channels"]
            return sorted(
                str(block.get("team")) for block in channels.values() if block.get("team")
            )

        assert teams(seen_by_bob) == []
        assert teams(seen_by_alice) == ["acme"]
        assert teams(seen_by_admin) == ["acme"]

    def test_a_member_cannot_try_another_teams_channel_by_guessing_its_key(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _put(world, "alice", "acme", "alerts", SLACK)
        key = next(
            name
            for name, block in Config().get("notifications.channels").items()
            if isinstance(block, dict) and block.get("team") == "acme"
        )

        # Testing a channel by name is an operator's route (open, held to the instance role).
        gina = make_person(world.teams, "gina", "operator", team="globex", team_role="operator")

        guessed = world.client.post(f"/api/config/notifications/{key}/test", headers=gina.headers)

        assert guessed.json()["ok"] is False
        assert "No notification channel" in guessed.json()["detail"]
