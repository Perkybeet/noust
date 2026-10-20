# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/instances/{name}/tls`` and a link's ``tls`` (checklist C-25, C-26).

The contract: reading the status and planning answer at once and change nothing; turning
TLS on, off or over restarts an engine, so it asks for sudo mode and runs as a job; what
could not succeed is refused when the request arrives, not in a failed job; and nothing the
API returns carries a key or a password.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import noust.web.api.databases.common as db_common
import noust.web.api.databases.jobs as jobs_module
import noust.web.api.databases.links as links_module
import noust.web.api.databases.tls as tls_module
from noust.core.db_instance_store import READY, DbInstanceStore
from noust.core.fs import RealFileSystem
from noust.core.runner import set_runner
from noust.managers.database import tls_authority
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.database_migration_support import Lab, build_lab
from tests.database_move_support import DOMAIN, HOST
from tests.test_database_tls import TlsHost, UnprivilegedFs


def without_chown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job builds its own manager over the process-wide filesystem; the test is not root."""
    write = RealFileSystem.write_text
    monkeypatch.setattr(
        RealFileSystem,
        "write_text",
        lambda self, path, content, *, mode=0o644, owner=None: write(
            self, path, content, mode=mode
        ),
    )


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    without_chown(monkeypatch)
    monkeypatch.setattr(tls_authority, "authority_dir", lambda: tmp_path / "etc" / "noust" / "tls")
    for built in build_lab(tmp_path, monkeypatch):
        host = TlsHost(tmp_path)
        set_runner(host)
        built.world.host = host
        built.managed._fs = UnprivilegedFs()
        built.managed.create("pg16", "postgresql", version="16", memory="1g")
        yield built


@pytest.fixture
def app(tmp_path: Path, lab: Lab, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    for module in (tls_module, links_module):
        monkeypatch.setattr(module, "service", lambda session=None, logger=None: lab.service)
    monkeypatch.setattr(
        jobs_module, "_service", lambda job_context, step, actor: lab.service, raising=True
    )
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(
            id=f"job-{len(created)}",
            status=SimpleNamespace(value="pending"),
            to_dict=lambda: {"id": f"job-{len(created)}", "status": "pending"},
        )

    monkeypatch.setattr(
        db_common, "get_job_manager", lambda: SimpleNamespace(create_job=create_job)
    )
    return created


def sign_in(app: FastAPI, *, elevate: bool) -> TestClient:
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    login = client.post("/api/auth/login", json={"token": token})
    assert login.status_code == 200, login.text
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    if elevate:
        assert client.post("/api/auth/elevate", json={"token": token}).status_code == 200
    return client


@pytest.fixture
def plain(app: FastAPI) -> TestClient:
    return sign_in(app, elevate=False)


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    return sign_in(app, elevate=True)


def run(job: dict[str, Any]) -> dict[str, Any]:
    return job["func"](**job["kwargs"])


URL = "/api/databases/instances/pg16/tls"


class TestReading:
    def test_an_instance_that_serves_nothing_says_so_and_which_modes_it_could(
        self, plain: TestClient
    ) -> None:
        response = plain.get(URL)

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["name"], body["key"], body["mode"]) == ("pg16", "postgresql@pg16", "off")
        assert body["certificate"] is None and body["ca"] is None
        assert body["supported"] == {"off": None, "optional": None, "required": None}

    def test_an_unknown_instance_is_a_404(self, plain: TestClient) -> None:
        assert plain.get("/api/databases/instances/nope/tls").status_code == 404

    def test_there_is_no_authority_until_an_instance_first_serves_tls(
        self, plain: TestClient
    ) -> None:
        assert plain.get("/api/databases/instances/tls-authority").json() == {"authority": None}

    def test_a_plan_names_the_applications_and_changes_nothing(
        self, plain: TestClient, lab: Lab
    ) -> None:
        response = plain.post(f"{URL}/plan", json={"mode": "required"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["current"] == "off" and body["restarts"] is True and body["blockers"] == []
        assert "localhost" in body["names"]
        assert any("refused" in warning for warning in body["warnings"])
        assert lab.managed.get("pg16").tls_mode == "off"

    def test_a_word_that_is_not_a_mode_is_a_400(self, plain: TestClient) -> None:
        response = plain.post(f"{URL}/plan", json={"mode": "mandatory"})

        assert response.status_code == 400
        assert "off, optional, required" in response.text


class TestChanging:
    def test_it_asks_for_sudo_mode(self, plain: TestClient, queued: list[dict[str, Any]]) -> None:
        response = plain.put(URL, json={"mode": "optional"})

        assert response.status_code in (401, 403) and queued == []

    def test_the_job_turns_it_on_and_carries_no_key(
        self, admin: TestClient, lab: Lab, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.put(URL, json={"mode": "optional"})

        assert response.status_code == 202, response.text
        [job] = queued
        assert (job["kwargs"]["name"], job["kwargs"]["mode"]) == ("pg16", "optional")
        result = run(job)
        assert result["mode"] == "optional"
        assert result["certificate"]["names"] and result["ca"]["path"].endswith("ca.crt")
        assert "PRIVATE KEY" not in str(result)
        assert lab.managed.get("pg16").tls_mode == "optional"

    def test_once_on_the_status_and_the_authority_are_read_back(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        admin.put(URL, json={"mode": "required"})
        run(queued[0])

        body = admin.get(URL).json()
        authority = admin.get("/api/databases/instances/tls-authority").json()["authority"]

        assert body["mode"] == "required" and body["certificate"]["days_left"] > 0
        assert authority["fingerprint"] == body["ca"]["fingerprint"]

    def test_what_the_engine_cannot_serve_is_refused_before_a_job_exists(
        self, admin: TestClient, lab: Lab, queued: list[dict[str, Any]]
    ) -> None:
        # The lab's service resolves PostgreSQL only: the record is all this needs.
        DbInstanceStore(lab.world.store).create(
            name="cache",
            flavour="redis",
            version="8.0",
            image="redis",
            digest="sha256:" + "b" * 64,
            host_port=6380,
            memory_limit_mb=256,
            created_by=None,
        )
        DbInstanceStore(lab.world.store).set_state("cache", READY)

        response = admin.put("/api/databases/instances/cache/tls", json={"mode": "optional"})

        assert response.status_code == 400
        assert "port of their own" in response.text
        assert queued == []

    def test_renewing_is_a_job_that_signs_the_certificate_again(
        self, admin: TestClient, lab: Lab, queued: list[dict[str, Any]]
    ) -> None:
        admin.put(URL, json={"mode": "optional"})
        run(queued[0])
        signed = lab.world.host.issued

        response = admin.post(f"{URL}/renew")

        assert response.status_code == 202, response.text
        run(queued[1])
        assert lab.world.host.issued > signed
        assert lab.managed.get("pg16").tls_mode == "optional"

    def test_renewing_asks_for_sudo_mode_and_an_unknown_instance_is_a_404(
        self, plain: TestClient, admin: TestClient
    ) -> None:
        assert plain.post(f"{URL}/renew").status_code in (401, 403)
        assert admin.post("/api/databases/instances/nope/tls/renew").status_code == 404


class TestLinkingWithTls:
    def test_a_mode_the_engine_cannot_keep_is_refused_before_a_job_exists(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post(
            f"/api/apps/{DOMAIN}/databases/link",
            json={"engine": HOST, "database": "shop_db", "tls": "verify-full"},
        )

        assert response.status_code == 422 or response.status_code == 400
        assert "Noust's authority" in response.text
        assert queued == []

    def test_the_mode_travels_to_the_job_and_reaches_the_application(
        self, admin: TestClient, lab: Lab, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post(
            f"/api/apps/{DOMAIN}/databases/link",
            json={"engine": HOST, "database": "shop_db", "tls": "require"},
        )

        assert response.status_code == 202, response.text
        [job] = queued
        assert job["kwargs"]["tls_mode"] == "require"
        run(job)
        assert "sslmode=require" in lab.env()["DATABASE_URL"]

    def test_the_applications_databases_say_how_each_is_reached(
        self, admin: TestClient, lab: Lab, queued: list[dict[str, Any]]
    ) -> None:
        admin.post(
            f"/api/apps/{DOMAIN}/databases/link",
            json={"engine": HOST, "database": "shop_db", "tls": "require"},
        )
        run(queued[0])

        body = admin.get(f"/api/apps/{DOMAIN}/databases").json()

        [entry] = body["databases"]
        assert entry["tls_mode"] == "require"
