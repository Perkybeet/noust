# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust token activity``: what a token did, by interventions, and leaving one as it was (item 185).

The same readers and the same way back the console's routes use, from the terminal: root is not asked
for sudo mode or a second person here (the command line is the emergency channel), and what ``--apply``
does is on the audit trail with the operating system identity of whoever ran it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core import audit
from noust.core.accounts import AccountManager, passwords
from noust.core.audit import Actor
from noust.core.config import Config
from noust.core.store import App, JobRecord, NoustStore
from noust.web.auth import STATE_DIR_ENV, SecurityConfig, TokenManager

SHOP = "shop.example.com"
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    Config.reset_instance()
    audit.reset()
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    store.create_app(App(domain=SHOP, app_type="nodejs", app_path=f"/srv/{SHOP}"))  # type: ignore[arg-type]
    yield store
    audit.reset()
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def token() -> None:
    owner = AccountManager().create("marta", "admin", password=PASSWORD)
    TokenManager(SecurityConfig()).create_api_token("ci", "admin", None, owner=owner)


def run(*args: str, code: int = 0) -> str:
    result = CliRunner().invoke(root_cli, ["token", "activity", *args])
    output = f"{result.output}{result.exception or ''}"
    assert result.exit_code == code, output
    return output


def did(path: str, *, method: str = "post", corr: str = "c1", by: str = "ci") -> None:
    audit.get_log().append(
        f"api.{method}",
        actor=Actor(kind="token", id=by, name=by),
        target=path,
        correlation_id=corr,
    )


def deployed(store: NoustStore, commit: str, *, by: str | None = None) -> int:
    job = None
    if by is not None:
        job = f"job-{commit}"
        store.create_job(
            JobRecord(
                id=job,
                type="update",
                name="Update",
                status="completed",
                domain=SHOP,
                actor=f"token:{by}",
                created_at=datetime.now().isoformat(),
            )
        )
    number = store.record_deployment_start(SHOP, "panel", git_commit=commit, job_id=job)
    store.finish_deployment(number, "success")
    return number


@pytest.fixture
def way_back(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, bool]]:
    rolled: list[tuple[str, int, bool]] = []

    class Outcome:
        release_id = None
        history_id = 5

    monkeypatch.setattr(
        "noust.deployers.lifecycle.rollback_availability",
        lambda records: {r.id: None for r in records},
    )
    monkeypatch.setattr(
        "noust.deployers.lifecycle.schema_changed_between",
        lambda records: {r.id: [] for r in records},
    )
    monkeypatch.setattr(
        "noust.deployers.lifecycle.rollback_to_deployment",
        lambda domain, number, **kwargs: rolled.append(
            (domain, number, bool(kwargs.get("schema_changed_ok")))
        )
        or Outcome(),
    )
    return rolled


@pytest.mark.usefixtures("token")
class TestReading:
    def test_the_interventions_of_a_token(self) -> None:
        did(f"/api/apps/{SHOP}/restart")
        output = run("ci")
        assert "Applications" in output and SHOP in output
        assert "(going on)" in output

    def test_as_json(self) -> None:
        did(f"/api/apps/{SHOP}/restart")
        body = json.loads(run("ci", "--json"))
        assert body["credential"] == "ci" and body["complete"] is True
        (found,) = body["interventions"]
        assert found["apps"] == [SHOP] and found["calls"] == 1

    def test_one_intervention_is_its_timeline(self) -> None:
        did(f"/api/apps/{SHOP}/restart")
        number = json.loads(run("ci", "--json"))["interventions"][0]["n"]
        output = run("ci", str(number))
        assert f"POST /api/apps/{SHOP}/restart" in output
        timeline = json.loads(run("ci", str(number), "--json"))["timeline"]
        assert timeline[0]["domain"] == SHOP and timeline[0]["result"] == "ok"

    def test_a_token_that_did_nothing_says_so(self) -> None:
        assert "did nothing" in run("ci")

    def test_a_name_nobody_has_is_said_with_where_to_look(self) -> None:
        output = run("ghost", code=1)
        assert "noust token list" in output

    def test_an_intervention_that_is_not_there_is_said(self) -> None:
        output = run("ci", "999999", code=1)
        assert "no intervention 999999" in output

    def test_the_assistant_of_a_person_is_read_by_its_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager_cls = TokenManager
        listed = manager_cls.list_api_tokens

        def tag(self: Any, owner: Any = None) -> Any:
            return [
                {**r, "caller": "assistant"} if r["name"] == "ci" else r
                for r in listed(self, owner)
            ]

        monkeypatch.setattr(manager_cls, "list_api_tokens", tag)
        did(f"/api/apps/{SHOP}/restart")
        body = json.loads(run("assistant:marta", "--json"))
        assert body["credential"] == "assistant:marta"
        assert body["interventions"][0]["conversation"] == "ci"
        assert "has not worked for" in run("assistant:olga", code=1)


@pytest.mark.usefixtures("token")
class TestLeavingItAsItWas:
    def history(self, store: NoustStore) -> tuple[int, int]:
        before = deployed(store, "aaa111")
        did(f"/api/apps/{SHOP}/update")
        after = deployed(store, "bbb222", by="ci")
        return before, after

    def test_the_plan_is_shown_and_nothing_changes(
        self, server: NoustStore, way_back: list[tuple[str, int, bool]]
    ) -> None:
        before, _after = self.history(server)
        number = json.loads(run("ci", "--json"))["interventions"][0]["n"]
        output = run("ci", str(number), "--undo")
        assert f"{SHOP}: ready, back to deployment {before} (aaa111)" in output
        plan = json.loads(run("ci", str(number), "--undo", "--json"))
        assert plan["available"] is True and plan["items"][0]["status"] == "ready"
        assert way_back == []

    def test_applying_puts_it_back_and_says_what_went(
        self, server: NoustStore, way_back: list[tuple[str, int, bool]]
    ) -> None:
        before, _after = self.history(server)
        number = json.loads(run("ci", "--json"))["interventions"][0]["n"]
        output = run("ci", str(number), "--apply")
        assert f"{SHOP} went back to deployment {before}" in output
        assert way_back == [(SHOP, before, False)]
        events = [
            json.loads(line)
            for line in audit.get_log().path.read_text().splitlines()
            if line.strip()
        ]
        assert [e for e in events if e["action"] == "auth.token.undo"]

    def test_what_cannot_go_back_is_said_and_the_command_fails(
        self, server: NoustStore, way_back: list[tuple[str, int, bool]]
    ) -> None:
        did(f"/api/apps/{SHOP}/update")
        deployed(server, "aaa111", by="ci")
        number = json.loads(run("ci", "--json"))["interventions"][0]["n"]
        output = run("ci", str(number), "--apply", code=1)
        assert "nothing_before" in output
        assert way_back == []
