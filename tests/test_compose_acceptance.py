# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a Compose stack was accepted with, kept in the store.

The guard judges when a stack is deployed and a unit starts it later, with the files and the variables
that are there at that moment. What the stack was given of the server is therefore written down when it
is activated, in the store, and that record (and what its containers have) is what "it already had it"
means: the tree it runs from is writable by the stack itself and is not a record of anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import compose_acceptance, schema_v18
from noust.core.store import App, AppStatus, AppType, NoustStore
from noust.deployers.helpers import sandbox

KEY = ("service web", "outside", "/srv/data")
OTHER = ("service web", "devices", "/dev/fuse")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def make_app(store: NoustStore, domain: str = "shop.example.com") -> None:
    store.create_app(
        App(
            domain=domain,
            app_type=AppType.DOCKER_COMPOSE.value,
            source="https://example.com/shop.git",
            app_path=f"/var/www/apps/{domain.replace('.', '-')}",
            status=AppStatus.RUNNING.value,
        )
    )


class TestTheRecord:
    def test_a_stack_nobody_accepted_has_none(self, store: NoustStore) -> None:
        assert compose_acceptance.get("shop.example.com", store=store) is None

    def test_what_is_recorded_is_read_back_whole(self, store: NoustStore) -> None:
        compose_acceptance.record(
            "shop.example.com", [KEY, OTHER], source="deploy", actor="alice", store=store
        )

        accepted = compose_acceptance.get("shop.example.com", store=store)

        assert accepted is not None
        assert accepted.keys == frozenset({KEY, OTHER})
        assert (accepted.source, accepted.actor, accepted.inherited) == ("deploy", "alice", False)
        assert accepted.accepted_at

    def test_a_record_with_nothing_given_is_a_record(self, store: NoustStore) -> None:
        compose_acceptance.record("shop.example.com", [], source="deploy", store=store)

        accepted = compose_acceptance.get("shop.example.com", store=store)

        assert accepted is not None and accepted.keys == frozenset()

    def test_a_second_record_adds_to_the_first_until_the_stack_serves(
        self, store: NoustStore
    ) -> None:
        # A failed update goes back to what it replaced: what that was must still be accepted.
        compose_acceptance.record("shop.example.com", [KEY], source="deploy", store=store)
        compose_acceptance.record("shop.example.com", [OTHER], source="update", store=store)

        accepted = compose_acceptance.get("shop.example.com", store=store)

        assert accepted is not None and accepted.keys == frozenset({KEY, OTHER})
        assert accepted.source == "update"

    def test_settling_replaces_it_with_what_runs(self, store: NoustStore) -> None:
        compose_acceptance.record("shop.example.com", [KEY, OTHER], source="update", store=store)

        compose_acceptance.record(
            "shop.example.com", [OTHER], source="update", replace=True, store=store
        )

        accepted = compose_acceptance.get("shop.example.com", store=store)
        assert accepted is not None and accepted.keys == frozenset({OTHER})

    def test_the_record_of_one_stack_is_not_the_record_of_another(self, store: NoustStore) -> None:
        compose_acceptance.record("shop.example.com", [KEY], source="deploy", store=store)

        assert compose_acceptance.get("blog.example.com", store=store) is None

    def test_what_the_upgrade_took_over_says_so(self, store: NoustStore) -> None:
        compose_acceptance.record(
            "shop.example.com", [KEY], source="upgrade", inherited=True, store=store
        )

        accepted = compose_acceptance.get("shop.example.com", store=store)

        assert accepted is not None and accepted.inherited
        assert compose_acceptance.inherited_domains(store=store) == ["shop.example.com"]

    def test_a_decision_that_is_taken_is_not_a_taken_over_one(self, store: NoustStore) -> None:
        compose_acceptance.record(
            "shop.example.com", [KEY], source="upgrade", inherited=True, store=store
        )
        compose_acceptance.record("shop.example.com", [KEY], source="deploy", store=store)

        assert compose_acceptance.inherited_domains(store=store) == []

    def test_a_key_that_is_not_three_texts_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValueError):
            compose_acceptance.record(
                "shop.example.com",
                [("only", "two")],
                source="deploy",
                store=store,  # type: ignore[list-item]
            )

    def test_a_source_nobody_knows_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValueError):
            compose_acceptance.record("shop.example.com", [KEY], source="whim", store=store)


class TestWhatTheGateSaid:
    def test_a_stack_waiting_for_the_upgrade_is_noted_with_why(self, store: NoustStore) -> None:
        compose_acceptance.note(
            "shop.example.com", "pending", "Compose said: no such file", store=store
        )

        note = compose_acceptance.note_of("shop.example.com", store=store)

        assert note is not None
        assert (note.state, note.detail) == ("pending", "Compose said: no such file")

    def test_a_refusal_replaces_a_pending_note(self, store: NoustStore) -> None:
        compose_acceptance.note("shop.example.com", "pending", "x", store=store)
        compose_acceptance.note("shop.example.com", "refused", "privileged", store=store)

        note = compose_acceptance.note_of("shop.example.com", store=store)

        assert note is not None and note.state == "refused"

    def test_a_note_is_cleared(self, store: NoustStore) -> None:
        compose_acceptance.note("shop.example.com", "refused", "privileged", store=store)
        compose_acceptance.clear_note("shop.example.com", store=store)

        assert compose_acceptance.note_of("shop.example.com", store=store) is None

    def test_a_state_nobody_knows_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValueError):
            compose_acceptance.note("shop.example.com", "angry", "x", store=store)


class TestItDiesWithTheApplication:
    def prepare(self, store: NoustStore) -> None:
        compose_acceptance.record("shop.example.com", [KEY], source="deploy", store=store)
        compose_acceptance.note("shop.example.com", "refused", "privileged", store=store)
        sandbox.set_compose_exception(
            "shop.example.com",
            allowed=True,
            actor="admin",
            reason="it controls this server's Docker",
            store=store,
        )

    def test_deleting_the_application_forgets_all_of_it(self, store: NoustStore) -> None:
        make_app(store)
        self.prepare(store)

        assert store.delete_app("shop.example.com")

        assert compose_acceptance.get("shop.example.com", store=store) is None
        assert compose_acceptance.note_of("shop.example.com", store=store) is None
        assert sandbox.get_compose_exception("shop.example.com", store=store) is None

    def test_an_exception_recorded_before_the_first_deployment_goes_with_the_deletion_too(
        self, store: NoustStore
    ) -> None:
        # No row yet: the exception names a domain, and it is the application that is deleted.
        self.prepare(store)

        assert not store.delete_app("shop.example.com")

        assert sandbox.get_compose_exception("shop.example.com", store=store) is None

    def test_another_domains_is_untouched(self, store: NoustStore) -> None:
        make_app(store)
        self.prepare(store)
        compose_acceptance.record("blog.example.com", [OTHER], source="deploy", store=store)

        store.delete_app("shop.example.com")

        assert compose_acceptance.get("blog.example.com", store=store) is not None

    def test_a_new_application_on_the_domain_starts_with_nothing(self, store: NoustStore) -> None:
        make_app(store)
        self.prepare(store)
        store.delete_app("shop.example.com")

        make_app(store)

        assert compose_acceptance.get("shop.example.com", store=store) is None
        assert sandbox.get_compose_exception("shop.example.com", store=store) is None


class TestAFirstDeploymentThatFailed:
    def test_what_the_guard_wrote_goes_and_the_exception_recorded_ahead_stays(
        self, store: NoustStore
    ) -> None:
        from noust.core.runner import FakeRunner
        from noust.deployers.docker_compose import DockerComposeDeployer

        TestItDiesWithTheApplication().prepare(store)
        compose = DockerComposeDeployer(runner=FakeRunner())
        compose.configure(
            "shop.example.com", "src", app_path=Path("/var/www/apps/shop-example-com")
        )
        compose.store = store

        from noust.deployers.helpers import compose_privileges

        compose_privileges.forget_rolled_back(compose)

        assert compose_acceptance.get("shop.example.com", store=store) is None
        assert compose_acceptance.note_of("shop.example.com", store=store) is None
        assert sandbox.get_compose_exception("shop.example.com", store=store) is not None

    def test_an_application_that_was_registered_goes_with_all_of_it(
        self, store: NoustStore
    ) -> None:
        from noust.core.runner import FakeRunner
        from noust.deployers.docker_compose import DockerComposeDeployer
        from noust.deployers.helpers import compose_privileges

        make_app(store)
        TestItDiesWithTheApplication().prepare(store)
        compose = DockerComposeDeployer(runner=FakeRunner())
        compose.configure(
            "shop.example.com", "src", app_path=Path("/var/www/apps/shop-example-com")
        )
        compose.store = store

        compose_privileges.forget_rolled_back(compose)

        assert store.get_app("shop.example.com") is None
        assert sandbox.get_compose_exception("shop.example.com", store=store) is None


class TestTheSchema:
    def test_a_new_store_has_the_tables_and_is_complete(self, store: NoustStore) -> None:
        cursor = store._get_connection().cursor()

        assert not schema_v18.incomplete(cursor)
        names = {
            row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"compose_accepted", "compose_gate_state"} <= names

    def test_a_store_of_a_development_build_gets_them_when_it_opens(self, tmp_path: Path) -> None:
        NoustStore.reset_instance()
        path = tmp_path / "noust.db"
        first = NoustStore(path)
        first._get_connection().executescript(
            "DROP TABLE compose_accepted; DROP TABLE compose_gate_state;"
        )
        first.close()
        NoustStore.reset_instance()

        second = NoustStore(path)
        try:
            names = {
                row[0]
                for row in second._get_connection().execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            NoustStore.reset_instance()

        assert {"compose_accepted", "compose_gate_state"} <= names
