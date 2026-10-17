# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/probes`` and ``noust probe``: the same probes, the same refusals (checklist G-17).

Both are thin fronts over :class:`~noust.managers.probes.ProbeManager`, so what is
asserted here is the part that is theirs: that a probe the API refuses the command
line refuses in the same words, that a route that changes something needs the right
permission and sudo mode, that publishing the status page is a job, and that nothing
the API returns is a credential.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

import noust.core.notifier as notifier_module
from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.store import NoustStore
from noust.managers import probes as probes_module
from noust.managers.probes import Answer, ProbeManager
from noust.web.api import probes as probes_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated

URL = "https://shop.example.com/health"
GOOD = Answer(True, 200, 87)
BAD = Answer(False, 503, 41, "HTTP 503")


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    yield path
    Config.reset_instance()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("93.184.216.34",))


class Asker:
    def __init__(self) -> None:
        self.answer = GOOD

    def __call__(self, probe: Any) -> Answer:
        return self.answer


@pytest.fixture
def asker(monkeypatch: pytest.MonkeyPatch) -> Asker:
    scripted = Asker()
    original = probes_module.ProbeManager.__init__

    def build(self: ProbeManager, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("ask_one", scripted)
        kwargs.setdefault("publish", lambda build_: None)
        original(self, *args, **kwargs)

    monkeypatch.setattr(probes_module.ProbeManager, "__init__", build)
    return scripted


@pytest.fixture
def client(store: NoustStore, config_file: Path, asker: Asker) -> TestClient:
    app = FastAPI()
    app.include_router(probes_api.router, prefix="/api/probes")
    install_error_handlers(app)
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test", "type": "master"}
    app.dependency_overrides[require_elevated] = lambda: {"session_id": "test", "type": "master"}
    return TestClient(app, raise_server_exceptions=True)


def put(client: TestClient, name: str = "shop", **fields: Any) -> Any:
    return client.put(f"/api/probes/{name}", json={"url": URL, **fields})


class TestTheApi:
    def test_a_probe_is_created_listed_and_removed(self, client: TestClient) -> None:
        created = put(client, public=True, title="Shop", interval_s=120)
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["name"] == "shop" and body["interval_s"] == 120
        assert body["status"]["state"] == "unknown" and body["address"] == URL

        listed = client.get("/api/probes").json()
        assert [item["name"] for item in listed["items"]] == ["shop"]
        assert listed["down"] == 0 and listed["status_page"]["components"] == 1

        assert client.delete("/api/probes/shop").status_code == 204
        assert client.delete("/api/probes/shop").status_code == 404
        assert client.get("/api/probes").json()["items"] == []

    def test_what_is_not_acceptable_is_refused_naming_the_field(self, client: TestClient) -> None:
        response = put(client, url="https://u:p@shop.example.com/")

        assert response.status_code == 400
        assert "url" in json.dumps(response.json())

    def test_an_address_on_the_servers_own_network_is_refused(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("127.0.0.1",))

        response = put(client)

        assert response.status_code == 400
        assert "allow_private_hosts" in json.dumps(response.json())

    def test_asking_a_probe_now_keeps_the_answer_and_moves_its_state(
        self, client: TestClient, asker: Asker
    ) -> None:
        put(client, fail_after=1)

        good = client.post("/api/probes/shop/check").json()
        assert (
            good["ok"] is True
            and good["status"] == 200
            and good["probe"]["status"]["state"] == "up"
        )

        asker.answer = BAD
        bad = client.post("/api/probes/shop/check").json()
        assert bad["ok"] is False and bad["error"] == "HTTP 503"
        assert bad["probe"]["status"]["state"] == "down"
        assert client.get("/api/probes").json()["down"] == 1

    def test_asking_one_that_does_not_exist_is_a_404(self, client: TestClient) -> None:
        assert client.post("/api/probes/nope/check").status_code == 404

    def test_the_history_has_the_incidents_and_the_latest_answers(
        self, client: TestClient, asker: Asker
    ) -> None:
        put(client, fail_after=1)
        client.post("/api/probes/shop/check")
        asker.answer = BAD
        client.post("/api/probes/shop/check")

        history = client.get("/api/probes/shop/history").json()

        assert [a["ok"] for a in history["answers"]] == [True, False]
        assert history["incidents"][0]["reason"] == "HTTP 503"
        assert history["incidents"][0]["ended_at"] is None
        assert client.get("/api/probes/nope/history").status_code == 404

    def test_replacing_a_probe_keeps_the_state_machine_honest(
        self, client: TestClient, asker: Asker
    ) -> None:
        put(client, fail_after=1)
        asker.answer = BAD
        client.post("/api/probes/shop/check")

        replaced = put(client, url="https://shop.example.com/other", fail_after=1).json()

        assert replaced["status"]["state"] == "unknown"


class TestTheStatusPage:
    def test_it_is_off_until_it_is_asked_for(self, client: TestClient) -> None:
        body = client.get("/api/probes/status-page").json()

        assert body["enabled"] is False and body["domain"] is None and body["title"] == "Status"

    def test_its_settings_are_changed_and_checked_by_the_configuration(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        ok = client.put(
            "/api/probes/status-page",
            json={"enabled": True, "title": "Acme", "directory": str(tmp_path / "public")},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["enabled"] is True and ok.json()["title"] == "Acme"
        assert ok.json()["directory"] == str(tmp_path / "public")

        bad = client.put("/api/probes/status-page", json={"directory": "relative/path"})
        assert bad.status_code == 400

    def test_a_field_left_out_is_kept(self, client: TestClient) -> None:
        client.put("/api/probes/status-page", json={"title": "Acme"})

        again = client.put("/api/probes/status-page", json={"enabled": True}).json()

        assert again["title"] == "Acme" and again["enabled"] is True

    def test_writing_it_now_writes_the_files(self, client: TestClient, tmp_path: Path) -> None:
        client.put("/api/probes/status-page", json={"directory": str(tmp_path / "public")})
        put(client, public=True)

        written = client.post("/api/probes/status-page/write").json()

        assert [Path(p).name for p in written["files"]] == ["index.html", "status.json"]
        assert (tmp_path / "public" / "index.html").is_file()

    def test_publishing_and_unpublishing_are_jobs(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queued: list[dict[str, Any]] = []

        class Manager:
            def create_job(self, **kwargs: Any) -> Any:
                queued.append(kwargs)
                return type(
                    "Job",
                    (),
                    {
                        "id": "j1",
                        "status": type("S", (), {"value": "pending"}),
                        "to_dict": lambda s: {"id": "j1"},
                    },
                )()

        monkeypatch.setattr(probes_api, "get_job_manager", lambda: Manager())

        published = client.post(
            "/api/probes/status-page/publish", json={"domain": "status.example.com"}
        )
        removed = client.delete("/api/probes/status-page/site")

        assert published.status_code == 202 and removed.status_code == 202
        assert queued[0]["kwargs"]["domain"] == "status.example.com"
        assert queued[0]["metadata"]["kind"] == "status_page_publish"
        assert queued[1]["metadata"]["kind"] == "status_page_unpublish"


class TestThePermissions:
    def test_each_route_declares_the_permission_it_needs(self) -> None:
        from noust.web.permissions import registry

        table = registry.route_map()
        wanted = {
            ("GET", "/api/probes"): "server.read",
            ("PUT", "/api/probes/{name}"): "server.manage",
            ("POST", "/api/probes/{name}/check"): "server.manage",
            ("DELETE", "/api/probes/{name}"): "server.manage",
            ("GET", "/api/probes/{name}/history"): "server.read",
            ("PUT", "/api/probes/status-page"): "server.manage",
            ("POST", "/api/probes/status-page/publish"): "server.host_access",
            ("DELETE", "/api/probes/status-page/site"): "server.host_access",
        }
        for route, permission in wanted.items():
            assert str(table[route]) == permission, route


@pytest.fixture
def run(config_file: Path, store: NoustStore, asker: Asker):  # type: ignore[no-untyped-def]
    runner = CliRunner()

    def invoke(*args: str):  # type: ignore[no-untyped-def]
        Config.reset_instance()
        return runner.invoke(root_cli, ["probe", *args])

    return invoke


class TestTheCommand:
    def test_a_probe_is_set_listed_and_removed(self, run) -> None:  # type: ignore[no-untyped-def]
        saved = run("set", "shop", "--url", URL, "--public", "--title", "Shop")
        assert saved.exit_code == 0, saved.output

        listed = json.loads(run("list", "--json").output)
        assert listed[0]["name"] == "shop" and listed[0]["public"] is True
        assert listed[0]["title"] == "Shop" and listed[0]["status"]["state"] == "unknown"

        assert run("remove", "shop").exit_code == 0
        assert run("remove", "shop").exit_code != 0

    def test_what_is_not_named_is_kept(self, run) -> None:  # type: ignore[no-untyped-def]
        run("set", "shop", "--url", URL, "--interval", "120", "--public")

        run("set", "shop", "--title", "Shop")
        (probe,) = json.loads(run("list", "--json").output)

        assert probe["interval_s"] == 120 and probe["public"] is True and probe["title"] == "Shop"

    def test_a_new_probe_needs_an_address(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run("set", "shop")

        assert result.exit_code != 0 and "--url" in result.output

    def test_the_same_refusals_as_the_api(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run("set", "shop", "--url", "ftp://example.com/")

        assert result.exit_code != 0
        assert "http://" in str(result.exception) or "http://" in result.output

    def test_running_one_asks_it_and_a_bad_answer_is_a_failing_exit(
        self,
        run,
        asker: Asker,  # type: ignore[no-untyped-def]
    ) -> None:
        run("set", "shop", "--url", URL)

        good = run("run", "shop", "--json")
        assert good.exit_code == 0 and json.loads(good.output)[0]["ok"] is True

        asker.answer = BAD
        bad = run("run", "shop", "--json")
        assert bad.exit_code == 1 and json.loads(bad.output)[0]["error"] == "HTTP 503"

    def test_running_every_probe(self, run) -> None:  # type: ignore[no-untyped-def]
        run("set", "one", "--url", URL)
        run("set", "two", "--url", "https://blog.example.com/")

        result = run("run", "--json")

        assert [item["name"] for item in json.loads(result.output)] == ["one", "two"]

    def test_the_history_lists_incidents(self, run, asker: Asker) -> None:  # type: ignore[no-untyped-def]
        run("set", "shop", "--url", URL, "--fail-after", "1")
        asker.answer = BAD
        run("run", "shop")

        body = json.loads(run("history", "shop", "--json").output)

        assert body[0]["reason"] == "HTTP 503" and body[0]["ended_at"] is None
        assert run("history", "nope").exit_code != 0

    def test_the_status_page_is_set_shown_and_written(
        self,
        run,
        tmp_path: Path,  # type: ignore[no-untyped-def]
    ) -> None:
        run("set", "shop", "--url", URL, "--public")
        assert (
            run(
                "status-page",
                "set",
                "--enable",
                "--title",
                "Acme",
                "--directory",
                str(tmp_path / "pub"),
            ).exit_code
            == 0
        )

        shown = json.loads(run("status-page", "show", "--json").output)
        assert shown["enabled"] is True and shown["title"] == "Acme" and shown["components"] == 1

        assert run("status-page", "write").exit_code == 0
        assert "Acme" in (tmp_path / "pub" / "index.html").read_text()

    def test_naming_nothing_to_change_is_an_error(self, run) -> None:  # type: ignore[no-untyped-def]
        assert run("status-page", "set").exit_code != 0
