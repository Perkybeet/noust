# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a change does when the audit trail cannot be written (I-40).

Outside the ENS profile the answer is the operator's (``audit.on_failure``:
warn, the default, makes the change and is loud; refuse stops it); under the
profile it is always refuse, because no change may go unaudited (op.exp.8).
The two doors changes come through, the API's middleware and the command
line's audit wrapper, both ask :mod:`noust.core.audit.failclosed`; reading,
signing in and the commands that repair the trail stay open.
"""

from __future__ import annotations

import os
from collections import namedtuple
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli import audit_policy
from noust.cli.app import Context, NoustGroup
from noust.core import audit
from noust.core.audit import failclosed, get_log, status
from noust.core.config import Config
from noust.core.exceptions import ConfigError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager


class Stand:
    """A configuration that answers from a dict, as the profile code tolerates."""

    def __init__(self, **values: Any) -> None:
        self.values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


@pytest.fixture(autouse=True)
def isolated_config(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The configuration file is the test's own, and forgotten when it ends."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()


def refuse_changes() -> None:
    """Set the policy the way an operator would."""
    Config().set("audit.on_failure", "refuse")


class TestThePolicy:
    def test_the_default_is_to_warn(self) -> None:
        assert failclosed.mode(Stand()) == failclosed.MODE_WARN

    def test_an_operator_can_choose_to_refuse(self) -> None:
        assert failclosed.mode(Stand(**{"audit.on_failure": "refuse"})) == failclosed.MODE_REFUSE

    def test_the_ens_profile_always_refuses(self) -> None:
        config = Stand(**{"security.profile": "ens-medium", "audit.on_failure": "warn"})

        assert failclosed.mode(config) == failclosed.MODE_REFUSE

    def test_a_typo_neither_stops_a_server_nor_loosens_one(self) -> None:
        loose = Stand(**{"audit.on_failure": "refues"})
        strict = Stand(**{"security.profile": "ens-medium", "audit.on_failure": "refues"})

        assert failclosed.mode(loose) == failclosed.MODE_WARN
        assert failclosed.mode(strict) == failclosed.MODE_REFUSE

    def test_the_setting_takes_a_word_it_knows(self) -> None:
        Config().set("audit.on_failure", " Refuse ")

        assert Config().get("audit.on_failure") == "refuse"
        with pytest.raises(ConfigError, match="warn, refuse"):
            Config().set("audit.on_failure", "sometimes")


class TestWhetherTheTrailCanTakeAnEvent:
    def test_a_working_log_can(self) -> None:
        get_log().append("system.start", actor=audit.Actor.system("test"))

        assert failclosed.unavailable() is None

    def test_a_failed_write_says_so_with_its_message(self) -> None:
        status.mark_failure("Cannot write the audit log /x: No space left on device")

        why = failclosed.unavailable()

        assert why is not None and "No space left on device" in why

    def test_a_directory_that_is_gone_is_found_before_a_write_fails(self, tmp_path: Path) -> None:
        why = failclosed.unavailable(tmp_path / "gone" / "audit.log")

        assert why is not None and "does not exist" in why

    def test_a_directory_nobody_can_write_to_is_found_before_a_write_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(failclosed.os, "access", lambda *args: False)

        why = failclosed.unavailable(tmp_path / "audit.log")

        assert why is not None and "cannot be written to" in why

    def test_a_full_disk_is_found_before_a_write_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        usage = namedtuple("usage", "total used free")
        monkeypatch.setattr(failclosed.shutil, "disk_usage", lambda path: usage(10**9, 10**9, 4096))

        why = failclosed.unavailable(tmp_path / "audit.log")

        assert why is not None and "full" in why and "4 KiB" in why

    def test_looking_never_writes(self, tmp_path: Path) -> None:
        failclosed.unavailable(tmp_path / "audit.log")

        assert os.listdir(tmp_path) == []

    def test_a_server_that_does_not_audit_has_nothing_to_wait_for(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_log(), "enabled", False)

        assert failclosed.unavailable() is None


class TestWhenAChangeIsRefused:
    def test_never_while_the_policy_is_to_warn(self) -> None:
        status.mark_failure("disk full")

        assert failclosed.refusal(Stand()) is None

    def test_while_the_policy_is_to_refuse_and_the_trail_is_down(self) -> None:
        status.mark_failure("disk full")

        why = failclosed.refusal(Stand(**{"audit.on_failure": "refuse"}))

        assert why is not None and "disk full" in why

    def test_not_while_the_trail_works(self) -> None:
        get_log().append("system.start", actor=audit.Actor.system("test"))

        assert failclosed.refusal(Stand(**{"audit.on_failure": "refuse"})) is None

    def test_not_in_a_rehearsal_that_writes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        status.mark_failure("disk full")
        monkeypatch.setattr(failclosed, "is_rehearsal", lambda: True)

        assert failclosed.refusal(Stand(**{"audit.on_failure": "refuse"})) is None


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of the test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore, runner: FakeRunner) -> FastAPI:
    """The console, with a log in its own state directory."""
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def token(app: FastAPI) -> str:
    """A master token for the console under test."""
    return get_token_manager().generate_master_token()


@pytest.fixture
def client(app: FastAPI, token: str) -> TestClient:
    """A cookie session signed in with the master token."""
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


CHANGE = {"domain": "shop.example.com", "force": True}


def refused(response: Any) -> bool:
    """Whether the answer is the refusal for a trail that cannot be written."""
    return response.status_code == 503 and response.json().get("error") == "audit_unavailable"


class TestTheApi:
    def test_a_change_is_made_while_the_policy_is_to_warn(self, client: TestClient) -> None:
        status.mark_failure("disk full")

        assert not refused(client.post("/api/jobs/update", json=CHANGE))

    def test_a_change_is_refused_with_the_reason_and_the_way_out(self, client: TestClient) -> None:
        refuse_changes()
        status.mark_failure("Cannot write the audit log: No space left on device")

        response = client.post("/api/jobs/update", json=CHANGE)

        assert refused(response)
        body = response.json()
        assert "No space left on device" in body["detail"]
        assert "noust audit status" in body["hint"]

    def test_every_way_of_changing_something_is_refused(self, client: TestClient) -> None:
        refuse_changes()

        for method in ("post", "put", "patch", "delete"):
            # A refusal is itself written, and a write that works is the trail mending: the
            # failure is put back before each one.
            status.mark_failure("disk full")
            response = getattr(client, method)("/api/config/webserver")
            assert refused(response), (method, response.status_code)

    def test_a_webhook_is_a_change_too(self, client: TestClient) -> None:
        refuse_changes()
        status.mark_failure("disk full")

        assert refused(client.post("/hooks/shop-example-com", json={}))

    def test_reading_stays_open(self, client: TestClient) -> None:
        refuse_changes()
        status.mark_failure("disk full")

        assert not refused(client.get("/api/auth/me"))
        assert not refused(client.get("/api/jobs"))
        assert status.failure()[0] is True

    def test_signing_in_and_out_stay_open(self, client: TestClient, token: str) -> None:
        refuse_changes()
        status.mark_failure("disk full")

        assert not refused(client.post("/api/auth/login", json={"token": token}))
        status.mark_failure("disk full")
        assert not refused(client.post("/api/auth/logout"))

    def test_a_directory_that_stopped_taking_writes_is_found_by_the_first_change(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        refuse_changes()
        monkeypatch.setattr(failclosed.os, "access", lambda *args: False)

        # Nothing has failed yet: the look at the directory is what refuses.
        assert status.failure()[0] is False
        assert refused(client.post("/api/jobs/update", json=CHANGE))

    def test_the_refusal_is_on_record_once_the_trail_takes_it(self, client: TestClient) -> None:
        refuse_changes()
        status.mark_failure("disk full")

        assert refused(client.post("/api/jobs/update", json=CHANGE))

        # The trail works: the refusal's own line was its first write, which is
        # also what clears the failure.
        assert [e for e in get_log().read(limit=100) if e["action"] == "audit.refused"]

    def test_the_trail_mending_itself_lets_changes_through_again(self, client: TestClient) -> None:
        refuse_changes()
        status.mark_failure("one write failed, then the disk was cleared")

        assert refused(client.post("/api/jobs/update", json=CHANGE))
        assert not refused(client.post("/api/jobs/update", json=CHANGE))


@click.group("audit", cls=NoustGroup)
def audit_group() -> None:
    """A group standing in for the commands that repair the trail."""


@audit_group.command("prune")
def prune() -> None:
    """Change the trail."""
    click.echo("pruned")


@click.group("thing", cls=NoustGroup)
def thing_group() -> None:
    """A group standing in for any command module."""


@thing_group.command("change")
def change() -> None:
    """Change something."""
    click.echo("changed")


@thing_group.command("look", read_only=True)
def look() -> None:
    """Only read."""
    click.echo("looked")


@click.group("preview", cls=NoustGroup)
def preview_group() -> None:
    """A group standing in for the sweep a timer runs."""


@preview_group.command("sweep")
def sweep() -> None:
    """What a unit runs."""
    click.echo("swept")


@click.group("noust", cls=NoustGroup)
def root() -> None:
    """The program."""


for group in (audit_group, thing_group, preview_group):
    root.add_command(group)


def run(*args: str) -> Any:
    return CliRunner().invoke(root, list(args), obj=Context(), catch_exceptions=False)


class TestTheCommandLine:
    @pytest.fixture(autouse=True)
    def standard_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(audit_policy, "security_profile", lambda: "standard")

    def test_a_change_goes_ahead_while_the_policy_is_to_warn(self) -> None:
        status.mark_failure("disk full")

        result = run("thing", "change")

        assert result.exit_code == 0 and "changed" in result.output

    def test_a_change_is_refused_before_it_does_anything(self) -> None:
        refuse_changes()
        status.mark_failure("disk full")

        result = CliRunner().invoke(root, ["thing", "change"], obj=Context())

        assert result.exit_code != 0
        assert "changed" not in result.output
        assert isinstance(result.exception, Exception)
        assert "audit trail cannot be written" in str(result.exception)
        assert "disk full" in str(result.exception)

    def test_reading_stays_open(self) -> None:
        refuse_changes()
        status.mark_failure("disk full")

        assert "looked" in run("thing", "look").output

    def test_the_commands_that_repair_the_trail_stay_open(self) -> None:
        refuse_changes()
        status.mark_failure("disk full")

        assert "pruned" in run("audit", "prune").output

    def test_what_a_timer_runs_is_not_stopped(self) -> None:
        refuse_changes()
        status.mark_failure("disk full")

        assert "swept" in run("preview", "sweep").output

    def test_a_change_goes_ahead_once_the_trail_works(self) -> None:
        refuse_changes()
        get_log().append("system.start", actor=audit.Actor.system("test"))

        assert "changed" in run("thing", "change").output
