# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``POST /hooks/registry/{domain}``: a registry says an image was pushed (checklist A-14).

Written as attacks first, like the forge hook: no credential, a wrong one, a domain without a
secret. Then a signed ``registry_package`` event from GitHub Container Registry and a Docker Hub
payload carrying the token of its address queue the one update job the console already has, only
when the image and the tag are the ones the application runs, once for the same digest, and not
while a pause or a freeze holds automatic deploys.
"""

# The forge-hook harness is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import deploy_guard
from noust.core.container_store import ContainerSettings, ContainerStore
from noust.core.registry_events import registry_hook_token
from noust.core.store import App, NoustStore
from noust.web.api import hooks as hooks_module
from noust.web.api import registry_hooks
from noust.web.api.hooks import mint_webhook_secret
from tests.test_web_hooks import (  # noqa: F401  (pytest resolves fixtures by name)
    app,
    client,
    fresh_delivery_cache,
    github_signature,
    store,
)

DOMAIN = "img.example.com"
DIGEST = "sha256:" + "a" * 64
NEWER = "sha256:" + "b" * 64
IMAGE = "ghcr.io/owner/app:1.4"


def ghcr_body(*, tag: str = "1.4", repo: str = "owner/app", digest: str = NEWER) -> bytes:
    return json.dumps(
        {
            "action": "published",
            "registry_package": {
                "package_type": "CONTAINER",
                "package_version": {
                    "version": digest,
                    "package_url": f"ghcr.io/{repo}:{tag}",
                    "container_metadata": {"tag": {"name": tag, "digest": digest}},
                },
            },
        }
    ).encode()


def hub_body(*, tag: str = "stable", repo: str = "owner/app") -> bytes:
    return json.dumps({"push_data": {"tag": tag}, "repository": {"repo_name": repo}}).encode()


def signed(
    secret: str, body: bytes, event: str = "registry_package", **extra: str
) -> dict[str, str]:
    return {
        "X-Hub-Signature-256": github_signature(secret, body),
        "X-GitHub-Event": event,
        "Content-Type": "application/json",
        **extra,
    }


@pytest.fixture
def image_app(store: NoustStore) -> App:
    created = store.create_app(
        App(
            domain=DOMAIN,
            app_type="container",
            source=IMAGE,
            port=3001,
            app_path="/var/www/apps/img-example-com",
        )
    )
    ContainerStore(store).save(
        DOMAIN,
        ContainerSettings(method="image", image=IMAGE, digest=DIGEST),
        actor="test",
    )
    return created


@pytest.fixture
def secret(store: NoustStore, image_app: App) -> str:
    return mint_webhook_secret(DOMAIN)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> Any:
        captured.append(kwargs)

        class Queued:
            id = "job-1"
            status = type("Status", (), {"value": "pending"})()

        return Queued()

    manager = type("FakeJobs", (), {"create_job": staticmethod(create_job)})()
    monkeypatch.setattr(registry_hooks, "get_job_manager", lambda: manager)
    return captured


class TestWhoMayCall:
    def test_a_domain_without_a_secret_is_the_404_every_unknown_one_is(
        self, client: TestClient, image_app: App
    ) -> None:
        assert client.post(f"/hooks/registry/{DOMAIN}", content=ghcr_body()).status_code == 404
        assert client.post("/hooks/registry/nothing.example.com").status_code == 404

    def test_no_credential_is_a_401_that_says_nothing_else(
        self, client: TestClient, secret: str
    ) -> None:
        response = client.post(f"/hooks/registry/{DOMAIN}", content=ghcr_body())

        assert response.status_code == 401
        assert secret not in response.text

    def test_a_signature_made_with_another_secret_is_refused(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]]
    ) -> None:
        body = ghcr_body()

        response = client.post(
            f"/hooks/registry/{DOMAIN}", content=body, headers=signed("not-the-secret", body)
        )

        assert response.status_code == 401 and jobs == []

    def test_a_wrong_token_is_refused_and_so_is_the_secret_itself_as_a_token(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]]
    ) -> None:
        for token in ("0" * 40, secret):
            response = client.post(f"/hooks/registry/{DOMAIN}/{token}", content=hub_body())
            assert response.status_code == 401
        assert jobs == []

    def test_the_token_stops_working_when_the_secret_is_rotated(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]]
    ) -> None:
        old = registry_hook_token(secret, DOMAIN)
        mint_webhook_secret(DOMAIN)

        response = client.post(f"/hooks/registry/{DOMAIN}/{old}", content=hub_body(tag="1.4"))

        assert response.status_code == 401 and jobs == []

    def test_too_many_wrong_ones_lock_the_application_out(
        self, client: TestClient, secret: str
    ) -> None:
        codes = [client.post(f"/hooks/registry/{DOMAIN}/{'0' * 40}").status_code for _ in range(12)]

        assert 401 in codes and 429 in codes


class TestGitHubContainerRegistry:
    def test_a_signed_push_of_the_image_queues_the_update(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]]
    ) -> None:
        body = ghcr_body()

        response = client.post(
            f"/hooks/registry/{DOMAIN}", content=body, headers=signed(secret, body)
        )

        assert response.status_code == 202, response.text
        assert response.json() == {"job_id": "job-1", "status": "pending", "merged": False}
        job = jobs[0]
        assert job["func"] is registry_hooks.registry_update_job
        assert job["kwargs"] == {"domain": DOMAIN}
        assert job["coalesce_key"] == f"update:{DOMAIN}"
        assert job["metadata"]["trigger"] == "webhook"
        assert job["metadata"]["provider"] == "ghcr"

    def test_the_digest_that_already_runs_deploys_nothing(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]]
    ) -> None:
        body = ghcr_body(digest=DIGEST)

        response = client.post(
            f"/hooks/registry/{DOMAIN}", content=body, headers=signed(secret, body)
        )

        assert response.status_code == 200
        assert response.json()["reason"] == "digest" and jobs == []

    @pytest.mark.parametrize(
        ("tag", "repo"), [("1.5", "owner/app"), ("1.4", "owner/other"), ("latest", "owner/app")]
    )
    def test_another_tag_or_image_is_acknowledged_and_ignored(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]], tag: str, repo: str
    ) -> None:
        body = ghcr_body(tag=tag, repo=repo)

        response = client.post(
            f"/hooks/registry/{DOMAIN}", content=body, headers=signed(secret, body)
        )

        assert response.status_code == 200
        assert response.json()["reason"] == "image" and jobs == []

    def test_a_ping_is_answered_and_nothing_else(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]]
    ) -> None:
        body = b"{}"

        response = client.post(
            f"/hooks/registry/{DOMAIN}", content=body, headers=signed(secret, body, "ping")
        )

        assert response.status_code == 200 and response.json()["event"] == "ping"
        assert jobs == []

    def test_an_event_that_is_not_a_package_is_ignored(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]]
    ) -> None:
        body = json.dumps({"ref": "refs/heads/main"}).encode()

        response = client.post(
            f"/hooks/registry/{DOMAIN}", content=body, headers=signed(secret, body, "push")
        )

        assert response.status_code == 202 and response.json()["reason"] == "event"
        assert jobs == []

    def test_the_same_delivery_twice_deploys_once(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]]
    ) -> None:
        body = ghcr_body()
        headers = signed(secret, body, **{"X-GitHub-Delivery": "d-1"})

        first = client.post(f"/hooks/registry/{DOMAIN}", content=body, headers=headers)
        second = client.post(f"/hooks/registry/{DOMAIN}", content=body, headers=headers)

        assert first.status_code == 202 and second.json()["reason"] == "duplicate"
        assert len(jobs) == 1


class TestDockerHub:
    def test_the_token_of_the_address_queues_the_update(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]], store: NoustStore
    ) -> None:
        ContainerStore(store).save(
            DOMAIN,
            ContainerSettings(method="image", image="owner/app:stable", digest=DIGEST),
            actor="test",
        )
        token = registry_hook_token(secret, DOMAIN)

        response = client.post(f"/hooks/registry/{DOMAIN}/{token}", content=hub_body())

        assert response.status_code == 202, response.text
        assert jobs[0]["metadata"]["provider"] == "dockerhub"
        assert jobs[0]["metadata"]["image"] == "owner/app:stable"

    def test_the_wrong_tag_is_ignored(
        self, client: TestClient, secret: str, jobs: list[dict[str, Any]], store: NoustStore
    ) -> None:
        ContainerStore(store).save(
            DOMAIN,
            ContainerSettings(method="image", image="owner/app:stable", digest=DIGEST),
            actor="test",
        )
        token = registry_hook_token(secret, DOMAIN)

        response = client.post(f"/hooks/registry/{DOMAIN}/{token}", content=hub_body(tag="nightly"))

        assert response.status_code == 200 and jobs == []


class TestWhatIsNotAnImageApplication:
    def test_a_repository_application_ignores_it(
        self, client: TestClient, store: NoustStore, jobs: list[dict[str, Any]]
    ) -> None:
        store.create_app(
            App(domain="repo.example.com", app_type="nodejs", app_path="/var/www/apps/repo")
        )
        repo_secret = mint_webhook_secret("repo.example.com")
        body = ghcr_body()

        response = client.post(
            "/hooks/registry/repo.example.com", content=body, headers=signed(repo_secret, body)
        )

        assert response.status_code == 200
        assert response.json()["reason"] == "not_an_image_application" and jobs == []


class TestAPause:
    def test_a_freeze_holds_what_a_registry_asks_like_it_holds_a_push(
        self,
        client: TestClient,
        secret: str,
        jobs: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gate = deploy_guard.Gate(
            "frozen", "deploys are frozen until Monday", "Wait for the freeze to end."
        )
        monkeypatch.setattr(hooks_module, "_held", lambda domain: gate)
        body = ghcr_body()

        response = client.post(
            f"/hooks/registry/{DOMAIN}", content=body, headers=signed(secret, body)
        )

        assert response.status_code == 200
        assert response.json()["reason"] == "held" and jobs == []


class TestTheJob:
    def test_a_tag_that_points_at_the_digest_that_runs_updates_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import image_updates

        ran: list[str] = []
        monkeypatch.setattr(registry_hooks, "run_update", lambda *a, **k: ran.append("update"))
        monkeypatch.setattr(hooks_module, "_held", lambda domain: None)
        monkeypatch.setattr(
            image_updates,
            "check_image",
            lambda domain, **kw: image_updates.ImageCheck(
                domain, IMAGE, DIGEST, DIGEST, False, "already running"
            ),
        )

        outcome = registry_hooks.registry_update_job(DOMAIN)

        assert outcome["status"] == "ignored" and ran == []

    def test_a_tag_that_moved_updates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.managers import image_updates

        monkeypatch.setattr(
            registry_hooks, "run_update", lambda domain, **k: {"domain": domain, "status": "ok"}
        )
        monkeypatch.setattr(hooks_module, "_held", lambda domain: None)
        monkeypatch.setattr(
            image_updates,
            "check_image",
            lambda domain, **kw: image_updates.ImageCheck(
                domain, IMAGE, DIGEST, NEWER, True, "moved"
            ),
        )

        assert registry_hooks.registry_update_job(DOMAIN)["status"] == "ok"

    def test_a_registry_that_cannot_be_asked_does_not_stop_the_update(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.exceptions import DockerError
        from noust.managers import image_updates

        def refuse(domain: str, **kw: Any) -> Any:
            raise DockerError("Failed to pull", details="no route to host")

        monkeypatch.setattr(
            registry_hooks, "run_update", lambda domain, **k: {"domain": domain, "status": "ok"}
        )
        monkeypatch.setattr(hooks_module, "_held", lambda domain: None)
        monkeypatch.setattr(image_updates, "check_image", refuse)

        assert registry_hooks.registry_update_job(DOMAIN)["status"] == "ok"
