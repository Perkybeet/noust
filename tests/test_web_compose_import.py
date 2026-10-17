# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Coolify template converted over the API and the command line.

What the conversion does is pinned in ``tests/test_coolify_import.py``. Here: who may ask, that the
answer carries the document, the values and the same look any pasted document gets, that nothing
secret is printed by the command (the values go to a file only the owner reads), that a template
the guard refuses exits 1, and that a rehearsal writes nothing.
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.store import App, NoustStore
from tests.teams_support import World, build_world

TEMPLATE = """\
# documentation: https://docs.example.org/wiki
# port: 3000
services:
  wiki:
    image: example/wiki:2.1
    environment:
      - SERVICE_URL_WIKI_3000
      - BASE_URL=$SERVICE_URL_WIKI
      - DB_PASSWORD=$SERVICE_PASSWORD_DB
  db:
    image: postgres:16
    environment:
      - POSTGRES_PASSWORD=$SERVICE_PASSWORD_DB
"""

SOCKET = """\
services:
  agent:
    image: example/agent:1
    environment:
      - SERVICE_URL_AGENT_9000
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
"""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


# -- the API -----------------------------------------------------------------------


def test_a_template_comes_back_as_a_document_with_its_values_and_its_look(world: World) -> None:
    response = world.client.post(
        "/api/compose/import",
        json={"document": TEMPLATE, "domain": "wiki.example.com", "port": 18080},
        headers=world.as_("admin"),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "127.0.0.1:${PORT:-18080}:3000" in body["document"]
    assert body["values"]["SERVICE_URL_WIKI"] == "https://wiki.example.com"
    assert len(body["values"]["SERVICE_PASSWORD_DB"]) == 32
    assert (body["routed_service"], body["routed_port"]) == ("wiki", 3000)
    assert body["analysis"]["deployable"] is True and body["analysis"]["web"] == ["wiki"]
    assert {v["name"]: v["origin"] for v in body["variables"]} == {
        "SERVICE_PASSWORD_DB": "generated",
        "SERVICE_URL_WIKI": "domain",
    }
    assert body["metadata"]["documentation"] == "https://docs.example.org/wiki"
    assert body["adjustments"] and body["warnings"] == []


def test_without_a_domain_the_addresses_are_asked_for(world: World) -> None:
    body = world.client.post(
        "/api/compose/import", json={"document": TEMPLATE}, headers=world.as_("admin")
    ).json()

    assert "SERVICE_URL_WIKI" not in body["values"]
    assert [v["origin"] for v in body["variables"] if v["name"] == "SERVICE_URL_WIKI"] == ["ask"]


def test_a_template_the_guard_refuses_is_converted_and_marked_not_deployable(world: World) -> None:
    body = world.client.post(
        "/api/compose/import", json={"document": SOCKET}, headers=world.as_("admin")
    ).json()

    assert body["analysis"]["deployable"] is False
    assert any("Docker socket" in problem for problem in body["analysis"]["refused"])


@pytest.mark.parametrize(
    "request_body",
    [
        {"document": "not: compose"},
        {"document": "services: {"},
        {"document": TEMPLATE, "domain": "not a name"},
        {"document": TEMPLATE, "domain": "a.example.com; reboot"},
    ],
)
def test_what_cannot_be_converted_is_a_400_in_words(
    world: World, request_body: dict[str, Any]
) -> None:
    response = world.client.post(
        "/api/compose/import", json=request_body, headers=world.as_("admin")
    )

    assert response.status_code == 400, response.text


def test_a_port_out_of_range_is_refused(world: World) -> None:
    response = world.client.post(
        "/api/compose/import",
        json={"document": TEMPLATE, "port": 70000},
        headers=world.as_("admin"),
    )

    assert response.status_code == 422


def test_creating_an_application_is_what_asking_for_it_takes(world: World) -> None:
    response = world.client.post(
        "/api/compose/import", json={"document": TEMPLATE}, headers=world.as_("dana")
    )

    assert response.status_code == 403


# -- the command line --------------------------------------------------------------


def invoke(*args: str, input_text: str | None = None) -> Any:
    return CliRunner().invoke(root_cli, ["compose", "import", *args], input=input_text)


@pytest.fixture
def template(tmp_path: Path) -> Path:
    path = tmp_path / "wiki.yaml"
    path.write_text(TEMPLATE)
    return path


def test_the_document_and_a_private_env_file_are_written_to_the_folder(
    template: Path, tmp_path: Path
) -> None:
    out = tmp_path / "wiki"

    result = invoke(str(template), "--domain", "wiki.example.com", "--out", str(out))

    assert result.exit_code == 0, result.output
    document = (out / "docker-compose.yml").read_text()
    assert "127.0.0.1:${PORT:-3000}:3000" in document and "Converted by Noust" in document
    env = out / ".env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    lines = dict(line.split("=", 1) for line in env.read_text().splitlines())
    assert lines["SERVICE_URL_WIKI"] == "https://wiki.example.com"
    secret = lines["SERVICE_PASSWORD_DB"]
    assert len(secret) == 32 and secret not in result.output
    assert f"noust create -d wiki.example.com -s {out}" in result.output


def test_without_a_folder_the_document_is_printed_and_nothing_secret_is(template: Path) -> None:
    result = invoke(str(template), "--domain", "wiki.example.com")

    assert result.exit_code == 0, result.output
    assert "services:" in result.output and "SERVICE_PASSWORD_DB" in result.output
    assert "Nothing was written" in result.output
    assert not re.search(r"\b[A-Za-z0-9]{32}\b", result.output)  # no generated secret


def test_the_json_never_carries_a_value(template: Path, tmp_path: Path) -> None:
    result = invoke(str(template), "--domain", "wiki.example.com", "--json")

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["deployable"] is True and body["routed"] == ["wiki", 3000]
    assert "values" not in body and "document" in body
    assert {v["name"] for v in body["variables"]} == {"SERVICE_PASSWORD_DB", "SERVICE_URL_WIKI"}


def test_a_template_the_guard_refuses_exits_1_and_says_why(tmp_path: Path) -> None:
    path = tmp_path / "socket.yaml"
    path.write_text(SOCKET)

    result = invoke(str(path), "--out", str(tmp_path / "out"))

    assert result.exit_code == 1
    assert "Docker socket" in result.output


def test_the_stack_is_published_on_a_free_port_not_on_the_containers_own(
    template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The container's own port is the web server's when it is 80 or 443 and as likely to be another
    application's when it is 3000 or 8080: "address already in use" when the stack started. What
    ``noust create`` gives the next application is what the document is converted with.
    """
    monkeypatch.setattr("noust.cli.commands.compose.default_host_port", lambda: 3017)
    out = tmp_path / "wiki"

    result = invoke(str(template), "--domain", "wiki.example.com", "--out", str(out))

    assert result.exit_code == 0, result.output
    assert "127.0.0.1:${PORT:-3017}:3000" in (out / "docker-compose.yml").read_text()


def test_a_port_the_operator_names_is_the_one_used(
    template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.cli.commands.compose.default_host_port", lambda: 3017)
    out = tmp_path / "wiki"

    invoke(str(template), "--port", "18080", "--out", str(out))

    assert "${PORT:-18080}" in (out / "docker-compose.yml").read_text()


def test_the_api_converts_with_a_free_port_too(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.web.api.compose.default_host_port", lambda: 3017)

    body = world.client.post(
        "/api/compose/import", json={"document": TEMPLATE}, headers=world.as_("admin")
    ).json()

    assert "127.0.0.1:${PORT:-3017}:3000" in body["document"]


def test_the_default_port_is_the_next_one_an_application_would_be_given(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.recipes import coolify

    monkeypatch.setattr(
        "noust.validators.port.is_port_available", lambda port, *a, **k: port != 3000
    )
    assert coolify.default_host_port() == 3001

    world.store.create_app(App(domain="taken.example.com", app_path="/srv/taken", port=3001))
    assert coolify.default_host_port() == 3002


REQUIRING = TEMPLATE.replace(
    "      - DB_PASSWORD=$SERVICE_PASSWORD_DB\n",
    "      - DB_PASSWORD=$SERVICE_PASSWORD_DB\n"
    "      - MAIL_DRIVER=${MAIL_DRIVER:?smtp or log}\n"
    "      - SMTP_HOST=${SMTP_HOST:-}\n",
)


def test_a_variable_the_template_requires_is_said_before_the_deploy_fails_on_it(
    tmp_path: Path,
) -> None:
    """
    Compose refuses to start a stack whose ``${NAME:?}`` has no value, with a message a long way
    from the import. Found by deploying Docmost's template, which requires ``MAIL_DRIVER``.
    """
    source = tmp_path / "requiring.yaml"
    source.write_text(REQUIRING)

    text = invoke(str(source), "--out", str(tmp_path / "stack"))
    as_json = invoke(str(source), "--json")

    assert text.exit_code == 0, text.output
    assert "MAIL_DRIVER has no default (smtp or log)" in text.output
    assert "SMTP_HOST" not in text.output  # it has a default
    needed = json.loads(as_json.output)["required"]
    assert needed == [{"name": "MAIL_DRIVER", "message": "smtp or log"}]


def test_a_template_that_requires_nothing_says_nothing_of_the_kind(template: Path) -> None:
    assert json.loads(invoke(str(template), "--json").output)["required"] == []


def test_a_rehearsal_writes_nothing(template: Path, tmp_path: Path) -> None:
    out = tmp_path / "rehearsed"

    result = CliRunner().invoke(
        root_cli, ["--dry-run", "compose", "import", str(template), "--out", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert not out.exists()


def test_standard_input_is_read_when_the_file_is_a_dash() -> None:
    result = invoke("-", "--domain", "wiki.example.com", "--json", input_text=TEMPLATE)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["deployable"] is True


def test_a_domain_that_is_not_one_is_a_usage_refusal(template: Path) -> None:
    result = invoke(str(template), "--domain", "not a domain")

    assert result.exit_code != 0
