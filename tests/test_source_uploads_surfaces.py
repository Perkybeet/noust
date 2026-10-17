"""
The ways an operator sends the code of an application as an archive (checklist A-16).

``noust upload`` and ``/api/uploads`` are clients of :mod:`noust.managers.source_uploads`, which
``tests/test_source_uploads.py`` covers; what is pinned here is what each adds: the chunk protocol
over HTTP (the offsets, the 409 that says where to resume, the size of a chunk), who may touch an
upload, the permission each route declares, and what the command prints.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import paths
from noust.core.store import App, NoustStore
from noust.managers.source_uploads import SourceUploads
from noust.validators.source import upload_tree
from noust.web.api.source_uploads import CHUNK_BYTES
from noust.web.permissions import Permission
from noust.web.permissions.routes_uploads import ROUTES
from tests.test_source_uploads import PROJECT, make_tgz, make_zip

DOMAIN = "shop.example.com"


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    monkeypatch.setattr(paths, "state_dir", lambda: root)
    return root


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def client_for(name: str = "alice") -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from noust.web.api import source_uploads as api
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(api.router, prefix="/api/uploads")
    session = {"type": "session", "sid": name, "scope": "admin", "account_id": 1, "username": name}
    app.dependency_overrides[get_current_session] = lambda: session
    return TestClient(app)


def test_every_route_declares_its_permission() -> None:
    assert set(ROUTES.values()) == {Permission.APPS_MANAGE}
    assert set(ROUTES) == {
        ("POST", "/api/uploads"),
        ("GET", "/api/uploads/{upload_id}"),
        ("PUT", "/api/uploads/{upload_id}"),
        ("POST", "/api/uploads/{upload_id}/complete"),
        ("DELETE", "/api/uploads/{upload_id}"),
    }


def upload_all(client: Any, name: str, data: bytes, *, chunk: int = 4096) -> dict[str, Any]:
    started = client.post("/api/uploads", json={"file_name": name, "size": len(data)})
    assert started.status_code == 201, started.text
    upload = started.json()
    for offset in range(0, len(data), chunk):
        sent = client.put(
            f"/api/uploads/{upload['id']}",
            params={"offset": offset},
            content=data[offset : offset + chunk],
        )
        assert sent.status_code == 200, sent.text
    return upload


class TestTheChunkProtocol:
    def test_an_archive_is_sent_and_finished(self, state: Path, store: NoustStore) -> None:
        client = client_for()
        upload = upload_all(client, "shop.zip", make_zip(PROJECT), chunk=64)

        status = client.get(f"/api/uploads/{upload['id']}").json()
        done = client.post(f"/api/uploads/{upload['id']}/complete")

        assert status["received"] == status["size"] and status["complete"] is False
        assert done.status_code == 200, done.text
        body = done.json()
        assert body["source"] == f"upload:{upload['id']}" and body["complete"] is True
        assert body["files"] == 2 and body["chunk_size"] == CHUNK_BYTES
        assert (upload_tree(upload["id"]) / "package.json").is_file()

    def test_the_start_says_how_big_a_chunk_may_be(self, state: Path, store: NoustStore) -> None:
        response = client_for().post("/api/uploads", json={"file_name": "shop.zip", "size": 10})

        assert response.json()["chunk_size"] == CHUNK_BYTES == 512 * 1024
        assert response.json()["received"] == 0

    def test_a_chunk_at_the_wrong_offset_is_a_409_that_says_where_to_resume(
        self, state: Path, store: NoustStore
    ) -> None:
        client = client_for()
        data = make_zip(PROJECT)
        upload = client.post(
            "/api/uploads", json={"file_name": "shop.zip", "size": len(data)}
        ).json()
        client.put(f"/api/uploads/{upload['id']}", params={"offset": 0}, content=data[:40])

        response = client.put(
            f"/api/uploads/{upload['id']}", params={"offset": 7}, content=data[7:47]
        )

        assert response.status_code == 409
        assert response.json()["received"] == 40 and response.json()["error"] == "upload_offset"

    def test_finishing_early_is_a_409_with_the_offset(self, state: Path, store: NoustStore) -> None:
        client = client_for()
        data = make_zip(PROJECT)
        upload = client.post(
            "/api/uploads", json={"file_name": "shop.zip", "size": len(data)}
        ).json()
        client.put(f"/api/uploads/{upload['id']}", params={"offset": 0}, content=data[:40])

        response = client.post(f"/api/uploads/{upload['id']}/complete")

        assert response.status_code == 409
        assert response.json()["error"] == "upload_incomplete" and response.json()["received"] == 40

    def test_a_chunk_past_what_fits_a_request_is_refused(
        self, state: Path, store: NoustStore
    ) -> None:
        client = client_for()
        size = CHUNK_BYTES + 10
        upload = client.post("/api/uploads", json={"file_name": "shop.zip", "size": size}).json()

        response = client.put(
            f"/api/uploads/{upload['id']}", params={"offset": 0}, content=b"x" * (CHUNK_BYTES + 1)
        )

        assert response.status_code == 400
        assert str(CHUNK_BYTES) in response.text

    def test_the_name_of_something_that_is_not_an_archive_is_a_400(
        self, state: Path, store: NoustStore
    ) -> None:
        response = client_for().post("/api/uploads", json={"file_name": "notes.txt", "size": 10})

        assert response.status_code == 400
        assert response.json()["fields"]["file_name"]

    def test_an_unsafe_archive_is_refused_with_the_reason_and_nothing_stays(
        self, state: Path, store: NoustStore
    ) -> None:
        client = client_for()
        upload = upload_all(client, "shop.zip", make_zip({"../escaped.txt": b"x"}))

        response = client.post(f"/api/uploads/{upload['id']}/complete")

        assert response.status_code == 400
        assert not upload_tree(upload["id"]).exists()
        assert client.get(f"/api/uploads/{upload['id']}").status_code == 400

    def test_a_digest_that_does_not_match_is_a_400_naming_the_field(
        self, state: Path, store: NoustStore
    ) -> None:
        client = client_for()
        data = make_zip(PROJECT)
        upload = client.post(
            "/api/uploads", json={"file_name": "shop.zip", "size": len(data), "sha256": "0" * 64}
        ).json()
        client.put(f"/api/uploads/{upload['id']}", params={"offset": 0}, content=data)

        response = client.post(f"/api/uploads/{upload['id']}/complete")

        assert response.status_code == 400
        assert "sha256" in json.dumps(response.json())


class TestWhoMayTouchAnUpload:
    def test_another_account_cannot_read_continue_or_finish_it(
        self, state: Path, store: NoustStore
    ) -> None:
        data = make_zip(PROJECT)
        upload = (
            client_for("alice")
            .post("/api/uploads", json={"file_name": "shop.zip", "size": len(data)})
            .json()
        )
        other = client_for("bob")

        assert other.get(f"/api/uploads/{upload['id']}").status_code == 400
        assert (
            other.put(
                f"/api/uploads/{upload['id']}", params={"offset": 0}, content=data
            ).status_code
            == 400
        )
        assert other.post(f"/api/uploads/{upload['id']}/complete").status_code == 400
        assert other.delete(f"/api/uploads/{upload['id']}").status_code == 400

    def test_an_identifier_that_is_a_path_reaches_nothing(
        self, state: Path, store: NoustStore
    ) -> None:
        client = client_for()

        for identifier in ("..", "not-an-id", "A" * 32):
            assert client.get(f"/api/uploads/{identifier}").status_code in (400, 404)
            assert client.delete(f"/api/uploads/{identifier}").status_code in (400, 404)

    def test_a_session_is_thrown_away_by_its_owner(self, state: Path, store: NoustStore) -> None:
        client = client_for()
        upload = client.post("/api/uploads", json={"file_name": "shop.zip", "size": 10}).json()

        assert client.delete(f"/api/uploads/{upload['id']}").status_code == 204
        assert client.get(f"/api/uploads/{upload['id']}").status_code == 400

    def test_an_extracted_upload_is_removed_by_its_owner_unless_an_application_uses_it(
        self, state: Path, store: NoustStore
    ) -> None:
        client = client_for()
        upload = upload_all(client, "shop.zip", make_zip(PROJECT))
        done = client.post(f"/api/uploads/{upload['id']}/complete").json()
        store.create_app(
            App(domain=DOMAIN, app_type="nodejs", source=done["source"], app_path="/tmp/x")
        )

        refused = client.delete(f"/api/uploads/{upload['id']}")
        assert refused.status_code == 400 and "deploys from that upload" in refused.text
        assert upload_tree(upload["id"]).is_dir()

        store.delete_app(DOMAIN)
        assert client.delete(f"/api/uploads/{upload['id']}").status_code == 204
        assert not upload_tree(upload["id"]).exists()


class TestAnUploadIsNotAPathSomebodyTyped:
    def test_the_privilege_a_directory_needs_is_not_asked_of_an_upload(self, state: Path) -> None:
        from noust.web.api.apps import _is_local_source

        tree = upload_tree("a" * 32)
        tree.mkdir(parents=True)

        assert _is_local_source(f"upload:{'a' * 32}") is False
        assert _is_local_source(str(tree)) is False
        # A directory that only looks like one, and any other, still is the operator's.
        other = paths.uploads_dir() / "somewhere"
        other.mkdir()
        assert _is_local_source(str(other)) is True
        assert _is_local_source(str(paths.uploads_dir() / ("a" * 32) / "..")) is True


@pytest.fixture
def cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from noust.core.config import Config

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()


def invoke(*args: str) -> Any:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli

    return CliRunner().invoke(root_cli, list(args))


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    path = tmp_path / "shop.tar.gz"
    path.write_bytes(make_tgz([("package.json", b"{}"), ("src/a.js", b"1")]))
    return path


class TestTheCommand:
    def test_add_extracts_and_prints_the_source_to_deploy(
        self, state: Path, store: NoustStore, cli_config: None, archive: Path
    ) -> None:
        result = invoke("upload", "add", str(archive))

        assert result.exit_code == 0, result.output
        assert "Extracted shop.tar.gz: 2 files" in result.output
        assert "noust create --source upload:" in result.output

    def test_add_in_json(
        self, state: Path, store: NoustStore, cli_config: None, archive: Path
    ) -> None:
        shown = json.loads(invoke("upload", "add", str(archive), "--json").output)

        assert shown["source"] == f"upload:{shown['id']}" and shown["files"] == 2
        assert shown["file_name"] == "shop.tar.gz" and shown["in_use"] is False
        assert (upload_tree(shown["id"]) / "package.json").is_file()

    def test_a_rehearsal_extracts_nothing(
        self, state: Path, store: NoustStore, cli_config: None, archive: Path
    ) -> None:
        result = invoke("--dry-run", "upload", "add", str(archive))

        assert result.exit_code == 0, result.output
        assert "Would extract" in result.output
        assert SourceUploads(store=store).items() == []

    def test_something_that_is_not_an_archive_is_refused(
        self, state: Path, store: NoustStore, cli_config: None, tmp_path: Path
    ) -> None:
        notes = tmp_path / "notes.txt"
        notes.write_text("hello")

        result = invoke("upload", "add", str(notes))

        assert result.exit_code != 0
        assert "does not look like a code archive" in str(result.exception)

    def test_list_says_which_ones_an_application_uses(
        self, state: Path, store: NoustStore, cli_config: None, archive: Path
    ) -> None:
        used = json.loads(invoke("upload", "add", str(archive), "--json").output)
        free = json.loads(invoke("upload", "add", str(archive), "--json").output)
        store.create_app(
            App(domain=DOMAIN, app_type="nodejs", source=used["source"], app_path="/tmp/x")
        )

        shown = {
            item["id"]: item["in_use"]
            for item in json.loads(invoke("upload", "list", "--json").output)
        }

        assert shown == {used["id"]: True, free["id"]: False}
        words = invoke("upload", "list").output
        assert "in use" in words and "not in use" in words

    def test_list_when_nothing_was_uploaded(
        self, state: Path, store: NoustStore, cli_config: None
    ) -> None:
        assert "Nothing was uploaded" in invoke("upload", "list").output

    def test_remove_deletes_one_and_refuses_one_in_use(
        self, state: Path, store: NoustStore, cli_config: None, archive: Path
    ) -> None:
        used = json.loads(invoke("upload", "add", str(archive), "--json").output)
        free = json.loads(invoke("upload", "add", str(archive), "--json").output)
        store.create_app(
            App(domain=DOMAIN, app_type="nodejs", source=used["source"], app_path="/tmp/x")
        )

        refused = invoke("upload", "remove", used["id"])
        removed = invoke("upload", "remove", free["source"])

        assert refused.exit_code != 0 and "deploys from that upload" in str(refused.exception)
        assert removed.exit_code == 0, removed.output
        assert not upload_tree(free["id"]).exists() and upload_tree(used["id"]).is_dir()

    def test_remove_of_something_that_is_not_an_upload(
        self, state: Path, store: NoustStore, cli_config: None
    ) -> None:
        result = invoke("upload", "remove", "../../etc")

        assert result.exit_code != 0 and "Not an upload" in str(result.exception)

    def test_prune_says_how_many_it_removed(
        self, state: Path, store: NoustStore, cli_config: None
    ) -> None:
        result = invoke("upload", "prune")

        assert result.exit_code == 0 and "Removed 0 unused upload(s)" in result.output
