# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a credential did, by interventions (item 185).

Read from the audit trail that is already kept and from the jobs and deployments the store has: an
intervention is what a credential did without a silence of more than thirty minutes, and the timeline says
the action, the resource, how it ended and the deployment it started.
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
from noust.core.credential_activity import (
    GAP,
    CredentialActivity,
    CredentialRef,
    credential_of,
    domain_of_path,
)
from noust.core.store import JobRecord, NoustStore

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
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def log(tmp_path: Path, clock: Clock) -> AuditLog:
    return AuditLog(tmp_path / "state" / "web-audit.log", settings=AuditSettings(journald="off"))


@pytest.fixture
def activity(log: AuditLog, store: NoustStore, clock: Clock) -> CredentialActivity:
    return CredentialActivity(log, store, now=clock)


def call(
    log: AuditLog,
    method: str,
    path: str,
    *,
    corr: str,
    actor: Actor = CI,
    result: str = "ok",
    also: tuple[str, ...] = (),
) -> None:
    """What the API records for a request: the events it raised, then its own line."""
    for name in also:
        log.append(name, actor=actor, target=path, correlation_id=corr)
    log.append(
        f"api.{method.lower()}", actor=actor, target=path, outcome=result, correlation_id=corr
    )


def queue_job(store: NoustStore, clock: Clock, domain: str, *, by: str = "ci") -> int:
    """A job a token queued now, and the deployment it produced."""
    job_id = f"job-{domain.split('.')[0]}-{int(clock.now.timestamp())}"
    queued = clock.now.astimezone().replace(tzinfo=None).isoformat()
    store.create_job(
        JobRecord(
            id=job_id,
            type="update",
            name=f"Update {domain}",
            status="completed",
            domain=domain,
            actor=f"token:{by}",
            created_at=queued,
        )
    )
    return store.record_deployment_start(domain, "panel", git_commit="abc1234", job_id=job_id)


class TestWhoDidWhat:
    @pytest.mark.parametrize(
        ("event", "expected"),
        [
            ({"who": {"kind": "token", "id": "ci", "name": "ci"}}, ("ci", None)),
            ({"actor": "token:ci"}, ("ci", None)),
            (
                {
                    "who": {
                        "kind": "fleet",
                        "id": "fleet-fra",
                        "name": "marta",
                        "via": "fleet:fleet-fra",
                    }
                },
                ("fleet-fra", "marta"),
            ),
            ({"actor": "fleet-fra on behalf of marta"}, ("fleet-fra", "marta")),
            ({"who": {"kind": "user", "id": "3", "name": "marta"}}, None),
            ({"actor": "master"}, None),
            ({"actor": "marta"}, None),
            ({}, None),
        ],
    )
    def test_the_token_behind_an_event(self, event: dict[str, Any], expected: Any) -> None:
        assert credential_of(event) == expected

    def test_the_application_a_route_names(self) -> None:
        assert domain_of_path(f"/api/apps/{SHOP}/update") == SHOP
        assert domain_of_path(f"/api/sites/{BLOG}/config") == BLOG
        assert domain_of_path("/api/config/approval-policy") is None
        assert domain_of_path(None) is None


class TestInterventions:
    def test_what_is_done_without_a_silence_of_half_an_hour_is_one_intervention(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1")
        clock.move(minutes=29, seconds=59)
        call(log, "POST", f"/api/apps/{BLOG}/restart", corr="a2")
        clock.move(minutes=45)
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="b1")

        found, complete = activity.interventions("ci")
        assert complete is True
        assert len(found) == 2
        # Newest first, numbered by the sequence number of the first event each holds.
        assert found[0].n > found[1].n
        assert [len(i.entries) for i in found] == [1, 2]
        assert found[1].apps == [SHOP, BLOG]

    def test_a_silence_longer_than_the_gap_splits_and_the_gap_itself_does_not(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1")
        clock.now += GAP
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a2")
        clock.now += GAP + timedelta(seconds=1)
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a3")
        assert [len(i.entries) for i in activity.interventions("ci")[0]] == [1, 2]

    def test_it_is_ongoing_while_the_last_thing_it_did_is_recent(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1")
        clock.move(minutes=10)
        assert activity.interventions("ci")[0][0].ongoing is True
        clock.move(minutes=21)
        assert activity.interventions("ci")[0][0].ongoing is False

    def test_the_intervention_is_found_by_its_number(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1")
        clock.move(hours=2)
        call(log, "POST", f"/api/apps/{BLOG}/restart", corr="b1")
        older = activity.interventions("ci")[0][1]
        found = activity.intervention("ci", older.n)
        assert found is not None and found.apps == [SHOP]
        assert activity.intervention("ci", 99999) is None

    def test_what_other_credentials_and_people_did_is_not_in_it(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1")
        call(
            log,
            "POST",
            f"/api/apps/{SHOP}/stop",
            corr="p1",
            actor=Actor(kind="user", id="3", name="marta"),
        )
        call(
            log,
            "POST",
            f"/api/apps/{SHOP}/stop",
            corr="o1",
            actor=Actor(kind="token", id="other", name="other"),
        )
        (only,), _ = activity.interventions("ci")
        assert [e.path for e in only.entries] == [f"/api/apps/{SHOP}/restart"]
        assert activity.interventions("ghost")[0] == []

    def test_only_the_window_asked_for_is_read(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="old")
        clock.move(days=40)
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="new")
        assert len(activity.interventions("ci", days=30)[0]) == 1
        assert len(activity.interventions("ci", days=60)[0]) == 2


class TestTheTimeline:
    def test_a_call_is_one_entry_with_what_it_raised_and_how_it_ended(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        call(
            log,
            "POST",
            f"/api/apps/{SHOP}/update",
            corr="a1",
            also=("apps.update",),
        )
        (found,), _ = activity.interventions("ci")
        (entry,) = found.entries
        assert (entry.kind, entry.method, entry.domain, entry.result) == (
            "change",
            "POST",
            SHOP,
            "ok",
        )
        assert entry.path == f"/api/apps/{SHOP}/update"
        assert entry.events == ["apps.update"]
        assert entry.via == "api"
        assert entry.seq is not None and entry.at.startswith("2026-11-12T09:00")

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            ("error:403", "refused"),
            ("error:404", "refused"),
            ("error:500", "failed"),
            ("error:409", "failed"),
        ],
    )
    def test_what_the_api_answered_is_what_it_says(
        self, log: AuditLog, activity: CredentialActivity, result: str, expected: str
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1", result=result)
        (found,), _ = activity.interventions("ci")
        assert found.entries[0].result == expected
        assert found.entries[0].status == int(result.split(":")[1])

    def test_a_call_that_waited_for_a_person_says_so_and_names_the_request(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        log.append(
            "approval.request",
            actor=CI,
            target="approval:7",
            correlation_id="a1",
            details={"approval_id": 7, "method": "POST", "path": f"/api/apps/{SHOP}/restart"},
        )
        log.append(
            "api.post",
            actor=CI,
            target=f"/api/apps/{SHOP}/restart",
            outcome="ok",
            correlation_id="a1",
        )
        (found,), _ = activity.interventions("ci")
        (entry,) = found.entries
        assert (entry.result, entry.approval) == ("proposed", 7)
        assert found.to_dict()["proposed"] == 1 and found.to_dict()["changes"] == 0

    def test_the_call_that_ran_once_approved_names_the_request_too(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        log.append(
            "approval.use",
            actor=CI,
            target="approval:7",
            correlation_id="a2",
            details={"approval_id": 7},
        )
        log.append(
            "api.post",
            actor=CI,
            target=f"/api/apps/{SHOP}/restart",
            outcome="ok",
            correlation_id="a2",
        )
        (found,), _ = activity.interventions("ci")
        assert found.entries[0].approval == 7 and found.entries[0].result == "ok"

    def test_a_tool_of_mcp_is_an_entry_with_its_tool_and_the_reads_are_told_apart(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        mcp = Actor(kind="token", id="ci", name="ci", via="mcp")
        log.append(
            "mcp.call",
            actor=mcp,
            target="tool:restart_application",
            correlation_id="m1",
            details={"tool": "restart_application", "domain": SHOP, "status": 200},
        )
        log.append(
            "api.post",
            actor=mcp,
            target=f"/api/apps/{SHOP}/restart",
            outcome="ok",
            correlation_id="m1",
        )
        log.append(
            "mcp.call",
            actor=mcp,
            target="tool:list_applications",
            correlation_id="m2",
            details={"tool": "list_applications", "status": 200},
        )
        log.append(
            "mcp.call",
            actor=mcp,
            target="tool:restart_application",
            outcome="proposed",
            correlation_id="m3",
            details={"tool": "restart_application", "domain": BLOG, "status": 202, "approval": 9},
        )
        (found,), _ = activity.interventions(
            "ci", is_read_tool=lambda name: name == "list_applications"
        )
        tool, read, proposed = found.entries
        assert (tool.via, tool.kind, tool.tool, tool.domain) == (
            "mcp",
            "tool",
            "restart_application",
            SHOP,
        )
        assert (read.kind, read.changes) == ("read", False)
        assert (proposed.result, proposed.approval) == ("proposed", 9)
        # The call the tool made and the tool's own event are one entry, because they share a request.
        assert len(found.entries) == 3

    def test_the_assistant_is_told_apart_by_its_channel(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        helper = Actor(kind="token", id="ci", name="ci", via="assistant")
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="h1", actor=helper)
        (found,), _ = activity.interventions("ci")
        assert found.entries[0].via == "assistant"

    def test_a_fleet_token_names_the_person_the_central_acted_for(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        fleet = Actor(kind="fleet", id="fleet-fra", name="marta", via="fleet:fleet-fra")
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="f1", actor=fleet)
        (found,), _ = activity.interventions("fleet-fra")
        assert (found.entries[0].via, found.entries[0].operator) == ("fleet", "marta")

    def test_the_machines_own_events_are_not_entries_of_their_own(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        log.append("host.exec", actor=CI, target="unit", correlation_id="a1")
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1")
        (found,), _ = activity.interventions("ci")
        assert len(found.entries) == 1
        assert "host.exec" not in found.entries[0].events

    def test_it_says_what_the_audit_trail_cannot_hold(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        # Nothing was recorded for the credential: there is nothing to say, not an error.
        assert activity.interventions("ci") == ([], True)


class TestTheDeploymentsItStarted:
    def test_a_call_is_linked_to_the_deployment_its_job_produced(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock, store: NoustStore
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/update", corr="a1")
        deployment = queue_job(store, clock, SHOP)
        (found,), _ = activity.interventions("ci")
        linked = found.entries[0].deployment
        assert linked is not None
        assert (linked["id"], linked["domain"], linked["commit"]) == (deployment, SHOP, "abc1234")
        assert found.to_dict()["deployments"] == 1
        assert activity.deployments(found) == [linked]

    def test_a_deployment_somebody_else_queued_is_not_the_credentials(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock, store: NoustStore
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/update", corr="a1")
        queue_job(store, clock, SHOP, by="other")
        (found,), _ = activity.interventions("ci")
        assert found.entries[0].deployment is None

    def test_a_deployment_of_another_application_or_long_after_is_not_linked(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock, store: NoustStore
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/update", corr="a1")
        queue_job(store, clock, BLOG)
        clock.move(hours=3)
        queue_job(store, clock, SHOP)
        (found,), _ = activity.interventions("ci")
        assert found.entries[0].deployment is None

    def test_two_updates_of_one_application_each_get_their_own_deployment(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock, store: NoustStore
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/update", corr="a1")
        first = queue_job(store, clock, SHOP)
        clock.move(minutes=5)
        call(log, "POST", f"/api/apps/{SHOP}/update", corr="a2")
        second = queue_job(store, clock, SHOP)
        (found,), _ = activity.interventions("ci")
        assert [e.deployment["id"] for e in found.entries if e.deployment] == [first, second]

    def test_a_call_that_was_refused_started_nothing(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock, store: NoustStore
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/update", corr="a1", result="error:403")
        queue_job(store, clock, SHOP)
        (found,), _ = activity.interventions("ci")
        assert found.entries[0].deployment is None


class TestWhatATeamSees:
    def test_an_entry_of_an_application_the_caller_does_not_see_is_not_there(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1")
        call(log, "POST", f"/api/apps/{BLOG}/restart", corr="a2")
        (found,), _ = activity.interventions("ci", visible=lambda domain: domain == SHOP)
        assert found.apps == [SHOP]
        assert len(found.entries) == 1

    def test_what_names_no_application_is_the_instances_and_a_team_does_not_see_it(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        call(log, "PUT", "/api/config/approval-policy", corr="a1")
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a2")
        everything, _ = activity.interventions("ci")
        assert len(everything[0].entries) == 2
        (team,), _ = activity.interventions("ci", visible=lambda domain: True)
        assert len(team.entries) == 1

    def test_an_intervention_with_nothing_a_team_sees_is_not_listed(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        call(log, "POST", f"/api/apps/{BLOG}/restart", corr="a1")
        assert activity.interventions("ci", visible=lambda domain: domain == SHOP)[0] == []

    def test_the_deployments_of_an_application_not_seen_are_not_linked(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock, store: NoustStore
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/update", corr="a1")
        queue_job(store, clock, SHOP)
        (found,), _ = activity.interventions("ci", visible=lambda domain: domain == SHOP)
        assert found.entries[0].deployment is not None


class TestTheAssistantIsOneCredential:
    """Each conversation has a token of its own: for a person they are one credential."""

    ASSISTANT = CredentialRef(
        "assistant:marta", ("conv-1", "conv-2", "conv-3"), by_conversation=True
    )

    @staticmethod
    def helper(name: str) -> Actor:
        return Actor(kind="token", id=name, name=name, via="assistant")

    def test_the_conversations_of_a_person_are_the_interventions_of_one_credential(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1", actor=self.helper("conv-1"))
        clock.move(minutes=2)
        # Another conversation begins within half an hour: it is another intervention all the same.
        call(log, "POST", f"/api/apps/{BLOG}/restart", corr="b1", actor=self.helper("conv-2"))
        clock.move(minutes=1)
        call(log, "POST", f"/api/apps/{BLOG}/stop", corr="b2", actor=self.helper("conv-2"))
        found, complete = activity.interventions(self.ASSISTANT)
        assert complete is True
        assert [(i.conversation, len(i.entries)) for i in found] == [("conv-2", 2), ("conv-1", 1)]
        assert found[1].entries[0].credential == "conv-1"
        assert found[0].to_dict()["conversation"] == "conv-2"

    def test_a_long_conversation_is_split_where_thirty_minutes_of_silence_fall(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1", actor=self.helper("conv-1"))
        clock.move(minutes=45)
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a2", actor=self.helper("conv-1"))
        found, _ = activity.interventions(self.ASSISTANT)
        assert [i.conversation for i in found] == ["conv-1", "conv-1"]

    def test_an_ordinary_token_is_not_split_by_who_it_is_and_has_no_conversation(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1")
        clock.move(minutes=2)
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a2")
        (found,), _ = activity.interventions("ci")
        assert found.conversation is None and len(found.entries) == 2

    def test_what_a_token_of_another_person_or_an_ordinary_one_did_is_not_in_it(
        self, log: AuditLog, activity: CredentialActivity
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a1", actor=self.helper("conv-1"))
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a2", actor=self.helper("conv-of-olga"))
        call(log, "POST", f"/api/apps/{SHOP}/restart", corr="a3")
        found, _ = activity.interventions(self.ASSISTANT)
        assert sum(len(i.entries) for i in found) == 1

    def test_what_a_conversation_deployed_is_linked_to_its_call(
        self, log: AuditLog, activity: CredentialActivity, clock: Clock, store: NoustStore
    ) -> None:
        call(log, "POST", f"/api/apps/{SHOP}/update", corr="a1", actor=self.helper("conv-3"))
        deployment = queue_job(store, clock, SHOP, by="conv-3")
        (found,), _ = activity.interventions(self.ASSISTANT)
        assert found.entries[0].deployment is not None
        assert found.entries[0].deployment["id"] == deployment

    def test_a_reference_can_be_made_from_a_name_a_list_or_a_reference(self) -> None:
        assert CredentialRef.of("ci") == CredentialRef("ci", ("ci",))
        assert CredentialRef.of(["a", "b"]).names == ("a", "b")
        assert CredentialRef.of(self.ASSISTANT) is self.ASSISTANT
