# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A call the approval policy holds for a person (item 184), at the level of the manager.

A request made by the policy and one made by the second-person rule are the same request with the same
fingerprint, once, for thirty minutes; what differs is who may decide it. A policy request is decided by
a person who holds what the call needs, and that can be the owner of the token (she is supervising her own
tool); a rule request still asks for another person, as it always did; a token, the master token and a
central never decide either.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import AccountManager, passwords
from noust.core.accounts.approvals import (
    ORIGIN_BOTH,
    ORIGIN_POLICY,
    ORIGIN_RULE,
    POLICY_RULE,
    ROOT_EQUIVALENT_RULE,
    ApprovalActor,
    ApprovalDenied,
    ApprovalManager,
    ApprovalPolicy,
    rule_for,
    snapshot,
)
from noust.core.caller_policy import CallerPolicy
from noust.core.store import NoustStore

PASSWORD = "correct horse battery staple"
CALL = b'{"commit": "abc123"}'
PATH = "/api/apps/shop.example.com/update"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def manager(store: NoustStore) -> ApprovalManager:
    return ApprovalManager(store, policy=ApprovalPolicy(enabled=False))


def account(
    store: NoustStore, name: str, role: str, *permissions: str, person: str | None = None
) -> ApprovalActor:
    made = AccountManager(store).create(name, role, password=PASSWORD, person_ref=person)
    return ApprovalActor(
        kind="account",
        id=str(made.id),
        name=made.username,
        role=made.role,
        person_ref=made.person_ref,
        permissions=frozenset(permissions),
    )


def token_of(owner: ApprovalActor, name: str = "ci") -> ApprovalActor:
    return ApprovalActor(
        kind="token", id=name, name=f"token:{name}", role=owner.role, account=owner.id
    )


def ask(
    manager: ApprovalManager,
    requester: ApprovalActor,
    *,
    origin: str = ORIGIN_POLICY,
    rule: Any = POLICY_RULE,
    needs: tuple[str, ...] = ("apps.deploy",),
    domain: str | None = "shop.example.com",
    environment: str | None = "production",
    caller: str | None = "token",
    client: dict[str, Any] | None = None,
) -> Any:
    digest, parameters = snapshot("POST", PATH, [], CALL)
    request, created = manager.request(
        rule,
        method="POST",
        path=PATH,
        parameters=parameters,
        fingerprint=digest,
        reason=None,
        requester=requester,
        origin=origin,
        caller=caller,
        environment=environment,
        domain=domain,
        needs=needs,
        client=client,
    )
    assert created
    return request


class TestWhatARequestRemembers:
    def test_who_asked_where_and_what_it_needs_survive_the_store(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin")
        client = {"name": "deploy-bot", "version": "1.2", "source": "user_agent", "declared": True}
        made = ask(manager, token_of(owner), client=client)
        found = manager.get(made.id)
        assert found.origin == ORIGIN_POLICY
        assert found.caller == "token"
        assert found.environment == "production"
        assert found.domain == "shop.example.com"
        assert found.needs == ("apps.deploy",)
        assert found.client == client
        shown = found.to_dict()
        assert shown["origin"] == ORIGIN_POLICY
        assert shown["needs"] == ["apps.deploy"]
        assert shown["client"] == client
        assert shown["action"] == POLICY_RULE.action
        assert shown["description"] == POLICY_RULE.description

    def test_a_request_of_the_rule_alone_keeps_saying_so(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin")
        digest, parameters = snapshot("POST", "/api/cron", [], b"{}")
        request, _ = manager.request(
            ROOT_EQUIVALENT_RULE,
            method="POST",
            path="/api/cron",
            parameters=parameters,
            fingerprint=digest,
            reason="why",
            requester=owner,
        )
        assert request.origin == ORIGIN_RULE
        assert request.caller is None
        assert request.needs == ()
        assert request.client is None
        assert request.environment is None

    def test_a_row_from_before_has_no_origin_and_is_a_rule(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin")
        made = ask(manager, owner, origin=ORIGIN_RULE, rule=ROOT_EQUIVALENT_RULE)
        with store._transaction() as cursor:
            cursor.execute(
                "UPDATE approval_requests SET origin = NULL, caller = NULL, needs = NULL, "
                "client = NULL, domain = NULL, environment = NULL WHERE id = ?",
                (made.id,),
            )
        found = manager.get(made.id)
        assert found.origin == ORIGIN_RULE
        assert found.needs == ()
        assert found.client is None

    def test_an_origin_nobody_defined_is_refused_before_it_is_kept(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin")
        with pytest.raises(ValueError, match="origin"):
            ask(manager, token_of(owner), origin="whenever")

    def test_the_list_can_be_asked_for_the_calls_of_a_class_or_an_origin(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin")
        ask(manager, token_of(owner, "ci"), caller="token")
        digest, parameters = snapshot("POST", "/api/other", [], b"{}")
        manager.request(
            POLICY_RULE,
            method="POST",
            path="/api/other",
            parameters=parameters,
            fingerprint=digest,
            reason=None,
            requester=token_of(owner, "helper"),
            origin=ORIGIN_POLICY,
            caller="assistant",
            needs=("apps.operate",),
        )
        assert {r.requester.name for r in manager.list(caller="token")} == {"token:ci"}
        assert {r.requester.name for r in manager.list(caller="assistant")} == {"token:helper"}
        assert len(manager.list(origin=ORIGIN_POLICY)) == 2
        assert manager.list(origin=ORIGIN_RULE) == []


class TestWhoDecidesWhatThePolicyHeld:
    def test_the_owner_of_the_token_may_decide_it_when_she_holds_what_the_call_needs(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        made = ask(manager, token_of(owner))
        assert manager.eligibility(made, owner) is None
        approved = manager.approve(made.id, owner)
        assert approved.state == "approved"
        assert approved.decider is not None and approved.decider.name == "marta"

    def test_another_person_who_holds_it_may_too_whatever_their_role(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        operator = account(store, "olga", "operator", "apps.deploy")
        made = ask(manager, token_of(owner))
        assert manager.eligibility(made, operator) is None

    def test_a_person_who_lacks_what_the_call_needs_may_not(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        viewer = account(store, "vera", "viewer", "apps.read")
        made = ask(manager, token_of(owner), needs=("apps.deploy", "backups.run"))
        refusal = manager.eligibility(made, viewer)
        assert refusal is not None
        assert "apps.deploy" in refusal and "backups.run" in refusal
        # Holding one of two is not holding what the call needs.
        partial = account(store, "pablo", "operator", "apps.deploy")
        assert manager.eligibility(made, partial) is not None
        with pytest.raises(ApprovalDenied):
            manager.approve(made.id, viewer)

    def test_a_token_the_master_token_and_a_central_never_decide(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        made = ask(manager, token_of(owner))
        for kind, name in (("token", "token:other"), ("master", "master"), ("fleet", "fleet-x")):
            decider = ApprovalActor(
                kind=kind,
                id=name,
                name=name,
                role="admin",
                account=owner.id,
                permissions=frozenset({"apps.deploy"}),
            )
            refusal = manager.eligibility(made, decider)
            assert refusal is not None and "token" in refusal

    def test_the_token_itself_never_approves_what_it_proposed(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        made = ask(manager, token_of(owner))
        same = ApprovalActor(
            kind="token",
            id="ci",
            name="token:ci",
            role="admin",
            account=owner.id,
            permissions=frozenset({"apps.deploy"}),
        )
        assert manager.eligibility(made, same) is not None

    def test_the_command_line_as_root_decides_it_like_any_request(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        made = ask(manager, token_of(owner))
        root = ApprovalActor(kind="cli", id="0", name="root")
        assert manager.eligibility(made, root) is None

    def test_a_decider_with_no_permissions_known_holds_none(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        nobody = ApprovalActor(kind="account", id="999", name="x", role="admin")
        made = ask(manager, token_of(owner))
        assert manager.eligibility(made, nobody) is not None

    def test_the_decision_and_the_use_are_the_flow_that_exists(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        token = token_of(owner)
        made = ask(manager, token)
        manager.approve(made.id, owner, "go")
        digest, _ = snapshot("POST", PATH, [], CALL)
        used = manager.consume(made.id, token, digest)
        assert used.state == "executed"
        with pytest.raises(Exception, match="already used"):
            manager.consume(made.id, token, digest)

    def test_a_call_other_than_the_one_proposed_is_not_what_was_approved(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        token = token_of(owner)
        made = ask(manager, token)
        manager.approve(made.id, owner)
        other, _ = snapshot("POST", PATH, [], b'{"commit": "evil"}')
        with pytest.raises(Exception, match="not the one that was approved"):
            manager.consume(made.id, token, other)


class TestWhatTheSecondPersonRuleStillAsks:
    def test_the_owner_of_a_token_still_cannot_approve_what_the_rule_holds(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "sam", "security", "apps.deploy")
        made = ask(manager, token_of(owner), origin=ORIGIN_RULE, rule=ROOT_EQUIVALENT_RULE)
        refusal = manager.eligibility(made, owner)
        assert refusal is not None and "own request" in refusal

    def test_a_call_both_hold_asks_for_the_second_person_too(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        officer = account(store, "sam", "security", "apps.deploy")
        made = ask(manager, token_of(owner), origin=ORIGIN_BOTH, rule=ROOT_EQUIVALENT_RULE)
        assert manager.eligibility(made, owner) is not None
        assert manager.eligibility(made, officer) is None

    def test_a_rule_that_needs_a_security_officer_is_not_opened_to_whoever_holds_the_permission(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        owner = account(store, "marta", "admin", "apps.deploy")
        operator = account(store, "olga", "operator", "apps.deploy")
        made = ask(manager, token_of(owner), origin=ORIGIN_RULE, rule=ROOT_EQUIVALENT_RULE)
        assert manager.eligibility(made, operator) is not None


class TestChangingThePolicyItself:
    def test_a_change_that_loosens_asks_for_a_second_person_and_one_that_tightens_does_not(
        self, store: NoustStore
    ) -> None:
        policy = CallerPolicy(store, production_floor=False)
        policy.set_cells([("token", "production", "apps.deploy", "propose")], by="marta")
        loosening = {
            "cells": [
                {
                    "caller": "token",
                    "environment": "production",
                    "permission": "apps.deploy",
                    "value": "alone",
                }
            ]
        }
        tightening = {
            "cells": [
                {
                    "caller": "token",
                    "environment": "production",
                    "permission": "apps.deploy",
                    "value": "never",
                }
            ]
        }
        template = "/api/config/approval-policy"
        assert rule_for("PUT", template, ["security.manage"], loosening, {}) is not None
        assert rule_for("PUT", template, ["security.manage"], tightening, {}) is None

    def test_putting_a_cell_back_to_what_is_shipped_loosens_when_it_was_stricter(
        self, store: NoustStore
    ) -> None:
        CallerPolicy(store, production_floor=False).set_cells(
            [("token", "production", "apps.deploy", "never")], by="marta"
        )
        body = {
            "reset": [{"caller": "token", "environment": "production", "permission": "apps.deploy"}]
        }
        rule = rule_for("PUT", "/api/config/approval-policy", ["security.manage"], body, {})
        assert rule is not None and rule.action == "approvals.policy"

    def test_a_body_that_names_no_cell_that_exists_is_left_to_the_handler_to_refuse(
        self, store: NoustStore
    ) -> None:
        template = "/api/config/approval-policy"
        for body in (None, [], {"cells": "x"}, {"cells": [{"caller": "person"}]}, {"cells": [3]}):
            assert rule_for("PUT", template, ["security.manage"], body, {}) is None

    def test_the_schema_publishes_it(self) -> None:
        from noust.core.accounts.approvals import route_requirement

        found = route_requirement("PUT", "/api/config/approval-policy", ["security.manage"])
        assert found is not None
        assert found["action"] == "approvals.policy"
        assert found["kind"] == "role_change"
        assert "more" in found["when"]
