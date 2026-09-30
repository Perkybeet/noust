"""
``noust fleet apps|certs|backups|updates|activity|run|jobs|retry`` and ``noust node label``.

The engines are tested on their own (the aggregator, the fleet jobs); these pin
what the commands add: the JSON envelope, the table and the nodes that did not
answer in their own words, the plan before anything runs, the exit status, and
labels set and removed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.web import fleet_jobs
from noust.web.auth import STATE_DIR_ENV
from tests.fleet_views_support import Central, build_central


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    built = build_central(tmp_path / "fleet")
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: built.manager)
    monkeypatch.setattr(fleet_jobs, "POLL_SECONDS", 0)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


def _invoke(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, list(args), input=input)


def _renews(central: Central, *failing: str) -> None:
    for name, node in central.nodes.items():
        node.handlers[("POST", "/api/certs/renew-all")] = (
            lambda request, node=node, fails=name in failing: node.queue(
                status="failed" if fails else "completed", error="certbot: DNS problem"
            )
        )


class TestViews:
    def test_apps_as_json(self, central):
        central.nodes["web-2"].apps = [{"domain": "shop.example.com", "status": "running"}]

        result = _invoke("fleet", "apps", "--json")

        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert [node["name"] for node in body["nodes"]] == ["web-2", "web-3"]
        assert body["items"][0]["href"] == "/n/web-2/apps/shop.example.com"

    def test_a_table_and_the_node_that_did_not_answer(self, central):
        central.nodes["web-2"].apps = [{"domain": "shop.example.com", "status": "running"}]
        central.nodes["web-3"].down = True

        result = _invoke("fleet", "apps")

        assert result.exit_code == 0, result.output
        assert "shop.example.com" in result.output
        assert "web-3: unreachable" in result.output
        assert "Connection refused" in result.output

    def test_a_filter(self, central):
        central.nodes["web-2"].apps = [
            {"domain": "a.example.com", "status": "running"},
            {"domain": "b.example.com", "status": "failed"},
        ]

        body = json.loads(
            _invoke("fleet", "apps", "--node", "web-2", "--status", "failed", "--json").output
        )

        assert [row["domain"] for row in body["items"]] == ["b.example.com"]

    @pytest.mark.parametrize("view", ["certs", "backups", "updates", "activity"])
    def test_every_view_answers(self, central, view):
        result = _invoke("fleet", view, "--json")

        assert result.exit_code == 0, result.output
        assert set(json.loads(result.output)) == {
            "resource",
            "generated_at",
            "partial",
            "nodes",
            "items",
        }


class TestRun:
    def test_a_plan_runs_nothing(self, central):
        _renews(central)

        result = _invoke("fleet", "run", "certs_renew", "--nodes", "web-2,web-3", "--plan")

        assert result.exit_code == 0, result.output
        assert "Renew certificates on 2 servers" in result.output
        assert not any(r.method == "POST" for r in central.nodes["web-2"].requests)

    def test_it_asks_before_running(self, central):
        _renews(central)

        result = _invoke("fleet", "run", "certs_renew", "--nodes", "web-2", input="n\n")

        assert "Nothing was run." in result.output
        assert not any(r.method == "POST" for r in central.nodes["web-2"].requests)

    def test_a_failure_exits_1_and_says_how_to_retry(self, central):
        _renews(central, "web-3")

        result = _invoke("fleet", "run", "certs_renew", "--nodes", "web-2,web-3", "-y")

        assert result.exit_code == 1, result.output
        assert "1 succeeded, 1 failed" in result.output
        assert "noust fleet retry" in result.output
        listing = json.loads(_invoke("fleet", "jobs", "--json").output)["jobs"]
        job_id = listing[0]["job_id"]
        shown = json.loads(_invoke("fleet", "jobs", job_id, "--json").output)
        assert {node["node"]: node["state"] for node in shown["nodes"]} == {
            "web-2": "succeeded",
            "web-3": "failed",
        }
        plan = json.loads(_invoke("fleet", "retry", job_id, "--plan", "--json").output)["plan"]
        assert [node["node"] for node in plan["nodes"]] == ["web-3"]

    def test_json_prints_the_result(self, central):
        _renews(central)

        result = _invoke("fleet", "run", "certs_renew", "--nodes", "web-2", "-y", "--json")

        assert result.exit_code == 0, result.output
        body = json.loads(result.output.strip().splitlines()[-1])
        assert body["status"] == "succeeded"

    def test_by_label(self, central):
        _renews(central)
        assert _invoke("node", "label", "web-3", "env=prod").exit_code == 0

        result = _invoke("fleet", "run", "certs_renew", "--label", "env=prod", "--plan", "--json")

        plan = json.loads(result.output)["plan"]
        assert [node["node"] for node in plan["nodes"]] == ["web-3"]


class TestLabels:
    def test_set_show_and_remove(self, central):
        assert _invoke("node", "label", "web-2", "env=prod", "role=web").exit_code == 0
        assert _invoke("node", "label", "web-2").output.strip() == "env=prod role=web"

        _invoke("node", "label", "web-2", "role-")
        shown = json.loads(_invoke("node", "label", "web-2", "--json").output)

        assert shown == {"node": "web-2", "labels": {"env": "prod"}}

    def test_a_bad_label_is_refused(self, central):
        result = _invoke("node", "label", "web-2", "Env=prod")

        assert result.exit_code != 0
        assert "Invalid label key" in str(result.exception)
