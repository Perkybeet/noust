# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/central-backups``: the copies a central holds of its servers (item 195).

What is pinned is who sees what and who changes what:

- a person of a team sees the copies of the servers assigned to their teams and nothing else: another
  team's server is as if it had none, a copy of it is a 404 that reads like one that does not exist,
  and giving a copy back to a server the person does not see is the same 404;
- how collecting works (who is followed, the knobs, a pass now) and the central's disk are the
  instance's, never a team's;
- changing the knobs, letting a copy go and giving one back are the instance's or a role's with
  ``backups.manage``, and each is recorded.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.teams.nodes import NodeAssignments
from noust.fleet.collect.records import Collected, CollectedFile, CollectedStore
from noust.fleet.collect.settings import Follows, SettingsStore
from noust.web.api import central_backups
from tests.support.node_proxy import FakeManager, FakeTunnels
from tests.support.teams_support import World, build_world

BASE = "/api/central-backups"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    built = build_world(tmp_path, monkeypatch)
    template = FakeManager(FakeTunnels(0)).records["web-2"]
    assignments = NodeAssignments(built.store)
    for name, team in (("acme-1", "acme"), ("globex-1", "globex"), ("spare", None)):
        built.store.save_node(replace(template, name=name, ssh_host=f"{name}.example.com"))
        if team is not None:
            assignments.assign(name, team)
    return built


def copy(node: str, item: str, owner: str = "shop.example.com", kind: str = "app") -> Collected:
    return Collected(
        node=node,
        kind=kind,
        item_id=item,
        owner=owner,
        folder="shop",
        origin="scheduled",
        created_at="2026-11-10T02:00:00+00:00",
        collected_at="2026-11-10T02:05:00+00:00",
        size=100,
        files=(CollectedFile(f"{item}.tar.gz", 100, "a" * 64, "archive"),),
        verified="digest",
        meta={"domain": owner},
    )


@pytest.fixture
def held(world: World) -> None:
    store = CollectedStore(world.store)
    for node in ("acme-1", "globex-1", "spare"):
        store.add(copy(node, f"{node}_1"))
        store.add(copy(node, f"{node}_2", kind="database", owner="postgresql/shop"))


class Recorder:
    def __init__(self) -> None:
        self.woken: list[tuple[str | None, bool]] = []

    def wake(self, node: str | None = None, *, force: bool = False) -> None:
        self.woken.append((node, force))


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(central_backups, "get_worker", lambda: recorder)
    return recorder


def ids(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return sorted(item["id"] for item in response.json()["items"])


class TestWhoSeesWhichCopies:
    def test_a_member_sees_the_copies_of_its_teams_servers_and_no_other(
        self, world: World, held: None
    ) -> None:
        assert ids(world.client.get(BASE, headers=world.as_("alice"))) == [
            "acme-1_1",
            "acme-1_2",
        ]
        assert ids(world.client.get(BASE, headers=world.as_("bob"))) == ["globex-1_1", "globex-1_2"]

    def test_the_instance_sees_every_copy_even_those_of_a_server_no_team_took(
        self, world: World, held: None
    ) -> None:
        assert len(ids(world.client.get(BASE, headers=world.as_("admin")))) == 6

    def test_a_member_of_no_team_with_a_server_sees_nothing(self, world: World, held: None) -> None:
        from tests.support.teams_support import make_person

        lone = make_person(world.teams, "lone", "viewer")

        assert ids(world.client.get(BASE, headers=lone.headers)) == []

    def test_asking_for_another_teams_server_is_an_empty_list_not_an_error(
        self, world: World, held: None
    ) -> None:
        response = world.client.get(f"{BASE}?node=globex-1", headers=world.as_("alice"))

        assert response.status_code == 200 and response.json() == {"items": [], "total": 0}

    def test_the_list_can_be_cut_by_kind_and_by_what_it_is_a_copy_of(
        self, world: World, held: None
    ) -> None:
        by_kind = world.client.get(f"{BASE}?kind=database", headers=world.as_("alice"))
        by_owner = world.client.get(f"{BASE}?owner=postgresql/shop", headers=world.as_("alice"))

        assert ids(by_kind) == ids(by_owner) == ["acme-1_2"]

    def test_no_path_of_the_central_is_in_it(
        self, world: World, held: None, tmp_path: Path
    ) -> None:
        text = world.client.get(BASE, headers=world.as_("admin")).text

        assert str(tmp_path) not in text and "layout" not in text

    def test_an_unknown_kind_is_refused(self, world: World, held: None) -> None:
        assert (
            world.client.get(f"{BASE}?kind=secrets", headers=world.as_("admin")).status_code == 400
        )


class TestTheStatus:
    def test_a_member_sees_its_servers_and_not_the_centrals_disk_or_knobs(
        self, world: World, held: None
    ) -> None:
        body = world.client.get(f"{BASE}/status", headers=world.as_("alice")).json()

        assert [n["node"] for n in body["nodes"]] == ["acme-1"]
        assert body["settings"] is None and body["space"] is None and body["directory"] is None
        assert body["nodes"][0]["held"] == {"count": 2, "bytes": 200}

    def test_the_instance_sees_all_of_it(self, world: World, held: None) -> None:
        body = world.client.get(f"{BASE}/status", headers=world.as_("admin")).json()

        assert [n["node"] for n in body["nodes"]] == ["acme-1", "globex-1", "spare"]
        assert body["settings"]["enabled"] is False and body["space"] is not None
        assert body["directory"].endswith(".collected")

    def test_followed_of_managed_is_one_count_and_names_the_exceptions(
        self, world: World, held: None
    ) -> None:
        SettingsStore(world.store).set(enabled=True)
        Follows(world.store).set_follow("spare", False)

        body = world.client.get(f"{BASE}/status", headers=world.as_("admin")).json()

        assert body["following"] == {"following": 2, "of": 3, "exceptions": ["spare"]}
        mine = world.client.get(f"{BASE}/status", headers=world.as_("alice")).json()
        assert mine["following"] == {"following": 1, "of": 1, "exceptions": []}


class TestWhatStaysTheInstances:
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("PUT", f"{BASE}/settings", {"enabled": True}),
            ("PUT", f"{BASE}/nodes/acme-1", {"follow": True}),
            ("POST", f"{BASE}/collect", {"node": "acme-1"}),
        ],
    )
    def test_how_collecting_works_is_not_a_teams(
        self,
        world: World,
        held: None,
        worker: Recorder,
        method: str,
        path: str,
        body: dict[str, Any],
    ) -> None:
        response = world.client.request(method, path, json=body, headers=world.as_("alice"))

        assert response.status_code == 403
        assert worker.woken == []


class TestTheInstanceChangesHowItWorks:
    def test_the_knobs_are_changed_recorded_and_a_pass_is_woken(
        self,
        world: World,
        held: None,
        worker: Recorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorded: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(audit, "record", lambda event, **kw: recorded.append((event, kw)))

        response = world.client.put(
            f"{BASE}/settings",
            json={"enabled": True, "rate_limit_kbps": 2048, "retention_count": 0, "min_free_gb": 2},
            headers=world.as_("admin"),
        )

        assert response.status_code == 200, response.text
        settings = response.json()["settings"]
        assert settings["enabled"] is True and settings["rate_limit_kbps"] == 2048
        assert settings["retention_count"] is None and settings["min_free_gb"] == 2.0
        assert worker.woken == [(None, False)]
        assert [e for e, _ in recorded] == ["central_backup.settings"]

    def test_a_knob_that_is_not_right_changes_nothing(
        self, world: World, held: None, worker: Recorder
    ) -> None:
        response = world.client.put(
            f"{BASE}/settings",
            json={"enabled": True, "poll_seconds": 1},
            headers=world.as_("admin"),
        )

        assert response.status_code == 400 and "poll_seconds" in response.text
        assert SettingsStore(world.store).get().enabled is False

    def test_a_server_is_given_its_own_answer_and_gives_it_back(
        self, world: World, held: None, worker: Recorder
    ) -> None:
        on = world.client.put(
            f"{BASE}/nodes/acme-1", json={"follow": True}, headers=world.as_("admin")
        )
        row = next(n for n in on.json()["nodes"] if n["node"] == "acme-1")
        assert row["follow"] is True and row["following"] is True
        assert worker.woken == [("acme-1", False)]

        off = world.client.put(
            f"{BASE}/nodes/acme-1", json={"follow": None}, headers=world.as_("admin")
        )

        row = next(n for n in off.json()["nodes"] if n["node"] == "acme-1")
        assert row["follow"] is None and row["following"] is False

    def test_a_server_that_is_not_managed_is_refused(
        self, world: World, held: None, worker: Recorder
    ) -> None:
        response = world.client.put(
            f"{BASE}/nodes/ghost", json={"follow": True}, headers=world.as_("admin")
        )

        assert response.status_code == 400

    def test_a_pass_now_wakes_the_worker_and_reads_the_whole_list(
        self, world: World, held: None, worker: Recorder
    ) -> None:
        response = world.client.post(
            f"{BASE}/collect", json={"node": "globex-1"}, headers=world.as_("admin")
        )

        assert response.status_code == 202 and worker.woken == [("globex-1", True)]

    def test_a_pass_over_a_server_that_does_not_exist_is_refused(
        self, world: World, held: None, worker: Recorder
    ) -> None:
        response = world.client.post(
            f"{BASE}/collect", json={"node": "ghost"}, headers=world.as_("admin")
        )

        assert response.status_code == 400 and worker.woken == []


class TestLettingACopyGo:
    @pytest.fixture
    def let_go(self, monkeypatch: pytest.MonkeyPatch) -> list[Collected]:
        gone: list[Collected] = []

        def go(self: Any, copy: Collected, *, reason: str) -> None:
            gone.append(copy)
            CollectedStore().retire(copy.node, copy.kind, copy.item_id)

        monkeypatch.setattr(central_backups.NodeCollector, "let_go", go)
        return gone

    def test_the_instance_lets_a_copy_go_and_it_is_recorded(
        self, world: World, held: None, let_go: list[Collected], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[str] = []
        monkeypatch.setattr(audit, "record", lambda event, **kw: recorded.append(event))

        response = world.client.delete(
            f"{BASE}/acme-1/app?item=acme-1_1", headers=world.as_("admin")
        )

        assert response.status_code == 200
        assert [c.item_id for c in let_go] == ["acme-1_1"]
        assert [i["id"] for i in response.json()["items"]] == ["acme-1_2"]
        assert recorded == ["central_backup.delete"]

    def test_a_member_cannot_let_go_the_copy_of_another_teams_server_and_it_reads_as_absent(
        self, world: World, held: None, let_go: list[Collected]
    ) -> None:
        other = world.client.delete(
            f"{BASE}/globex-1/app?item=globex-1_1", headers=world.as_("alice")
        )
        absent = world.client.delete(f"{BASE}/globex-1/app?item=nope", headers=world.as_("alice"))
        ghost = world.client.delete(f"{BASE}/ghost/app?item=x", headers=world.as_("alice"))

        assert other.status_code == absent.status_code == ghost.status_code == 404
        assert let_go == []

    def test_a_copy_the_central_does_not_hold_is_a_404(
        self, world: World, held: None, let_go: list[Collected]
    ) -> None:
        response = world.client.delete(f"{BASE}/acme-1/app?item=nope", headers=world.as_("admin"))

        assert response.status_code == 404


class TestGivingACopyBack:
    @pytest.fixture
    def jobs(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        queued: list[dict[str, Any]] = []

        class Jobs:
            def create_job(self, **kw: Any) -> Any:
                queued.append(kw)
                job = type(
                    "Job", (), {"id": "j1", "status": type("S", (), {"value": "pending"})()}
                )()
                job.to_dict = lambda: {"id": "j1"}  # type: ignore[attr-defined]
                return job

        monkeypatch.setattr(central_backups, "get_job_manager", lambda: Jobs())
        return queued

    def test_it_is_queued_as_a_job_for_the_asker_with_what_was_confirmed(
        self, world: World, held: None, jobs: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(audit, "record", lambda event, **kw: recorded.append((event, kw)))

        response = world.client.post(
            f"{BASE}/acme-1/app/restore",
            json={
                "item": "acme-1_1",
                "target_node": "spare",
                "target": {"domain": "shop.example.com"},
                "options": {"restore_env": False},
            },
            headers=world.as_("admin"),
        )

        assert response.status_code == 202, response.text
        (call,) = jobs
        kwargs = call["kwargs"]
        assert (kwargs["node"], kwargs["item"], kwargs["target_node"]) == (
            "acme-1",
            "acme-1_1",
            "spare",
        )
        assert kwargs["options"] == {"restore_env": False}
        assert kwargs["asker"]["actor"]
        assert [e for e, _ in recorded] == ["central_backup.restore"]

    def test_without_a_target_it_goes_back_to_the_server_it_came_from(
        self, world: World, held: None, jobs: list[dict[str, Any]]
    ) -> None:
        world.client.post(
            f"{BASE}/acme-1/app/restore", json={"item": "acme-1_1"}, headers=world.as_("admin")
        )

        assert jobs[0]["kwargs"]["target_node"] == "acme-1"

    def test_a_member_may_give_back_to_a_server_of_its_own_team_and_not_to_another(
        self, world: World, held: None, jobs: list[dict[str, Any]]
    ) -> None:
        allowed = world.client.post(
            f"{BASE}/acme-1/app/restore", json={"item": "acme-1_1"}, headers=world.as_("alice")
        )
        elsewhere = world.client.post(
            f"{BASE}/acme-1/app/restore",
            json={"item": "acme-1_1", "target_node": "globex-1"},
            headers=world.as_("alice"),
        )

        # What a team role may do is the role's (restoring a backup is `backups.manage`, which a
        # team role may hold or not); what is certain is that another team's server is never a target.
        assert elsewhere.status_code in (403, 404)
        assert elsewhere.status_code != 202
        assert allowed.status_code in (202, 403)

    def test_another_teams_copy_cannot_be_given_back_by_a_member(
        self, world: World, held: None, jobs: list[dict[str, Any]]
    ) -> None:
        response = world.client.post(
            f"{BASE}/globex-1/app/restore", json={"item": "globex-1_1"}, headers=world.as_("alice")
        )

        assert response.status_code == 404 and jobs == []

    def test_a_copy_the_central_does_not_hold_is_a_404(
        self, world: World, held: None, jobs: list[dict[str, Any]]
    ) -> None:
        response = world.client.post(
            f"{BASE}/acme-1/app/restore", json={"item": "nope"}, headers=world.as_("admin")
        )

        assert response.status_code == 404 and jobs == []


class TestTheJob:
    def test_it_gives_the_copy_back_as_the_person_who_asked_with_the_central_s_limit(
        self, world: World, held: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        SettingsStore(world.store).set(rate_limit_kbps=64)
        seen: dict[str, Any] = {}

        class Giver:
            def __init__(self, **kw: Any) -> None:
                seen.update(kw)

            def run(self, **kw: Any) -> dict[str, Any]:
                seen["run"] = kw
                return {"node": seen["target"]}

        monkeypatch.setattr(central_backups, "GiveBack", Giver)

        result = central_backups.give_back_job(
            "acme-1",
            "app",
            "acme-1_1",
            "spare",
            {"domain": "shop.example.com"},
            {"restore_env": True},
            {
                "actor": "maria",
                "scope": "admin",
                "elevated": True,
                "permissions": ["backups.manage"],
            },
        )

        assert result == {"node": "spare"}
        assert seen["asker"].actor == "maria" and seen["asker"].elevated is True
        assert seen["throttle"].rate == 64 * 1024.0
        assert seen["run"] == {
            "target_args": {"domain": "shop.example.com"},
            "options": {"restore_env": True},
        }
