# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``/api/streams``.

The rules of publishing a port are pinned in ``tests/test_stream_exposure.py``. What this
module owns: the shapes the console reads, that opening and closing ask for sudo mode while
looking and planning do not, and that what the manager refuses is the status the console
branches on (400 for a port that cannot be had, 409 for a server that is not ready, 404
for a port that is not published).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers import stream_exposure as stream_module
from noust.managers.stream_exposure import StreamExposures
from noust.web.api import streams as streams_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth
from tests.test_stream_exposure import (
    DEBIAN_V,
    NO_STREAM_V,
    FakeFirewall,
    dump,
)

BEARER = {"sid": "token", "scope": "admin", "source": "bearer", "type": "api_token"}
COOKIE = {"sid": "s1", "scope": "admin", "source": "cookie", "type": "session"}
BODY = {"name": "minecraft", "protocol": "tcp", "listen_port": 25565, "target_port": 25565}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def runner() -> FakeRunner:
    fake = FakeRunner()
    fake.script(("nginx", "-V"), stderr=DEBIAN_V)
    fake.script(("nginx", "-T"), stdout=dump())
    fake.script(("nginx", "-t"))
    fake.script(("systemctl", "reload", "nginx"))
    fake.script(("ss", "-ltnpH"), stdout="")
    fake.script(("ss", "-lunpH"), stdout="")
    return fake


@pytest.fixture
def firewall() -> FakeFirewall:
    return FakeFirewall()


@pytest.fixture(autouse=True)
def wired(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    firewall: FakeFirewall,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        stream_module, "Config", lambda: SimpleNamespace(get=lambda key, default=None: default)
    )
    conf = tmp_path / "etc-nginx"
    (conf / "modules-enabled").mkdir(parents=True)
    monkeypatch.setattr(
        streams_api,
        "StreamExposures",
        lambda: StreamExposures(
            store=store,
            runner=runner,
            conf_root=conf,
            firewall=lambda _runner: firewall,  # type: ignore[arg-type,return-value]
        ),
    )


def build(session: dict[str, Any]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(streams_api.router, prefix="/api/streams")
    app.dependency_overrides[require_auth] = lambda: session
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return build(BEARER)


def test_nothing_published_and_what_nginx_offers(client: TestClient) -> None:
    response = client.get("/api/streams")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exposures"] == []
    assert body["support"] == {
        "available": True,
        "module": "dynamic",
        "loader": "installable",
        "problem": None,
        "hint": None,
    }


def test_planning_shows_the_file_and_the_rules_and_changes_nothing(
    client: TestClient, firewall: FakeFirewall
) -> None:
    response = client.post("/api/streams/plan", json={**BODY, "sources": ["203.0.113.0/24"]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert "proxy_pass 127.0.0.1:25565;" in body["config"]
    assert body["file"].endswith("noust-stream/minecraft.conf")
    assert body["loader"] == "write"
    assert body["firewall"] == [{"port": 25565, "proto": "tcp", "source": "203.0.113.0/24"}]
    assert any("Nothing listens" in w for w in body["warnings"])
    assert client.get("/api/streams").json()["exposures"] == []
    assert firewall.allowed == []


def test_publishing_and_closing_a_port(client: TestClient, firewall: FakeFirewall) -> None:
    opened = client.post("/api/streams", json={**BODY, "sources": ["203.0.113.0/24"]})

    assert opened.status_code == 201, opened.text
    body = opened.json()
    assert body["exposure"]["state"] == "open"
    assert body["exposure"]["target"] == "127.0.0.1:25565"
    assert body["exposure"]["firewall"]["made"] == [
        {"port": 25565, "proto": "tcp", "source": "203.0.113.0/24"}
    ]
    assert "ufw now allows 1 rule(s)" in body["firewall"]
    listed = client.get("/api/streams").json()["exposures"]
    assert [e["name"] for e in listed] == ["minecraft"]

    closed = client.delete("/api/streams/minecraft")

    assert closed.status_code == 200, closed.text
    assert [r.source for r in firewall.removed] == ["203.0.113.0/24"]
    assert client.get("/api/streams").json()["exposures"] == []


def test_opening_and_closing_ask_for_sudo_mode_and_looking_does_not() -> None:
    client = build(COOKIE)

    assert client.get("/api/streams").status_code == 200
    assert client.post("/api/streams/plan", json=BODY).status_code == 200
    opened = client.post("/api/streams", json=BODY)
    closed = client.delete("/api/streams/minecraft")

    assert opened.status_code == 403 and opened.json()["error"] == "elevation_required"
    assert closed.status_code == 403 and closed.json()["error"] == "elevation_required"


def test_a_port_that_is_not_free_is_a_400_that_says_why(client: TestClient) -> None:
    response = client.post("/api/streams", json={**BODY, "listen_port": 443})

    assert response.status_code == 400
    assert "Port 443 is the web server's" in response.json()["detail"]


def test_a_server_that_cannot_publish_is_a_409_with_the_fix(
    client: TestClient, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-V"), stderr=NO_STREAM_V)

    response = client.post("/api/streams", json=BODY)

    assert response.status_code == 409
    body = response.json()
    assert "built without the stream module" in body["detail"]
    assert "libnginx-mod-stream" in body["hint"]


def test_nginx_refusing_the_configuration_is_a_400_with_nginxs_own_words(
    client: TestClient, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-t"), stderr="nginx: [emerg] unknown directive", exit_code=1)

    response = client.post("/api/streams", json=BODY)

    assert response.status_code == 400
    assert "nginx: [emerg] unknown directive" in response.json()["output"]


def test_closing_what_is_not_published_is_a_404(client: TestClient) -> None:
    assert client.delete("/api/streams/ghost").status_code == 404


@pytest.mark.parametrize("seconds", [0, 10**6])
def test_a_timeout_out_of_range_is_refused_before_anything_runs(
    client: TestClient, seconds: int
) -> None:
    response = client.post("/api/streams/plan", json={**BODY, "timeout_seconds": seconds})

    assert response.status_code == 422
