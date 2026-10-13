# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The files of an application through the API and the command line (checklist B-55).

``tests/test_app_files.py`` pins what the manager does to the files; here, how it
travels: every route asks for the shell-as-the-application permission, anything
that reads or changes content asks for sudo mode too, an upload is the request's
body and is bounded, a download is the file byte for byte, and the command line is a
client of the same manager.
"""

# The fixtures of the manager's tests are reused, not copied.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.store import NoustStore
from noust.core.teams import ScopedStore
from noust.managers import app_files
from noust.web.api import app_files as files_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from tests.test_app_files import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    TreeRunner,
    app,
    root,
    runner,
    store,
)


@pytest.fixture(autouse=True)
def tree(
    app: Any, runner: TreeRunner, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """The manager runs over the real tree, and only root can enter its staging directory."""
    monkeypatch.setattr(app_files, "get_runner", lambda: runner)
    monkeypatch.setattr(files_api, "scoped_store", lambda: ScopedStore(store=store))
    monkeypatch.setattr(app_files, "get_store", lambda: store)
    monkeypatch.setattr(app_files.paths, "state_dir", lambda: tmp_path / "state")
    yield


def client(*, elevated: bool = True) -> TestClient:
    api = FastAPI()
    install_error_handlers(api)
    api.include_router(files_api.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    api.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    api.dependency_overrides[require_elevated] = elevation
    return TestClient(api)


def test_a_directory_is_listed_without_sudo_mode(root: Path) -> None:
    body = client(elevated=False).get(f"/api/apps/{DOMAIN}/files", params={"path": "shared"}).json()

    assert body["path"] == "shared"
    assert [entry["name"] for entry in body["entries"]][:1] == ["uploads"]
    assert body["limits"] == {
        "edit": app_files.EDIT_MAX_BYTES,
        "upload": app_files.UPLOAD_MAX_BYTES,
    }


def test_the_application_directory_is_the_default_and_a_link_is_refused(root: Path) -> None:
    ok = client(elevated=False).get(f"/api/apps/{DOMAIN}/files")
    through_a_link = client(elevated=False).get(
        f"/api/apps/{DOMAIN}/files", params={"path": "current"}
    )

    assert ok.status_code == 200
    assert through_a_link.status_code == 400
    assert "is a link" in through_a_link.text


def test_a_path_that_climbs_is_refused_naming_the_field(root: Path) -> None:
    response = client(elevated=False).get(f"/api/apps/{DOMAIN}/files", params={"path": "../../etc"})

    assert response.status_code == 400
    assert "path" in response.json()["fields"]


def test_an_unknown_application_reads_as_absent() -> None:
    assert client().get("/api/apps/nobody.example.com/files").status_code == 404


def test_a_text_file_opens_and_needs_sudo_mode() -> None:
    refused = client(elevated=False).get(
        f"/api/apps/{DOMAIN}/files/content", params={"path": "shared/notes.txt"}
    )
    opened = client().get(f"/api/apps/{DOMAIN}/files/content", params={"path": "shared/notes.txt"})

    assert refused.status_code == 403
    assert opened.json() == {
        "domain": DOMAIN,
        "path": "shared/notes.txt",
        "size": 6,
        "content": "hello\n",
    }


def test_the_environment_is_not_opened_here(root: Path) -> None:
    response = client().get(f"/api/apps/{DOMAIN}/files/content", params={"path": "shared/.env"})

    assert response.status_code == 400 and "Environment" in response.text


def test_a_file_is_downloaded_byte_for_byte_with_its_name(root: Path) -> None:
    response = client().get(
        f"/api/apps/{DOMAIN}/files/download", params={"path": "shared/uploads/logo.png"}
    )

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x00binary"
    assert 'filename="logo.png"' in response.headers["content-disposition"]


def test_the_copy_made_for_a_download_does_not_stay_on_disk(root: Path, tmp_path: Path) -> None:
    client().get(f"/api/apps/{DOMAIN}/files/download", params={"path": "shared/notes.txt"})
    client().get(f"/api/apps/{DOMAIN}/files/download", params={"path": "shared/ghost.txt"})

    assert list((tmp_path / "state" / "file-staging").iterdir()) == []


def test_an_edit_is_saved_and_needs_sudo_mode(root: Path) -> None:
    body = {"path": "shared/notes.txt", "content": "edited\n"}

    refused = client(elevated=False).put(f"/api/apps/{DOMAIN}/files/content", json=body)
    saved = client().put(f"/api/apps/{DOMAIN}/files/content", json=body)

    assert refused.status_code == 403
    assert saved.json()["bytes"] == 7
    assert (root / "shared" / "notes.txt").read_text() == "edited\n"


def test_an_upload_is_the_body_of_the_request(root: Path) -> None:
    response = client().put(
        f"/api/apps/{DOMAIN}/files/upload",
        params={"path": "shared/data.bin"},
        content=b"\x00\x01\x02",
    )

    assert response.json()["bytes"] == 3
    assert (root / "shared" / "data.bin").read_bytes() == b"\x00\x01\x02"


def test_an_upload_does_not_replace_a_file_unless_asked(root: Path) -> None:
    refused = client().put(
        f"/api/apps/{DOMAIN}/files/upload", params={"path": "shared/notes.txt"}, content=b"x"
    )
    replaced = client().put(
        f"/api/apps/{DOMAIN}/files/upload",
        params={"path": "shared/notes.txt", "overwrite": "true"},
        content=b"x",
    )

    assert refused.status_code == 400 and "exists already" in refused.text
    assert replaced.status_code == 200
    assert (root / "shared" / "notes.txt").read_bytes() == b"x"


def test_an_upload_larger_than_the_limit_is_refused_before_it_is_written(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_files, "UPLOAD_MAX_BYTES", 4)

    response = client().put(
        f"/api/apps/{DOMAIN}/files/upload", params={"path": "shared/big.bin"}, content=b"0123456789"
    )

    assert response.status_code == 400 and "uploads up to" in response.text
    assert not (root / "shared" / "big.bin").exists()


def test_a_directory_is_made_and_something_is_moved_and_deleted(root: Path) -> None:
    made = client().post(f"/api/apps/{DOMAIN}/files/directory", json={"path": "shared/exports"})
    moved = client().post(
        f"/api/apps/{DOMAIN}/files/move",
        json={"source": "shared/notes.txt", "destination": "shared/exports/notes.txt"},
    )
    deleted = client().delete(
        f"/api/apps/{DOMAIN}/files", params={"path": "shared/exports", "recursive": "true"}
    )

    assert made.status_code == 201 and moved.status_code == 200 and deleted.status_code == 200
    assert not (root / "shared" / "exports").exists()


def test_every_route_asks_for_the_shell_as_the_application_permission() -> None:
    from noust.web.permissions.routes_app_files import ROUTES

    assert len(ROUTES) == 8
    assert set(ROUTES.values()) == {"apps.terminal"}


def test_a_stack_has_no_files_to_browse(store: NoustStore) -> None:
    from noust.core.store import App

    store.create_app(App(domain="stack.example.com", app_type="docker-compose", app_path="/srv/s"))

    response = client().get("/api/apps/stack.example.com/files")

    assert response.status_code == 400 and "containers" in response.text


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_the_command_lists_as_json(root: Path) -> None:
    result = CliRunner().invoke(root_cli, ["--json", "app", "files", "list", DOMAIN, "shared"])

    assert result.exit_code == 0, result.output
    names = [entry["name"] for entry in json.loads(result.output)["entries"]]
    assert "notes.txt" in names and ".env" in names


def test_the_command_copies_a_file_out_and_in(root: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    got = CliRunner().invoke(
        root_cli, ["app", "files", "get", DOMAIN, "shared/notes.txt", str(out)]
    )
    source = tmp_path / "in.txt"
    source.write_text("from the command line\n")
    put = CliRunner().invoke(
        root_cli, ["app", "files", "put", DOMAIN, str(source), "shared/in.txt"]
    )

    assert got.exit_code == 0, got.output
    assert out.read_text() == "hello\n"
    assert put.exit_code == 0, put.output
    assert (root / "shared" / "in.txt").read_text() == "from the command line\n"


def test_the_command_does_not_replace_a_file_unless_asked(root: Path, tmp_path: Path) -> None:
    source = tmp_path / "in.txt"
    source.write_text("x")

    refused = CliRunner().invoke(
        root_cli, ["app", "files", "put", DOMAIN, str(source), "shared/notes.txt"]
    )
    replaced = CliRunner().invoke(
        root_cli, ["app", "files", "put", DOMAIN, str(source), "shared/notes.txt", "--replace"]
    )

    assert refused.exit_code != 0
    assert replaced.exit_code == 0, replaced.output


def test_the_command_asks_before_deleting(root: Path) -> None:
    no = CliRunner().invoke(
        root_cli, ["app", "files", "remove", DOMAIN, "shared/notes.txt"], input="n\n"
    )
    yes = CliRunner().invoke(
        root_cli, ["app", "files", "remove", DOMAIN, "shared/notes.txt", "--yes"]
    )

    assert no.exit_code != 0 and (root / "shared").exists()
    assert yes.exit_code == 0, yes.output
    assert not os.path.lexists(root / "shared" / "notes.txt")
