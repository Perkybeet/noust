# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A console with the built-in assistant on, a scripted model and applications to look at (item 171).

What the tests of the loop share, and nothing that is a test: one console served over HTTP with two
applications (one in production, one in staging), a signed-in administrator in a browser of their own, the
provider's model scripted from the test, the stand-ins of what the machine answers (the journal, a restart),
and the way a turn is asked and waited for. The turn calls the API back through a client of its own that
carries **only the assistant's token**, never the person's session, as the server does.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.accounts import AuthPolicy, passwords
from noust.core.assistant.records import Proposals, Turns
from noust.core.fs import get_fs
from noust.core.plans.registry import plan_routes
from noust.core.runner import get_runner
from noust.core.store import NoustStore
from noust.core.teams import Scope, TeamStore
from noust.web.api import apps as apps_api
from noust.web.api import assistant_chat
from noust.web.api.apps import AppActionResponse
from noust.web.assistant.turns import registry, tokens
from noust.web.auth import SecurityConfig
from noust.web.events import hub
from noust.web.mcp.gateway import GatewayResponse
from noust.web.server import create_app
from tests.support.policy_people import Person
from tests.support.scripted_model import Reply, ScriptedModel
from tests.support.teams_support import make_app

KEY = "sk-provider-9d8c7b6a5f4e3d2c1b0a"
SHOP = "shop.example.com"
BETA = "beta.example.com"
GOOD_PROBE = Reply.call("get_status", {"service": "probe"})


def _is_a_plan(method: str, path: str) -> bool:
    """
    Args:
        method: A call's method.
        path: Its path, parameters in place.

    Returns:
        Whether it is one of the routes the registry of plans lists: what an operation would do,
        read without doing it.
    """
    return any(
        verb == method.upper()
        and re.fullmatch(re.sub(r"\{[^/{}]+\}", "[^/]+", template), path) is not None
        for verb, template in plan_routes()
    )


class ClientBridge:
    """The API, called with a bearer token and nothing else, as the loop calls it."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        #: What reached the API, in order, without the reads of what a proposal would do.
        self.calls: list[tuple[str, str]] = []
        #: The plans read: a plan is asked with a POST and changes nothing, so it is not a change.
        self.plans: list[tuple[str, str]] = []
        self.raised: list[tuple[str, str]] = []

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str,
        query: Sequence[tuple[str, str]] = (),
        body: Any = None,
        request_id: str | None = None,
    ) -> GatewayResponse:
        (self.plans if _is_a_plan(method, path) else self.calls).append((method, path))
        headers = {"Authorization": f"Bearer {token}"}
        if request_id:
            headers["X-Noust-Request-Id"] = request_id
        response = self.client.request(method, path, params=list(query), json=body, headers=headers)
        return GatewayResponse(
            status=response.status_code,
            headers={k.lower(): v for k, v in response.headers.items()},
            body=response.content,
        )

    def attempt(
        self,
        method: str,
        path: str,
        *,
        token: str,
        query: Sequence[tuple[str, str]] = (),
        body: Any = None,
        request_id: str | None = None,
    ) -> GatewayResponse | None:
        try:
            return self.call(
                method, path, token=token, query=query, body=body, request_id=request_id
            )
        except Exception:
            # What the real bridge does with a route that raises instead of answering.
            self.raised.append((method, path))
            return None


class FakeServices:
    """What the machine answers about a unit: its journal and that it can be restarted."""

    journal = "listening on 3000\n"

    def __init__(self, verbose: bool = False) -> None:
        pass

    def _systemctl(self, verb: str, name: str) -> bool:
        # Through the runner, as the real one does: in a plan that is the rehearsal's own, which
        # writes the order down and runs nothing.
        get_runner().run(["systemctl", verb, f"{name}.service"], timeout=30)
        return True

    def restart(self, name: str) -> bool:
        return self._systemctl("restart", name)

    def stop(self, name: str) -> bool:
        return self._systemctl("stop", name)

    def start(self, name: str) -> bool:
        return self._systemctl("start", name)

    def logs(self, name: str, lines: int = 100) -> str:
        return FakeServices.journal

    def get_status(self, name: str) -> dict[str, Any]:
        return {"exists": True, "active": True}

    def serving_units(self, app: Any) -> list[str]:
        return []


@dataclass
class Lab:
    """A console, a model and the people to ask it things."""

    app: Any
    client: TestClient
    store: NoustStore
    teams: TeamStore
    model: ScriptedModel
    bridge: ClientBridge
    marta: Person
    root: Path
    restarts: list[str] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)

    # -- setting it up -----------------------------------------------------------------------------

    def configure(self, **extra: Any) -> None:
        """Choose the scripted model, probe it, and turn the assistant on."""
        self.model.script(GOOD_PROBE)
        self.marta.elevate()
        response = self.marta.put(
            "/api/assistant/settings",
            {
                "base_url": self.model.base_url,
                "model": "tiny",
                "api_key": KEY,
                "allow_private_host": True,
                **extra,
            },
        )
        assert response.status_code == 200, response.text
        self.marta.elevate()
        response = self.marta.put("/api/assistant/settings", {"enabled": True})
        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is True
        # What the probe asked is not what a turn asked.
        self.model.requests.clear()

    def policy(self, *cells: tuple[str, str, str, str]) -> None:
        """Set cells of the approval policy: ``(caller, environment, permission, value)``."""
        from noust.core.caller_policy import CallerPolicy

        CallerPolicy(self.store).set_cells(list(cells), by="test")

    def write_env(self, domain: str, text: str) -> None:
        """Give an application a ``.env`` whose values Noust knows are secret."""
        folder = self.root / domain
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".env").write_text(text)

    def failed_deployment(
        self,
        domain: str,
        log: str,
        *,
        error: str = "build failed",
        message: str | None = None,
        branch: str | None = None,
    ) -> int:
        """Record a deployment that failed, with its captured log. Returns its number."""
        deployment = self.store.record_deployment_start(
            domain, "panel", git_commit="a3f9c21", git_branch=branch
        )
        if message is not None:
            self.store.annotate_deployment(deployment, commit_message=message)
        self.store.finish_deployment(deployment, "failed", error)
        directory = self.store.db_path.parent / "deploy-logs" / domain
        get_fs().make_dir(directory, mode=0o700, parents=True)
        path = directory / f"{deployment}.log"
        get_fs().write_text(path, log, mode=0o600)
        self.store.annotate_deployment(deployment, log_path=str(path))
        return deployment

    # -- asking ------------------------------------------------------------------------------------

    def conversation(self, person: Person | None = None) -> str:
        who = person or self.marta
        response = who.post("/api/assistant/conversations", {})
        assert response.status_code == 200, response.text
        return str(response.json()["id"])

    def begin(
        self, conversation: str, message: str, person: Person | None = None, **body: Any
    ) -> Any:
        who = person or self.marta
        return who.post(
            f"/api/assistant/conversations/{conversation}/turns", {"message": message, **body}
        )

    def ask(
        self, conversation: str, message: str, person: Person | None = None, **body: Any
    ) -> dict[str, Any]:
        """Ask a question, wait for the turn to end and give it as the console reads it."""
        who = person or self.marta
        response = self.begin(conversation, message, who, **body)
        assert response.status_code == 202, response.text
        turn_id = response.json()["turn"]
        assert registry().wait(turn_id, 60), "the turn did not end"
        return self.turn(conversation, turn_id, who)

    def turn(self, conversation: str, turn_id: str, person: Person | None = None) -> dict[str, Any]:
        who = person or self.marta
        response = who.get(f"/api/assistant/conversations/{conversation}/turns")
        assert response.status_code == 200, response.text
        found = [t for t in response.json()["turns"] if t["id"] == turn_id]
        assert len(found) == 1
        return found[0]  # type: ignore[no-any-return]

    def requests(self) -> list[Any]:
        return self.model.chat_requests()

    def proposals(self, conversation: str) -> list[Any]:
        return Proposals(self.store).for_conversation(conversation, int(self.marta.account.id))

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        """What the assistant announced on the stream, as events."""
        found: list[dict[str, Any]] = []
        for frame in self.frames:
            name, _, rest = frame.partition("\n")
            if name != "event: assistant":
                continue
            payload = json.loads(rest.removeprefix("data: ").strip())
            if kind is None or payload["type"] == kind:
                found.append(payload)
        return found

    def audit_events(self) -> list[dict[str, Any]]:
        from noust.core import audit

        path = audit.get_log().path
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def build_lab(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel
) -> Iterator[Lab]:
    """
    Build the console and everything a turn is asked of.

    Args:
        sandbox: The test's own directory.
        monkeypatch: Patching helper.
        model: The scripted model.

    Yields:
        The lab.
    """
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)
    NoustStore.reset_instance()
    store = NoustStore(sandbox / "noust.db")
    config = SecurityConfig(
        state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
    )
    app = create_app(config)
    teams = TeamStore(store)
    teams.create_environment("default", "default", "Staging")
    production = teams.resolve_target(Scope.everything(), team="default")
    staging = teams.resolve_target(Scope.everything(), team="default", environment="staging")
    make_app(store, SHOP, production, sandbox / "apps")
    make_app(store, BETA, staging, sandbox / "apps")
    FakeServices.journal = "listening on 3000\n"
    lab = Lab(
        app=app,
        client=TestClient(app, client=("testclient", 50000)),
        store=store,
        teams=teams,
        model=model,
        bridge=ClientBridge(TestClient(app, client=("testclient", 50000))),
        marta=Person(app, "marta", "admin"),
        root=sandbox / "apps",
    )

    def action(domain: str, verb: str, past: str) -> AppActionResponse:
        lab.restarts.append(f"{verb} {domain}")
        return AppActionResponse(
            success=True, message=f"Application {past}: {domain}", domain=domain
        )

    monkeypatch.setattr(apps_api, "_service_action", action)
    monkeypatch.setattr(apps_api, "ServiceManager", FakeServices)
    monkeypatch.setattr(assistant_chat, "bridge_for", lambda request, loop: lab.bridge)
    tokens().clear()
    hub.attach(lab.frames.append)
    try:
        yield lab
    finally:
        hub.detach(lab.frames.append)
        registry().cancel_all()
        tokens().clear()
        NoustStore.reset_instance()


__all__ = [
    "BETA",
    "GOOD_PROBE",
    "KEY",
    "SHOP",
    "FakeServices",
    "Lab",
    "Reply",
    "Turns",
    "build_lab",
]
