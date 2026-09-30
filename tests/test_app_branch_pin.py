"""
Pinning the branch an application deploys from.

With a branch pinned, a webhook push to any other branch is ignored and every
update builds the pinned one; without one, any push deploys. The webhook's
guided setup offers "Pin the branch"; this is what it calls, and the CLI's
``noust app branch``. The branch must exist on the remote, asked with the
non-interactive git (``GIT_TERMINAL_PROMPT=0``) before anything is recorded.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import NoustError, SourceError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import App, NoustStore
from noust.deployers import lifecycle
from noust.managers.source_manager import _GIT_SAFE_CONFIG
from noust.web.permissions.routes_apps import ROUTES

DOMAIN = "shop.example.com"
SOURCE = "https://github.com/example/shop.git"
HEAD = "4f1c2b9d8e7a6f5c4b3a29181716151413121110"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    monkeypatch.setattr("noust.cli.commands.app.get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def git(runner: FakeRunner) -> Iterator[FakeRunner]:
    """git answering ls-remote for the release branch only."""
    git = ["git", *_GIT_SAFE_CONFIG, "ls-remote", "--exit-code", "--", SOURCE]
    runner.script([*git, "refs/heads/release"], stdout=f"{HEAD}\trefs/heads/release\n")
    runner.script([*git, "refs/heads/nope"], exit_code=2)
    set_runner(runner)
    yield runner
    set_runner(None)


def app(store: NoustStore, *, source: str = SOURCE, branch: str | None = "main") -> App:
    return store.create_app(
        App(domain=DOMAIN, app_type="nodejs", source=source, branch=branch, app_path="/tmp/x")
    )


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record",
        lambda event, **kw: events.append({"event": event, **kw}),
    )
    return events


class TestPinning:
    def test_a_branch_that_exists_is_pinned_and_audited(
        self, store: NoustStore, git: FakeRunner, audited: list[dict[str, Any]]
    ) -> None:
        app(store)

        pin = lifecycle.set_branch(DOMAIN, "release")

        assert (pin.branch, pin.commit, pin.previous) == ("release", HEAD, "main")
        assert store.get_app(DOMAIN).branch == "release"  # type: ignore[union-attr]
        # Recorded as chosen, so an update follows it rather than the checkout.
        assert lifecycle.pinned_branch(DOMAIN) == "release"
        [call] = [c for c in git.calls if "ls-remote" in c]
        index = git.calls.index(call)
        assert (git.envs[index] or {}).get("GIT_TERMINAL_PROMPT") == "0"
        [event] = audited
        assert event["event"] == "apps.source"
        assert event["target"] == f"app:{DOMAIN}"
        assert event["details"] == {"branch": "release", "previous": "main", "commit": HEAD}

    def test_a_branch_the_remote_does_not_have_is_refused_and_nothing_changes(
        self, store: NoustStore, git: FakeRunner, audited: list[dict[str, Any]]
    ) -> None:
        app(store)

        with pytest.raises(SourceError, match="does not exist"):
            lifecycle.set_branch(DOMAIN, "nope")

        assert store.get_app(DOMAIN).branch == "main"  # type: ignore[union-attr]
        assert lifecycle.pinned_branch(DOMAIN) is None
        assert audited == []

    def test_unpinning_asks_the_remote_nothing(
        self, store: NoustStore, git: FakeRunner, audited: list[dict[str, Any]]
    ) -> None:
        app(store)
        lifecycle.set_branch(DOMAIN, "release")
        git.calls.clear()
        audited.clear()

        pin = lifecycle.set_branch(DOMAIN, None)

        assert (pin.branch, pin.commit) == (None, None)
        assert store.get_app(DOMAIN).branch is None  # type: ignore[union-attr]
        assert lifecycle.pinned_branch(DOMAIN) is None
        assert not [c for c in git.calls if c[:1] == ("git",)]
        assert audited[0]["details"]["branch"] is None

    def test_an_application_not_deployed_from_git_has_no_branch(
        self, store: NoustStore, git: FakeRunner
    ) -> None:
        app(store, source="/srv/site")

        with pytest.raises(SourceError, match="Not a git source"):
            lifecycle.set_branch(DOMAIN, "release")

    def test_an_unknown_application(self, store: NoustStore, git: FakeRunner) -> None:
        with pytest.raises(NoustError, match="not found"):
            lifecycle.set_branch(DOMAIN, "release")

    def test_an_unsafe_name_is_refused_before_git_runs(
        self, store: NoustStore, git: FakeRunner
    ) -> None:
        app(store)

        with pytest.raises(SourceError):
            lifecycle.set_branch(DOMAIN, "--upload-pack=touch /tmp/x")

        assert not [c for c in git.calls if c[:1] == ("git",)]


class TestTheApi:
    @pytest.fixture
    def client(self, store: NoustStore, git: FakeRunner) -> TestClient:
        from noust.web.api import apps as apps_api
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers, require_elevated

        server = FastAPI()
        install_error_handlers(server)
        server.include_router(apps_api.router, prefix="/api/apps")
        session = {"sid": "operator", "type": "master"}
        server.dependency_overrides[get_current_session] = lambda: session
        server.dependency_overrides[require_elevated] = lambda: session
        return TestClient(server, raise_server_exceptions=False)

    def test_pinning_answers_the_branch_and_its_head(
        self, client: TestClient, store: NoustStore
    ) -> None:
        app(store)

        response = client.patch(f"/api/apps/{DOMAIN}/branch", json={"branch": "release"})

        assert response.status_code == 200, response.text
        assert response.json() == {
            "domain": DOMAIN,
            "branch": "release",
            "pinned": True,
            "commit": HEAD,
            "previous": "main",
        }

    def test_unpinning_with_null(self, client: TestClient, store: NoustStore) -> None:
        app(store)

        response = client.patch(f"/api/apps/{DOMAIN}/branch", json={"branch": None})

        assert response.status_code == 200, response.text
        assert response.json()["pinned"] is False

    def test_a_missing_branch_is_a_400_with_git_s_answer(
        self, client: TestClient, store: NoustStore
    ) -> None:
        app(store)

        response = client.patch(f"/api/apps/{DOMAIN}/branch", json={"branch": "nope"})

        assert response.status_code == 400, response.text
        assert "does not exist" in response.text

    def test_the_route_needs_apps_manage(self) -> None:
        assert ROUTES[("PATCH", "/api/apps/{domain}/branch")] == "apps.manage"


class TestTheCommand:
    def test_pin_show_and_unpin(self, store: NoustStore, git: FakeRunner) -> None:
        from noust.cli.app import cli as root_cli

        app(store)

        pinned = CliRunner().invoke(root_cli, ["app", "branch", DOMAIN, "release"])
        shown = CliRunner().invoke(root_cli, ["app", "branch", DOMAIN, "--json"])
        unpinned = CliRunner().invoke(root_cli, ["app", "branch", DOMAIN, "--unpin"])

        assert pinned.exit_code == 0, pinned.output
        assert "release" in pinned.output
        assert json.loads(shown.output) == {"domain": DOMAIN, "branch": "release", "pinned": True}
        assert unpinned.exit_code == 0, unpinned.output
        assert store.get_app(DOMAIN).branch is None  # type: ignore[union-attr]
