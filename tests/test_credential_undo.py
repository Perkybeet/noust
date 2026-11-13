# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
"Leave it as it was" (item 185): the plan, and the work.

The plan only proposes: for each application a credential deployed, the way back Noust already has, to the
version before the first thing it deployed there, or why there is none. What it did that has no way back here
is listed, never silently left out. Applying is the work: it plans again at that moment, goes through the
list and says what went back and what did not; twice in a row the second finds it already as it was.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.audit import Actor
from noust.core.audit.log import AuditLog
from noust.core.audit.settings import AuditSettings
from noust.core.credential_activity import CredentialActivity
from noust.core.exceptions import DeploymentError
from noust.core.store import App, JobRecord, NoustStore
from noust.deployers.credential_undo import Undoer

START = datetime(2026, 11, 12, 9, 0, 0, tzinfo=timezone.utc)
CI = Actor(kind="token", id="ci", name="ci", via="bearer")
SHOP = "shop.example.com"
BLOG = "blog.example.com"


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def move(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr("noust.core.audit.log._utcnow", fake)
    return fake


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for domain in (SHOP, BLOG):
        instance.create_app(App(domain=domain, app_type="nodejs", app_path=f"/srv/{domain}"))  # type: ignore[arg-type]
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def log(tmp_path: Path, clock: Clock) -> AuditLog:
    return AuditLog(tmp_path / "state" / "web-audit.log", settings=AuditSettings(journald="off"))


class World:
    """An audit trail, a store and an undoer whose way back is a recorder."""

    def __init__(self, log: AuditLog, store: NoustStore, clock: Clock) -> None:
        self.log, self.store, self.clock = log, store, clock
        self.activity = CredentialActivity(log, store, now=clock)
        self.rolled: list[tuple[str, int, bool]] = []
        self.audited: list[Any] = []
        self.unavailable: dict[int, str] = {}
        self.schema: dict[int, list[int]] = {}
        self.fails: set[str] = set()
        self.undoer = Undoer(
            self.activity,
            store,
            availability=lambda records: {
                r.id: self.unavailable.get(r.id) for r in records if r.id
            },
            schema_between=lambda records: {
                r.id: self.schema.get(r.id, []) for r in records if r.id
            },
            rollback=self.rollback,
            audit=lambda *args, **kwargs: self.audited.append((args, kwargs)),
        )
        self.counter = 0

    def rollback(self, domain: str, deployment: int, *, schema_changed_ok: bool = False) -> Any:
        if domain in self.fails:
            raise DeploymentError(
                f"{domain} did not pass its health check", details="Look at its log."
            )
        self.rolled.append((domain, deployment, schema_changed_ok))

        class Outcome:
            release_id = f"r{deployment}"
            history_id = 900 + deployment

        return Outcome()

    def deployment(
        self, domain: str, commit: str, status: str = "success", *, by: str | None = None
    ) -> int:
        """A deployment row; one a token queued has the job that links it to its call."""
        self.counter += 1
        job = None
        if by is not None:
            job = f"job-{self.counter}"
            self.store.create_job(
                JobRecord(
                    id=job,
                    type="update",
                    name=f"Update {domain}",
                    status="completed",
                    domain=domain,
                    actor=f"token:{by}",
                    created_at=self.clock.now.astimezone().replace(tzinfo=None).isoformat(),
                )
            )
        number = self.store.record_deployment_start(domain, "panel", git_commit=commit, job_id=job)
        self.store.finish_deployment(number, status)
        return number

    def token_updates(self, domain: str, commit: str, status: str = "success") -> int:
        """The token calls the API to update an application, and its job deploys."""
        self.log.append(
            "api.post",
            actor=CI,
            target=f"/api/apps/{domain}/update",
            outcome="ok",
            correlation_id=f"c{self.counter + 1}",
        )
        number = self.deployment(domain, commit, status, by="ci")
        self.clock.move(minutes=2)
        return number

    def plan(self, **kwargs: Any) -> Any:
        found, _ = self.activity.interventions("ci", **kwargs)
        return self.undoer.plan("ci", found[0])

    def apply(self, domains: Any, **kwargs: Any) -> Any:
        found, _ = self.activity.interventions("ci")
        return self.undoer.apply("ci", found[0].n, domains=domains, **kwargs)


@pytest.fixture
def world(log: AuditLog, store: NoustStore, clock: Clock) -> World:
    return World(log, store, clock)


class TestThePlan:
    def test_an_application_goes_back_to_the_version_before_the_first_thing_it_deployed(
        self, world: World
    ) -> None:
        before = world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222")
        second = world.token_updates(SHOP, "ccc333")
        plan = world.plan()
        (item,) = plan.items
        assert item.status == "ready"
        assert (item.domain, item.back_to, item.back_to_commit) == (SHOP, before, "aaa111")
        assert len(item.deployments) == 2 and item.deployments[-1] == second
        assert plan.available is True
        assert plan.to_dict()["items"][0]["kind"] == "rollback"

    def test_the_first_deployment_of_an_application_has_nothing_before_it(
        self, world: World
    ) -> None:
        world.token_updates(SHOP, "aaa111")
        (item,) = world.plan().items
        assert (item.status, item.reason) == ("blocked", "nothing_before")
        assert item.back_to is None
        assert world.plan().available is False

    def test_everything_it_deployed_failing_its_checks_leaves_what_served_serving(
        self, world: World
    ) -> None:
        world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222", status="failed")
        (item,) = world.plan().items
        assert (item.status, item.reason) == ("blocked", "failed_only")

    def test_an_application_that_no_longer_exists_is_said(
        self, world: World, store: NoustStore
    ) -> None:
        world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222")
        store.delete_app(SHOP)
        (item,) = world.plan().items
        assert (item.status, item.reason) == ("blocked", "app_gone")

    def test_a_deployment_of_somebody_else_on_top_is_not_taken_away(self, world: World) -> None:
        world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222")
        theirs = world.deployment(SHOP, "ddd444")
        (item,) = world.plan().items
        assert (item.status, item.reason) == ("blocked", "superseded")
        assert item.superseded_by == [theirs]
        assert "history" in (item.detail or "")

    def test_a_version_that_cannot_be_gone_back_to_says_why_in_nousts_words(
        self, world: World
    ) -> None:
        before = world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222")
        world.unavailable[before] = "Release r1 is no longer on disk; rebuild its commit instead"
        (item,) = world.plan().items
        assert (item.status, item.reason) == ("blocked", "unavailable")
        assert item.detail == "Release r1 is no longer on disk; rebuild its commit instead"

    def test_going_back_past_a_change_of_the_schema_needs_the_confirmation(
        self, world: World
    ) -> None:
        before = world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        changed = world.token_updates(SHOP, "bbb222")
        world.schema[before] = [changed]
        (item,) = world.plan().items
        assert item.status == "needs_confirmation"
        assert item.schema_changed == [changed]
        assert world.plan().available is True

    def test_one_item_for_each_application_it_deployed(self, world: World) -> None:
        world.deployment(SHOP, "aaa111")
        world.deployment(BLOG, "bbb111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "aaa222")
        world.token_updates(BLOG, "bbb222")
        assert {i.domain for i in world.plan().items} == {SHOP, BLOG}

    def test_an_application_a_team_does_not_see_is_not_in_the_plan(self, world: World) -> None:
        world.deployment(SHOP, "aaa111")
        world.deployment(BLOG, "bbb111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "aaa222")
        world.token_updates(BLOG, "bbb222")
        plan = world.plan(visible=lambda domain: domain == SHOP)
        assert [i.domain for i in plan.items] == [SHOP]


class TestWhatCannotBeUndoneHere:
    def call(self, world: World, method: str, path: str, result: str = "ok") -> None:
        world.log.append(
            f"api.{method.lower()}",
            actor=CI,
            target=path,
            outcome=result,
            correlation_id=f"x{world.counter}",
        )
        world.counter += 1
        world.clock.move(minutes=1)

    def test_each_is_listed_with_its_reason_and_what_needs_no_undoing_is_not(
        self, world: World
    ) -> None:
        self.call(world, "DELETE", f"/api/apps/{BLOG}")
        self.call(world, "POST", f"/api/apps/{SHOP}/stop")
        self.call(world, "PUT", f"/api/apps/{SHOP}/env")
        self.call(world, "POST", "/api/databases/postgresql/shop/restore")
        self.call(world, "POST", f"/api/apps/{SHOP}/restart")
        self.call(world, "POST", f"/api/apps/{SHOP}/update")
        self.call(world, "POST", "/api/cron", result="error:403")
        self.call(world, "PUT", f"/api/sites/{SHOP}/config")
        plan = world.plan()
        why = {(i.method, i.path): i.why for i in plan.irreversible}
        assert why == {
            ("DELETE", f"/api/apps/{BLOG}"): "deleted",
            ("POST", f"/api/apps/{SHOP}/stop"): "service_state",
            ("PUT", f"/api/apps/{SHOP}/env"): "variables",
            ("POST", "/api/databases/postgresql/shop/restore"): "database",
            ("PUT", f"/api/sites/{SHOP}/config"): "no_way_back",
        }
        assert all(len(i.detail) > 20 for i in plan.irreversible)
        assert "never the data" in next(i.detail for i in plan.irreversible if i.why == "database")

    def test_a_call_that_was_refused_or_only_proposed_changed_nothing_and_is_not_listed(
        self, world: World
    ) -> None:
        self.call(world, "DELETE", f"/api/apps/{BLOG}", result="error:403")
        world.log.append(
            "approval.request",
            actor=CI,
            target="approval:1",
            correlation_id="p1",
            details={"approval_id": 1, "method": "DELETE", "path": f"/api/apps/{BLOG}"},
        )
        world.log.append(
            "api.delete", actor=CI, target=f"/api/apps/{BLOG}", outcome="ok", correlation_id="p1"
        )
        assert world.plan().irreversible == []


class TestTheWork:
    def test_what_can_go_back_goes_and_the_answer_says_what_went(self, world: World) -> None:
        before = world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222")
        steps: list[Any] = []
        result = world.apply([SHOP], on_step=steps.append)
        assert world.rolled == [(SHOP, before, False)]
        assert [item.domain for item in steps] == [SHOP]
        assert result.status == "undone"
        assert result.reverted == [
            {"domain": SHOP, "back_to": before, "release": f"r{before}", "history": 900 + before}
        ]
        assert result.not_reverted == []
        assert result.to_dict()["status"] == "undone"

    def test_only_the_applications_asked_for_go_back(self, world: World) -> None:
        world.deployment(SHOP, "aaa111")
        world.deployment(BLOG, "bbb111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "aaa222")
        world.token_updates(BLOG, "bbb222")
        world.apply([BLOG])
        assert [domain for domain, _, _ in world.rolled] == [BLOG]

    def test_a_change_of_schema_goes_back_only_when_it_was_confirmed(self, world: World) -> None:
        before = world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        changed = world.token_updates(SHOP, "bbb222")
        world.schema[before] = [changed]
        refused = world.apply([SHOP])
        assert world.rolled == []
        assert refused.status == "nothing"
        assert refused.not_reverted[0]["reason"] == "schema_changed"
        assert "never the database" in refused.not_reverted[0]["detail"]
        done = world.apply([SHOP], confirmed=[SHOP])
        assert world.rolled == [(SHOP, before, True)]
        assert done.status == "undone"

    def test_what_is_blocked_is_reported_not_attempted(self, world: World) -> None:
        world.token_updates(SHOP, "aaa111")
        result = world.apply([SHOP])
        assert world.rolled == []
        assert result.not_reverted[0]["reason"] == "nothing_before"

    def test_one_application_failing_does_not_stop_the_others(self, world: World) -> None:
        world.deployment(SHOP, "aaa111")
        world.deployment(BLOG, "bbb111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "aaa222")
        world.token_updates(BLOG, "bbb222")
        world.fails.add(SHOP)
        result = world.apply([SHOP, BLOG])
        assert result.status == "partly"
        assert [r["domain"] for r in result.reverted] == [BLOG]
        (failed,) = result.not_reverted
        assert failed["domain"] == SHOP and failed["reason"] == "failed"
        assert failed["detail"] == "shop.example.com did not pass its health check"
        assert failed["hint"] == "Look at its log."

    def test_twice_in_a_row_the_second_finds_it_as_it_was(self, world: World) -> None:
        before = world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222")
        world.apply([SHOP])
        # What the rollback leaves: the application serves the version before again, as a new row.
        world.deployment(SHOP, "aaa111")
        again = world.apply([SHOP])
        assert world.rolled == [(SHOP, before, False)]
        assert again.status == "nothing"
        assert again.not_reverted[0]["reason"] == "already"

    def test_what_changed_since_the_person_looked_is_judged_as_it_is_now(
        self, world: World
    ) -> None:
        world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222")
        assert world.plan().items[0].status == "ready"
        world.deployment(SHOP, "ddd444")
        result = world.apply([SHOP])
        assert world.rolled == []
        assert result.not_reverted[0]["reason"] == "superseded"

    def test_an_application_the_caller_does_not_see_does_not_go_back(self, world: World) -> None:
        world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222")
        world.apply([SHOP], visible=lambda domain: False)
        assert world.rolled == []

    def test_an_intervention_the_log_no_longer_holds_is_said(self, world: World) -> None:
        result = world.undoer.apply("ci", 123456, domains=[SHOP])
        assert result.not_reverted[0]["reason"] == "intervention_gone"

    def test_what_has_no_way_back_is_reported_with_the_result(self, world: World) -> None:
        world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222")
        world.log.append(
            "api.delete", actor=CI, target=f"/api/apps/{BLOG}", outcome="ok", correlation_id="d1"
        )
        result = world.apply([SHOP])
        assert [i["why"] for i in result.irreversible] == ["deleted"]


class TestWhatIsPutOnRecord:
    def test_applying_is_on_the_audit_trail_with_what_went_back_and_what_did_not(
        self, world: World
    ) -> None:
        world.deployment(SHOP, "aaa111")
        world.clock.move(hours=1)
        world.token_updates(SHOP, "bbb222")
        world.apply([SHOP])
        ((args, kwargs),) = world.audited
        assert args == ("auth.token.undo",)
        assert kwargs["target"] == "token:ci" and kwargs["outcome"] == "ok"
        assert kwargs["details"]["reverted"] == [SHOP] and kwargs["details"]["not_reverted"] == []

    def test_a_failure_is_on_it_as_one(self, world: World) -> None:
        world.token_updates(SHOP, "aaa111")
        world.apply([SHOP])
        ((_, kwargs),) = world.audited
        assert kwargs["outcome"] == "failure"
        assert kwargs["details"]["not_reverted"] == [{"domain": SHOP, "reason": "nothing_before"}]
