# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The preview policy through the API and the command line: a template for the name,
search engines, and approving the commit of a fork's pull request
(checklist B-30, B-34).

The rules are ``tests/test_previews_policy.py``'s; here are the doors.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner, Result
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.preview_store import PreviewStore
from noust.core.store import NoustStore, PreviewRecord
from noust.managers import previews
from tests.test_web_previews import (
    BASE,
    PARENT,
    app,
    elevated,
    master_token,
    no_timer,
    queued,
    session,
    store,
)

FIXTURES = (app, elevated, master_token, no_timer, queued, session, store)

CHILD = f"pr-7-shop-example-com.{BASE}"


def waiting(store: NoustStore, number: int = 7, sha: str = "a" * 40) -> PreviewRecord:
    return store.save_preview(
        PreviewRecord(
            parent_domain=PARENT,
            domain=f"pr-{number}-shop-example-com.{BASE}",
            number=number,
            branch="feature/x",
            provider="github",
            expires_at="2099-10-05T12:00:00+00:00",
            head_sha=sha,
            repository="acme/shop",
            status="awaiting",
        )
    )


@pytest.fixture
def on(elevated: TestClient, store: NoustStore) -> TestClient:
    response = elevated.put(
        f"/api/apps/{PARENT}/previews/settings",
        json={"base_domain": BASE, "fork_policy": "approve"},
    )
    assert response.status_code == 200, response.text
    return elevated


def invoke(*args: str) -> Result:
    return CliRunner().invoke(root_cli, list(args))


class TestSettingsOverHttp:
    def test_the_policy_round_trips(self, elevated: TestClient, store: NoustStore) -> None:
        response = elevated.put(
            f"/api/apps/{PARENT}/previews/settings",
            json={
                "base_domain": BASE,
                "domain_template": "{{pr_id}}-{{name}}.{{base}}",
                "noindex": False,
                "fork_policy": "approve",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["domain_template"] == "{{pr_id}}-{{name}}.{{base}}"
        assert body["noindex"] is False and body["fork_policy"] == "approve"
        listed = elevated.get(f"/api/apps/{PARENT}/previews").json()["settings"]
        assert listed["fork_policy"] == "approve"

    def test_defaults_name_them_the_old_way_and_say_noindex(self, on: TestClient) -> None:
        body = on.get(f"/api/apps/{PARENT}/previews").json()["settings"]

        assert body["domain_template"] is None and body["noindex"] is True

    @pytest.mark.parametrize(
        "extra",
        [
            {"domain_template": "same.{{base}}"},
            {"domain_template": "{{pr_id}}-{{oops}}.{{base}}"},
            {"fork_policy": "allow"},
            {"data_mode": "clone"},  # the fixture application has no database to copy
        ],
    )
    def test_what_cannot_be_is_a_400_and_changes_nothing(
        self, elevated: TestClient, store: NoustStore, extra: dict[str, Any]
    ) -> None:
        response = elevated.put(
            f"/api/apps/{PARENT}/previews/settings", json={"base_domain": BASE, **extra}
        )

        assert response.status_code == 400, response.text
        assert store.get_preview_settings(PARENT) is None


class TestApprovingOverHttp:
    def test_a_waiting_commit_is_listed_approved_and_built(
        self, on: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        waiting(store)
        listed = on.get(f"/api/apps/{PARENT}/previews").json()["previews"]
        assert [p["status"] for p in listed] == ["awaiting"]

        approved = on.post(f"/api/apps/{PARENT}/previews/7/approve")

        assert approved.status_code == 202, approved.text
        assert approved.json()["preview"]["status"] == "pending"
        assert PreviewStore(store).origin(CHILD).approved_sha == "a" * 40
        assert len(queued) == 1

    def test_what_is_not_waiting_cannot_be_approved(
        self, on: TestClient, store: NoustStore
    ) -> None:
        record = waiting(store)
        store.update_preview(PARENT, record.number, {"status": "ready"})

        response = on.post(f"/api/apps/{PARENT}/previews/7/approve")

        assert response.status_code == 400
        assert "not waiting for approval" in response.text

    def test_rejecting_forgets_it(self, on: TestClient, store: NoustStore) -> None:
        waiting(store)

        response = on.post(f"/api/apps/{PARENT}/previews/7/reject")

        assert response.status_code == 200, response.text
        assert store.get_preview(PARENT, 7) is None

    def test_an_unknown_preview_is_a_404(self, on: TestClient) -> None:
        assert on.post(f"/api/apps/{PARENT}/previews/99/approve").status_code == 404
        assert on.post(f"/api/apps/{PARENT}/previews/99/reject").status_code == 404


class TestTheCommandLine:
    def test_enable_takes_the_policy(self, store: NoustStore) -> None:
        result = invoke(
            "preview",
            "enable",
            PARENT,
            "--domain",
            BASE,
            "--domain-template",
            "{{pr_id}}.{{domain}}",
            "--index",
            "--forks",
            "approve",
        )

        assert result.exit_code == 0, result.output
        options = PreviewStore(store).options(PARENT)
        assert options.domain_template == "{{pr_id}}.{{domain}}"
        assert options.noindex is False and options.fork_policy == "approve"
        assert "wait for approval" in result.output
        assert "may index" in result.output

    def test_enable_refuses_a_template_that_cannot_name_one(self, store: NoustStore) -> None:
        result = invoke(
            "preview", "enable", PARENT, "--domain", BASE, "--domain-template", "same.{{base}}"
        )

        assert result.exit_code == 1
        assert store.get_preview_settings(PARENT) is None

    def test_approve_builds_here_and_says_where_it_answers(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        invoke("preview", "enable", PARENT, "--domain", BASE, "--forks", "approve")
        waiting(store)
        built: list[tuple[str, int]] = []
        monkeypatch.setattr(
            previews,
            "build_in_process",
            lambda parent, number, logger: built.append((parent, number)) or {},
        )

        result = invoke("preview", "approve", CHILD, "--json")

        assert result.exit_code == 0, result.output
        assert built == [(PARENT, 7)]
        body = json.loads(result.output)
        assert body["domain"] == CHILD and body["kind"] == "pr"
        assert PreviewStore(store).origin(CHILD).approved_by

    def test_reject_forgets_it(self, store: NoustStore) -> None:
        invoke("preview", "enable", PARENT, "--domain", BASE, "--forks", "approve")
        waiting(store)

        result = invoke("preview", "reject", CHILD)

        assert result.exit_code == 0, result.output
        assert store.get_preview(PARENT, 7) is None

    def test_approving_what_does_not_wait_is_an_error(self, store: NoustStore) -> None:
        invoke("preview", "enable", PARENT, "--domain", BASE)
        record = waiting(store)
        store.update_preview(PARENT, record.number, {"status": "ready"})

        result = invoke("preview", "approve", CHILD)

        assert result.exit_code == 1
