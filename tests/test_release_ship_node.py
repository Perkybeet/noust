# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The node's side of a central's build server (checklist A-49): the spool a built bundle waits in,
and the API a central moves it with.

``noust releases bundle`` and ``receive`` are ``test_release_bundles.py``'s; here is what a central
adds: a bundle read by ranges and let go of, an upload that resumes, and two jobs, one that builds
and packs into the spool and one that activates what arrived and never keeps the file.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import deploy_freeze
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.store import App, AppLayout, NoustStore
from noust.deployers.bundle import Bundle, Manifest
from noust.deployers.bundle_spool import (
    BUNDLE_UPLOADS,
    MAX_READ_BYTES,
    SPOOL_TTL,
    BundleSpool,
)
from noust.deployers.release_bundles import Reception
from noust.managers.database.uploads import UploadStore
from noust.web.api import app_release_bundles as api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers
from noust.web.permissions import Permission
from noust.web.permissions.routes_app_bundles import ROUTES

DOMAIN = "shop.example.com"
RELEASE = "20261016-101500-abc1234"
COMMIT = "abc1234" + "0" * 33
PAYLOAD = bytes(range(256)) * 400


def packed(path: Path, data: bytes = PAYLOAD) -> Bundle:
    """What packing a release leaves: the file and what the manifest says."""
    path.write_bytes(data)
    manifest = Manifest(
        format=1,
        app_type="nodejs",
        domain=DOMAIN,
        release_id=RELEASE,
        commit=COMMIT,
        branch="main",
        app_root="/var/www/apps/shop-example-com",
        built_at="2026-10-16T10:15:00+00:00",
        builder="build-1",
        noust_version="3.4.0",
        files=120,
        bytes=len(data) * 3,
    )
    return Bundle(
        path=path, sha256=hashlib.sha256(data).hexdigest(), size=len(data), manifest=manifest
    )


@pytest.fixture
def spool(tmp_path: Path) -> BundleSpool:
    return BundleSpool(root=tmp_path / "spool")


class TestTheSpool:
    def test_a_bundle_waits_and_is_read_by_ranges(self, spool: BundleSpool) -> None:
        bundle_id, destination = spool.reserve()
        spooled = spool.keep(bundle_id, DOMAIN, packed(destination))

        assert spool.get(bundle_id, DOMAIN) == spooled
        assert spooled.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
        assert spooled.release_id == RELEASE and spooled.builder == "build-1"
        assert spool.read(bundle_id, DOMAIN, 0, 10) == PAYLOAD[:10]
        assert spool.read(bundle_id, DOMAIN, 100, 50) == PAYLOAD[100:150]
        # The end of the file is short, and past it there is nothing.
        assert spool.read(bundle_id, DOMAIN, len(PAYLOAD) - 3, 100) == PAYLOAD[-3:]
        assert spool.read(bundle_id, DOMAIN, len(PAYLOAD) + 10, 100) == b""

    def test_it_is_owner_only(self, spool: BundleSpool) -> None:
        bundle_id, destination = spool.reserve()
        spool.keep(bundle_id, DOMAIN, packed(destination))

        assert (spool.root.stat().st_mode & 0o777) == 0o700
        assert ((spool.root / bundle_id).stat().st_mode & 0o777) == 0o700

    def test_another_applications_bundle_is_not_found(self, spool: BundleSpool) -> None:
        bundle_id, destination = spool.reserve()
        spool.keep(bundle_id, DOMAIN, packed(destination))

        with pytest.raises(ValidationError, match="no such bundle"):
            spool.get(bundle_id, "other.example.com")
        with pytest.raises(ValidationError, match="no such bundle"):
            spool.read(bundle_id, "other.example.com", 0, 10)
        assert spool.list("other.example.com") == []

    @pytest.mark.parametrize("bad", ["../etc", "..", "x" * 32, "ABCDEF" * 5 + "AB", "", "a/b"])
    def test_no_request_names_a_path(self, spool: BundleSpool, bad: str) -> None:
        with pytest.raises(ValidationError):
            spool.get(bad, DOMAIN)
        with pytest.raises(ValidationError):
            spool.path(bad, DOMAIN)

    @pytest.mark.parametrize(("offset", "length"), [(-1, 10), (0, 0), (0, MAX_READ_BYTES + 1)])
    def test_a_range_that_is_not_one_is_refused(
        self, spool: BundleSpool, offset: int, length: int
    ) -> None:
        bundle_id, destination = spool.reserve()
        spool.keep(bundle_id, DOMAIN, packed(destination))

        with pytest.raises(ValidationError, match="not a range"):
            spool.read(bundle_id, DOMAIN, offset, length)

    def test_letting_go_removes_it(self, spool: BundleSpool) -> None:
        bundle_id, destination = spool.reserve()
        spool.keep(bundle_id, DOMAIN, packed(destination))

        spool.discard(bundle_id, DOMAIN)

        assert not (spool.root / bundle_id).exists()
        with pytest.raises(ValidationError, match="no such bundle"):
            spool.get(bundle_id, DOMAIN)

    def test_a_place_made_for_a_build_that_failed_is_dropped(self, spool: BundleSpool) -> None:
        bundle_id, _destination = spool.reserve()

        spool.abandon(bundle_id)

        assert not (spool.root / bundle_id).exists()

    def test_a_bundle_nobody_fetched_is_swept_after_a_day(self, spool: BundleSpool) -> None:
        old_id, old_destination = spool.reserve()
        spool.keep(old_id, DOMAIN, packed(old_destination))
        new_id, new_destination = spool.reserve()
        spool.keep(new_id, DOMAIN, packed(new_destination))
        past = time.time() - SPOOL_TTL - 60
        for leaf in [spool.root / old_id, *(spool.root / old_id).iterdir()]:
            os.utime(leaf, (past, past))

        removed = spool.sweep()

        assert removed == [old_id]
        assert [item.id for item in spool.list(DOMAIN)] == [new_id]

    def test_the_listing_is_newest_first_and_only_this_applications(
        self, spool: BundleSpool
    ) -> None:
        ids = []
        for stamp in ("2026-10-16T09:00:00+00:00", "2026-10-16T11:00:00+00:00"):
            bundle_id, destination = spool.reserve()
            bundle = packed(destination)
            bundle = Bundle(
                path=bundle.path,
                sha256=bundle.sha256,
                size=bundle.size,
                manifest=Manifest(**{**bundle.manifest.__dict__, "built_at": stamp}),
            )
            spool.keep(bundle_id, DOMAIN, bundle)
            ids.append(bundle_id)

        assert [item.id for item in spool.list(DOMAIN)] == [ids[1], ids[0]]


# ---------------------------------------------------------------------------- the API


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
            layout=AppLayout.RELEASES.value,
        )
    )
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    made: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> Any:
            made.append(kwargs)
            return SimpleNamespace(
                id=f"job-{len(made)}",
                status=SimpleNamespace(value="pending"),
                to_dict=lambda: {"id": f"job-{len(made)}"},
            )

    monkeypatch.setattr(api, "get_job_manager", lambda: Manager())
    return made


@pytest.fixture
def places(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    spool = BundleSpool(root=tmp_path / "spool")
    uploads = UploadStore(root=tmp_path / "uploads", profile=BUNDLE_UPLOADS)
    monkeypatch.setattr(api, "_spool", lambda: spool)
    monkeypatch.setattr(api, "_uploads", lambda: uploads)
    return SimpleNamespace(spool=spool, uploads=uploads)


@pytest.fixture
def client(store: NoustStore, places: SimpleNamespace) -> TestClient:
    server = FastAPI()
    install_error_handlers(server)
    server.include_router(api.router, prefix="/api/apps")
    server.dependency_overrides[get_current_session] = lambda: {
        "type": "session",
        "sid": "abcdef012345",
        "scope": "admin",
        "account_id": 1,
        "username": "alice",
    }
    return TestClient(server)


def test_every_route_declares_a_deploy_permission() -> None:
    assert set(ROUTES.values()) == {Permission.APPS_DEPLOY}
    declared = {
        (method, "/api/apps" + route.path)
        for route in api.router.routes
        for method in getattr(route, "methods", set())
    }
    assert declared == set(ROUTES)


class TestBuilding:
    def test_building_queues_a_job(self, client: TestClient, jobs: list[dict[str, Any]]) -> None:
        response = client.post(
            f"/api/apps/{DOMAIN}/releases/bundle", json={"commit": "abc1234", "keep": True}
        )

        assert response.status_code == 202, response.text
        assert jobs[0]["func"] is api.bundle_release_job
        assert jobs[0]["kwargs"] == {
            "domain": DOMAIN,
            "commit": "abc1234",
            "release_id": None,
            "keep": True,
        }

    def test_what_is_not_an_application_or_a_release_is_refused(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        assert client.post("/api/apps/gone.example.com/releases/bundle", json={}).status_code == 404
        refused = client.post(
            f"/api/apps/{DOMAIN}/releases/bundle", json={"release_id": "not-a-release"}
        )
        assert refused.status_code == 400 and jobs == []

    def test_the_job_leaves_the_bundle_in_the_spool_and_says_where(
        self, places: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def pack(domain: str, destination: Path, **kwargs: Any) -> Bundle:
            seen.update(domain=domain, kwargs=kwargs)
            return packed(destination)

        monkeypatch.setattr(api, "build_and_pack", pack)

        result = api.bundle_release_job(DOMAIN, commit="abc1234")

        bundle = result["bundle"]
        assert result["status"] == "packed" and seen["domain"] == DOMAIN
        assert seen["kwargs"]["commit"] == "abc1234"
        assert bundle["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
        assert places.spool.read(bundle["id"], DOMAIN, 0, 5) == PAYLOAD[:5]

    def test_a_build_that_fails_leaves_nothing_in_the_spool(
        self, places: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def pack(domain: str, destination: Path, **kwargs: Any) -> Bundle:
            destination.write_bytes(b"half")
            raise DeploymentError("The build failed", details="npm ERR!")

        monkeypatch.setattr(api, "build_and_pack", pack)

        with pytest.raises(DeploymentError):
            api.bundle_release_job(DOMAIN)

        assert places.spool.list(DOMAIN) == []
        assert not any(places.spool.root.iterdir())


class TestFetching:
    def spooled(self, places: SimpleNamespace) -> str:
        bundle_id, destination = places.spool.reserve()
        places.spool.keep(bundle_id, DOMAIN, packed(destination))
        return bundle_id

    def test_the_listing_and_the_description(
        self, client: TestClient, places: SimpleNamespace
    ) -> None:
        bundle_id = self.spooled(places)

        listed = client.get(f"/api/apps/{DOMAIN}/releases/bundles").json()["bundles"]
        described = client.get(f"/api/apps/{DOMAIN}/releases/bundles/{bundle_id}").json()

        assert [item["id"] for item in listed] == [bundle_id]
        assert described["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
        assert described["size"] == len(PAYLOAD) and described["chunk_size"] == 512 * 1024

    def test_ranges_are_raw_bytes(self, client: TestClient, places: SimpleNamespace) -> None:
        bundle_id = self.spooled(places)

        response = client.get(
            f"/api/apps/{DOMAIN}/releases/bundles/{bundle_id}/data",
            params={"offset": 256, "length": 512},
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.content == PAYLOAD[256:768]

    def test_a_range_past_the_limit_is_refused(
        self, client: TestClient, places: SimpleNamespace
    ) -> None:
        bundle_id = self.spooled(places)

        response = client.get(
            f"/api/apps/{DOMAIN}/releases/bundles/{bundle_id}/data",
            params={"offset": 0, "length": MAX_READ_BYTES + 1},
        )

        assert response.status_code == 422

    def test_letting_go(self, client: TestClient, places: SimpleNamespace) -> None:
        bundle_id = self.spooled(places)

        assert client.delete(f"/api/apps/{DOMAIN}/releases/bundles/{bundle_id}").status_code == 204
        assert client.get(f"/api/apps/{DOMAIN}/releases/bundles/{bundle_id}").status_code == 400

    def test_a_bundle_of_a_name_that_is_not_an_id_names_no_path(self, client: TestClient) -> None:
        response = client.get(f"/api/apps/{DOMAIN}/releases/bundles/..%2F..%2Fetc/data?offset=0")
        assert response.status_code in (400, 404)


class TestReceiving:
    def start(self, client: TestClient, data: bytes = PAYLOAD, sha256: str | None = None) -> Any:
        return client.post(
            f"/api/apps/{DOMAIN}/releases/uploads",
            json={"size": len(data), "sha256": sha256 or hashlib.sha256(data).hexdigest()},
        )

    def send(self, client: TestClient, upload_id: str, data: bytes, chunk: int = 1000) -> None:
        for offset in range(0, len(data), chunk):
            sent = client.put(
                f"/api/apps/{DOMAIN}/releases/uploads/{upload_id}",
                params={"offset": offset},
                content=data[offset : offset + chunk],
            )
            assert sent.status_code == 200, sent.text

    def test_a_bundle_arrives_in_chunks_and_is_checked_against_its_digest(
        self, client: TestClient
    ) -> None:
        started = self.start(client)
        assert started.status_code == 201, started.text
        upload = started.json()
        self.send(client, upload["id"], PAYLOAD)

        status = client.get(f"/api/apps/{DOMAIN}/releases/uploads/{upload['id']}").json()
        done = client.post(f"/api/apps/{DOMAIN}/releases/uploads/{upload['id']}/complete")

        assert status["received"] == len(PAYLOAD) and status["complete"] is False
        assert done.status_code == 200 and done.json()["complete"] is True

    def test_a_chunk_at_the_wrong_offset_says_where_to_resume(self, client: TestClient) -> None:
        upload = self.start(client).json()
        client.put(
            f"/api/apps/{DOMAIN}/releases/uploads/{upload['id']}",
            params={"offset": 0},
            content=PAYLOAD[:500],
        )

        response = client.put(
            f"/api/apps/{DOMAIN}/releases/uploads/{upload['id']}",
            params={"offset": 7},
            content=PAYLOAD[7:507],
        )

        assert response.status_code == 409
        assert response.json()["received"] == 500 and response.json()["error"] == "upload_offset"

    def test_a_bundle_whose_digest_is_not_the_declared_one_is_thrown_away(
        self, client: TestClient, places: SimpleNamespace
    ) -> None:
        upload = self.start(client, sha256="0" * 64).json()
        self.send(client, upload["id"], PAYLOAD)

        done = client.post(f"/api/apps/{DOMAIN}/releases/uploads/{upload['id']}/complete")

        assert done.status_code == 400
        assert not (places.uploads.root / upload["id"]).exists()

    def test_the_digest_is_mandatory(self, client: TestClient) -> None:
        response = client.post(
            f"/api/apps/{DOMAIN}/releases/uploads", json={"size": 10, "sha256": ""}
        )
        assert response.status_code == 422

    def test_another_session_cannot_continue_it(
        self, client: TestClient, places: SimpleNamespace
    ) -> None:
        upload = self.start(client).json()

        with pytest.raises(ValidationError, match="no such upload"):
            places.uploads.get(upload["id"], owner="mallory")

    def test_receiving_needs_a_finished_upload(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        upload = self.start(client).json()

        response = client.post(
            f"/api/apps/{DOMAIN}/releases/receive", json={"upload_id": upload["id"]}
        )

        assert response.status_code == 400 and jobs == []

    def test_receiving_queues_a_job_for_a_finished_upload(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        upload = self.start(client).json()
        self.send(client, upload["id"], PAYLOAD)
        client.post(f"/api/apps/{DOMAIN}/releases/uploads/{upload['id']}/complete")

        response = client.post(
            f"/api/apps/{DOMAIN}/releases/receive",
            json={"upload_id": upload["id"], "schema_changed_ok": True},
        )

        assert response.status_code == 202, response.text
        assert jobs[0]["func"] is api.receive_bundle_job
        assert jobs[0]["kwargs"] == {
            "domain": DOMAIN,
            "upload_id": upload["id"],
            "schema_changed_ok": True,
            "frozen_ok": False,
        }

    def test_a_freeze_holds_it_until_a_person_says_so(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        upload = self.start(client).json()
        self.send(client, upload["id"], PAYLOAD)
        client.post(f"/api/apps/{DOMAIN}/releases/uploads/{upload['id']}/complete")
        deploy_freeze.create("incident 412")

        held = client.post(f"/api/apps/{DOMAIN}/releases/receive", json={"upload_id": upload["id"]})
        allowed = client.post(
            f"/api/apps/{DOMAIN}/releases/receive",
            json={"upload_id": upload["id"], "frozen_ok": True},
        )

        assert held.status_code == 409 and held.json()["error"] == "deploys_frozen"
        assert allowed.status_code == 202

    def test_the_job_activates_with_the_declared_digest_and_never_keeps_the_file(
        self, client: TestClient, places: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        upload = self.start(client).json()
        self.send(client, upload["id"], PAYLOAD)
        client.post(f"/api/apps/{DOMAIN}/releases/uploads/{upload['id']}/complete")
        seen: dict[str, Any] = {}

        def fake(domain: str, path: Path, **kwargs: Any) -> Reception:
            seen.update(domain=domain, content=path.read_bytes(), **kwargs)
            return Reception(
                domain=domain,
                release=RELEASE,
                commit=COMMIT,
                deployment_id=9,
                manifest=packed(path.parent / "m.tar.gz").manifest,
            )

        monkeypatch.setattr(api, "receive", fake)

        result = api.receive_bundle_job(DOMAIN, upload["id"], schema_changed_ok=True)

        assert seen["content"] == PAYLOAD
        assert seen["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
        assert seen["schema_changed_ok"] is True
        assert result["release_id"] == RELEASE and result["built_on"] == "build-1"
        assert not (places.uploads.root / upload["id"]).exists()

    def test_a_release_that_does_not_answer_still_removes_the_file(
        self, client: TestClient, places: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        upload = self.start(client).json()
        self.send(client, upload["id"], PAYLOAD)
        client.post(f"/api/apps/{DOMAIN}/releases/uploads/{upload['id']}/complete")

        def refuse(domain: str, path: Path, **kwargs: Any) -> Reception:
            raise DeploymentError("Release did not pass its health check")

        monkeypatch.setattr(api, "receive", refuse)

        with pytest.raises(DeploymentError):
            api.receive_bundle_job(DOMAIN, upload["id"])

        assert not (places.uploads.root / upload["id"]).exists()
