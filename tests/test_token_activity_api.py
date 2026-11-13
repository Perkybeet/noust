# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/auth/tokens/{token_id}/activity``: what a credential did, and leaving it as it was (item 185).

Driven over HTTP against the whole console. A token makes real calls (the restart of an application, whose
manager is a recorder) and what the audit trail then holds is read back by interventions; what it deployed is
the jobs and deployments of the store. The way back is the one Noust already has, replaced here by a recorder,
and the job is run by the test, so that what is pinned is the route's: who may read what, what the plan says,
the confirmation of a schema change, and the sudo mode and the permission of putting it back.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.accounts import AuthPolicy, passwords
from noust.core.store import App, JobRecord, NoustStore
from noust.core.teams import Scope, TeamStore
from noust.web.api import apps as apps_api
from noust.web.api import token_activity
from noust.web.api.apps import AppActionResponse
from noust.web.auth import SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.support.policy_people import (
    Person,
    forget_assistants,
    issue,
    mark_records_as_assistant,
)
from tests.support.teams_support import make_app

SHOP = "shop.example.com"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def clean(real_config: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    forget_assistants()

    def restart(domain: str, verb: str, past: str) -> AppActionResponse:
        return AppActionResponse(
            success=True, message=f"Application {past}: {domain}", domain=domain
        )

    monkeypatch.setattr(apps_api, "_service_action", restart)
    yield
    forget_assistants()


@pytest.fixture
def app(sandbox: Path) -> Any:
    return create_app(
        SecurityConfig(
            state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
        )
    )


@pytest.fixture
def client(app: Any) -> TestClient:
    return TestClient(app, client=("testclient", 50000))


@pytest.fixture
def marta(app: Any, store: NoustStore, tmp_path: Path) -> Person:
    teams = TeamStore(store)
    make_app(store, SHOP, teams.resolve_target(Scope.everything(), team="default"), tmp_path)
    return Person(app, "marta", "admin")


class Jobs:
    """Stands in for the job manager: keeps what was queued, and the test runs it."""

    queued: list[dict[str, Any]] = []

    def create_job(self, **kwargs: Any) -> Any:
        self.queued.append(kwargs)

        class Queued:
            id = "undo-1"

            class status:
                value = "pending"

            def to_dict(self) -> dict[str, Any]:
                return {"id": "undo-1"}

        return Queued()


class Context:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.steps: list[Any] = []

    def set_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value

    def update(self, step: Any, progress: int) -> None:
        self.steps.append((step, progress))


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> type[Jobs]:
    Jobs.queued = []
    monkeypatch.setattr(token_activity, "get_job_manager", lambda: Jobs())
    return Jobs


class Lifecycle:
    """The way back Noust already has, as a recorder."""

    rolled: list[tuple[str, int, bool]] = []
    changed: dict[int, list[int]] = {}
    unavailable: dict[int, str] = {}


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> type[Lifecycle]:
    Lifecycle.rolled, Lifecycle.changed, Lifecycle.unavailable = [], {}, {}

    def availability(records: Any) -> dict[int, str | None]:
        return {r.id: Lifecycle.unavailable.get(r.id) for r in records}

    def between(records: Any) -> dict[int, list[int]]:
        return {r.id: Lifecycle.changed.get(r.id, []) for r in records}

    def rollback(domain: str, deployment: int, **kwargs: Any) -> Any:
        Lifecycle.rolled.append((domain, deployment, bool(kwargs.get("schema_changed_ok"))))

        class Outcome:
            release_id = None
            history_id = 77

        return Outcome()

    monkeypatch.setattr("noust.deployers.lifecycle.rollback_availability", availability)
    monkeypatch.setattr("noust.deployers.lifecycle.schema_changed_between", between)
    monkeypatch.setattr("noust.deployers.lifecycle.rollback_to_deployment", rollback)
    return Lifecycle


def deploy(store: NoustStore, commit: str, *, by: str | None = None) -> int:
    """A deployment of the shop; one a token queued has the job that links it to its call."""
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


def token_record(name: str) -> dict[str, Any]:
    (found,) = [t for t in get_token_manager().list_api_tokens() if t["name"] == name]
    return found


def url(name: str, tail: str = "") -> str:
    return f"/api/auth/tokens/{token_record(name)['id']}/activity{tail}"


class TestReading:
    def test_the_owner_reads_what_her_token_did_by_interventions(
        self, client: TestClient, marta: Person
    ) -> None:
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        assert client.post(f"/api/apps/{SHOP}/restart", headers=ci).status_code == 200

        listing = marta.get(url("ci"))
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["credential"]["name"] == "ci"
        assert (body["credential"]["kind"], body["credential"]["owner"]) == ("token", "marta")
        assert body["credential"]["revoked"] is False
        assert body["credential"]["clients"][0]["label"] == "pipeline-ci 1.4"
        assert body["credential"]["clients"][0]["declared"] is True
        assert body["complete"] is True and body["days"] == 30
        (found,) = body["interventions"]
        assert (found["calls"], found["changes"], found["apps"]) == (1, 1, [SHOP])
        assert found["ongoing"] is True
        assert found["client"]["label"] == "pipeline-ci 1.4"

    def test_one_intervention_is_its_timeline(self, client: TestClient, marta: Person) -> None:
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        client.post(f"/api/apps/{SHOP}/restart", headers=ci)
        client.post(f"/api/apps/{SHOP}/restart", headers=ci)
        (found,) = marta.get(url("ci")).json()["interventions"]
        detail = marta.get(url("ci", f"/{found['n']}"))
        assert detail.status_code == 200, detail.text
        timeline = detail.json()["timeline"]
        assert len(timeline) == 2
        assert (timeline[0]["method"], timeline[0]["path"]) == ("POST", f"/api/apps/{SHOP}/restart")
        assert (timeline[0]["domain"], timeline[0]["result"], timeline[0]["via"]) == (
            SHOP,
            "ok",
            "api",
        )
        assert detail.json()["undo"] == {"available": False, "items": 0, "irreversible": 0}
        assert marta.get(url("ci", "/99999999")).status_code == 404

    def test_what_a_token_did_is_not_a_secret_from_whoever_reads_the_audit_trail_and_only_from_them(
        self, app: Any, client: TestClient, marta: Person
    ) -> None:
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        client.post(f"/api/apps/{SHOP}/restart", headers=ci)
        vera = Person(app, "vera", "viewer")
        assert vera.get(url("ci")).status_code == 404
        assert vera.get(url("ci", "/1")).status_code == 404
        assert vera.get(url("ci", "/1/undo")).status_code == 404
        sam = Person(app, "sam", "security")
        assert sam.get(url("ci")).status_code == 200
        assert client.get(url("ci")).status_code == 401

    def test_a_token_that_does_not_exist_is_a_404(self, marta: Person) -> None:
        assert marta.get("/api/auth/tokens/999999/activity").status_code == 404

    def test_the_owner_reads_her_own_tokens_and_whoever_governs_accounts_reads_everybodys(
        self, app: Any, client: TestClient, marta: Person
    ) -> None:
        olga = Person(app, "olga", "operator")
        issue(olga.account, "olgas", "apps.read")
        issue(marta.account, "ci", "apps.read")
        assert olga.get(url("olgas")).status_code == 200
        assert olga.get(url("ci")).status_code == 404
        # An administrator governs accounts, and tokens with them.
        assert marta.get(url("olgas")).status_code == 200

    def test_a_team_sees_only_what_names_its_own_applications(
        self, app: Any, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        teams = TeamStore(store)
        for slug in ("acme", "globex"):
            teams.create_team(slug.title(), slug=slug)
        make_app(
            store,
            "shop.acme.example",
            teams.resolve_target(Scope.everything(), team="acme"),
            tmp_path,
        )
        make_app(
            store,
            "blog.globex.example",
            teams.resolve_target(Scope.everything(), team="globex"),
            tmp_path,
        )
        alice = Person(app, "alice", "viewer", team="acme")
        mine = issue(alice.account, "alice-ci", "apps.operate", "apps.read")
        client.post("/api/apps/shop.acme.example/restart", headers=mine)
        found = alice.get(url("alice-ci")).json()["interventions"]
        assert [i["apps"] for i in found] == [["shop.acme.example"]]


class TestLeavingItAsItWas:
    def history(
        self, marta: Person, store: NoustStore, client: TestClient
    ) -> tuple[int, int, dict[str, str]]:
        """Version A served; the token updated to version B."""
        before = deploy(store, "aaa111")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        client.post(f"/api/apps/{SHOP}/restart", headers=ci)
        after = deploy(store, "bbb222", by="ci")
        return before, after, ci

    def test_the_plan_proposes_the_way_back_for_each_application_it_deployed(
        self,
        client: TestClient,
        marta: Person,
        store: NoustStore,
        lifecycle: type[Lifecycle],
    ) -> None:
        before, after, _ci = self.history(marta, store, client)
        (found,) = marta.get(url("ci")).json()["interventions"]
        assert found["deployments"] == 1
        detail = marta.get(url("ci", f"/{found['n']}")).json()
        assert detail["deployments"][0]["id"] == after
        assert detail["timeline"][0]["deployment"]["commit"] == "bbb222"
        assert detail["undo"]["available"] is True

        plan = marta.get(url("ci", f"/{found['n']}/undo"))
        assert plan.status_code == 200, plan.text
        body = plan.json()
        assert body["available"] is True and body["credential"] == "ci"
        (item,) = body["items"]
        assert (item["kind"], item["domain"], item["status"]) == ("rollback", SHOP, "ready")
        assert (item["back_to"], item["back_to_commit"]) == (before, "aaa111")
        assert lifecycle.rolled == []

    def test_putting_it_back_is_a_job_in_sudo_mode_that_says_what_went_back(
        self,
        client: TestClient,
        marta: Person,
        store: NoustStore,
        lifecycle: type[Lifecycle],
        jobs: type[Jobs],
    ) -> None:
        before, _after, _ci = self.history(marta, store, client)
        (found,) = marta.get(url("ci")).json()["interventions"]
        asked = marta.post(url("ci", f"/{found['n']}/undo"), {})
        assert asked.status_code == 403
        assert asked.json()["error"] == "elevation_required"
        assert jobs.queued == []

        marta.elevate()
        queued = marta.post(url("ci", f"/{found['n']}/undo"), {})
        assert queued.status_code == 202, queued.text
        assert queued.json()["job_id"] == "undo-1"
        (job,) = jobs.queued
        assert job["kwargs"]["domains"] == [SHOP]
        assert job["kwargs"]["credential"] == "ci" and job["kwargs"]["intervention"] == found["n"]
        assert job["actor"] == "marta"
        assert lifecycle.rolled == []  # the route queues; the job does

        context = Context()
        result = job["func"](**job["kwargs"], job_context=context)
        assert result["status"] == "undone"
        assert [r["domain"] for r in result["reverted"]] == [SHOP]
        assert lifecycle.rolled == [(SHOP, before, False)]
        assert context.metadata == {"credential": "ci", "intervention": found["n"]}

    def test_going_back_past_a_change_of_the_schema_needs_the_confirmation(
        self,
        client: TestClient,
        marta: Person,
        store: NoustStore,
        lifecycle: type[Lifecycle],
        jobs: type[Jobs],
    ) -> None:
        before, after, _ci = self.history(marta, store, client)
        lifecycle.changed[before] = [after]
        (found,) = marta.get(url("ci")).json()["interventions"]
        plan = marta.get(url("ci", f"/{found['n']}/undo")).json()
        assert plan["items"][0]["status"] == "needs_confirmation"
        assert plan["items"][0]["schema_changed"] == [after]
        marta.elevate()
        refused = marta.post(url("ci", f"/{found['n']}/undo"), {})
        assert refused.status_code == 409
        assert refused.json()["error"] == "schema_changed"
        assert SHOP in refused.json()["detail"]
        assert jobs.queued == []
        confirmed = marta.post(url("ci", f"/{found['n']}/undo"), {"schema_changed_ok": [SHOP]})
        assert confirmed.status_code == 202, confirmed.text
        assert jobs.queued[0]["kwargs"]["confirmed"] == [SHOP]

    def test_what_cannot_go_back_is_a_409_with_the_plan_to_read_why(
        self,
        client: TestClient,
        marta: Person,
        store: NoustStore,
        lifecycle: type[Lifecycle],
        jobs: type[Jobs],
    ) -> None:
        before, _after, _ci = self.history(marta, store, client)
        lifecycle.unavailable[before] = (
            "Release r1 is no longer on disk; rebuild its commit instead"
        )
        (found,) = marta.get(url("ci")).json()["interventions"]
        plan = marta.get(url("ci", f"/{found['n']}/undo")).json()
        assert plan["available"] is False
        assert plan["items"][0]["reason"] == "unavailable"
        assert plan["items"][0]["detail"].startswith("Release r1")
        marta.elevate()
        refused = marta.post(url("ci", f"/{found['n']}/undo"), {})
        assert refused.status_code == 409
        assert refused.json()["error"] == "undo_unavailable"
        assert jobs.queued == []

    def test_only_the_applications_asked_for_are_queued(
        self,
        client: TestClient,
        marta: Person,
        store: NoustStore,
        lifecycle: type[Lifecycle],
        jobs: type[Jobs],
    ) -> None:
        self.history(marta, store, client)
        (found,) = marta.get(url("ci")).json()["interventions"]
        marta.elevate()
        nothing = marta.post(url("ci", f"/{found['n']}/undo"), {"apps": ["other.example.com"]})
        assert nothing.status_code == 409
        asked = marta.post(url("ci", f"/{found['n']}/undo"), {"apps": [SHOP]})
        assert asked.status_code == 202
        assert jobs.queued[0]["kwargs"]["domains"] == [SHOP]

    def test_the_permission_to_put_it_back_is_deploying(self, app: Any, marta: Person) -> None:
        from noust.web.permissions.registry import permission_for_route

        route = "/api/auth/tokens/{token_id}/activity/{n}/undo"
        assert permission_for_route("POST", route) == "apps.deploy"
        assert permission_for_route("GET", route) == "self"


class TestTheAssistantIsOneCredentialForEachPerson:
    """Each conversation of the assistant has a token: for a person they are one entry, one page."""

    @pytest.fixture
    def conversations(
        self, client: TestClient, marta: Person, monkeypatch: pytest.MonkeyPatch
    ) -> dict[str, dict[str, str]]:
        names = ("conv-a", "conv-b", "conv-c")
        tokens = {n: issue(marta.account, n, "apps.operate", "apps.read") for n in names}
        mark_records_as_assistant(monkeypatch, *names)
        for name in names:
            tokens[name]["User-Agent"] = "noust-assistant/1"
            assert client.post(f"/api/apps/{SHOP}/restart", headers=tokens[name]).status_code == 200
        return tokens

    def test_the_list_has_one_entry_for_all_of_them_and_one_for_each_ordinary_token(
        self, marta: Person, conversations: dict[str, dict[str, str]]
    ) -> None:
        issue(marta.account, "ci", "apps.read")
        listed = marta.get("/api/auth/credentials").json()["credentials"]
        kinds = sorted((c["kind"], c["name"], c["tokens"]) for c in listed)
        assert kinds == [("assistant", "assistant:marta", 3), ("token", "ci", 1)]
        (group,) = [c for c in listed if c["kind"] == "assistant"]
        assert group["owner"] == "marta" and group["scope"] is None and group["revoked"] is False
        assert group["id"] == max(token_record(n)["id"] for n in conversations)

    def test_every_token_of_it_opens_the_same_page_whose_interventions_are_the_conversations(
        self, client: TestClient, marta: Person, conversations: dict[str, dict[str, str]]
    ) -> None:
        first = marta.get(url("conv-a")).json()
        last = marta.get(url("conv-c")).json()
        assert first["credential"] == last["credential"]
        credential = first["credential"]
        assert (credential["kind"], credential["name"], credential["tokens"]) == (
            "assistant",
            "assistant:marta",
            3,
        )
        assert credential["owner"] == "marta" and credential["clients"] == []
        conversation_names = {i["conversation"] for i in first["interventions"]}
        assert conversation_names == {"conv-a", "conv-b", "conv-c"}
        assert len(first["interventions"]) == 3
        detail = marta.get(url("conv-b", f"/{first['interventions'][0]['n']}")).json()
        assert detail["timeline"][0]["credential"] in conversation_names
        assert detail["clients"] == []

    def test_an_ordinary_token_is_what_it_was(
        self, client: TestClient, marta: Person, conversations: dict[str, dict[str, str]]
    ) -> None:
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        client.post(f"/api/apps/{SHOP}/restart", headers=ci)
        body = marta.get(url("ci")).json()
        assert body["credential"]["kind"] == "token" and body["credential"]["tokens"] == 1
        assert [i["conversation"] for i in body["interventions"]] == [None]

    def test_the_assistant_of_one_person_is_not_the_assistant_of_another(
        self, app: Any, marta: Person, conversations: dict[str, dict[str, str]]
    ) -> None:
        olga = Person(app, "olga", "operator")
        assert olga.get(url("conv-a")).status_code == 404
        assert olga.get("/api/auth/credentials").json()["credentials"] == []

    def test_whoever_reads_the_audit_trail_sees_the_assistant_of_everybody_as_one_each(
        self, app: Any, marta: Person, conversations: dict[str, dict[str, str]]
    ) -> None:
        sam = Person(app, "sam", "security")
        listed = sam.get("/api/auth/credentials").json()["credentials"]
        assert [(c["kind"], c["tokens"]) for c in listed] == [("assistant", 3)]
        assert sam.get(url("conv-a")).status_code == 200


class TestWhoTheCredentialIs:
    @pytest.mark.parametrize(
        ("record", "kind"),
        [
            ({"scope": "admin"}, "token"),
            ({"scope": "fleet"}, "fleet"),
            ({"scope": "admin", "caller": "assistant"}, "assistant"),
            ({"scope": "admin", "caller": "token"}, "token"),
            ({"scope": "fleet", "caller": "assistant"}, "assistant"),
        ],
    )
    def test_a_token_is_a_token_a_central_s_or_the_assistants(
        self, record: dict[str, Any], kind: str
    ) -> None:
        assert token_activity._kind(record) == kind


def test_the_marker_app_is_available(store: NoustStore) -> None:
    # Guards the fixtures above: an application row is what a plan reads.
    assert App is not None and store is not None
