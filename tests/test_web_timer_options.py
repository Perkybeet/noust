# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The time limit and the time zone of a backup schedule over the API (checklist H-01).

An application's schedule takes them in its body and says them back; a value out of range is
refused by the model before anything is written, and a zone the server does not know is refused by
the scheduler's own rule, with nothing written to the units.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_web_backup_schedules import app, client, elevate, systemd_dir  # noqa: F401

UNIT = "noust-backup-shop-example-com"


@pytest.fixture
def zoneinfo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "zoneinfo"
    for name in ("UTC", "Europe/Madrid"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"TZif2")
    monkeypatch.setattr("noust.managers.backup_timer_options.ZONEINFO_DIRS", (root,))


class TestAnApplicationsSchedule:
    def test_the_limit_and_the_zone_are_written_and_said_back(
        self, client: TestClient, systemd_dir: Path, zoneinfo: None
    ) -> None:
        elevate(client)

        response = client.post(
            "/api/backup-schedules",
            json={
                "domain": "shop.example.com",
                "schedule": "daily",
                "max_runtime_seconds": 7200,
                "timezone": "Europe/Madrid",
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()["schedule"]
        assert body["max_runtime_seconds"] == 7200 and body["timezone"] == "Europe/Madrid"
        assert "TimeoutStartSec=7200\n" in (systemd_dir / f"{UNIT}.service").read_text()
        assert "Europe/Madrid" in (systemd_dir / f"{UNIT}.timer").read_text()

    def test_saving_it_again_without_them_clears_them(
        self, client: TestClient, systemd_dir: Path, zoneinfo: None
    ) -> None:
        elevate(client)
        client.post(
            "/api/backup-schedules",
            json={
                "domain": "shop.example.com",
                "schedule": "daily",
                "max_runtime_seconds": 7200,
                "timezone": "UTC",
            },
        )

        response = client.put(
            "/api/backup-schedules/shop.example.com",
            json={"domain": "shop.example.com", "schedule": "daily"},
        )

        assert response.status_code == 200, response.text

        assert "TimeoutStartSec" not in (systemd_dir / f"{UNIT}.service").read_text()

    @pytest.mark.parametrize("seconds", [59, 604801, 0])
    def test_a_limit_out_of_range_is_refused_before_anything_is_written(
        self, seconds: int, client: TestClient, systemd_dir: Path
    ) -> None:
        elevate(client)

        response = client.post(
            "/api/backup-schedules",
            json={
                "domain": "shop.example.com",
                "schedule": "daily",
                "max_runtime_seconds": seconds,
            },
        )

        assert response.status_code == 422
        assert list(systemd_dir.iterdir()) == []

    def test_a_zone_the_server_does_not_know_is_refused_and_writes_nothing(
        self, client: TestClient, systemd_dir: Path, zoneinfo: None
    ) -> None:
        elevate(client)

        response = client.post(
            "/api/backup-schedules",
            json={"domain": "shop.example.com", "schedule": "daily", "timezone": "Mars/Olympus"},
        )

        assert response.status_code in (400, 422)
        assert "time zone" in response.text
        assert list(systemd_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("module", "model"),
    [
        ("noust.web.api.databases.backups", "SetPolicyRequest"),
        ("noust.web.api.volume_backups", "SetVolumePolicyRequest"),
        ("noust.web.api.self_backup", "SetScheduleRequest"),
        ("noust.web.api.backup_schedules", "CreateScheduleRequest"),
    ],
)
def test_every_request_that_sets_a_schedule_takes_the_two_options(module: str, model: str) -> None:
    import importlib

    request = getattr(importlib.import_module(module), model)
    fields = getattr(request, "model_fields", None) or request.__fields__

    assert "max_runtime_seconds" in fields and "timezone" in fields
