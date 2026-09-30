# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Two 2.2 follow-ups that answered 500 for something that is not the server's fault.

- A backup destination that cannot be reached is the operator's to fix (its
  address, its credentials, its permissions), so testing it answers a 4xx that
  carries rclone's own words, not a 500.
- A directory in ``releases/`` named like a release but not a date
  (``20261399-256199-...``) made the listing raise; it is not a release, and
  nothing lists, activates or prunes it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.releases import ReleaseManager, is_release_id, release_order_key
from noust.managers.backup_scheduler import BackupScheduler
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app as build_app
from noust.web.server import get_token_manager

SFTP_FIELDS = {"host": "nas.example.com", "user": "noust", "pass": "s3cr3t-password-value"}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Give the console a store of its own.

    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Point the scheduler's unit directory into the sandbox.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The directory.
    """
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


@pytest.fixture
def client(tmp_path: Path, store: NoustStore, runner: FakeRunner) -> TestClient:
    """
    Build a signed-in client in sudo mode, with rclone known to the fake runner.

    Args:
        tmp_path: Per-test temporary directory.
        store: The store fixture.
        runner: The fake runner.

    Returns:
        The client.
    """
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
    app: FastAPI = build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))
    token = get_token_manager().generate_master_token()
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    login = signed_in.post("/api/auth/login", json={"token": token})
    signed_in.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    assert signed_in.post("/api/auth/elevate", json={"token": token}).status_code == 200
    return signed_in


# -- the destination test -------------------------------------------------------------


def create_destination(client: TestClient) -> None:
    """
    Create the destination the tests use.

    Args:
        client: The signed-in client.
    """
    response = client.post(
        "/api/backup-destinations", json={"name": "nas", "backend": "sftp", "fields": SFTP_FIELDS}
    )
    assert response.status_code in (200, 201), response.text


def test_an_unreachable_destination_is_a_4xx_with_rclones_words(
    client: TestClient, runner: FakeRunner
) -> None:
    create_destination(client)
    runner.script(
        ("rclone", "mkdir", "nas:wasm-backups"),
        stderr="Failed to create file system for nas:: couldn't connect SSH: dial tcp: i/o timeout",
        exit_code=1,
    )

    response = client.post("/api/backup-destinations/nas/test")

    assert 400 <= response.status_code < 500, response.text
    body = response.json()
    assert body["error"] == "destination_unreachable"
    assert "nas" in body["detail"]
    assert "couldn't connect SSH: dial tcp: i/o timeout" in body["output"]
    assert body["hint"]


def test_a_destination_that_fails_the_listing_is_a_4xx_too(
    client: TestClient, runner: FakeRunner
) -> None:
    create_destination(client)
    runner.script(
        ("rclone", "lsf", "nas:wasm-backups", "--max-depth", "1"),
        stderr="permission denied",
        exit_code=3,
    )

    response = client.post("/api/backup-destinations/nas/test")

    assert 400 <= response.status_code < 500
    assert "permission denied" in response.json()["output"]


def test_no_secret_reaches_the_output_of_a_failed_test(
    client: TestClient, runner: FakeRunner
) -> None:
    create_destination(client)
    runner.script(
        ("rclone", "mkdir", "nas:wasm-backups"),
        stderr="login failed for user noust password s3cr3t-password-value",
        exit_code=1,
    )

    response = client.post("/api/backup-destinations/nas/test")

    assert "s3cr3t-password-value" not in response.text


def test_a_reachable_destination_still_answers_200(client: TestClient, runner: FakeRunner) -> None:
    create_destination(client)
    runner.script(
        ("rclone", "lsf", "nas:wasm-backups", "--max-depth", "1"), stdout="shop-example-com/\n"
    )

    response = client.post("/api/backup-destinations/nas/test")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "entries": ["shop-example-com/"]}


def test_browsing_an_unreachable_destination_is_a_4xx_too(
    client: TestClient, runner: FakeRunner
) -> None:
    create_destination(client)
    runner.script(("rclone", "lsjson"), stderr="connection refused", exit_code=1)

    response = client.get("/api/backup-destinations/nas/backups")

    assert 400 <= response.status_code < 500, response.text
    assert response.json()["error"] == "destination_unreachable"


# -- the release id -------------------------------------------------------------------

VALID = "20260925-120000-aaaaaaa"
NOT_A_DATE = "20261399-256199-aaaaaaa"


@pytest.mark.parametrize(
    "release_id",
    [NOT_A_DATE, "20260230-120000-aaaaaaa", "20260925-246000-aaaaaaa", "20260925-120060-aaaaaaa"],
)
def test_a_name_shaped_like_a_release_that_is_not_a_date_is_not_a_release_id(
    release_id: str,
) -> None:
    assert is_release_id(release_id) is False
    with pytest.raises(ValueError, match="not a release id"):
        release_order_key(release_id)


def test_a_real_release_id_is_still_one() -> None:
    assert is_release_id(VALID) is True
    assert is_release_id("20260925-120000-nogit") is True
    assert is_release_id("20260925-120000-aaaaaaa-2") is True


def test_listing_releases_skips_a_directory_that_is_not_a_date(tmp_path: Path) -> None:
    (tmp_path / "releases" / VALID).mkdir(parents=True)
    (tmp_path / "releases" / NOT_A_DATE).mkdir(parents=True)

    listed = ReleaseManager(tmp_path).list()

    assert [release.id for release in listed] == [VALID]


def test_the_releases_endpoint_answers_200_with_a_directory_that_is_not_a_date(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    root = tmp_path / "apps" / "rel-example-com"
    (root / "releases" / VALID).mkdir(parents=True)
    (root / "releases" / NOT_A_DATE).mkdir(parents=True)
    store.create_app(
        App(
            domain="rel.example.com",
            app_type="nodejs",
            source="https://github.com/you/app",
            port=3000,
            layout="releases",
            app_path=str(root),
        )
    )

    response = client.get("/api/apps/rel.example.com/releases")

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [VALID]


def test_activating_a_name_that_is_not_a_date_is_refused_as_a_bad_request_not_a_500(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    root = tmp_path / "apps" / "rel-example-com"
    (root / "releases" / VALID).mkdir(parents=True)
    (root / "releases" / NOT_A_DATE).mkdir(parents=True)
    store.create_app(
        App(
            domain="rel.example.com",
            app_type="nodejs",
            source="https://github.com/you/app",
            port=3000,
            layout="releases",
            app_path=str(root),
        )
    )

    response = client.post(f"/api/apps/rel.example.com/releases/{NOT_A_DATE}/activate")

    assert response.status_code in (400, 404, 422), response.text
    assert "release" in response.json()["detail"].lower()
