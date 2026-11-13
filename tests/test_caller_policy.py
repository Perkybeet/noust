# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The approval policy for a caller that is not a person (item 184): the table, its floor, its factory
values and how a call is judged by it.

What these tests hold: a server that upgrades does not change for its tokens (a test walks every
permission and every operation of the API and finds ``alone``), the built-in assistant proposes what
changes something and never touches what its floor forbids, what asks for sudo mode is never promised by
a cell, an environment comes from the resource and never from a parameter, and the database refuses a
cell outside the closed lists.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import caller_policy as cp
from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.caller_policy import (
    ALONE,
    NEVER,
    OTHER,
    PRODUCTION,
    PROPOSE,
    CallerPolicy,
)
from noust.core.callers import ASSISTANT, PERSON, TOKEN
from noust.core.exceptions import ValidationError
from noust.core.operation_classes import (
    ACTION,
    CHANGE_PERMISSIONS,
    READ,
    STRONG,
    SUDO_ROWS,
    OperationClass,
    classify_schema,
)
from noust.core.permission_catalog import DESCRIPTIONS
from noust.core.store import App, NoustStore
from noust.core.teams import Scope, TeamStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def policy(store: NoustStore) -> CallerPolicy:
    return CallerPolicy(store, production_floor=False)


def op(kind: str, permission: str = "apps.deploy", **extra: Any) -> OperationClass:
    return OperationClass("POST", "/api/x", permission, kind, **extra)


class TestTheTable:
    def test_a_row_for_every_permission_that_changes_something_and_for_no_other(
        self, policy: CallerPolicy
    ) -> None:
        rows = policy.rows()
        assert {row.permission for row in rows} == set(CHANGE_PERMISSIONS)
        assert "apps.read" not in {row.permission for row in rows}
        assert "self" not in {row.permission for row in rows}

    def test_rows_are_grouped_by_area_like_the_table_of_roles(self, policy: CallerPolicy) -> None:
        areas = [row.area for row in policy.rows()]
        # The part before the dot, and a permission with no dot is its own area.
        assert areas == sorted(areas)
        assert {"apps", "backups", "databases", "root_equivalent", "secrets"} <= set(areas)
        apps = [row.permission for row in policy.rows() if row.area == "apps"]
        assert apps == sorted(apps)

    def test_every_row_has_two_classes_by_two_environments(self, policy: CallerPolicy) -> None:
        for row in policy.rows():
            keys = {(cell.caller, cell.environment) for cell in row.cells}
            assert keys == {
                (TOKEN, PRODUCTION),
                (TOKEN, OTHER),
                (ASSISTANT, PRODUCTION),
                (ASSISTANT, OTHER),
            }

    def test_every_row_says_what_it_is_in_words(self, policy: CallerPolicy) -> None:
        for row in policy.rows():
            assert row.description == DESCRIPTIONS[row.permission]


class TestTheFactoryValues:
    """The test that walks the whole catalog: a server that updates changes nothing for its tokens."""

    def test_every_cell_of_a_token_is_alone_so_a_pipeline_that_deployed_yesterday_deploys_tomorrow(
        self, policy: CallerPolicy
    ) -> None:
        for row in policy.rows():
            for cell in row.cells:
                if cell.caller == TOKEN:
                    assert cell.default == ALONE, (cell.permission, cell.environment)
                    assert cell.value == ALONE
                    assert cell.chosen is None
                    assert cell.legacy is (cell.permission in SUDO_ROWS)

    def test_the_assistant_proposes_what_changes_something_in_both_environments(
        self, policy: CallerPolicy
    ) -> None:
        for row in policy.rows():
            for cell in row.cells:
                if cell.caller != ASSISTANT:
                    continue
                expected = NEVER if cell.permission in ASSISTANT_FLOOR else PROPOSE
                assert cell.default == expected, (cell.permission, cell.environment)
                assert cell.value == expected

    def test_a_token_is_judged_alone_on_every_operation_of_the_api(
        self, policy: CallerPolicy, schema: dict[str, Any]
    ) -> None:
        classes = classify_schema(schema)
        assert len(classes) > 1000
        for environment in (PRODUCTION, OTHER):
            for found in classes.values():
                verdict = policy.decide(TOKEN, environment, found, [found.permission])
                assert verdict.value == ALONE, (found.method, found.path, environment)

    def test_the_assistant_never_does_without_a_person_what_changes_something(
        self, policy: CallerPolicy, schema: dict[str, Any]
    ) -> None:
        for found in classify_schema(schema).values():
            for environment in (PRODUCTION, OTHER):
                verdict = policy.decide(ASSISTANT, environment, found, [found.permission])
                if found.kind == READ and found.permission not in ASSISTANT_FLOOR:
                    assert verdict.value == ALONE, (found.method, found.path)
                elif found.changes:
                    assert verdict.value in (PROPOSE, NEVER), (found.method, found.path)
                if found.kind == STRONG:
                    assert verdict.value == NEVER, (found.method, found.path)
                if found.changes and found.permission in ASSISTANT_FLOOR:
                    assert verdict.value == NEVER, (found.method, found.path)

    def test_a_person_is_never_held_by_the_table(
        self, policy: CallerPolicy, schema: dict[str, Any]
    ) -> None:
        for found in classify_schema(schema).values():
            assert policy.decide(PERSON, PRODUCTION, found, [found.permission]).value == ALONE


class TestTheRowsThatAskYouToConfirmItIsYou:
    """The key: where most of what a permission does asks for sudo mode or a person, ``alone`` is not offered."""

    def test_alone_is_not_offered_in_a_key_row_to_a_token_or_to_the_assistant(
        self, policy: CallerPolicy
    ) -> None:
        for permission in SUDO_ROWS:
            for caller in (TOKEN, ASSISTANT):
                for environment in (PRODUCTION, OTHER):
                    cell = policy.cell(caller, environment, permission)
                    assert ALONE not in cell.offered, (permission, caller, environment)
                    assert cell.reason in ("confirm", "assistant_floor"), (permission, caller)

    def test_a_token_keeps_doing_what_it_does_until_a_value_that_is_offered_is_chosen(
        self, policy: CallerPolicy
    ) -> None:
        cell = policy.cell(TOKEN, PRODUCTION, "apps.manage")
        assert (cell.value, cell.default, cell.legacy) == (ALONE, ALONE, True)
        assert cell.offered == (PROPOSE, NEVER)
        policy.set_cells([(TOKEN, PRODUCTION, "apps.manage", PROPOSE)], by="marta")
        chosen = policy.cell(TOKEN, PRODUCTION, "apps.manage")
        assert (chosen.value, chosen.chosen, chosen.legacy) == (PROPOSE, PROPOSE, False)
        # And it cannot be chosen back: a key row does not offer it.
        with pytest.raises(ValidationError) as refused:
            policy.set_cells([(TOKEN, PRODUCTION, "apps.manage", ALONE)], by="marta")
        assert "confirm it is you" in refused.value.details

    def test_what_asks_nothing_of_you_is_not_a_key_row(self, policy: CallerPolicy) -> None:
        for permission in ("apps.operate", "apps.deploy", "backups.run", "databases.write"):
            assert permission not in SUDO_ROWS
            assert not policy.cell(TOKEN, PRODUCTION, permission).legacy

    def test_the_assistant_is_never_left_alone_in_a_key_row(self, policy: CallerPolicy) -> None:
        with pytest.raises(ValidationError):
            policy.set_cells([(ASSISTANT, OTHER, "apps.manage", ALONE)], by="marta")
        policy.set_cells([(ASSISTANT, OTHER, "apps.manage", NEVER)], by="marta")
        assert policy.cell(ASSISTANT, OTHER, "apps.manage").value == NEVER

    def test_the_rows_say_which_are_key_rows(self, policy: CallerPolicy) -> None:
        assert {row.permission for row in policy.rows() if row.key} == set(SUDO_ROWS)


class TestTheFloor:
    def test_what_the_assistant_may_never_touch_is_fixed_at_never(
        self, policy: CallerPolicy
    ) -> None:
        for permission in ASSISTANT_FLOOR & CHANGE_PERMISSIONS:
            for environment in (PRODUCTION, OTHER):
                cell = policy.cell(ASSISTANT, environment, permission)
                assert cell.value == NEVER
                assert cell.fixed is True
                assert cell.offered == (NEVER,)
                assert cell.reason == "assistant_floor"

    def test_the_floor_names_the_secrets_the_terminal_the_accounts_the_audit_and_the_settings(
        self,
    ) -> None:
        for permission in (
            "secrets.reveal",
            "root_equivalent",
            "server.host_access",
            "server.terminal",
            "apps.terminal",
            "settings.manage",
            "security.manage",
            "accounts.manage",
            "audit.manage",
            "audit.read",
        ):
            assert permission in ASSISTANT_FLOOR

    def test_the_floor_is_the_assistants_own_closed_list_and_narrower_than_the_tools_of_mcp(
        self,
    ) -> None:
        from noust.web.mcp import tools

        assert ASSISTANT_FLOOR == {
            "secrets.reveal",
            "root_equivalent",
            "server.host_access",
            "server.terminal",
            "apps.terminal",
            "accounts.manage",
            "accounts.read",
            "audit.manage",
            "audit.read",
            "security.manage",
            "settings.manage",
            "fleet.manage",
            "teams.manage",
        }
        # What the assistant never does is out of reach of a tool of MCP too, and MCP keeps more out.
        assert ASSISTANT_FLOOR < tools.FORBIDDEN_PERMISSIONS
        # What the owner asked the assistant to be able to propose is not locked by the floor.
        for permission in (
            "apps.manage",
            "databases.write",
            "databases.manage",
            "backups.manage",
            "server.manage",
        ):
            assert permission not in ASSISTANT_FLOOR
            cell = CallerPolicy(production_floor=False).cell(ASSISTANT, PRODUCTION, permission)
            assert cell.offered == (PROPOSE, NEVER)

    def test_the_assistant_may_be_left_alone_only_where_it_is_narrow_and_reversible(
        self, policy: CallerPolicy
    ) -> None:
        for permission in ("apps.operate", "apps.deploy", "backups.run"):
            assert ALONE in policy.cell(ASSISTANT, PRODUCTION, permission).offered
        writes = policy.cell(ASSISTANT, PRODUCTION, "databases.write")
        assert writes.offered == (PROPOSE, NEVER)
        assert writes.reason == "assistant_narrow"

    def test_a_token_may_be_given_any_value_where_nothing_forces_one(
        self, policy: CallerPolicy
    ) -> None:
        cell = policy.cell(TOKEN, PRODUCTION, "backups.run")
        assert cell.offered == (ALONE, PROPOSE, NEVER)
        assert cell.fixed is False
        assert cell.reason is None

    def test_what_asks_for_sudo_is_never_the_assistants_whatever_the_table_says(
        self, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(ASSISTANT, PRODUCTION, "apps.deploy", ALONE)], by="admin")
        strong = op(STRONG, "apps.deploy", elevation=True)
        verdict = policy.decide(ASSISTANT, PRODUCTION, strong, ["apps.deploy"])
        assert verdict.value == NEVER
        assert verdict.reason == "assistant_strong"
        # The same permission, an operation that asks for nobody, is what the table says.
        action = op(ACTION, "apps.deploy")
        assert policy.decide(ASSISTANT, PRODUCTION, action, ["apps.deploy"]).value == ALONE

    def test_a_cell_cannot_promise_a_token_more_than_its_own_sudo_mode_allows(
        self, policy: CallerPolicy
    ) -> None:
        # ``alone`` means the table asks for nothing more: a token not issued to act in sudo mode is
        # still refused on a route that asks for it, by the route, as before.
        strong = op(STRONG, "apps.manage", elevation=True)
        assert policy.decide(TOKEN, PRODUCTION, strong, ["apps.manage"]).value == ALONE
        policy.set_cells([(TOKEN, PRODUCTION, "apps.manage", PROPOSE)], by="admin")
        assert policy.decide(TOKEN, PRODUCTION, strong, ["apps.manage"]).value == PROPOSE

    def test_the_assistant_never_touches_its_own_credentials(self, policy: CallerPolicy) -> None:
        own = OperationClass("DELETE", "/api/auth/tokens/{token_id}", "self", "own")
        assert policy.decide(ASSISTANT, PRODUCTION, own, ["self"]).value == NEVER
        assert policy.decide(TOKEN, PRODUCTION, own, ["self"]).value == ALONE

    def test_the_assistant_never_reads_what_it_may_never_hold(self, policy: CallerPolicy) -> None:
        reveal = OperationClass("GET", "/api/apps/{domain}/env/reveal", "secrets.reveal", "read")
        assert policy.decide(ASSISTANT, PRODUCTION, reveal, ["secrets.reveal"]).value == NEVER
        assert policy.decide(TOKEN, PRODUCTION, reveal, ["secrets.reveal"]).value == ALONE


class TestWhatTheOperatorChooses:
    def test_a_choice_is_kept_and_is_what_judges(
        self, policy: CallerPolicy, store: NoustStore
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.deploy", PROPOSE)], by="marta")
        cell = policy.cell(TOKEN, PRODUCTION, "apps.deploy")
        assert (cell.value, cell.chosen, cell.default) == (PROPOSE, PROPOSE, ALONE)
        # The other environment, the other class and the other permission did not move.
        assert policy.cell(TOKEN, OTHER, "apps.deploy").value == ALONE
        assert policy.cell(ASSISTANT, PRODUCTION, "apps.deploy").value == PROPOSE
        assert policy.cell(TOKEN, PRODUCTION, "apps.operate").value == ALONE
        # It outlives the process.
        again = CallerPolicy(store, production_floor=False)
        assert again.cell(TOKEN, PRODUCTION, "apps.deploy").value == PROPOSE

    def test_who_chose_it_and_when_is_kept(self, policy: CallerPolicy, store: NoustStore) -> None:
        policy.set_cells([(TOKEN, OTHER, "apps.operate", NEVER)], by="marta")
        with store._transaction() as cursor:
            cursor.execute("SELECT updated_by, updated_at FROM caller_policy")
            row = cursor.fetchone()
        assert row[0] == "marta"
        assert row[1].startswith("20")

    def test_setting_several_cells_is_all_or_nothing(self, policy: CallerPolicy) -> None:
        with pytest.raises(ValidationError):
            policy.set_cells(
                [
                    (TOKEN, PRODUCTION, "apps.deploy", PROPOSE),
                    (TOKEN, PRODUCTION, "apps.operate", "sometimes"),
                ],
                by="marta",
            )
        assert policy.cell(TOKEN, PRODUCTION, "apps.deploy").chosen is None

    @pytest.mark.parametrize(
        ("cell", "word"),
        [
            ((PERSON, PRODUCTION, "apps.deploy", ALONE), "caller"),
            (("robot", PRODUCTION, "apps.deploy", ALONE), "caller"),
            ((TOKEN, "staging", "apps.deploy", ALONE), "environment"),
            ((TOKEN, PRODUCTION, "apps.read", ALONE), "permission"),
            ((TOKEN, PRODUCTION, "no.such", ALONE), "permission"),
            ((TOKEN, PRODUCTION, "self", ALONE), "permission"),
            ((TOKEN, PRODUCTION, "apps.deploy", "ALONE"), "value"),
            ((TOKEN, PRODUCTION, "apps.deploy", ""), "value"),
        ],
    )
    def test_a_cell_outside_the_closed_lists_is_refused_before_it_reaches_the_database(
        self, policy: CallerPolicy, cell: tuple[str, str, str, str], word: str
    ) -> None:
        with pytest.raises(ValidationError) as refused:
            policy.set_cells([cell], by="marta")
        assert word in str(refused.value)

    def test_the_floor_cannot_be_lowered_and_the_refusal_says_why(
        self, policy: CallerPolicy
    ) -> None:
        with pytest.raises(ValidationError) as refused:
            policy.set_cells([(ASSISTANT, PRODUCTION, "secrets.reveal", PROPOSE)], by="marta")
        assert "never" in str(refused.value)
        with pytest.raises(ValidationError):
            policy.set_cells([(ASSISTANT, OTHER, "databases.write", ALONE)], by="marta")
        assert policy.cell(ASSISTANT, PRODUCTION, "secrets.reveal").chosen is None

    def test_a_value_the_cell_may_have_is_accepted_and_the_rest_are_listed_in_the_refusal(
        self, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(ASSISTANT, OTHER, "backups.run", ALONE)], by="marta")
        assert policy.cell(ASSISTANT, OTHER, "backups.run").value == ALONE

    def test_putting_a_cell_back_returns_it_to_what_the_release_ships(
        self, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.deploy", NEVER)], by="marta")
        assert policy.reset_cells([(TOKEN, PRODUCTION, "apps.deploy")]) == 1
        cell = policy.cell(TOKEN, PRODUCTION, "apps.deploy")
        assert (cell.value, cell.chosen) == (ALONE, None)
        assert policy.reset_cells([(TOKEN, PRODUCTION, "apps.deploy")]) == 0

    def test_the_database_itself_refuses_a_value_outside_the_list(self, store: NoustStore) -> None:
        with pytest.raises(sqlite3.IntegrityError), store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO caller_policy (caller, environment, permission, value, updated_at) "
                "VALUES ('token', 'production', 'apps.deploy', 'whenever', 'x')"
            )
        with pytest.raises(sqlite3.IntegrityError), store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO caller_policy (caller, environment, permission, value, updated_at) "
                "VALUES ('person', 'production', 'apps.deploy', 'alone', 'x')"
            )


class TestTheProfile:
    def test_under_a_profile_that_asks_for_it_a_token_in_production_proposes(
        self, store: NoustStore
    ) -> None:
        ens = CallerPolicy(store, production_floor=True)
        for permission in sorted(CHANGE_PERMISSIONS):
            production = ens.cell(TOKEN, PRODUCTION, permission)
            assert production.default == PROPOSE, permission
            assert production.value == PROPOSE
            assert ALONE not in production.offered
            assert production.reason == "profile_floor"
            # Every other environment is as it was.
            assert ens.cell(TOKEN, OTHER, permission).default == ALONE

    def test_a_choice_below_the_profile_is_refused_and_one_above_is_kept(
        self, store: NoustStore
    ) -> None:
        ens = CallerPolicy(store, production_floor=True)
        with pytest.raises(ValidationError):
            ens.set_cells([(TOKEN, PRODUCTION, "apps.deploy", ALONE)], by="marta")
        ens.set_cells([(TOKEN, PRODUCTION, "apps.deploy", NEVER)], by="marta")
        assert ens.cell(TOKEN, PRODUCTION, "apps.deploy").value == NEVER

    def test_a_choice_made_before_the_profile_is_raised_to_it_without_being_lost(
        self, store: NoustStore
    ) -> None:
        before = CallerPolicy(store, production_floor=False)
        before.set_cells([(TOKEN, PRODUCTION, "apps.deploy", ALONE)], by="marta")
        after = CallerPolicy(store, production_floor=True)
        cell = after.cell(TOKEN, PRODUCTION, "apps.deploy")
        assert cell.value == PROPOSE
        assert cell.chosen == ALONE
        # And when the profile goes away the operator's own choice is back.
        assert before.cell(TOKEN, PRODUCTION, "apps.deploy").value == ALONE

    def test_the_profile_does_not_touch_the_assistants_cells(self, store: NoustStore) -> None:
        ens = CallerPolicy(store, production_floor=True)
        assert ens.cell(ASSISTANT, PRODUCTION, "apps.deploy").default == PROPOSE
        assert ens.cell(ASSISTANT, PRODUCTION, "apps.deploy").reason is None

    def test_the_control_reads_the_configuration_when_nothing_says_otherwise(
        self, store: NoustStore, real_config: Any
    ) -> None:
        real_config.set("security.token_production_approval", True)
        assert CallerPolicy(store).cell(TOKEN, PRODUCTION, "apps.deploy").value == PROPOSE
        real_config.set("security.token_production_approval", False)
        assert CallerPolicy(store).cell(TOKEN, PRODUCTION, "apps.deploy").value == ALONE

    def test_a_framework_that_asks_for_it_forces_it(
        self, store: NoustStore, real_config: Any
    ) -> None:
        real_config.set("security.profile", "ens-medium")
        assert CallerPolicy(store).cell(TOKEN, PRODUCTION, "apps.deploy").value == PROPOSE
        assert CallerPolicy(store).cell(TOKEN, OTHER, "apps.deploy").value == ALONE


class TestJudgingACall:
    def test_the_strictest_of_the_permissions_a_call_needs_decides(
        self, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "security.manage", NEVER)], by="marta")
        call = op(ACTION, "settings.manage")
        verdict = policy.decide(TOKEN, PRODUCTION, call, ["settings.manage", "security.manage"])
        assert verdict.value == NEVER
        assert verdict.permission == "security.manage"
        assert verdict.reason == "table"

    def test_the_permissions_that_only_read_have_no_say(self, policy: CallerPolicy) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.deploy", NEVER)], by="marta")
        call = op(ACTION, "apps.deploy")
        assert policy.decide(TOKEN, PRODUCTION, call, ["fleet.read", "apps.deploy"]).value == NEVER
        read = OperationClass("GET", "/api/apps", "apps.read", READ)
        assert policy.decide(TOKEN, PRODUCTION, read, ["apps.read"]).value == ALONE

    def test_a_read_route_whose_body_writes_is_judged_by_the_permission_of_writing(
        self, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "databases.write", PROPOSE)], by="marta")
        query = OperationClass("POST", "/api/databases/query", "databases.read", READ)
        assert policy.decide(TOKEN, PRODUCTION, query, ["databases.read"]).value == ALONE
        escalated = policy.decide(
            TOKEN, PRODUCTION, query, ["databases.read"], escalated="databases.write"
        )
        assert escalated.value == PROPOSE
        assert escalated.permission == "databases.write"

    def test_a_cell_is_of_the_environment_of_the_call(self, policy: CallerPolicy) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.deploy", PROPOSE)], by="marta")
        call = op(ACTION, "apps.deploy")
        assert policy.decide(TOKEN, PRODUCTION, call, ["apps.deploy"]).value == PROPOSE
        assert policy.decide(TOKEN, OTHER, call, ["apps.deploy"]).value == ALONE

    def test_an_environment_that_is_neither_is_the_one_that_is_judged(
        self, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.deploy", NEVER)], by="marta")
        call = op(ACTION, "apps.deploy")
        assert policy.decide(TOKEN, "wherever", call, ["apps.deploy"]).value == NEVER

    def test_the_verdict_says_who_and_where(self, policy: CallerPolicy) -> None:
        call = op(ACTION, "apps.deploy")
        verdict = policy.decide(ASSISTANT, OTHER, call, ["apps.deploy"])
        assert (verdict.caller, verdict.environment) == (ASSISTANT, OTHER)
        assert verdict.value == PROPOSE


class TestTheEnvironmentOfAResource:
    @pytest.fixture
    def world(self, store: NoustStore) -> TeamStore:
        teams = TeamStore(store)
        teams.create_environment("default", "default", "Staging")
        for domain in ("shop.example.com", "beta.example.com"):
            store.create_app(App(domain=domain, app_type="nodejs", app_path=f"/srv/{domain}"))  # type: ignore[arg-type]
        teams.move_app_to(Scope.everything(), "beta.example.com", environment="staging")
        return teams

    def test_an_application_of_the_production_environment_is_production(
        self, world: TeamStore
    ) -> None:
        assert cp.environment_of("shop.example.com") == PRODUCTION

    def test_an_application_of_another_environment_is_the_other(self, world: TeamStore) -> None:
        assert cp.environment_of("beta.example.com") == OTHER

    def test_what_has_no_environment_or_is_not_found_is_production(self, world: TeamStore) -> None:
        assert cp.environment_of("ghost.example.com") == PRODUCTION
        assert cp.environment_of(None) == PRODUCTION
        assert cp.environment_of("") == PRODUCTION

    def test_a_resource_the_caller_does_not_see_is_judged_as_production(
        self, world: TeamStore
    ) -> None:
        from noust.core.teams import bind

        with bind(Scope.nothing()):
            assert cp.environment_of("beta.example.com") == PRODUCTION

    def test_the_environment_is_not_something_a_caller_can_pass(self) -> None:
        import inspect

        assert list(inspect.signature(cp.environment_of).parameters) == ["domain"]


@pytest.fixture(scope="module")
def schema(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from noust.core.accounts import AuthPolicy
    from noust.web.auth import SecurityConfig
    from noust.web.server import create_app

    root = tmp_path_factory.mktemp("policy")
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
