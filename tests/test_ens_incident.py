# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust incident freeze`` and the console lockdown (ENS G17: op.exp.7.r2, op.exp.9).

The package holds the audit log with its verification, journal excerpts, the
configuration without secrets, a store snapshot, running units and listening
sockets, each file hashed in a manifest whose own hash is an audit event (the
copy shipped off the machine anchors the package). The lockdown refuses every
new console session of an account; the master token, the break-glass way in,
still signs in.
"""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.config import Config
from noust.core.ens import incident
from noust.core.ens.incident import IncidentLockdownError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(incident, "record", lambda event, **kw: recorded.append((event, kw)))
    return recorded


@pytest.fixture
def runner() -> FakeRunner:
    fake = FakeRunner()
    fake.script(["journalctl"], stdout="2026-09-30T10:00:00+0000 host noust-web[1]: started\n")
    fake.script(["systemctl", "list-units"], stdout="noust-web.service loaded active running\n")
    fake.script(["ss", "-tulpn"], stdout='tcp LISTEN 0 128 127.0.0.1:8080 users:(("noust"))\n')
    return fake


def freeze(store: NoustStore, runner: FakeRunner, **kwargs: Any) -> incident.FreezeResult:
    audit.record("system.start")
    return incident.freeze(
        reason="INC-42 suspicious sign-ins",
        actor="cli:root",
        runner=runner,
        store=store,
        sessions=lambda: [{"sid": "abc", "account": "maria"}],
        tokens=lambda: [{"id": 1, "name": "ci"}],
        **kwargs,
    )


class TestThePackage:
    def test_it_holds_the_evidence_with_a_manifest(self, store, runner, events) -> None:
        Config().set("monitor.smtp.password", "hunter2hunter2")

        result = freeze(store, runner)

        root = result.directory
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        names = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
        assert "audit/verify.json" in names
        assert any(name.startswith("audit/") and name.endswith(".log") for name in names)
        assert "store/noust.db" in names
        assert "config/config.redacted.json" in names
        assert "system/running-units.txt" in names
        assert "system/listening-sockets.txt" in names
        assert "console/sessions.json" in names and "console/tokens.json" in names
        assert any(name.startswith("journal/") for name in names)
        assert {"manifest.json", "MANIFEST.sha256"} <= names
        assert "hunter2hunter2" not in (root / "config" / "config.redacted.json").read_text()
        for path in root.rglob("*"):
            if path.is_file():
                assert stat.S_IMODE(path.stat().st_mode) == 0o600, path

    def test_every_file_is_in_the_manifest_with_its_sha256(self, store, runner, events) -> None:
        result = freeze(store, runner)

        lines = (result.directory / "MANIFEST.sha256").read_text().splitlines()
        assert lines
        for line in lines:
            digest, name = line.split("  ", 1)
            assert hashlib.sha256((result.directory / name).read_bytes()).hexdigest() == digest
        manifest_bytes = (result.directory / "MANIFEST.sha256").read_bytes()
        assert result.manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()

    def test_the_freeze_is_on_record_with_the_manifests_hash(self, store, runner, events) -> None:
        result = freeze(store, runner)

        name, kwargs = next(item for item in events if item[0] == "incident.freeze")
        assert kwargs["details"]["manifest_sha256"] == result.manifest_sha256
        assert kwargs["details"]["reason"] == "INC-42 suspicious sign-ins"

    def test_a_missing_tool_is_a_problem_not_a_failure(self, store, events) -> None:
        runner = FakeRunner().only_knows("systemctl")

        result = freeze(store, runner)

        assert any("journalctl" in problem for problem in result.problems)
        assert (result.directory / "manifest.json").is_file()


class TestTheLockdown:
    def test_a_freeze_locks_the_console_and_unfreeze_lifts_it(self, store, runner, events) -> None:
        result = freeze(store, runner)

        state = incident.lockdown_state(store)
        assert result.locked and state is not None
        assert state["package"] == str(result.directory)
        assert any(name == "incident.lockdown" for name, _ in events)

        lifted = incident.lift_lockdown(actor="cli:root", reason="INC-42 closed", store=store)

        assert lifted is not None and incident.lockdown_state(store) is None
        assert events[-1][0] == "incident.unfreeze"

    def test_freeze_can_leave_the_console_open(self, store, runner, events) -> None:
        result = freeze(store, runner, lock=False)

        assert not result.locked and incident.lockdown_state(store) is None

    def test_an_account_cannot_sign_in_during_the_lockdown(self, store, runner, events) -> None:
        freeze(store, runner)

        with pytest.raises(IncidentLockdownError) as caught:
            incident.refuse_new_session(account_id=7, client_ip="10.0.0.9", store=store)

        assert "incident" in caught.value.message.lower()
        assert events[-1][0] == "auth.lockdown.denied"

    def test_the_break_glass_token_still_signs_in(self, store, runner, events) -> None:
        freeze(store, runner)

        incident.refuse_new_session(account_id=None, client_ip="10.0.0.9", store=store)

    def test_the_session_chokepoint_applies_it(self, store, runner, events, tmp_path) -> None:
        from noust.web.auth import SecurityConfig, TokenManager

        freeze(store, runner)
        manager = TokenManager(SecurityConfig(state_dir=tmp_path / "web"))
        try:
            with pytest.raises(IncidentLockdownError):
                manager.create_session("10.0.0.9", account_id=3, auth_method="password")
            assert manager.create_session("10.0.0.9").session_id
        finally:
            manager.sessions.close()

    def test_revoking_sessions_is_asked_for(self, store, runner, events) -> None:
        calls: list[str] = []

        result = freeze(store, runner, revoke=lambda: calls.append("revoked") or 3)

        assert calls == ["revoked"] and result.sessions_revoked == 3

    def test_the_lockdown_file_is_owner_only(self, store, runner, events) -> None:
        freeze(store, runner)

        path = incident.lockdown_path(store)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert json.loads(path.read_text())["reason"] == "INC-42 suspicious sign-ins"
