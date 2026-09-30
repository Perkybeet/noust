"""
Four-eyes approvals (ENS G08): which calls need one, who decides, and single use.

:class:`~noust.core.accounts.approvals.ApprovalManager` is the one
implementation: the console's chokepoint and ``noust approval`` call it. What
is pinned here is the ENS review's rule (§4.2.2, §4.9) as the spec decided it
(§2.5): a request snapshots one exact call; by default only ``security``
decides, an ``admin`` other than the requester may approve infrastructure when
the operator allows it, never a role change; nobody decides their own request
or one of another account of theirs; an approval allows that call once, by
that requester, for a limited time.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.accounts import AccountManager, passwords
from noust.core.accounts.approvals import (
    KIND_INFRASTRUCTURE,
    KIND_ROLE_CHANGE,
    ApprovalActor,
    ApprovalDenied,
    ApprovalError,
    ApprovalManager,
    ApprovalNotFound,
    ApprovalPolicy,
    build_approval_policy,
    rule_for,
    snapshot,
)
from noust.core.store import NoustStore

PASSWORD = "correct horse battery staple"


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


class Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def manager_with(store: NoustStore, clock: Clock, **policy: Any) -> ApprovalManager:
    settings = {"enabled": True, **policy}
    return ApprovalManager(store, policy=ApprovalPolicy(**settings), clock=clock)


@pytest.fixture
def manager(store: NoustStore, clock: Clock) -> ApprovalManager:
    return manager_with(store, clock)


def person(store: NoustStore, name: str, role: str, person_ref: str | None = None) -> ApprovalActor:
    account = AccountManager(store).create(name, role, password=PASSWORD, person_ref=person_ref)
    return ApprovalActor(
        kind="account",
        id=str(account.id),
        name=account.username,
        role=account.role,
        person_ref=account.person_ref,
    )


ROOT_RULE = rule_for("POST", "/api/cron", ["root_equivalent"], {"name": "x"}, {})


def ask(
    manager: ApprovalManager,
    requester: ApprovalActor,
    *,
    body: bytes = b'{"name": "nightly", "command": "/usr/bin/true"}',
    reason: str | None = "Weekly report",
) -> Any:
    assert ROOT_RULE is not None
    digest, parameters = snapshot("POST", "/api/cron", [], body)
    request, _created = manager.request(
        ROOT_RULE,
        method="POST",
        path="/api/cron",
        parameters=parameters,
        fingerprint=digest,
        reason=reason,
        requester=requester,
    )
    return request, digest


def events() -> list[dict[str, Any]]:
    path = audit.get_log().path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestWhatNeedsApproval:
    @pytest.mark.parametrize(
        ("method", "template", "permissions", "body", "action", "kind"),
        [
            ("POST", "/api/cron", ["root_equivalent"], {}, "root_equivalent", KIND_INFRASTRUCTURE),
            (
                "PUT",
                "/api/services/{name}/config",
                ["root_equivalent"],
                {},
                "root_equivalent",
                KIND_INFRASTRUCTURE,
            ),
            (
                "PUT",
                "/api/sites/{domain}/config",
                ["root_equivalent"],
                {},
                "root_equivalent",
                KIND_INFRASTRUCTURE,
            ),
            (
                "POST",
                "/api/backup-schedules",
                ["root_equivalent"],
                {},
                "root_equivalent",
                KIND_INFRASTRUCTURE,
            ),
            ("POST", "/api/nodes", ["fleet.manage"], {}, "fleet.node.add", KIND_INFRASTRUCTURE),
            (
                "DELETE",
                "/api/nodes/{node}",
                ["fleet.manage"],
                None,
                "fleet.node.remove",
                KIND_INFRASTRUCTURE,
            ),
            (
                "POST",
                "/api/databases/query",
                ["databases.write"],
                {"mode": "write"},
                "db.query.write",
                KIND_INFRASTRUCTURE,
            ),
            (
                "POST",
                "/api/apps",
                ["apps.manage"],
                {"source": "/srv/shop"},
                "apps.local_source",
                KIND_INFRASTRUCTURE,
            ),
        ],
    )
    def test_the_actions_the_spec_names(
        self, method: str, template: str, permissions: list[str], body: Any, action: str, kind: str
    ) -> None:
        rule = rule_for(method, template, permissions, body, {})
        assert rule is not None
        assert (rule.action, rule.kind) == (action, kind)

    @pytest.mark.parametrize(
        ("method", "template", "permissions", "body"),
        [
            ("GET", "/api/cron", ["apps.read"], None),
            ("POST", "/api/cron/{name}/run", ["apps.operate"], None),
            ("POST", "/api/services/verify", ["root_equivalent"], {"content": "[Unit]"}),
            ("POST", "/api/databases/query", ["databases.write"], {"mode": "read"}),
            ("POST", "/api/apps", ["apps.manage"], {"source": "https://github.com/a/b.git"}),
            ("GET", "/api/nodes/{node}/key", ["fleet.manage"], None),
            ("POST", "/api/nodes/{node}/test", ["fleet.read"], None),
        ],
    )
    def test_everything_else_does_not(
        self, method: str, template: str, permissions: list[str], body: Any
    ) -> None:
        assert rule_for(method, template, permissions, body, {}) is None

    def test_a_role_change_does_and_other_account_edits_do_not(self, store: NoustStore) -> None:
        AccountManager(store).create("maria", "operator", password=PASSWORD)
        template = "/api/auth/accounts/{username}"
        change = rule_for(
            "PATCH", template, ["accounts.manage"], {"role": "admin"}, {"username": "maria"}
        )
        assert change is not None and change.kind == KIND_ROLE_CHANGE
        assert (
            rule_for(
                "PATCH", template, ["accounts.manage"], {"role": "operator"}, {"username": "maria"}
            )
            is None
        )
        assert (
            rule_for(
                "PATCH", template, ["accounts.manage"], {"display_name": "M"}, {"username": "maria"}
            )
            is None
        )


class TestPolicy:
    def test_off_by_default_and_on_under_the_ens_profile(self) -> None:
        assert not build_approval_policy({}).enabled
        assert build_approval_policy({"profile": "ens-medium"}).enabled
        assert build_approval_policy({"enabled": True}).enabled

    def test_security_decides_unless_admins_are_allowed_too(self) -> None:
        assert build_approval_policy({"enabled": True}).approvers == ("security",)
        wider = build_approval_policy({"enabled": True, "approvers": ["security", "admin"]})
        assert wider.approvers == ("security", "admin")
        odd = build_approval_policy({"enabled": True, "approvers": ["operator", "viewer"]})
        assert odd.approvers == ("security",)

    def test_the_profile_asks_for_a_reason(self) -> None:
        assert build_approval_policy({"profile": "ens-medium"}).reason_required
        assert not build_approval_policy({"enabled": True}).reason_required


class TestDeciding:
    def test_security_approves_and_the_requester_executes_once(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        alice = person(store, "alice", "admin")
        sam = person(store, "sam", "security")
        request, digest = ask(manager, alice)
        assert request.state == "requested"

        approved = manager.approve(request.id, sam, "Checked the command")
        assert approved.state == "approved"
        assert approved.decider is not None and approved.decider.name == "sam"

        executed = manager.consume(request.id, alice, digest)
        assert executed.state == "executed"
        with pytest.raises(ApprovalError) as caught:
            manager.consume(request.id, alice, digest)
        assert caught.value.code == "approval_used"

    def test_nobody_decides_their_own_request(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        sam = person(store, "sam", "security")
        request, _digest = ask(manager, sam)
        with pytest.raises(ApprovalDenied):
            manager.approve(request.id, sam)

    def test_nor_with_another_account_of_theirs(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        maria = person(store, "maria", "admin", person_ref="maria@example.com")
        AccountManager(store).add_exception(
            "maria@example.com", "Sole operator of the site", days=30
        )
        maria_sec = person(store, "maria.sec", "security", person_ref="maria@example.com")
        request, _digest = ask(manager, maria)
        with pytest.raises(ApprovalDenied, match="same person"):
            manager.approve(request.id, maria_sec)

    def test_nor_through_a_token_of_their_own(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        # Security review 3.1, finding 2: a token is its owner when deciding.
        sam = person(store, "sam", "security")
        token = ApprovalActor(
            kind="token", id="sam-ci", name="token:sam-ci", role="security", account=sam.id
        )
        request, _digest = ask(manager, token)
        assert manager.get(request.id).requester.account == sam.id
        with pytest.raises(ApprovalDenied, match="own request"):
            manager.approve(request.id, sam)

    def test_an_admin_decides_only_when_allowed(self, store: NoustStore, clock: Clock) -> None:
        alice = person(store, "alice", "admin")
        bob = person(store, "bob", "admin")
        strict = manager_with(store, clock)
        request, _digest = ask(strict, alice)
        with pytest.raises(ApprovalDenied):
            strict.approve(request.id, bob)

        wider = manager_with(store, clock, approvers=("security", "admin"))
        assert wider.approve(request.id, bob).state == "approved"

    def test_an_admin_never_approves_a_role_change(self, store: NoustStore, clock: Clock) -> None:
        manager = manager_with(store, clock, approvers=("security", "admin"))
        sam = person(store, "sam", "security")
        bob = person(store, "bob", "admin")
        AccountManager(store).create("maria", "operator", password=PASSWORD)
        rule = rule_for(
            "PATCH",
            "/api/auth/accounts/{username}",
            ["accounts.manage"],
            {"role": "admin"},
            {"username": "maria"},
        )
        assert rule is not None
        digest, parameters = snapshot("PATCH", "/api/auth/accounts/maria", [], b'{"role": "admin"}')
        request, _created = manager.request(
            rule,
            method="PATCH",
            path="/api/auth/accounts/maria",
            parameters=parameters,
            fingerprint=digest,
            reason="Promotion",
            requester=sam,
        )
        with pytest.raises(ApprovalDenied, match="role"):
            manager.approve(request.id, bob)

    @pytest.mark.parametrize("kind", ["master", "token", "fleet"])
    def test_only_people_decide(
        self, manager: ApprovalManager, store: NoustStore, kind: str
    ) -> None:
        request, _digest = ask(manager, person(store, "alice", "admin"))
        with pytest.raises(ApprovalDenied):
            manager.approve(request.id, ApprovalActor(kind=kind, id="x", name="x", role="admin"))

    def test_root_at_the_terminal_decides(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        request, _digest = ask(manager, person(store, "alice", "admin"))
        decided = manager.approve(request.id, ApprovalActor(kind="cli", id="0", name="root"))
        assert decided.state == "approved"

    def test_a_rejection_is_final(self, manager: ApprovalManager, store: NoustStore) -> None:
        alice = person(store, "alice", "admin")
        sam = person(store, "sam", "security")
        request, digest = ask(manager, alice)
        assert manager.reject(request.id, sam, "Not this week").state == "rejected"
        with pytest.raises(ApprovalError) as caught:
            manager.approve(request.id, sam)
        assert caught.value.code == "approval_rejected"
        with pytest.raises(ApprovalError):
            manager.consume(request.id, alice, digest)


class TestExecution:
    def test_only_the_requester_executes(self, manager: ApprovalManager, store: NoustStore) -> None:
        alice = person(store, "alice", "admin")
        bob = person(store, "bob", "admin")
        sam = person(store, "sam", "security")
        request, digest = ask(manager, alice)
        manager.approve(request.id, sam)
        with pytest.raises(ApprovalDenied):
            manager.consume(request.id, bob, digest)

    def test_the_call_must_be_the_one_approved(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        alice = person(store, "alice", "admin")
        request, _digest = ask(manager, alice)
        manager.approve(request.id, person(store, "sam", "security"))
        other, _parameters = snapshot(
            "POST", "/api/cron", [], b'{"name": "nightly", "command": "/bin/sh"}'
        )
        with pytest.raises(ApprovalError) as caught:
            manager.consume(request.id, alice, other)
        assert caught.value.code == "approval_mismatch"

    def test_a_pending_request_is_not_an_approval(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        alice = person(store, "alice", "admin")
        request, digest = ask(manager, alice)
        with pytest.raises(ApprovalError) as caught:
            manager.consume(request.id, alice, digest)
        assert caught.value.code == "approval_pending"

    def test_an_approval_expires_if_unused(
        self, manager: ApprovalManager, store: NoustStore, clock: Clock
    ) -> None:
        alice = person(store, "alice", "admin")
        request, digest = ask(manager, alice)
        manager.approve(request.id, person(store, "sam", "security"))
        clock.now += 31 * 60
        with pytest.raises(ApprovalError) as caught:
            manager.consume(request.id, alice, digest)
        assert caught.value.code == "approval_expired"
        assert manager.get(request.id).state == "expired"

    def test_a_request_nobody_decides_expires(
        self, manager: ApprovalManager, store: NoustStore, clock: Clock
    ) -> None:
        sam = person(store, "sam", "security")
        request, _digest = ask(manager, person(store, "alice", "admin"))
        clock.now += 25 * 3600
        expired = manager.expire_due()
        assert [item.id for item in expired] == [request.id]
        with pytest.raises(ApprovalError) as caught:
            manager.approve(request.id, sam)
        assert caught.value.code == "approval_expired"

    def test_asking_twice_for_the_same_call_is_one_request(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        alice = person(store, "alice", "admin")
        first, _digest = ask(manager, alice)
        second, _digest = ask(manager, alice)
        assert first.id == second.id

    def test_an_unknown_request(self, manager: ApprovalManager) -> None:
        with pytest.raises(ApprovalNotFound):
            manager.get(999)


class TestRecord:
    def test_both_identities_are_on_record(
        self, manager: ApprovalManager, store: NoustStore
    ) -> None:
        alice = person(store, "alice", "admin")
        request, digest = ask(manager, alice)
        manager.approve(request.id, person(store, "sam", "security"), "ok")
        manager.consume(request.id, alice, digest)

        names = [entry["action"] for entry in events()]
        assert names[-3:] == ["approval.request", "approval.approve", "approval.use"]
        use = events()[-1]
        assert use["who"]["name"] == "alice"
        assert use["details"]["approved_by"]["name"] == "sam"
        assert use["details"]["approval_id"] == request.id

    def test_the_snapshot_hides_secrets_but_the_fingerprint_does_not(self) -> None:
        digest_a, shown = snapshot("POST", "/api/x", [], b'{"user": "u", "password": "one"}')
        digest_b, _ = snapshot("POST", "/api/x", [], b'{"password": "two", "user": "u"}')
        digest_c, _ = snapshot("POST", "/api/x", [], b'{"password": "one", "user": "u"}')
        assert shown["body"]["password"] != "one"
        assert shown["body"]["user"] == "u"
        assert digest_a != digest_b
        assert digest_a == digest_c  # key order is not a different call

    def test_the_query_is_part_of_the_call(self) -> None:
        plain, _ = snapshot("DELETE", "/api/nodes/web2", [], b"")
        forced, _ = snapshot("DELETE", "/api/nodes/web2", [("force", "true")], b"")
        assert plain != forced


class TestConfiguration:
    def test_the_configuration_turns_it_on(
        self, sandbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.accounts.approvals import load_approval_policy
        from noust.core.config import Config

        monkeypatch.setattr(
            "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "config.yaml"
        )
        Config.reset_instance()
        try:
            assert not load_approval_policy().enabled
            Config().set("approval.enabled", True)
            Config().set("approval.approvers", ["security", "admin"])
            policy = load_approval_policy()
            assert policy.enabled and policy.approvers == ("security", "admin")
            Config().set("approval.enabled", False)
            Config().set("security.profile", "ens-medium")
            assert load_approval_policy().enabled
        finally:
            Config.reset_instance()


class TestNotifications:
    """The deciders hear of a request, and the requester of the decision."""

    @pytest.fixture
    def sent(self, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        from noust.core.notifications.context import NotificationContext

        notifications: list[Any] = []
        monkeypatch.setattr(
            "noust.core.notifier.notify_composed",
            lambda build: notifications.append(build(NotificationContext("en", "web-1", ""))),
        )
        return notifications

    def test_a_new_request_is_announced_once(
        self, manager: ApprovalManager, store: NoustStore, sent: list[Any]
    ) -> None:
        alice = person(store, "alice", "admin")

        request, _digest = ask(manager, alice)
        ask(manager, alice)

        [notification] = sent
        facts = {fact.key: fact.value for fact in notification.facts}
        assert notification.kind == "approval_requested"
        assert facts["request"] == f"#{request.id}"
        assert facts["call"] == "POST /api/cron"
        assert facts["requested_by"] == "alice"
        assert facts["reason"] == "Weekly report"
        assert notification.command is not None
        assert notification.command.value == f"noust approval approve {request.id}"

    def test_the_decision_is_announced(
        self, manager: ApprovalManager, store: NoustStore, sent: list[Any]
    ) -> None:
        alice = person(store, "alice", "admin")
        sam = person(store, "sam", "security")
        first, _digest = ask(manager, alice)
        second, _digest = ask(manager, alice, body=b'{"name": "other", "command": "/bin/true"}')

        manager.approve(first.id, sam, "Checked the command")
        manager.reject(second.id, sam, "Not today")

        approved, rejected = sent[-2:]
        assert (approved.kind, approved.code) == ("approval_decided", "approval.approved")
        assert (rejected.kind, rejected.code) == ("approval_decided", "approval.rejected")
        assert {fact.key: fact.value for fact in rejected.facts}["decided_by"] == "sam"
        assert {fact.key: fact.value for fact in rejected.facts}["comment"] == "Not today"
