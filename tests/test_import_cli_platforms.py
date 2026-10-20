# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How an operator reaches the CapRover and Dokku importers (checklist N-13).

``tests/test_import_remote_surfaces.py`` pins ``noust import --from coolify`` and the API for
Coolify and Dokploy; the importers themselves are pinned by ``test_importers_caprover.py`` and
``test_importers_dokku.py``. What is tested here is what the command adds: CapRover takes the
dashboard's password where the others take a token (from a file or the environment, never an
argument), Dokku takes no address at all (its server is this machine, or ``--ssh``), the proposal
carries the storage and databases with their commands, and the console's API reads CapRover too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.deployers.importers import caprover
from tests.teams_support import World, build_world
from tests.test_importers_caprover import PASSWORD, server
from tests.test_importers_dokku import runner_for


@pytest.fixture(autouse=True)
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()
    set_runner(None)


@pytest.fixture
def captain(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Point the CapRover importer at a fake server and record the password it was given."""
    seen: list[str] = []
    fake = server()
    real = caprover.connect

    def connect(url: str, token: str, **_k: Any) -> Any:
        seen.append(token)
        return real(url, token, opener=fake)

    monkeypatch.setattr(caprover, "connect", connect)
    return seen


def invoke(*args: str, env: dict[str, str] | None = None) -> Any:
    return CliRunner().invoke(root_cli, list(args), env=env)


@pytest.fixture
def password_file(tmp_path: Path) -> Path:
    path = tmp_path / "password"
    path.write_text(f"{PASSWORD}\nsecond line\n")
    return path


# --------------------------------------------------------------------------- CapRover


def test_caprover_lists_its_applications_with_the_password_from_a_file(
    captain: list[str], password_file: Path
) -> None:
    result = invoke(
        "import",
        "--from",
        "caprover",
        "--url",
        "https://captain.apps.example.com",
        "--token-file",
        str(password_file),
    )

    assert result.exit_code == 0, result.output
    assert "shop" in result.output and "database (postgresql)" in result.output
    assert captain == [PASSWORD]
    assert PASSWORD not in result.output


def test_the_password_may_come_from_the_environment_never_from_an_argument(
    captain: list[str],
) -> None:
    result = invoke(
        "import",
        "--from",
        "caprover",
        "--url",
        "https://captain.apps.example.com",
        env={"NOUST_IMPORT_TOKEN": PASSWORD},
    )

    assert result.exit_code == 0, result.output
    assert captain == [PASSWORD]
    refused = invoke(
        "import", "--from", "caprover", "--url", "https://x.example.com", "--token", PASSWORD
    )
    assert refused.exit_code == 2


def test_one_caprover_application_is_read_with_its_storage_and_databases(
    captain: list[str], password_file: Path
) -> None:
    result = invoke(
        "import",
        "--from",
        "caprover",
        "--url",
        "https://captain.apps.example.com",
        "--token-file",
        str(password_file),
        "--app",
        "shop",
        "--json",
    )

    assert result.exit_code == 0, result.output
    proposal = json.loads(result.output)
    assert proposal["platform"] == "caprover"
    assert proposal["persistent_paths"] == ["uploads"]
    assert {d["name"] for d in proposal["database_details"]} == {"shop-db", "cache"}
    assert proposal["storage"][0]["volume"] == "shop-uploads"
    assert PASSWORD not in result.output
    assert "REPO-PASSWORD" not in result.output


def test_the_text_form_lists_the_storage_and_the_databases(
    captain: list[str], password_file: Path
) -> None:
    result = invoke(
        "import",
        "--from",
        "caprover",
        "--url",
        "https://captain.apps.example.com",
        "--token-file",
        str(password_file),
        "--app",
        "shop",
    )

    assert result.exit_code == 0, result.output
    assert "storage /app/uploads" in result.output
    assert "--persist uploads" in result.output
    assert "database shop-db" in result.output and "srv-captain--shop-db" in result.output


def test_caprover_needs_an_address() -> None:
    result = invoke("import", "--from", "caprover", env={"NOUST_IMPORT_TOKEN": PASSWORD})

    assert result.exit_code == 2
    assert "--url is required for caprover" in result.output


# --------------------------------------------------------------------------- Dokku


def test_dokku_is_asked_on_this_machine_without_an_address() -> None:
    set_runner(runner_for())

    result = invoke("import", "--from", "dokku")

    assert result.exit_code == 0, result.output
    assert "node-js-app" in result.output
    assert "noust import --from dokku --app NAME" in result.output


def test_dokku_over_ssh_says_so_in_the_next_step() -> None:
    fake = FakeRunner()
    fake.script(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "dokku@d.example.com",
            "apps:list",
        ],
        stdout="node-js-app\n",
    )
    set_runner(fake)

    result = invoke("import", "--from", "dokku", "--ssh", "dokku@d.example.com")

    assert result.exit_code == 0, result.output
    assert "noust import --from dokku --ssh dokku@d.example.com --app NAME" in result.output


def test_one_dokku_application_comes_with_its_storage_and_databases_as_json() -> None:
    set_runner(runner_for())

    result = invoke("import", "--from", "dokku", "--app", "node-js-app", "--json")

    assert result.exit_code == 0, result.output
    proposal = json.loads(result.output)
    assert proposal["platform"] == "dokku"
    assert proposal["port"] == 5000
    assert proposal["persistent_paths"] == ["storage"]
    assert [d["engine"] for d in proposal["database_details"]] == ["postgresql", "redis"]


def test_dokku_has_no_url_or_token() -> None:
    result = invoke("import", "--from", "dokku", "--url", "https://x.example.com")

    assert result.exit_code == 2
    assert "dokku has no API" in result.output


def test_ssh_is_for_dokku_only(captain: list[str], password_file: Path) -> None:
    result = invoke(
        "import",
        "--from",
        "caprover",
        "--url",
        "https://captain.apps.example.com",
        "--token-file",
        str(password_file),
        "--ssh",
        "dokku@host.example.com",
    )

    assert result.exit_code == 2
    assert "--ssh is for dokku" in result.output


def test_a_server_that_is_not_a_host_is_refused_before_ssh_is_run() -> None:
    fake = FakeRunner()
    set_runner(fake)

    result = invoke("import", "--from", "dokku", "--ssh", "-oProxyCommand=x")

    assert result.exit_code != 0
    assert fake.calls == []


def test_dokku_is_not_read_from_a_repository(tmp_path: Path) -> None:
    from noust.core.exceptions import ValidationError
    from noust.deployers.importers import read_platform

    with pytest.raises(ValidationError, match="command line"):
        read_platform("dokku", tmp_path)


# --------------------------------------------------------------------------- the API


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def test_the_api_lists_and_proposes_for_caprover(world: World, captain: list[str]) -> None:
    headers = world.as_("admin")
    body = {"platform": "caprover", "url": "https://captain.apps.example.com", "token": PASSWORD}

    listing = world.client.post("/api/import/applications", json=body, headers=headers)

    assert listing.status_code == 200, listing.text
    assert {a["name"] for a in listing.json()["applications"]} >= {"shop", "shop-db"}
    proposal = world.client.post(
        "/api/import/proposal",
        json={**body, "ref": "shop", "domain": "shop.example.com"},
        headers=headers,
    )
    assert proposal.status_code == 200, proposal.text
    detail = proposal.json()["proposal"]
    assert detail["persistent_paths"] == ["uploads"]
    assert [d["name"] for d in detail["database_details"]] == ["shop-db", "cache"]
    assert PASSWORD not in proposal.text
