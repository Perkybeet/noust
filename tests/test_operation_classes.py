# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The class of every operation of the API: reads, changes, and what asks for a person (item 184).

One definition, computed from what the API already publishes (``x-noust-permission``,
``x-noust-requires-elevation``, ``x-noust-requires-approval``) and never from the verb: eighteen
``POST`` routes of ``databases.read`` change nothing, and a ``GET`` is not what makes a route safe. A
test walks the whole application and fails for an operation, or a permission, nobody classified, as the
permission and scope tests do for a route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import operation_classes as oc
from noust.core.accounts import AuthPolicy
from noust.core.operation_classes import (
    ACTION,
    OWN,
    PUBLIC_CLASS,
    READ,
    STRONG,
    Unclassified,
    classify_parts,
)
from noust.core.permission_catalog import ALL_PERMISSIONS
from noust.core.store import NoustStore
from noust.web.auth import SecurityConfig
from noust.web.server import create_app


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(scope="module")
def schema(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("classes")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(root))
        patch.setenv("XDG_DATA_HOME", str(root / "data"))
        patch.setenv("XDG_CONFIG_HOME", str(root / "config"))
        NoustStore.reset_instance()
        NoustStore(root / "noust.db")
        try:
            app = create_app(
                SecurityConfig(
                    state_dir=root / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
                )
            )
            document: dict[str, Any] = app.openapi()
            return document
        finally:
            NoustStore.reset_instance()


class TestTheRules:
    def test_a_get_reads_whatever_permission_it_needs(self) -> None:
        # Revealing a secret is a read of a secret; the permission is what guards it, and the policy
        # about changes is not about it.
        assert classify_parts("GET", "/api/x", "secrets.reveal").kind == READ
        assert classify_parts("GET", "/api/x", "apps.read").kind == READ

    def test_a_post_that_changes_something_is_an_action(self) -> None:
        found = classify_parts("POST", "/api/apps/{domain}/restart", "apps.operate")
        assert found.kind == ACTION
        assert found.permission == "apps.operate"

    def test_asking_for_sudo_mode_makes_it_strong(self) -> None:
        found = classify_parts("DELETE", "/api/apps/{domain}", "apps.manage", elevation=True)
        assert found.kind == STRONG
        assert found.elevation is True

    def test_an_approval_that_always_applies_makes_it_strong(self) -> None:
        found = classify_parts("POST", "/api/nodes", "fleet.manage", approval="always")
        assert found.kind == STRONG

    def test_an_approval_that_depends_on_the_body_leaves_it_an_action(self) -> None:
        # Creating an application is an action; only deploying from a directory of the server is
        # held for a person, and the guard judges that by the body.
        found = classify_parts("POST", "/api/apps", "apps.manage", approval="source is a directory")
        assert found.kind == ACTION
        assert found.approval == "source is a directory"

    def test_a_post_of_a_read_permission_needs_its_name_in_the_list_of_reads(self) -> None:
        listed = next(iter(oc.READS_BY_POST))
        assert classify_parts(listed[0], listed[1], "apps.read").kind == READ
        with pytest.raises(Unclassified):
            classify_parts("POST", "/api/not-listed/plan", "databases.read")

    def test_what_is_own_is_the_callers_own_business(self) -> None:
        assert classify_parts("POST", "/api/auth/password", "self").kind == OWN
        assert classify_parts("GET", "/api/auth/tokens", "self").kind == READ

    def test_a_public_route_is_public(self) -> None:
        assert classify_parts("POST", "/api/auth/login", "public").kind == PUBLIC_CLASS

    def test_an_unknown_permission_is_not_guessed(self) -> None:
        with pytest.raises(Unclassified):
            classify_parts("POST", "/api/x", "made.up")

    def test_a_route_with_no_permission_is_not_guessed(self) -> None:
        with pytest.raises(Unclassified):
            classify_parts("POST", "/api/x", None)

    def test_the_verb_is_not_case_sensitive(self) -> None:
        assert classify_parts("get", "/api/x", "apps.read").kind == READ


class TestThePermissions:
    def test_every_permission_is_read_a_change_or_neutral(self) -> None:
        sets = (oc.READ_PERMISSIONS, oc.CHANGE_PERMISSIONS, oc.NEUTRAL_PERMISSIONS)
        for first in range(len(sets)):
            for second in range(first + 1, len(sets)):
                assert not sets[first] & sets[second]
        assert set(ALL_PERMISSIONS) - set().union(*sets) == set()

    def test_a_read_permission_is_named_like_one(self) -> None:
        assert all(name.endswith(".read") for name in oc.READ_PERMISSIONS)

    def test_a_change_is_never_called_a_read(self) -> None:
        assert not {name for name in oc.CHANGE_PERMISSIONS if name.endswith(".read")}


class TestTheWholeApplication:
    def test_every_operation_has_a_class(self, schema: dict[str, Any]) -> None:
        # The test that fails for an operation nobody classified: say what to do in its message.
        classes = oc.classify_schema(schema)
        operations = [
            (method.upper(), path)
            for path, item in schema["paths"].items()
            for method in item
            if method.upper() in oc.HTTP_METHODS
        ]
        assert len(classes) == len(operations) > 1000

    def test_every_exception_is_still_an_operation_of_the_api(self, schema: dict[str, Any]) -> None:
        classes = oc.classify_schema(schema)
        assert sorted(key for key in oc.READS_BY_POST if key not in classes) == []

    def test_every_exception_is_a_post_of_a_read_permission(self, schema: dict[str, Any]) -> None:
        # An exception that is not needed any more is a rule nobody reads.
        classes = oc.classify_schema(schema)
        for key in oc.READS_BY_POST:
            found = classes[key]
            assert found.kind == READ
            assert found.permission in oc.READ_PERMISSIONS, key
            assert key[0] not in oc.SAFE_METHODS, key

    def test_every_exception_says_why(self) -> None:
        assert all(len(reason) > 10 for reason in oc.READS_BY_POST.values())

    def test_every_route_of_root_equivalent_is_strong(self, schema: dict[str, Any]) -> None:
        # The one root-equivalent route that changes nothing is checking a candidate unit, which the
        # four-eyes rule exempts too: it asks for sudo mode like the rest and is not held for a person.
        exempt = {("POST", "/api/services/verify")}
        found = {
            key: value
            for key, value in oc.classify_schema(schema).items()
            if value.permission == "root_equivalent" and key[0] not in oc.SAFE_METHODS
        }
        assert found
        assert sorted(key for key, value in found.items() if value.kind != STRONG) == sorted(exempt)

    def test_every_route_that_asks_for_sudo_is_strong_unless_it_only_reads(
        self, schema: dict[str, Any]
    ) -> None:
        for (method, path), found in oc.classify_schema(schema).items():
            if found.elevation:
                assert found.kind in (STRONG, READ, OWN), (method, path, found.kind)

    def test_the_key_rows_are_the_permissions_most_of_whose_operations_ask_for_a_person(
        self, schema: dict[str, Any]
    ) -> None:
        # The list is written down and pinned here to the rule that makes it: a route that moves a
        # permission across the line fails this test instead of changing the table.
        assert oc.sudo_rows(oc.classify_schema(schema)) == oc.SUDO_ROWS
        assert oc.SUDO_ROWS <= oc.CHANGE_PERMISSIONS
        assert "apps.operate" not in oc.SUDO_ROWS and "apps.manage" in oc.SUDO_ROWS

    def test_what_is_known_of_a_few_operations(self, schema: dict[str, Any]) -> None:
        classes = oc.classify_schema(schema)
        assert classes[("GET", "/api/apps")].kind == READ
        assert classes[("POST", "/api/apps/{domain}/restart")].kind == ACTION
        assert classes[("POST", "/api/databases/query")].kind == READ
        assert classes[("DELETE", "/api/apps/{domain}")].kind == STRONG
        assert classes[("POST", "/api/auth/login")].kind == PUBLIC_CLASS
        assert classes[("POST", "/api/nodes")].kind == STRONG

    def test_an_operation_with_no_permission_in_the_document_is_reported(self) -> None:
        document = {"paths": {"/api/x": {"post": {"summary": "x"}}}}
        with pytest.raises(oc.UnclassifiedOperations) as raised:
            oc.classify_schema(document)
        assert "POST /api/x" in str(raised.value)


class TestWhatAReadRouteCanBecome:
    def test_a_query_of_a_read_permission_that_writes_is_judged_by_the_permission_of_writing(
        self,
    ) -> None:
        assert oc.escalated_permission("db.query.write") == "databases.write"
        assert oc.escalated_permission("db.query.analyze") == "databases.write"

    def test_nothing_else_escalates(self) -> None:
        assert oc.escalated_permission("root_equivalent") is None
        assert oc.escalated_permission(None) is None
