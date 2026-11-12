# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/backup-collection/returns`` and ``/restore``: a copy the central holds comes back (item 195).

The central gives a copy back as an upload that resumes: a chunk at the offset the server holds,
the digest checked when it completes, only the one that started it may go on. Restoring what arrived
is ``backups.manage`` with sudo mode (the central vouches for it), is held to the server's ceiling
(``admin``), and goes through the manager of the kind of copy it is.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet import policy
from noust.managers.backup_return import ReturnRequest
from noust.managers.database.uploads import UploadStore
from noust.web.api import backup_returns
from noust.web.auth import (
    FLEET_ACTOR_HEADER,
    FLEET_ACTOR_PERMISSIONS_HEADER,
    FLEET_ELEVATED_HEADER,
)
from noust.web.server import create_app, get_token_manager
from tests.support.fleet_node_security import client_from, fleet_headers
from tests.support.web_auth import make_config

DATA = bytes(range(256)) * 20
SHA = hashlib.sha256(DATA).hexdigest()
BASE = "/api/backup-collection"


@pytest.fixture
def app(sandbox: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    return create_app(make_config(sandbox))


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(
        backup_returns,
        "_returns",
        lambda: UploadStore(root=tmp_path / "returned", profile=backup_returns.RETURNS),
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def token(app: Any) -> str:
    return str(get_token_manager().create_fleet_token("fleet-nas")["token"])


def central(token: str, *, actor: str = "maria", elevated: bool = False) -> dict[str, str]:
    extra = {
        FLEET_ACTOR_HEADER: actor,
        FLEET_ACTOR_PERMISSIONS_HEADER: "backups.manage",
    }
    if elevated:
        extra[FLEET_ELEVATED_HEADER] = "1"
    return fleet_headers(token, **extra)


def start(app: Any, token: str, **kw: Any) -> dict[str, Any]:
    response = client_from(app).post(
        f"{BASE}/returns",
        json={"size": len(DATA), "sha256": SHA, "file_name": "shop_1.tar.gz"},
        headers=central(token, **kw),
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def send(app: Any, token: str, upload: str, offset: int, data: bytes, **kw: Any) -> Any:
    return client_from(app).put(
        f"{BASE}/returns/{upload}",
        params={"offset": offset},
        content=data,
        headers=central(token, **kw),
    )


class TestTheUpload:
    def test_a_copy_arrives_in_chunks_and_completes_when_the_digest_matches(
        self, app: Any, token: str
    ) -> None:
        upload = start(app, token)

        for offset in range(0, len(DATA), 2000):
            response = send(app, token, upload["id"], offset, DATA[offset : offset + 2000])
            assert response.status_code == 200, response.text
        done = client_from(app).post(
            f"{BASE}/returns/{upload['id']}/complete", headers=central(token)
        )

        assert done.status_code == 200 and done.json()["complete"] is True
        assert done.json()["received"] == len(DATA)

    def test_a_chunk_at_the_wrong_offset_says_where_to_resume(self, app: Any, token: str) -> None:
        upload = start(app, token)
        send(app, token, upload["id"], 0, DATA[:1000])

        response = send(app, token, upload["id"], 5000, DATA[5000:6000])

        assert response.status_code == 409 and response.json()["received"] == 1000

    def test_a_chunk_resent_after_a_failure_never_writes_twice(self, app: Any, token: str) -> None:
        upload = start(app, token)
        send(app, token, upload["id"], 0, DATA[:1000])

        again = send(app, token, upload["id"], 0, DATA[:1000])

        assert again.status_code == 409
        status = (
            client_from(app).get(f"{BASE}/returns/{upload['id']}", headers=central(token)).json()
        )
        assert status["received"] == 1000

    def test_a_digest_that_does_not_match_is_refused_and_the_data_is_gone(
        self, app: Any, token: str
    ) -> None:
        upload = start(app, token)
        send(app, token, upload["id"], 0, b"\x00" * len(DATA))

        response = client_from(app).post(
            f"{BASE}/returns/{upload['id']}/complete", headers=central(token)
        )

        assert response.status_code == 400

    def test_an_upload_that_is_not_all_there_cannot_be_closed(self, app: Any, token: str) -> None:
        upload = start(app, token)
        send(app, token, upload["id"], 0, DATA[:1000])

        response = client_from(app).post(
            f"{BASE}/returns/{upload['id']}/complete", headers=central(token)
        )

        assert response.status_code == 409 and response.json()["error"] == "upload_incomplete"

    def test_only_the_one_that_started_it_may_go_on(self, app: Any, token: str) -> None:
        upload = start(app, token, actor="maria")

        response = send(app, token, upload["id"], 0, DATA[:10], actor="mallory")

        assert response.status_code in (400, 404)

    @pytest.mark.parametrize("name", ["../x.tar.gz", "a/b.tar.gz", "x.exe", "x"])
    def test_a_name_that_is_not_a_plain_file_name_of_a_copy_is_refused(
        self, app: Any, token: str, name: str
    ) -> None:
        response = client_from(app).post(
            f"{BASE}/returns",
            json={"size": 10, "sha256": SHA, "file_name": name},
            headers=central(token),
        )

        assert response.status_code == 400

    def test_a_chunk_bigger_than_the_server_takes_is_refused(self, app: Any, token: str) -> None:
        upload = start(app, token)

        response = send(app, token, upload["id"], 0, b"x" * (backup_returns.CHUNK_BYTES + 1))

        assert response.status_code == 400

    def test_it_can_be_thrown_away(self, app: Any, token: str) -> None:
        upload = start(app, token)

        gone = client_from(app).delete(f"{BASE}/returns/{upload['id']}", headers=central(token))

        assert gone.status_code == 204
        assert client_from(app).get(
            f"{BASE}/returns/{upload['id']}", headers=central(token)
        ).status_code in (400, 404)


class TestWhoMayGiveACopyBack:
    @pytest.mark.parametrize(
        ("level", "allowed"), [("read", False), ("deploy", False), ("admin", True)]
    )
    def test_the_nodes_ceiling_decides_since_a_restore_replaces_data(
        self, app: Any, token: str, level: str, allowed: bool
    ) -> None:
        policy.set_access(policy.FleetAccess(level))  # type: ignore[arg-type]

        response = client_from(app).post(
            f"{BASE}/returns",
            json={"size": 10, "sha256": SHA, "file_name": "a.tar.gz"},
            headers=central(token),
        )

        assert (response.status_code == 201) is allowed

    def test_a_central_that_does_not_ask_for_the_permission_is_refused(
        self, app: Any, token: str
    ) -> None:
        response = client_from(app).post(
            f"{BASE}/returns",
            json={"size": 10, "sha256": SHA, "file_name": "a.tar.gz"},
            headers=fleet_headers(
                token,
                **{FLEET_ACTOR_HEADER: "maria", FLEET_ACTOR_PERMISSIONS_HEADER: "backups.collect"},
            ),
        )

        assert response.status_code == 403


class TestRestoringWhatArrived:
    def finished(self, app: Any, token: str) -> str:
        upload = start(app, token)
        send(app, token, upload["id"], 0, DATA[:2000])
        send(app, token, upload["id"], 2000, DATA[2000:])
        client_from(app).post(f"{BASE}/returns/{upload['id']}/complete", headers=central(token))
        return str(upload["id"])

    def test_it_needs_sudo_mode_which_the_central_vouches_for(self, app: Any, token: str) -> None:
        upload = self.finished(app, token)

        refused = client_from(app).post(
            f"{BASE}/restore",
            json={"upload_id": upload, "kind": "app", "meta": {"domain": "shop.example.com"}},
            headers=central(token),
        )

        assert refused.status_code == 403 and "elevation" in refused.text

    def test_with_it_a_job_is_queued(
        self, app: Any, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        upload = self.finished(app, token)
        queued: list[dict[str, Any]] = []

        class Jobs:
            def create_job(self, **kw: Any) -> Any:
                queued.append(kw)
                job = type(
                    "Job", (), {"id": "j1", "status": type("S", (), {"value": "pending"})()}
                )()
                job.to_dict = lambda: {"id": "j1"}  # type: ignore[attr-defined]
                return job

        monkeypatch.setattr(backup_returns, "get_job_manager", lambda: Jobs())

        response = client_from(app).post(
            f"{BASE}/restore",
            json={
                "upload_id": upload,
                "kind": "app",
                "meta": {"domain": "shop.example.com"},
                "options": {"restore_env": False},
            },
            headers=central(token, elevated=True),
        )

        assert response.status_code == 202, response.text
        (call,) = queued
        assert call["kwargs"]["kind"] == "app" and call["kwargs"]["options"] == {
            "restore_env": False
        }
        assert call["metadata"]["domain"] == "shop.example.com"

    def test_an_upload_that_is_not_finished_is_not_restored(self, app: Any, token: str) -> None:
        upload = start(app, token)

        response = client_from(app).post(
            f"{BASE}/restore",
            json={"upload_id": upload["id"], "kind": "app"},
            headers=central(token, elevated=True),
        )

        assert response.status_code == 400 and "finished arriving" in response.text

    def test_a_kind_nobody_knows_is_refused(self, app: Any, token: str) -> None:
        upload = self.finished(app, token)

        response = client_from(app).post(
            f"{BASE}/restore",
            json={"upload_id": upload, "kind": "secrets"},
            headers=central(token, elevated=True),
        )

        assert response.status_code == 400


class TestTheJob:
    def test_the_file_goes_to_the_manager_of_its_kind_and_is_let_go_whatever_happens(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        returned = UploadStore(root=tmp_path / "returned", profile=backup_returns.RETURNS)
        monkeypatch.setattr(backup_returns, "_returns", lambda: returned)
        upload = returned.create("shop_1.tar.gz", len(DATA), owner="maria", sha256=SHA)
        returned.append(upload.id, 0, DATA, owner="maria")
        returned.complete(upload.id, owner="maria")
        seen: list[ReturnRequest] = []

        class Boom:
            def restore(self, request: ReturnRequest) -> dict[str, Any]:
                seen.append(request)
                assert request.path.read_bytes() == DATA
                raise RuntimeError("the restore failed")

        monkeypatch.setattr(backup_returns, "BackupReturn", Boom)

        with pytest.raises(RuntimeError):
            backup_returns.return_restore_job(
                upload.id, "app", meta={"domain": "shop.example.com"}, owner="maria"
            )

        assert seen[0].sha256 == SHA and seen[0].file_name == "shop_1.tar.gz"
        assert not (tmp_path / "returned" / upload.id).exists()
