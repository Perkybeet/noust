# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A Compose document pasted into the console (checklist D-22): looked at, then deployed like any stack."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.deployers.compose_paste import MAX_DOCUMENT_BYTES, analyze_document
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

DIGEST = "sha256:" + "ab" * 32

BLOG = f"""
services:
  web:
    image: ghcr.io/o/blog:2.1.0@{DIGEST}
    restart: unless-stopped
    environment:
      DATABASE_URL: postgres://blog:${{DB_PASSWORD}}@db/blog
      SECRET_KEY: ${{SECRET_KEY:?set a secret key}}
      GREETING: ${{GREETING:-hello}}
    ports:
      - "127.0.0.1:8080:2368"
    depends_on:
      - db
  db:
    image: postgres:16
    volumes:
      - data:/var/lib/postgresql/data
volumes:
  data:
"""


class TestWhatItReads:
    def test_services_images_ports_and_dependencies(self) -> None:
        found = analyze_document(BLOG)

        assert [(item.name, item.image) for item in found.services] == [
            ("web", f"ghcr.io/o/blog:2.1.0@{DIGEST}"),
            ("db", "postgres:16"),
        ]
        web = found.services[0]
        assert web.publishes == ("8080:2368/tcp",) and web.depends_on == ("db",)
        assert found.web == ["web"]
        assert found.deployable and found.refused == []

    def test_the_variables_it_reads_say_which_need_a_value(self) -> None:
        variables = {item.name: item for item in analyze_document(BLOG).variables}

        assert variables["GREETING"].default == "hello" and variables["GREETING"].required is False
        assert variables["SECRET_KEY"].required is True
        assert variables["SECRET_KEY"].message == "set a secret key"
        assert (
            variables["DB_PASSWORD"].required is True and variables["DB_PASSWORD"].default is None
        )
        assert variables["DB_PASSWORD"].services == ("web",)

    def test_an_escaped_dollar_is_not_a_variable(self) -> None:
        found = analyze_document(
            "services:\n  a:\n    image: x:1\n    command: echo $$HOME ${REAL}\n"
        )

        assert [item.name for item in found.variables] == ["REAL"]

    def test_an_image_that_moves_is_a_warning_not_a_refusal(self) -> None:
        found = analyze_document("services:\n  a:\n    image: nginx\n  b:\n    image: redis:7\n")

        assert found.deployable
        assert any("service a" in text and "moves" in text for text in found.warned)
        assert any("service b" in text and "pinned by tag only" in text for text in found.warned)

    def test_a_port_on_every_address_is_worth_knowing(self) -> None:
        found = analyze_document(
            "services:\n  a:\n    image: x@"
            + DIGEST.replace("sha256:", "sha256:")
            + '\n    ports: ["8080:80"]\n'
        )

        assert any("every address" in text for text in found.warned)


class TestWhatRefusesIt:
    @pytest.mark.parametrize(
        ("snippet", "needle"),
        [
            ("privileged: true", "privileged"),
            ("volumes: ['/var/run/docker.sock:/var/run/docker.sock']", "Docker socket"),
            ("build: .", "builds from"),
            ("build: {context: ./app}", "builds from"),
            ("env_file: secrets.env", "env_file"),
        ],
    )
    def test_root_on_the_host_and_files_it_does_not_have(self, snippet: str, needle: str) -> None:
        document = f"services:\n  a:\n    image: x:1\n    {snippet}\n"
        if snippet.startswith("build"):
            document = f"services:\n  a:\n    {snippet}\n"

        found = analyze_document(document)

        assert not found.deployable
        assert any(needle in problem for problem in found.refused)

    def test_a_remote_build_context_is_not_refused(self) -> None:
        found = analyze_document("services:\n  a:\n    build: https://github.com/o/r.git#main\n")

        assert found.deployable

    def test_the_stacks_own_env_file_is_fine(self) -> None:
        assert analyze_document("services:\n  a:\n    image: x:1\n    env_file: .env\n").deployable

    def test_a_service_with_nothing_to_run_is_refused(self) -> None:
        found = analyze_document("services:\n  a:\n    restart: always\n")

        assert not found.deployable and "neither an image nor a build" in found.refused[0]

    @pytest.mark.parametrize(
        "document",
        [
            "",
            "- just\n- a list\n",
            "services: {}\n",
            "services: [a, b]\n",
            "services: {'-bad': {image: x}}\n",
        ],
    )
    def test_something_that_is_not_a_compose_document_is_an_error(self, document: str) -> None:
        with pytest.raises(ValidationError):
            analyze_document(document)

    def test_a_document_too_large_is_an_error(self) -> None:
        with pytest.raises(ValidationError, match="256 KiB"):
            analyze_document("# " + "x" * MAX_DOCUMENT_BYTES)

    def test_bad_yaml_is_an_error_not_a_crash(self) -> None:
        with pytest.raises(ValidationError, match="not valid YAML"):
            analyze_document("services: [unclosed")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore, runner: object) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> Any:
        captured.append(kwargs)

        class Queued:
            id = "job-1"
            status = type("Status", (), {"value": "pending"})()

            def to_dict(self) -> dict[str, Any]:
                return {"id": self.id}

        return Queued()

    manager = type("FakeJobs", (), {"create_job": staticmethod(create_job)})()
    monkeypatch.setattr("noust.web.api.apps.get_job_manager", lambda: manager)
    return captured


class TestTheApi:
    def test_analyze_answers_what_the_preview_shows(self, client: TestClient) -> None:
        response = client.post("/api/compose/analyze", json={"document": BLOG})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["deployable"] is True and body["web"] == ["web"]
        assert {item["name"] for item in body["variables"]} == {
            "DB_PASSWORD",
            "SECRET_KEY",
            "GREETING",
        }

    def test_analyze_says_what_refuses_it(self, client: TestClient) -> None:
        document = "services:\n  a:\n    image: x:1\n    privileged: true\n"

        body = client.post("/api/compose/analyze", json={"document": document}).json()

        assert body["deployable"] is False and "privileged" in body["refused"][0]

    def test_analyze_of_nonsense_is_a_400(self, client: TestClient) -> None:
        response = client.post("/api/compose/analyze", json={"document": "nope: true"})

        assert response.status_code == 400

    def test_deploying_a_document_queues_a_stack_made_from_it(
        self, client: TestClient, queued: list[dict[str, Any]], store: NoustStore
    ) -> None:
        response = client.post(
            "/api/apps",
            json={
                "domain": "blog.example.com",
                "compose_document": BLOG,
                "env_vars": {"DB_PASSWORD": "x", "SECRET_KEY": "y"},
                "ssl": False,
            },
        )

        assert response.status_code == 202, response.text
        kwargs = queued[0]["kwargs"]
        assert kwargs["app_type"] == "docker-compose"
        source = Path(kwargs["source"])
        assert (source / "docker-compose.yml").read_text().startswith("\nservices:")
        assert kwargs["env_vars"] == {"DB_PASSWORD": "x", "SECRET_KEY": "y"}

    def test_a_document_that_is_refused_queues_nothing_and_writes_nothing(
        self, client: TestClient, queued: list[dict[str, Any]], store: NoustStore, tmp_path: Path
    ) -> None:
        response = client.post(
            "/api/apps",
            json={
                "domain": "blog.example.com",
                "compose_document": "services:\n  a:\n    image: x:1\n    privileged: true\n",
            },
        )

        assert response.status_code == 400
        assert "privileged" in json.dumps(response.json())
        assert queued == []
        assert not (store.db_path.parent / "recipe-sources" / "blog-example-com").exists()

    def test_a_document_and_a_source_cannot_be_given_together(
        self, client: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            "/api/apps",
            json={
                "domain": "blog.example.com",
                "compose_document": BLOG,
                "source": "https://github.com/o/r.git",
            },
        )

        assert response.status_code == 400 and queued == []


class TestTheCommand:
    def test_it_reports_and_exits_1_for_a_document_that_is_refused(self, tmp_path: Path) -> None:
        refused = tmp_path / "docker-compose.yml"
        refused.write_text("services:\n  a:\n    image: x:1\n    privileged: true\n")
        fine = tmp_path / "ok.yml"
        fine.write_text(BLOG)

        bad = CliRunner().invoke(cli, ["compose", "check", str(refused), "--json"])
        good = CliRunner().invoke(cli, ["compose", "check", str(fine)])

        assert bad.exit_code == 1 and json.loads(bad.output)["deployable"] is False
        assert good.exit_code == 0, good.output
        assert "needs a value" in good.output and "can be deployed" in good.output

    def test_it_reads_standard_input(self) -> None:
        result = CliRunner().invoke(cli, ["compose", "check", "-"], input=BLOG)

        assert result.exit_code == 0, result.output
        assert "web" in result.output
