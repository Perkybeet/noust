# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``POST /api/sources/events``: a server queues what an event its central forwarded asks for.

What is pinned: the server does exactly what its own webhook does for the same event (the same
function queues the update, so the same applications follow the same branch and decline the same
skip markers), says which applications it queued, which declined and why, and which pull requests
it handed to the previews; a full queue is the error the forge would have had, so the central tries
again; only a central can forward, and an event that is not exactly one is refused by name.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import app_source_store
from noust.core.forge_events import (
    Forge,
    PullRequestAction,
    PullRequestEvent,
    PushChange,
    PushEvent,
    TagEvent,
    event_to_dict,
)
from noust.core.store import App, NoustStore
from noust.web.api import hooks as hooks_module
from noust.web.api.deps import install_error_handlers, require_auth
from noust.web.api.source_events import router
from noust.web.permissions.registry import permission_for_route
from noust.web.permissions.scopes import INSTANCE, rule_for

SHA = "c" * 40
CENTRAL: dict[str, Any] = {"type": "api_token", "fleet": True, "token_name": "fleet-nas"}
PERSON: dict[str, Any] = {"type": "session", "username": "yago", "account_id": 1}


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def client_as(session: dict[str, Any]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/sources")
    app.dependency_overrides[require_auth] = lambda: session
    return TestClient(app)


@pytest.fixture
def central() -> TestClient:
    return client_as(CENTRAL)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the update jobs instead of running them."""
    captured: list[dict[str, Any]] = []

    class Jobs:
        def create_job(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return type("Job", (), {"id": f"job-{len(captured)}"})()

        def get_active_jobs(self) -> list[Any]:
            return []

    monkeypatch.setattr(hooks_module, "get_job_manager", lambda: Jobs())
    return captured


def push(branch: str = "main", **extra: Any) -> PushEvent:
    return PushEvent(
        forge=Forge.GITHUB,
        repository="you/app",
        clone_url="https://github.com/you/app.git",
        branch=branch,
        head_sha=SHA,
        installation_id=77,
        **extra,
    )


def body(event: Any, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "delivery": "d-1",
        "host": "github.com",
        "event": event_to_dict(event),
        **extra,
    }


def deploy(store: NoustStore, domain: str, source: str, **fields: Any) -> None:
    store.create_app(App(domain=domain, source=source, **fields))


class TestAPush:
    def test_it_queues_the_update_of_the_applications_that_follow_the_branch(
        self, central: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        deploy(store, "shop.example.com", "github:you/app", branch="main")
        deploy(store, "dev.example.com", "github:you/app", branch="dev")
        deploy(store, "other.example.com", "github:you/other", branch="main")

        response = central.post(
            "/api/sources/events", json=body(push("main"), "push", default_branch="main")
        )

        assert response.status_code == 200, response.text
        answer = response.json()
        assert [j["domain"] for j in answer["jobs"]] == ["shop.example.com"]
        assert answer["jobs"][0]["job_id"] == "job-1"
        assert [job["kwargs"] for job in queued] == [{"domain": "shop.example.com"}]
        assert queued[0]["metadata"]["delivery"] == "d-1"
        assert queued[0]["metadata"]["commit"] == SHA
        assert queued[0]["description"] == "GitHub push to you/app@main"

    def test_the_default_branch_is_the_one_an_application_with_none_follows(
        self, central: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        deploy(store, "shop.example.com", "github:you/app")

        response = central.post(
            "/api/sources/events", json=body(push("trunk"), "push", default_branch="trunk")
        )

        assert [j["domain"] for j in response.json()["jobs"]] == ["shop.example.com"]

    def test_an_application_that_declines_is_said_with_why(
        self, central: TestClient, store: NoustStore, queued: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        for name, paths in (
            ("web.example.com", ["apps/web/**"]),
            ("api.example.com", ["apps/api/**"]),
        ):
            store.create_app(
                App(
                    domain=name,
                    app_type="nodejs",
                    source="github:you/app",
                    branch="main",
                    port=3000,
                    app_path=str(tmp_path / name),
                    layout="releases",
                )
            )
            app_source_store.set_options(name, watch_paths=paths, store=store)
        touched = push(change=PushChange(messages=("fix",), files=("apps/api/server.ts",)))

        response = central.post(
            "/api/sources/events", json=body(touched, "push", default_branch="main")
        )

        assert response.status_code == 200, response.text
        answer = response.json()
        assert [j["domain"] for j in answer["jobs"]] == ["api.example.com"]
        assert [(i["domain"], i["reason"]) for i in answer["ignored"]] == [
            ("web.example.com", "paths")
        ]

    def test_a_marked_push_is_declined_by_everyone(
        self, central: TestClient, store: NoustStore, queued: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        store.create_app(
            App(
                domain="web.example.com",
                app_type="nodejs",
                source="github:you/app",
                branch="main",
                port=3000,
                app_path=str(tmp_path / "web"),
                layout="releases",
            )
        )
        marked = push(change=PushChange(messages=("docs [skip cd]",), files=("a.md",)))

        response = central.post(
            "/api/sources/events", json=body(marked, "push", default_branch="main")
        )

        assert response.json()["jobs"] == [] and queued == []
        assert {i["reason"] for i in response.json()["ignored"]} == {"skip"}

    def test_nothing_following_it_is_a_quiet_answer(
        self, central: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        response = central.post("/api/sources/events", json=body(push(), "push"))

        assert response.status_code == 200
        assert response.json()["jobs"] == [] and queued == []

    def test_a_host_the_central_names_decides_which_repositories_count(
        self, central: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        deploy(store, "shop.example.com", "https://ghe.example.com/you/app.git", branch="main")
        event = push()

        answer = central.post("/api/sources/events", json=body(event, "push")).json()
        assert answer["jobs"] == []

        answer = central.post(
            "/api/sources/events", json={**body(event, "push"), "host": "ghe.example.com"}
        ).json()
        assert [j["domain"] for j in answer["jobs"]] == ["shop.example.com"]


class TestATag:
    def test_it_is_decided_by_the_applications_that_follow_tags(
        self, central: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        deploy(store, "shop.example.com", "github:you/app", follow_tags="v*")
        deploy(store, "dev.example.com", "github:you/app", branch="main")
        tag = TagEvent(
            forge=Forge.GITHUB,
            tag="v1.2.0",
            source="release",
            repository="you/app",
            clone_url="https://github.com/you/app.git",
            installation_id=77,
        )

        response = central.post("/api/sources/events", json=body(tag, "tag"))

        assert response.status_code == 200, response.text
        answer = response.json()
        assert [j["domain"] for j in answer["jobs"]] == ["shop.example.com"]
        assert queued and queued[0]["kwargs"] == {"domain": "shop.example.com", "tag": "v1.2.0"}


class TestAPullRequest:
    def pull(self, action: PullRequestAction = PullRequestAction.OPENED) -> PullRequestEvent:
        return PullRequestEvent(
            forge=Forge.GITHUB,
            action=action,
            repository="you/app",
            clone_url="https://github.com/you/app.git",
            number=7,
            title="A thing",
            branch="feature/x",
            base_branch="main",
            head_sha=SHA,
            installation_id=77,
            author="ana",
            sender="ana",
        )

    def test_it_goes_to_the_previews_and_says_which_applications_preview_it(
        self, central: TestClient, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deploy(store, "shop.example.com", "github:you/app", branch="main")
        received: list[PullRequestEvent] = []

        def handle(event: PullRequestEvent, **_: Any) -> list[str]:
            received.append(event)
            return ["job-p"]

        monkeypatch.setattr("noust.managers.previews.handle_pull_request", handle)
        monkeypatch.setattr(
            "noust.managers.previews.previewing_apps",
            lambda event, **_: [(store.get_app("shop.example.com"), object())],
        )

        response = central.post("/api/sources/events", json=body(self.pull(), "pull_request"))

        assert response.status_code == 200, response.text
        assert received == [self.pull()]
        assert response.json()["previews"] == [
            {"parent": "shop.example.com", "number": 7, "action": "opened"}
        ]
        assert response.json()["job_ids"] == ["job-p"]

    def test_nobody_previewing_it_is_a_quiet_answer(
        self, central: TestClient, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.managers.previews.handle_pull_request", lambda event, **_: [])
        monkeypatch.setattr("noust.managers.previews.previewing_apps", lambda event, **_: [])

        response = central.post("/api/sources/events", json=body(self.pull(), "pull_request"))

        assert response.json()["previews"] == [] and response.json()["job_ids"] == []


class TestWhoMayForward:
    def test_a_person_cannot(self, store: NoustStore, queued: list[dict[str, Any]]) -> None:
        deploy(store, "shop.example.com", "github:you/app", branch="main")

        response = client_as(PERSON).post("/api/sources/events", json=body(push(), "push"))

        assert response.status_code == 403
        assert response.json()["coded"]["code"] == "sources.central_only"
        assert queued == []

    def test_an_event_that_is_not_one_is_refused_by_name(
        self, central: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        wrong = body(push(), "push")
        wrong["event"]["branch"] = "--upload-pack=x"

        response = central.post("/api/sources/events", json=wrong)

        assert response.status_code == 400
        assert "branch" in response.text
        assert queued == []

    def test_a_kind_nobody_defined_is_refused(self, central: TestClient) -> None:
        response = central.post("/api/sources/events", json=body(push(), "deploy"))

        assert response.status_code in (400, 422)

    def test_the_route_has_a_permission_and_the_servers_scope(self) -> None:
        assert permission_for_route("POST", "/api/sources/events") is not None
        rule = rule_for("POST", "/api/sources/events")
        assert rule is not None and rule.scoping == INSTANCE
