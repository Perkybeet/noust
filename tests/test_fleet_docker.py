# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Docker zone of the whole fleet: every server's containers, images and the rest in one list.

Docker is the machine's, so each server answers for its own and the central puts the answers side by
side with the server as a column. What is held here is the shape of that and what a central that is
newer than a node has to say about it.

- **A row is the server's own**, labelled with the server and its page.
- **A server before 4.3 does not count Docker**: it answers 404, which is an outcome (``unsupported``, with
  its version and a sentence) and never an error of the view or a reason to hide the others.
- **A server with no Docker has no rows** and says why, in a note of its own.
- **What asks for attention is one list, worst first**, with the server on each item.
- **A server is asked a page of what it has**, bounded, with the filters it knows and nothing else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.fleet import docker as fleet_docker
from noust.fleet.aggregate import gather, set_aggregator
from tests.support.fleet_views_support import Central, build_central

SUMMARY = "/api/docker/summary"
CONTAINERS = "/api/docker/containers"
ENGINE = "/api/docker/engine"


def container(name: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": name.encode().hex()[:64],
        "name": name,
        "image": "nginx:1.27",
        "state": "running",
        "state_kind": "running",
        "problem": False,
        "owner": {"kind": "nobody", "subject": "", "project": ""},
        **fields,
    }


def summary(**fields: Any) -> dict[str, Any]:
    return {
        "available": True,
        "engine": {
            "ok": True,
            "state": "ok",
            "usable": True,
            "facts": [],
            "limits": [],
            "info": {
                "server_version": "29.1.3",
                "api": "1.52",
                "compose_version": "2.40.3",
                "image_store": "containerd",
                "rootless": False,
                "os": "Ubuntu 24.04",
            },
        },
        "counts": {"containers": {"total": 3, "running": 2, "failing": 1, "stopped": 0}},
        "attention": [],
        "disk": {"free_bytes": 28 * 2**30, "total_bytes": 160 * 2**30},
        "can_manage": True,
        **fields,
    }


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path, ("web-2", "web-3", "web-4"), web_4="4.2.1")
    fleet_docker.register()
    built.nodes["web-2"].responses[SUMMARY] = (
        200,
        summary(
            attention=[
                {
                    "kind": "disk",
                    "subject": None,
                    "owner": None,
                    "count": 1,
                    "facts": {"free_bytes": 28 * 2**30},
                },
                {
                    "kind": "stopped",
                    "subject": "adminer",
                    "owner": {"kind": "nobody", "subject": "", "project": ""},
                    "count": 1,
                    "facts": {"exit_code": 143},
                },
            ]
        ),
    )
    built.nodes["web-3"].responses[SUMMARY] = (
        200,
        summary(
            attention=[
                {
                    "kind": "loop",
                    "subject": "api-1",
                    "owner": {"kind": "app", "subject": "api.example.org", "project": "api"},
                    "count": 1,
                    "facts": {"exit_code": 137, "oom": True},
                }
            ]
        ),
    )
    built.nodes["web-2"].responses[CONTAINERS] = (
        200,
        {
            "available": True,
            "items": [container("portainer"), container("adminer", state="exited")],
            "total": 2,
            "offset": 0,
            "limit": 200,
            "can_manage": True,
        },
    )
    built.nodes["web-3"].responses[CONTAINERS] = (
        200,
        {"available": True, "items": [container("api-1", state_kind="loop")], "total": 1},
    )
    # web-4 runs Noust 4.2.1: it has no route for any of it, and answers 404 to all.
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


def asked(central: Central, node: str, path: str) -> list[dict[str, str]]:
    return [dict(r.url.params) for r in central.nodes[node].requests if r.url.path == path]


class TestTheRows:
    def test_each_is_the_servers_own_with_the_server_and_its_page(self, central: Central) -> None:
        rows = gather("docker_containers", manager=central.manager).items

        portainer = next(r for r in rows if r["name"] == "portainer")
        assert portainer["node"] == "web-2" and portainer["local"] is False
        assert portainer["page"] == "/server/docker/containers"
        assert portainer["href"] == "/n/web-2/server/docker/containers"
        assert {r["node"] for r in rows} == {"web-2", "web-3"}
        assert portainer["owner"]["kind"] == "nobody"

    def test_a_server_that_is_down_leaves_the_others_in_the_list(self, central: Central) -> None:
        central.nodes["web-3"].down = True

        result = gather("docker_containers", manager=central.manager)

        assert {r["node"] for r in result.items} == {"web-2"}
        assert result.outcome("web-3") is not None and result.outcome("web-3").status != "ok"  # type: ignore[union-attr]

    def test_a_server_with_more_than_the_page_says_how_many_it_has(self, central: Central) -> None:
        central.nodes["web-2"].responses[CONTAINERS] = (
            200,
            {"available": True, "items": [container("one")], "total": 340},
        )

        result = gather("docker_containers", manager=central.manager)

        warnings = result.outcome("web-2").warnings  # type: ignore[union-attr]
        assert any("340" in note and "web-2" in note for note in warnings)


class TestAServerBefore43:
    def test_it_is_an_outcome_with_its_version_and_a_sentence_and_not_an_error(
        self, central: Central
    ) -> None:
        result = gather("docker_containers", manager=central.manager)

        outcome = result.outcome("web-4")
        assert outcome is not None
        assert outcome.status == "unsupported" and outcome.code == "not_offered"
        assert outcome.version == "4.2.1"
        assert "/api/docker/containers" in (outcome.message or "")
        assert outcome.hint and "older Noust" in outcome.hint

    def test_the_servers_that_count_docker_are_not_hidden_by_it(self, central: Central) -> None:
        result = gather("docker_containers", manager=central.manager)

        assert {r["node"] for r in result.items} == {"web-2", "web-3"}
        assert result.outcome("web-2").status == "ok"  # type: ignore[union-attr]

    def test_every_view_says_it_the_same_way(self, central: Central) -> None:
        for view in (
            "docker_summary",
            "docker_attention",
            "docker_engine",
            "docker_stacks",
            "docker_images",
            "docker_volumes",
            "docker_networks",
        ):
            outcome = gather(view, manager=central.manager).outcome("web-4")
            assert outcome is not None and outcome.status == "unsupported", view
            assert outcome.code == "not_offered", view


class TestAServerWithoutDocker:
    def test_it_has_no_rows_and_says_why_in_the_words_of_its_state(self, central: Central) -> None:
        central.nodes["web-2"].responses[CONTAINERS] = (
            200,
            {
                "available": False,
                "engine": {"state": "missing", "usable": False, "ok": False},
                "items": [],
                "total": 0,
            },
        )

        result = gather("docker_containers", manager=central.manager)

        assert {r["node"] for r in result.items} == {"web-3"}
        warnings = result.outcome("web-2").warnings  # type: ignore[union-attr]
        assert any("no Docker" in note and "missing" in note for note in warnings)
        assert result.outcome("web-2").status == "ok"  # type: ignore[union-attr]

    def test_the_summary_still_has_its_row_and_says_it_is_not_available(
        self, central: Central
    ) -> None:
        central.nodes["web-2"].responses[SUMMARY] = (
            200,
            {"available": False, "engine": {"state": "missing", "usable": False, "ok": False}},
        )

        rows = {r["node"]: r for r in gather("docker_summary", manager=central.manager).items}

        assert rows["web-2"]["available"] is False
        assert rows["web-2"]["engine"]["state"] == "missing"
        assert rows["web-2"]["counts"] == {} and rows["web-2"]["attention"] == []
        assert rows["web-3"]["available"] is True


class TestTheSummaryPerServer:
    def test_each_server_is_a_row_with_its_engine_its_counters_and_its_disk(
        self, central: Central
    ) -> None:
        rows = {r["node"]: r for r in gather("docker_summary", manager=central.manager).items}

        row = rows["web-2"]
        assert row["outcome"] == "ok" and row["available"] is True
        assert row["engine"]["version"] == "29.1.3" and row["engine"]["compose"] == "2.40.3"
        assert row["counts"]["containers"]["failing"] == 1
        assert row["disk"]["free_bytes"] == 28 * 2**30
        assert row["can_manage"] is True
        assert row["page"] == "/server/docker"

    def test_a_server_that_did_not_answer_has_a_row_with_nothing_in_it(
        self, central: Central
    ) -> None:
        rows = {r["node"]: r for r in gather("docker_summary", manager=central.manager).items}

        assert rows["web-4"]["available"] is None and rows["web-4"]["outcome"] == "unsupported"
        assert rows["web-4"]["engine"] is None and rows["web-4"]["attention"] == []


class TestWhatAsksForAttention:
    def test_it_is_one_list_for_the_fleet_worst_first_with_the_server_on_each(
        self, central: Central
    ) -> None:
        rows = gather("docker_attention", manager=central.manager).items

        assert [(r["kind"], r["node"], r["subject"]) for r in rows] == [
            ("loop", "web-3", "api-1"),
            ("stopped", "web-2", "adminer"),
            ("disk", "web-2", None),
        ]

    def test_an_item_about_a_container_leads_to_the_containers_and_one_about_the_disk_to_the_summary(
        self, central: Central
    ) -> None:
        rows = gather("docker_attention", manager=central.manager).items

        pages = {r["kind"]: r["href"] for r in rows}
        assert pages["loop"] == "/n/web-3/server/docker/containers"
        assert pages["disk"] == "/n/web-2/server/docker"

    def test_the_order_is_the_one_the_gate_decides_and_nothing_here(self) -> None:
        from noust.core.docker.summary import ORDER

        assert ORDER[:2] == ("loop", "stopped") and ORDER[-1] == "disk"


class TestTheEngines:
    def test_each_server_is_a_row_with_its_doctors_sentences_as_it_said_them(
        self, central: Central
    ) -> None:
        central.nodes["web-2"].responses[ENGINE] = (
            200,
            {
                "ok": False,
                "state": "ok",
                "usable": True,
                "facts": [{"code": "docker.engine.version", "params": {}, "text": "Docker 29"}],
                "limits": [{"code": "docker.compose.missing", "params": {}, "text": "No Compose"}],
                "info": {"server_version": "29.1.3", "api": "1.52"},
            },
        )

        rows = {r["node"]: r for r in gather("docker_engine", manager=central.manager).items}

        assert rows["web-2"]["engine"]["limits"] == ["docker.compose.missing"]
        assert rows["web-2"]["limits"][0]["text"] == "No Compose"
        assert rows["web-2"]["info"]["server_version"] == "29.1.3"
        assert rows["web-4"]["engine"] is None


class TestWhatAServerIsAsked:
    def test_it_is_asked_for_two_hundred_by_default(self, central: Central) -> None:
        gather("docker_containers", manager=central.manager)

        assert asked(central, "web-2", CONTAINERS) == [{"limit": "200"}]

    @pytest.mark.parametrize(("given", "sent"), [(0, "1"), (7, "7"), (99999, "1000"), ("x", "200")])
    def test_the_limit_is_kept_inside_what_a_server_takes(
        self, central: Central, given: Any, sent: str
    ) -> None:
        gather("docker_containers", {"limit": given}, manager=central.manager)

        assert asked(central, "web-2", CONTAINERS)[0]["limit"] == sent

    def test_the_filters_are_the_servers_own_and_nothing_else_is_passed_on(
        self, central: Central
    ) -> None:
        gather(
            "docker_containers",
            {
                "owner": "nobody",
                "state": "exited",
                "app": "x",
                "search": "porta",
                "q": "y",
                "node": "z",
            },
            manager=central.manager,
        )

        assert asked(central, "web-2", CONTAINERS) == [
            {"limit": "200", "owner": "nobody", "app": "x", "state": "exited", "search": "porta"}
        ]

    def test_a_listing_that_has_no_such_filter_does_not_pass_it(self, central: Central) -> None:
        central.nodes["web-2"].responses["/api/docker/images"] = (
            200,
            {"available": True, "items": []},
        )

        gather("docker_images", {"state": "exited", "owner": "app"}, manager=central.manager)

        assert asked(central, "web-2", "/api/docker/images") == [{"limit": "200", "owner": "app"}]
