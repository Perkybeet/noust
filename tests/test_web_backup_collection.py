# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/backup-collection``: what a central takes away from a server and says it holds (item 195).

A central's token holds ``backups.collect`` or it holds nothing here: a ceiling of ``read`` does not
give the data away, and a person's role that only reads does not either. The files are served by what
the list says and never by a path. A central says what it holds in place of what it said before, as
itself and no other.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet import policy
from noust.web.api import backup_collection
from noust.web.auth import FLEET_ACTOR_HEADER, FLEET_ACTOR_PERMISSIONS_HEADER
from noust.web.server import create_app, get_token_manager
from tests.support.backup_collection_support import Disk, sha
from tests.support.fleet_node_security import client_from, fleet_headers
from tests.support.web_auth import make_config

COLLECT = "backups.collect"


@pytest.fixture
def app(sandbox: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    return create_app(make_config(sandbox))


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Disk:
    made = Disk(tmp_path / "copies")
    monkeypatch.setattr(backup_collection, "BackupCatalogue", made.catalogue)
    return made


@pytest.fixture
def token(app: Any) -> str:
    return str(get_token_manager().create_fleet_token("fleet-nas")["token"])


def central(token: str, *permissions: str) -> dict[str, str]:
    """What a central sends, for a call it makes with no operator behind it."""
    return fleet_headers(
        token,
        **{
            FLEET_ACTOR_HEADER: "noust-collect",
            FLEET_ACTOR_PERMISSIONS_HEADER: ",".join(permissions),
        },
    )


class TestWhoMayCollect:
    def test_a_central_that_asks_for_the_permission_lists_the_copies(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        disk.everything()

        response = client_from(app).get("/api/backup-collection", headers=central(token, COLLECT))

        assert response.status_code == 200, response.text
        body = response.json()
        assert {item["kind"] for item in body["items"]} == {"app", "database", "volume", "noust"}
        assert body["fingerprint"] and body["unchanged"] is False

    def test_a_central_that_does_not_ask_for_it_is_refused(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        response = client_from(app).get(
            "/api/backup-collection", headers=central(token, "backups.read")
        )

        assert response.status_code == 403

    def test_a_central_with_no_actor_headers_reads_and_collects_nothing(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        response = client_from(app).get("/api/backup-collection", headers=fleet_headers(token))

        assert response.status_code == 403

    @pytest.mark.parametrize(
        ("level", "allowed"), [("read", False), ("deploy", True), ("admin", True)]
    )
    def test_the_nodes_ceiling_decides(
        self, app: Any, disk: Disk, token: str, level: str, allowed: bool
    ) -> None:
        policy.set_access(policy.FleetAccess(level))  # type: ignore[arg-type]

        response = client_from(app).get("/api/backup-collection", headers=central(token, COLLECT))

        assert (response.status_code == 200) is allowed

    def test_a_ceiling_that_lets_a_central_read_still_says_which_centrals_collect(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        policy.set_access(policy.FleetAccess("read"))

        response = client_from(app).get(
            "/api/backup-collection/held", headers=central(token, "backups.read")
        )

        assert response.status_code == 200

    def test_no_credential_at_all_is_a_401(self, app: Any, disk: Disk) -> None:
        assert client_from(app).get("/api/backup-collection").status_code == 401


class TestTheList:
    def test_an_unknown_kind_is_refused_naming_the_kinds(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        response = client_from(app).get(
            "/api/backup-collection?kinds=app,secrets", headers=central(token, COLLECT)
        )

        assert response.status_code == 400
        assert "secrets" in response.text

    def test_a_kind_can_be_asked_for_alone(self, app: Any, disk: Disk, token: str) -> None:
        disk.everything()

        response = client_from(app).get(
            "/api/backup-collection?kinds=volume", headers=central(token, COLLECT)
        )

        assert {item["kind"] for item in response.json()["items"]} == {"volume"}

    def test_asked_again_with_the_fingerprint_it_says_nothing_changed(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        disk.everything()
        headers = central(token, COLLECT)
        first = client_from(app).get("/api/backup-collection", headers=headers).json()

        again = (
            client_from(app)
            .get(f"/api/backup-collection?known={first['fingerprint']}", headers=headers)
            .json()
        )
        disk.apps.add(stamp="20261111_020000")
        changed = (
            client_from(app)
            .get(f"/api/backup-collection?known={first['fingerprint']}", headers=headers)
            .json()
        )

        assert again["unchanged"] is True and again["items"] == []
        assert changed["unchanged"] is False and len(changed["items"]) == 5

    def test_no_path_of_this_server_is_in_it(
        self, app: Any, disk: Disk, token: str, tmp_path: Path
    ) -> None:
        disk.everything()

        response = client_from(app).get("/api/backup-collection", headers=central(token, COLLECT))

        assert str(tmp_path) not in response.text


class TestReadingAFile:
    def piece(
        self, app: Any, token: str, item: Any, name: str, offset: int, length: int | None = None
    ) -> Any:
        query = f"kind={item.kind}&item={item.id}&name={name}&offset={offset}"
        if length is not None:
            query += f"&length={length}"
        return client_from(app).get(
            f"/api/backup-collection/files?{query}", headers=central(token, COLLECT)
        )

    def test_a_file_comes_in_pieces_that_put_together_are_the_file(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        data = bytes(range(256)) * 40
        meta = disk.apps.add(data=data)
        (item,) = disk.catalogue().build(["app"]).items
        name = f"{meta.id}.tar.gz"
        received = b""

        while len(received) < len(data):
            response = self.piece(app, token, item, name, len(received), 1000)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/octet-stream")
            received += response.content

        assert received == data and sha(received) == item.files[0].sha256

    def test_past_the_end_there_is_nothing_and_it_is_not_an_error(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        meta = disk.apps.add(data=b"abc")
        (item,) = disk.catalogue().build(["app"]).items

        response = self.piece(app, token, item, f"{meta.id}.tar.gz", 10)

        assert response.status_code == 200 and response.content == b""

    def test_a_piece_may_not_be_bigger_than_the_server_allows(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        meta = disk.apps.add()
        (item,) = disk.catalogue().build(["app"]).items

        response = self.piece(
            app, token, item, f"{meta.id}.tar.gz", 0, backup_collection.MAX_PIECE_BYTES + 1
        )

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "name",
        [
            "../etc/passwd",
            "/etc/passwd",
            "..%2F..%2Fetc%2Fpasswd",
            "shop-example-com_20261110_020000.tar.gz%00.json",
            "other.tar.gz",
            "",
        ],
    )
    def test_a_name_the_list_does_not_have_is_not_found_whatever_it_looks_like(
        self, app: Any, disk: Disk, token: str, name: str
    ) -> None:
        disk.apps.add()
        (item,) = disk.catalogue().build(["app"]).items

        response = self.piece(app, token, item, name, 0)

        assert response.status_code in (404, 422), (name, response.status_code)
        assert b"root:" not in response.content

    def test_a_copy_the_list_does_not_have_is_not_found(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        disk.apps.add()
        response = client_from(app).get(
            "/api/backup-collection/files?kind=app&item=nope&name=x&offset=0",
            headers=central(token, COLLECT),
        )

        assert response.status_code == 404

    def test_a_file_that_became_a_link_after_the_list_is_not_followed(
        self, app: Any, disk: Disk, token: str, tmp_path: Path
    ) -> None:
        meta = disk.apps.add()
        (item,) = disk.catalogue().build(["app"]).items
        secret = tmp_path / "shadow"
        secret.write_text("root:x:0:0")
        archive = disk.apps.local_files(meta)[0]
        archive.unlink()
        archive.symlink_to(secret)

        response = self.piece(app, token, item, f"{meta.id}.tar.gz", 0)

        assert response.status_code == 404
        assert b"root:" not in response.content

    def test_the_reading_is_recorded_where_it_begins_and_only_there(
        self, app: Any, disk: Disk, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            audit, "record", lambda event, **kw: recorded.append((event, kw)) or None
        )
        meta = disk.apps.add(data=b"x" * 3000)
        (item,) = disk.catalogue().build(["app"]).items
        name = f"{meta.id}.tar.gz"

        self.piece(app, token, item, name, 0, 1000)
        self.piece(app, token, item, name, 1000, 1000)
        self.piece(app, token, item, name, 2000, 1000)

        reads = [kw for event, kw in recorded if event == "backup.collection.read"]
        assert len(reads) == 1
        assert reads[0]["details"] == {"copy": item.id, "file": name, "size": 3000}


class TestWhatACentralSaysItHolds:
    def say(self, app: Any, token: str, items: list[dict[str, Any]], *permissions: str) -> Any:
        return client_from(app).put(
            "/api/backup-collection/held",
            json={"items": items},
            headers=central(token, *(permissions or (COLLECT,))),
        )

    def test_it_is_recorded_under_the_central_the_token_belongs_to(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        response = self.say(app, token, [{"kind": "app", "id": "shop_1", "sha256": sha(b"a")}])

        assert response.status_code == 200, response.text
        assert response.json()["central"] == "nas" and response.json()["held"] == 1
        listed = (
            client_from(app)
            .get("/api/backup-collection/held", headers=central(token, "backups.read"))
            .json()
        )
        assert [(c["central"], c["held"], c["fresh"]) for c in listed["centrals"]] == [
            ("nas", 1, True)
        ]

    def test_a_body_cannot_name_the_central(self, app: Any, disk: Disk, token: str) -> None:
        response = client_from(app).put(
            "/api/backup-collection/held",
            json={"central": "other", "items": []},
            headers=central(token, COLLECT),
        )

        assert response.status_code == 200 and response.json()["central"] == "nas"

    def test_what_it_says_replaces_what_it_said_before(
        self, app: Any, disk: Disk, token: str
    ) -> None:
        from noust.core.backup_collection_store import CentralHeld

        self.say(app, token, [{"kind": "app", "id": "a"}, {"kind": "app", "id": "b"}])
        self.say(app, token, [{"kind": "app", "id": "b"}])

        assert CentralHeld().holders("app", "a") == []
        assert CentralHeld().holders("app", "b") == ["nas"]

    def test_a_person_with_a_token_of_their_own_does_not_speak_for_a_central(
        self, app: Any, disk: Disk, sandbox: Path
    ) -> None:
        master = get_token_manager()
        own = str(master.create_api_token("someone", "admin")["token"])

        response = client_from(app, ("10.0.0.5", 4000)).put(
            "/api/backup-collection/held",
            json={"items": []},
            headers={"Authorization": f"Bearer {own}"},
        )

        assert response.status_code == 400
        assert "central" in response.text

    @pytest.mark.parametrize(
        "item",
        [
            {"kind": "App", "id": "x"},
            {"kind": "app", "id": ""},
            {"kind": "app", "id": "x\ny"},
            {"kind": "app", "id": "x", "sha256": "nope"},
            {"kind": "../app", "id": "x"},
        ],
    )
    def test_a_copy_that_is_not_one_is_refused_and_nothing_is_recorded(
        self, app: Any, disk: Disk, token: str, item: dict[str, Any]
    ) -> None:
        from noust.core.backup_collection_store import CentralHeld

        response = self.say(app, token, [{"kind": "app", "id": "ok"}, item])

        assert response.status_code == 400
        assert CentralHeld().syncs() == []

    def test_saying_it_needs_the_permission_too(self, app: Any, disk: Disk, token: str) -> None:
        response = self.say(app, token, [], "backups.read")

        assert response.status_code == 403


class TestACopyThatMayNotLeaveUnsealed:
    def test_it_is_listed_with_the_reason_and_its_files_are_refused(
        self, app: Any, disk: Disk, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers.backup_catalogue import BackupCatalogue

        meta = disk.apps.add()
        required = BackupCatalogue(
            apps=disk.apps,  # type: ignore[arg-type]
            volumes=disk.volumes,  # type: ignore[arg-type]
            databases=disk.databases,  # type: ignore[arg-type]
            bundles=disk.bundles,  # type: ignore[arg-type]
            sealing_required=lambda: True,
        )
        monkeypatch.setattr(backup_collection, "BackupCatalogue", lambda: required)
        headers = central(token, COLLECT)

        listed = client_from(app).get("/api/backup-collection", headers=headers).json()
        refused = client_from(app).get(
            f"/api/backup-collection/files?kind=app&item={meta.id}&name={meta.id}.tar.gz&offset=0",
            headers=headers,
        )

        assert listed["items"][0]["blocked"] == "sealing_required"
        assert refused.status_code == 400
        assert "backup encryption" in refused.text
        assert refused.content != b"archive bytes"
