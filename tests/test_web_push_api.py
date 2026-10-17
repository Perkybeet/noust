"""
``/api/push`` and ``/api/apps/{domain}/push``: deploying with git push over the API (checklist A-23).

The routes are a client of :mod:`noust.pushdeploy`; what is pinned is what each adds: the permission
it declares and sudo mode for what grants someone the power to deploy, the error contract (a 400
that names the field, a 404 for an application the principal cannot see), and that no key or
secret is in anything it answers.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.web.permissions import Permission
from noust.web.permissions.routes_push import ROUTES
from tests.push_support import DOMAIN, OTHER, PushRig, build_rig, ed25519


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PushRig]:
    yield from build_rig(tmp_path, monkeypatch)


def client_for(rig: PushRig, monkeypatch: pytest.MonkeyPatch, *, elevated: bool = True) -> Any:
    """A client for the push routers alone, authenticated, elevated or not, on the rig's server."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from noust.web.api import push as api
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    monkeypatch.setattr(api, "PushRepos", lambda actor=None: rig.repos)
    monkeypatch.setattr(api, "PushHost", lambda: rig.world.host())
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(api.router, prefix="/api/push")
    app.include_router(api.apps_router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


@pytest.fixture
def api(rig: PushRig, monkeypatch: pytest.MonkeyPatch) -> Any:
    rig.set_up()
    rig.app()
    return client_for(rig, monkeypatch)


def test_every_route_declares_its_permission() -> None:
    assert ROUTES[("POST", "/api/push/setup")] == Permission.SERVER_HOST_ACCESS
    assert ROUTES[("GET", "/api/push")] == Permission.SERVER_READ
    assert ROUTES[("GET", "/api/apps/{domain}/push")] == Permission.APPS_READ
    assert ROUTES[("GET", "/api/apps/{domain}/push/keys")] == Permission.APPS_READ
    for key in (
        ("PUT", "/api/apps/{domain}/push"),
        ("DELETE", "/api/apps/{domain}/push"),
        ("POST", "/api/apps/{domain}/push/keys"),
        ("DELETE", "/api/apps/{domain}/push/keys/{label}"),
        ("POST", "/api/apps/{domain}/push/refresh"),
    ):
        assert ROUTES[key] == Permission.APPS_MANAGE, key


class TestTheServer:
    def test_it_says_whether_it_takes_pushes_and_who_deploys_that_way(
        self, rig: PushRig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig.app()
        client = client_for(rig, monkeypatch)

        before = client.get("/api/push").json()
        assert before["host"]["ready"] is False and before["apps"] == []
        assert any("noust push setup" in problem for problem in before["host"]["problems"])

        assert client.post("/api/push/setup").status_code == 200
        client.put(f"/api/apps/{DOMAIN}/push", json={})
        after = client.get("/api/push").json()
        assert after["host"]["ready"] is True
        assert [item["domain"] for item in after["apps"]] == [DOMAIN]

    def test_setting_up_makes_the_account_and_says_so(
        self, rig: PushRig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = client_for(rig, monkeypatch).post("/api/push/setup")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["account"] == "noust-git" and body["account_created"] is True
        assert body["keys_file"].endswith("noust-git.keys") and body["changed"] is True
        assert rig.world.dropin.exists()

    def test_setting_up_needs_sudo_mode(
        self, rig: PushRig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = client_for(rig, monkeypatch, elevated=False).post("/api/push/setup")
        assert response.status_code == 403
        assert not rig.world.dropin.exists()


class TestAnApplication:
    def test_turning_it_on_reading_it_and_turning_it_off(self, api: Any, rig: PushRig) -> None:
        assert api.get(f"/api/apps/{DOMAIN}/push").json()["enabled"] is False

        enabled = api.put(
            f"/api/apps/{DOMAIN}/push",
            json={
                "branch": "release",
                "key": ed25519(1),
                "label": "laptop",
                "host": "git.example.org",
            },
        )

        assert enabled.status_code == 200, enabled.text
        body = enabled.json()
        assert body["enabled"] is True and body["branch"] == "release"
        assert body["remote"] == f"noust-git@git.example.org:{DOMAIN}.git"
        assert [key["label"] for key in body["keys"]] == ["laptop"]
        assert body["hook_ok"] is True and body["problems"] == []
        assert ed25519(1).split()[1] not in enabled.text

        read = api.get(f"/api/apps/{DOMAIN}/push", params={"host": "other.example.org"}).json()
        assert read["remote"] == f"noust-git@other.example.org:{DOMAIN}.git"

        off = api.delete(f"/api/apps/{DOMAIN}/push")
        assert off.status_code == 200 and off.json()["enabled"] is False
        assert (rig.world.home / f"{DOMAIN}.git").is_dir()

        api.put(f"/api/apps/{DOMAIN}/push", json={})
        gone = api.delete(f"/api/apps/{DOMAIN}/push", params={"delete_repository": "true"})
        assert gone.status_code == 200
        assert not (rig.world.home / f"{DOMAIN}.git").exists()

    def test_turning_it_on_or_off_needs_sudo_mode(
        self, rig: PushRig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig.set_up()
        rig.app()
        client = client_for(rig, monkeypatch, elevated=False)
        assert client.put(f"/api/apps/{DOMAIN}/push", json={}).status_code == 403
        assert client.delete(f"/api/apps/{DOMAIN}/push").status_code == 403
        assert (
            client.post(
                f"/api/apps/{DOMAIN}/push/keys", json={"label": "x", "key": ed25519(1)}
            ).status_code
            == 403
        )
        assert client.delete(f"/api/apps/{DOMAIN}/push/keys/x").status_code == 403
        # Reading and writing the hook again change nothing a person could use.
        assert client.get(f"/api/apps/{DOMAIN}/push").status_code == 200

    def test_a_branch_that_is_not_one_is_a_400(self, api: Any) -> None:
        response = api.put(f"/api/apps/{DOMAIN}/push", json={"branch": "--evil"})
        assert response.status_code == 400

    def test_a_key_without_a_name_is_a_400_that_names_the_field(self, api: Any) -> None:
        response = api.put(f"/api/apps/{DOMAIN}/push", json={"key": ed25519(1)})
        assert response.status_code == 400
        assert "label" in response.json().get("fields", {})

    def test_a_server_that_is_not_set_up_says_what_to_run(
        self, rig: PushRig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig.app()
        response = client_for(rig, monkeypatch).put(f"/api/apps/{DOMAIN}/push", json={})
        assert response.status_code == 409
        assert "noust push setup" in response.text

    def test_an_application_that_does_not_exist_is_a_404_everywhere(self, api: Any) -> None:
        for method, path in (
            ("get", f"/api/apps/{OTHER}/push"),
            ("put", f"/api/apps/{OTHER}/push"),
            ("delete", f"/api/apps/{OTHER}/push"),
            ("post", f"/api/apps/{OTHER}/push/refresh"),
            ("get", f"/api/apps/{OTHER}/push/keys"),
            ("delete", f"/api/apps/{OTHER}/push/keys/x"),
        ):
            body = {"json": {}} if method == "put" else {}
            response = api.request(method.upper(), path, **body)
            assert response.status_code == 404, (method, path)

    def test_refreshing_writes_the_hook_again(self, api: Any, rig: PushRig) -> None:
        api.put(f"/api/apps/{DOMAIN}/push", json={})
        hook = rig.world.home / f"{DOMAIN}.git" / "hooks" / "post-receive"
        hook.write_text("#!/bin/sh\n")

        body = api.post(f"/api/apps/{DOMAIN}/push/refresh").json()

        assert body["hook_ok"] is True
        assert hook.read_text().startswith("#!/usr/bin/python3")

    def test_refreshing_what_is_not_on_is_a_400(self, api: Any) -> None:
        assert api.post(f"/api/apps/{DOMAIN}/push/refresh").status_code == 400


class TestKeys:
    def test_adding_listing_and_removing(self, api: Any) -> None:
        api.put(f"/api/apps/{DOMAIN}/push", json={})

        added = api.post(f"/api/apps/{DOMAIN}/push/keys", json={"label": "ci", "key": ed25519(2)})

        assert added.status_code == 201, added.text
        assert [key["label"] for key in added.json()] == ["ci"]
        listed = api.get(f"/api/apps/{DOMAIN}/push/keys").json()
        assert listed[0]["fingerprint"].startswith("SHA256:") and "key" not in listed[0]
        left = api.delete(f"/api/apps/{DOMAIN}/push/keys/ci")
        assert left.status_code == 200 and left.json() == []

    def test_a_key_that_is_not_one_is_a_400(self, api: Any) -> None:
        api.put(f"/api/apps/{DOMAIN}/push", json={})
        response = api.post(
            f"/api/apps/{DOMAIN}/push/keys", json={"label": "ci", "key": "nonsense"}
        )
        assert response.status_code == 400
        assert "key" in response.json().get("fields", {})

    def test_a_key_that_is_already_another_applications_is_a_400(
        self, api: Any, rig: PushRig
    ) -> None:
        rig.app(OTHER)
        api.put(f"/api/apps/{DOMAIN}/push", json={"key": ed25519(1), "label": "laptop"})
        api.put(f"/api/apps/{OTHER}/push", json={})

        response = api.post(
            f"/api/apps/{OTHER}/push/keys", json={"label": "laptop", "key": ed25519(1)}
        )

        assert response.status_code == 400
        assert "already pushes to" in response.text

    def test_removing_a_key_that_is_not_there_is_a_400(self, api: Any) -> None:
        api.put(f"/api/apps/{DOMAIN}/push", json={})
        assert api.delete(f"/api/apps/{DOMAIN}/push/keys/ghost").status_code == 400

    def test_a_label_too_long_is_refused_before_the_handler(self, api: Any) -> None:
        response = api.post(
            f"/api/apps/{DOMAIN}/push/keys", json={"label": "x" * 41, "key": ed25519(1)}
        )
        assert response.status_code in (400, 422)
