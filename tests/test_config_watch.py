# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A change made to ``config.yaml`` by another process reaches the console that is running.

``noust compliance enable``, ``noust config set`` and every other command write the file from
a process of their own. The console read it once, when it started, and kept that copy: a
control a person turned off at the terminal (``restricted_tokens``, in the fleet's case) kept
refusing until ``systemctl restart noust-web``. What is pinned: a change of the file is read
again by the next request that comes after the check interval; an unchanged file is never read
again; the file is looked at at most once per interval, however many requests arrive, and by
nothing but a request (no thread polls); and a file that cannot be read leaves the settings
the console already has.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.accounts import AuthPolicy, passwords
from noust.core.compliance import resolve
from noust.core.config import Config
from noust.web import config_watch
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.config_watch import ConfigWatch
from noust.web.server import create_app, get_token_manager


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestTheWatch:
    @pytest.fixture
    def file(self, tmp_path: Path) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text("security: {frameworks: []}\n")
        return path

    def watch(self, file: Path, clock: Clock, reloads: list[str], interval: float = 1.0) -> Any:
        return ConfigWatch(
            lambda: file, lambda: reloads.append("reload"), interval=interval, clock=clock
        )

    def test_a_file_that_did_not_change_is_not_read_again(self, file: Path) -> None:
        clock, reloads = Clock(), []
        watch = self.watch(file, clock, reloads)

        for _ in range(5):
            clock.now += 2
            assert watch.check() is False

        assert reloads == []

    def test_a_file_that_changed_is_read_again_once(self, file: Path) -> None:
        clock, reloads = Clock(), []
        watch = self.watch(file, clock, reloads)
        file.write_text("security: {frameworks: [nis2]}\n")

        clock.now += 2
        assert watch.check() is True
        clock.now += 2
        assert watch.check() is False

        assert reloads == ["reload"]

    def test_a_replaced_file_of_the_same_size_and_time_is_still_a_change(self, file: Path) -> None:
        """Writers replace the file through a rename: a new inode, whatever the clock says."""
        import os

        clock, reloads = Clock(), []
        watch = self.watch(file, clock, reloads)
        before = file.stat()
        replacement = file.with_name("config.yaml.new")
        replacement.write_text(file.read_text())
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(file)

        clock.now += 2

        assert watch.check() is True

    def test_it_looks_at_the_file_once_per_interval_however_many_ask(self, file: Path) -> None:
        clock, reloads = Clock(), []
        looked: list[Path] = []
        watch = ConfigWatch(
            lambda: file,
            lambda: reloads.append("reload"),
            interval=60.0,
            clock=clock,
            stat=lambda path: looked.append(path) or path.stat(),
        )
        looked.clear()  # the look at construction is what the process loaded
        file.write_text("security: {frameworks: [nis2]}\n")

        for _ in range(1000):
            watch.check()
        assert looked == [] and reloads == []

        clock.now += 61
        for _ in range(1000):
            watch.check()

        assert len(looked) == 1 and reloads == ["reload"]

    def test_a_change_inside_the_interval_waits_for_the_next_one(self, file: Path) -> None:
        clock, reloads = Clock(), []
        watch = self.watch(file, clock, reloads, interval=10.0)
        clock.now += 11
        watch.check()
        file.write_text("security: {frameworks: [nis2]}\n")

        clock.now += 5
        assert watch.check() is False
        clock.now += 6
        assert watch.check() is True

    def test_a_file_that_disappears_is_a_change_and_one_that_returns_is_another(
        self, file: Path
    ) -> None:
        clock, reloads = Clock(), []
        watch = self.watch(file, clock, reloads)
        file.unlink()

        clock.now += 2
        assert watch.check() is True
        clock.now += 2
        assert watch.check() is False
        file.write_text("security: {frameworks: [nis2]}\n")
        clock.now += 2
        assert watch.check() is True

    def test_a_file_that_cannot_be_read_leaves_what_the_console_has(
        self, file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = Clock()

        def broken() -> None:
            raise OSError("permission denied")

        watch = ConfigWatch(lambda: file, broken, interval=1.0, clock=clock)
        file.write_text("security: {frameworks: [nis2]}\n")
        clock.now += 2

        assert watch.check() is False
        assert "permission denied" in caplog.text

    def test_it_is_not_a_thread(self) -> None:
        import threading

        before = threading.active_count()

        ConfigWatch(lambda: Path("/nonexistent"), lambda: None)

        assert threading.active_count() == before


PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


class TestTheRunningConsole:
    """The case that was reported: a control changed at the terminal while the console runs."""

    @pytest.fixture
    def console(
        self, sandbox: Path, real_config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> TestClient:
        # No wait between two requests: the interval is what the unit tests above pin.
        monkeypatch.setattr(config_watch, "CHECK_INTERVAL_SECONDS", 0.0)
        real_config.set("security.frameworks", [])
        assert real_config.save() is True
        app = create_app(
            SecurityConfig(
                state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
            )
        )
        manager = get_token_manager().accounts
        account = manager.create("maria", "admin", password=PASSWORD)
        secret = manager.begin_totp(account.id)
        codes = manager.confirm_totp(account.id, totp.totp_now(secret))
        assert codes is not None
        client = TestClient(app, client=("testclient", 50000))
        response = client.post(
            "/api/auth/login",
            json={"username": "maria", "password": PASSWORD, "totp_code": codes.pop()},
        )
        assert response.status_code == 200, response.text
        client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
        return client

    def written_elsewhere(self, real_config: Config, frameworks: list[str]) -> None:
        """What `noust compliance enable` does from its own process: rewrite the file."""
        path = Path(str(real_config.path))
        document: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
        document.setdefault("security", {})["frameworks"] = frameworks
        path.write_text(yaml.safe_dump(document))

    def enabled(self, console: TestClient) -> list[str]:
        return [
            f["id"] for f in console.get("/api/compliance").json()["frameworks"] if f["enabled"]
        ]

    def test_a_framework_turned_on_at_the_terminal_is_on_in_the_console(
        self, console: TestClient, real_config: Config
    ) -> None:
        assert self.enabled(console) == []

        self.written_elsewhere(real_config, ["nis2"])

        assert self.enabled(console) == ["nis2"]
        assert resolve.enabled_ids() == ["nis2"]

    def test_and_off_again(self, console: TestClient, real_config: Config) -> None:
        self.written_elsewhere(real_config, ["nis2"])
        assert self.enabled(console) == ["nis2"]

        self.written_elsewhere(real_config, [])

        assert self.enabled(console) == []

    def test_a_control_the_terminal_turned_off_stops_refusing_without_a_restart(
        self, console: TestClient, real_config: Config
    ) -> None:
        self.written_elsewhere(real_config, ["ens-media"])
        controls = console.get("/api/compliance").json()["controls"]
        assert next(c for c in controls if c["id"] == "restricted_tokens")["enabled"] is True

        self.written_elsewhere(real_config, [])

        controls = console.get("/api/compliance").json()["controls"]
        assert next(c for c in controls if c["id"] == "restricted_tokens")["enabled"] is False
        assert get_token_manager().policy().requires("restricted_tokens") is False
