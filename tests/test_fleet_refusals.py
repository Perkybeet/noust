# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Servers that refuse the operator, told as a group (``noust.web.fleet_refusals``).

What is pinned: the permission a refusal is about comes from the central's own map of routes, not
from reading a node's English; servers refused the same permission are one group with a count, the
names, what is missing and how to give it, as a sentence with a code the console translates; a
refusal that is not about a permission keeps the node's own words; and nothing a person is shown is
the JSON a node answered with.
"""

from __future__ import annotations

import pytest

from noust.core import server_text
from noust.fleet.aggregate import Asker, NodeOutcome
from noust.web import fleet_refusals
from noust.web.fleet_refusals import annotate

VIEWER = Asker(actor="ana", scope="read", role="viewer")
TOKEN = Asker(actor="token:ci", scope="read", role=None)


def _refused(
    name: str,
    path: str | None = "/api/audit",
    *,
    code: str = "permission_denied",
    local: bool = False,
    message: str = "This needs the 'audit.read' permission, which viewer does not hold",
    hint: str | None = "Ask a security officer for an account with a role that holds it.",
) -> NodeOutcome:
    return NodeOutcome(
        name=name,
        local=local,
        status="forbidden",
        code=code,
        message=message,
        hint=hint,
        path=path,
    )


def _ok(name: str) -> NodeOutcome:
    return NodeOutcome(name=name, status="ok")


def _body(*outcomes: NodeOutcome) -> dict[str, object]:
    return {"nodes": [outcome.to_dict() for outcome in outcomes]}


class TestTheGroup:
    def test_servers_refused_the_same_permission_are_one_group(self) -> None:
        outcomes = [_refused("web-1"), _refused("web-2"), _refused("web-3"), _ok("web-4")]
        body = _body(*outcomes)

        groups = annotate(body, outcomes, "activity", VIEWER)

        assert len(groups) == 1
        group = groups[0]
        assert group["servers"] == ["web-1", "web-2", "web-3"]
        assert group["count"] == 3
        assert group["permission"] == "audit.read"
        assert group["reason"] == "permission"
        assert group["code"] == "permission_denied"
        assert group["resource"] == "activity"
        assert group["includes_central"] is False

    def test_what_is_missing_is_a_sentence_with_a_code_and_its_fix(self) -> None:
        outcomes = [_refused("web-1")]

        (group,) = annotate(_body(*outcomes), outcomes, "activity", VIEWER)

        sentence = server_text.say(
            "fleet.refused.permission", role="viewer", permission="audit.read"
        )
        assert group["message"] == sentence.text
        assert group["hint"] == sentence.hint
        assert group["coded"] == sentence.to_dict()
        assert group["coded"]["params"] == {"role": "viewer", "permission": "audit.read"}

    def test_a_credential_without_a_role_is_named_by_its_scope(self) -> None:
        outcomes = [_refused("web-1")]

        (group,) = annotate(_body(*outcomes), outcomes, "activity", TOKEN)

        assert group["coded"]["params"]["role"] == "read"

    def test_each_server_says_what_it_was_refused(self) -> None:
        outcomes = [_refused("web-1"), _ok("web-2")]
        body = _body(*outcomes)

        annotate(body, outcomes, "activity", VIEWER)

        refused, fine = body["nodes"]  # type: ignore[misc]
        assert (refused["permission"], refused["refusal"]) == ("audit.read", "permission")
        assert (fine["permission"], fine["refusal"]) == (None, None)

    def test_the_central_is_one_of_them(self) -> None:
        outcomes = [_refused("nas", local=True), _refused("web-1")]

        (group,) = annotate(_body(*outcomes), outcomes, "activity", VIEWER)

        assert group["servers"] == ["nas", "web-1"]
        assert group["includes_central"] is True

    def test_different_permissions_are_different_groups_the_larger_first(self) -> None:
        outcomes = [
            _refused("a", "/api/audit"),
            _refused("b", "/api/audit"),
            _refused("c", "/api/alerts/rules"),
        ]

        groups = annotate(_body(*outcomes), outcomes, "index", VIEWER)

        assert [(g["permission"], g["count"]) for g in groups] == [
            ("audit.read", 2),
            ("server.read", 1),
        ]

    def test_a_group_of_servers_is_listed_in_the_order_they_were_asked(self) -> None:
        outcomes = [_refused("z"), _refused("a")]

        (group,) = annotate(_body(*outcomes), outcomes, "activity", VIEWER)

        assert group["servers"] == ["z", "a"]


class TestWhatIsNotAboutAPermission:
    def test_a_refusal_with_another_code_keeps_the_nodes_own_words(self) -> None:
        outcomes = [
            _refused(
                "web-1",
                code="mfa_required",
                message="Set up two-factor authentication before doing anything else",
                hint="Enrol an authenticator.",
            ),
            _refused(
                "web-2",
                code="mfa_required",
                message="Set up two-factor authentication before doing anything else",
                hint="Enrol an authenticator.",
            ),
        ]
        body = _body(*outcomes)

        (group,) = annotate(body, outcomes, "activity", VIEWER)

        assert group["reason"] == "other"
        assert group["code"] == "mfa_required"
        assert group["permission"] is None
        assert group["coded"] is None
        assert group["message"] == "Set up two-factor authentication before doing anything else"
        assert group["hint"] == "Enrol an authenticator."
        assert group["servers"] == ["web-1", "web-2"]

    def test_a_permission_the_central_has_no_route_for_is_not_guessed(self) -> None:
        outcomes = [_refused("web-1", "/api/not-a-route-here")]

        (group,) = annotate(_body(*outcomes), outcomes, "activity", VIEWER)

        assert (group["reason"], group["permission"], group["coded"]) == ("other", None, None)

    def test_an_outcome_that_is_not_a_refusal_makes_no_group(self) -> None:
        down = NodeOutcome(name="web-1", status="unreachable", code="node_unreachable")
        outcomes = [down, _ok("web-2")]

        assert annotate(_body(*outcomes), outcomes, "apps", VIEWER) == []


@pytest.mark.parametrize(
    ("path", "permission"),
    [
        ("/api/audit", "audit.read"),
        ("/api/apps", "apps.read"),
        ("/api/databases/databases", "databases.read"),
        ("/api/sites", "apps.read"),
        ("/api/cron", "apps.read"),
        ("/api/alerts/rules", "server.read"),
        ("/api/backups", "backups.read"),
        ("/api/logs/search", "apps.read"),
    ],
)
def test_the_permission_of_every_endpoint_a_view_asks_is_the_centrals_own_map(
    path: str, permission: str
) -> None:
    assert fleet_refusals.permission_of(path) == permission


def test_every_endpoint_a_fleet_view_asks_has_a_permission_in_the_map() -> None:
    from noust.fleet.aggregate import RESOURCES

    unmapped = sorted(
        part.path
        for resource in RESOURCES.values()
        for part in resource.parts
        if fleet_refusals.permission_of(part.path) is None
    )

    assert unmapped == []
