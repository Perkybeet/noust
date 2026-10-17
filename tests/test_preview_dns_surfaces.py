# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the DNS of the previews is said: when they are turned on, when one is built, and by the API.

The check itself is pinned in ``test_preview_dns.py``. Here: ``noust preview enable`` ends with
whether the wildcard record reaches the server, the job of a preview says in its log, once and
before the build, that its name points elsewhere, and ``GET /api/apps/{domain}/previews/dns``
answers the same thing.
"""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.store import NoustStore
from noust.deployers.domains import DnsCheck
from noust.managers import preview_dns, previews
from noust.web.api import previews as previews_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers
from tests.test_previews import (
    BASE,
    PARENT,
    FakeContext,
    clock,
    jobs,
    posted,
    store,
    stored_preview,
    timer,
)

FIXTURES = (clock, jobs, posted, store, timer)

SERVER = ("203.0.113.10",)


def pointing(verdict: str, resolved: tuple[str, ...] = ()) -> Any:
    def check(domain: str, **kwargs: Any) -> DnsCheck:
        return DnsCheck(
            domain=domain,
            expected_addresses=SERVER,
            resolved_addresses=resolved,
            points_here=verdict in ("here", "cdn"),
            verdict=verdict,
        )

    return check


def test_turning_previews_on_ends_with_the_wildcard_record_that_is_missing(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview_dns, "_default_check", lambda: pointing("none"))

    result = CliRunner().invoke(root_cli, ["preview", "enable", PARENT, "--domain", BASE])

    assert result.exit_code == 0, result.output
    assert f"*.{BASE}" in result.output
    assert "Add a record" in result.output


def test_turning_previews_on_says_when_the_record_is_there(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview_dns, "_default_check", lambda: pointing("here", SERVER))

    result = CliRunner().invoke(root_cli, ["preview", "enable", PARENT, "--domain", BASE])

    assert result.exit_code == 0, result.output
    assert f"*.{BASE} reaches this server" in result.output


class TestTheBuild:
    def build(self, store: NoustStore, monkeypatch: pytest.MonkeyPatch, check: Any) -> list[str]:
        from noust.web import jobs as jobs_module

        settings = previews.enable_previews(PARENT, BASE)
        monkeypatch.setattr(preview_dns, "_default_check", lambda: check)
        monkeypatch.setattr(jobs_module, "deploy_app_job", lambda **kwargs: {})
        parent = store.get_app(PARENT)
        assert parent is not None
        context = FakeContext()

        previews._deploy(parent, stored_preview(store), context, settings)
        return context.logs

    def test_a_name_that_points_elsewhere_is_said_in_the_log_once(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logs = self.build(store, monkeypatch, pointing("elsewhere", ("198.51.100.4",)))

        said = [line for line in logs if "198.51.100.4" in line]
        assert len(said) == 1 and "certificate" in said[0]

    def test_a_name_that_reaches_the_server_says_nothing(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logs = self.build(store, monkeypatch, pointing("here", SERVER))

        assert not any("certificate" in line for line in logs)

    def test_a_check_that_cannot_be_made_does_not_stop_the_build(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(domain: str, **kwargs: Any) -> DnsCheck:
            raise OSError("no resolver")

        logs = self.build(store, monkeypatch, broken)

        assert not any("does not resolve" in line for line in logs)


def test_the_api_answers_the_same(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> None:
    previews.enable_previews(PARENT, BASE)
    monkeypatch.setattr(preview_dns, "_default_check", lambda: pointing("none"))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(previews_api.router, prefix="/api/apps")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test"}
    client = TestClient(app)

    body = client.get(f"/api/apps/{PARENT}/previews/dns").json()

    assert body["ok"] is False
    assert body["wildcard"] == f"*.{BASE}"
    assert body["verdict"] == "none"
    assert body["expected_addresses"] == list(SERVER)
    assert "Add a record" in body["advice"]


def test_the_api_refuses_an_application_with_previews_off(store: NoustStore) -> None:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(previews_api.router, prefix="/api/apps")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test"}

    response = TestClient(app).get(f"/api/apps/{PARENT}/previews/dns")

    assert response.status_code == 400
    assert "Previews are off" in response.text
