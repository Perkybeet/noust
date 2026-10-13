# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving a database to another engine (3.4, C-21 and the way C-13 moves a version).

What is pinned is what makes a move safe to start:

- **the source is never touched**: whatever fails, the database is where it was
  and so are the applications' variables;
- **the accounts arrive first**, with the passwords Noust keeps, so an
  application's connection string changes in its host and port only;
- **an application that does not come up on the target puts everything back**:
  its variables, the ones of every application changed before it, the database
  and the accounts the move made;
- **nothing is moved that Noust cannot point an application away from**: a
  variable it did not write, a public database, an older server, a name taken.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.exceptions import DatabaseNotFoundError, DeploymentError, ValidationError
from noust.core.store import App
from noust.managers.database.moves import DatabaseMover
from noust.managers.database.records import DatabaseLink, DatabaseRecords
from tests.database_move_support import (
    APP_PASSWORD,
    DOMAIN,
    HOST,
    INSTANCE_KEY,
    FakePolicies,
    Rig,
    build_rig,
)


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Rig]:
    yield from build_rig(tmp_path, monkeypatch)


def mover(rig: Rig, policies: FakePolicies | None = None) -> DatabaseMover:
    return DatabaseMover(rig.service, backups=policies or FakePolicies())


# ====================================================================== planning


class TestWhatTheMoveWouldDo:
    def test_it_names_the_applications_the_accounts_and_the_versions(self, rig: Rig) -> None:
        plan = mover(rig).plan(HOST, "shop_db", INSTANCE_KEY)

        assert plan.blockers == []
        assert plan.apps == [DOMAIN]
        assert plan.accounts == ["reporter", "shop_user"]
        assert plan.new_passwords == ["reporter"], "Noust never learnt reporter's password"
        assert (plan.source_version, plan.target_version) == ("16.4", "17.2")
        assert any("reporter" in warning for warning in plan.warnings)

    def test_planning_changes_nothing(self, rig: Rig) -> None:
        mover(rig).plan(HOST, "shop_db", INSTANCE_KEY)

        assert rig.instance["dbs"] == {}
        assert rig.instance["calls"] == []
        assert rig.gate["restarts"] == []

    def test_a_database_that_is_not_on_the_source_is_not_found(self, rig: Rig) -> None:
        with pytest.raises(DatabaseNotFoundError):
            mover(rig).plan(HOST, "nothing", INSTANCE_KEY)

    def test_the_same_engine_is_refused(self, rig: Rig) -> None:
        plan = mover(rig).plan(HOST, "shop_db", HOST)

        assert any("same engine" in blocker for blocker in plan.blockers)

    def test_an_older_server_is_refused_because_its_loader_would_not_read_the_dump(
        self, rig: Rig
    ) -> None:
        rig.host["dbs"]["shop_db"]["rows"] = "live"
        rig.instance["dbs"]["old_db"] = {"owner": None, "rows": "x"}
        # The way back is a move to an older server: it is what a dump cannot do.
        plan = mover(rig).plan(INSTANCE_KEY, "old_db", HOST)

        assert any("older than the source" in blocker for blocker in plan.blockers)

    def test_a_name_the_target_holds_is_refused_unless_it_is_to_be_replaced(self, rig: Rig) -> None:
        rig.instance["dbs"]["shop_db"] = {"owner": None, "rows": "stale"}

        taken = mover(rig).plan(HOST, "shop_db", INSTANCE_KEY)
        replacing = mover(rig).plan(HOST, "shop_db", INSTANCE_KEY, replace=True)

        assert any("already has a database" in blocker for blocker in taken.blockers)
        assert replacing.blockers == [] and replacing.exists_on_target

    def test_a_database_made_public_must_be_closed_first(self, rig: Rig) -> None:
        from noust.core.database_exposures import ExposureStore

        ExposureStore(rig.store).create(
            engine=HOST,
            db_name="shop_db",
            port=15432,
            bind_address="0.0.0.0",  # noqa: S104 - a recorded value, nothing binds
            sources=("203.0.113.7",),
            tls="required",
            remote_user="reporter",
            profile="read_only",
            unit="noust-dbpub-shop",
            created_by=None,
        )

        plan = mover(rig).plan(HOST, "shop_db", INSTANCE_KEY)

        assert any("reachable from the network" in blocker for blocker in plan.blockers)

    def test_a_variable_noust_did_not_write_is_a_blocker(self, rig: Rig) -> None:
        records = DatabaseRecords(rig.store)
        records.delete_link(rig.app.id or 0, HOST, "shop_db")
        records.save_link(
            DatabaseLink(app_id=rig.app.id or 0, engine=HOST, db_name="shop_db", username=None)
        )

        plan = mover(rig).plan(HOST, "shop_db", INSTANCE_KEY)

        assert any("did not write" in blocker for blocker in plan.blockers)

    def test_an_owner_without_a_link_is_a_blocker(self, rig: Rig) -> None:
        DatabaseRecords(rig.store).delete_link(rig.app.id or 0, HOST, "shop_db")

        plan = mover(rig).plan(HOST, "shop_db", INSTANCE_KEY)

        assert any("owns it without a link" in blocker for blocker in plan.blockers)

    def test_an_account_an_application_signs_in_as_needs_a_password_noust_knows(
        self, rig: Rig
    ) -> None:
        rig.service.secrets.delete("databases/postgresql/shop_user")

        plan = mover(rig).plan(HOST, "shop_db", INSTANCE_KEY)

        assert any("shop_user signs an application in" in blocker for blocker in plan.blockers)


# ========================================================================== a move


class TestAMoveThatSucceeds:
    def test_the_data_arrives_and_the_source_stays_as_it_was(self, rig: Rig) -> None:
        outcome = mover(rig).move(HOST, "shop_db", INSTANCE_KEY)

        assert rig.instance["dbs"]["shop_db"]["rows"] == "live"
        assert rig.host["dbs"]["shop_db"]["rows"] == "live", "the source is not touched"
        assert outcome.dump and outcome.source_dropped is False
        assert ("drop_database", "shop_db") not in rig.host["calls"]

    def test_accounts_exist_on_the_target_with_the_passwords_noust_kept(self, rig: Rig) -> None:
        outcome = mover(rig).move(HOST, "shop_db", INSTANCE_KEY)

        assert rig.instance["users"]["shop_user"] == APP_PASSWORD
        assert rig.instance["users"]["reporter"] != "theirs", "its password was never known"
        assert outcome.new_passwords == ["reporter"]
        assert (
            rig.service.reveal_password(INSTANCE_KEY, "reporter")
            == (rig.instance["users"]["reporter"])
        )
        assert rig.instance["profiles"][("reporter", "shop_db")] == "read_only"
        assert rig.instance["profiles"][("shop_user", "shop_db")] == "owner"

    def test_accounts_are_made_before_the_dump_is_loaded(self, rig: Rig) -> None:
        mover(rig).move(HOST, "shop_db", INSTANCE_KEY)

        names = [call[0] for call in rig.instance["calls"]]
        assert names.index("create_user") < names.index("load")

    def test_the_application_is_pointed_at_the_target_behind_its_gate(self, rig: Rig) -> None:
        outcome = mover(rig).move(HOST, "shop_db", INSTANCE_KEY)

        url = rig.env()["DATABASE_URL"]
        assert url.startswith("postgresql://shop_user:") and "@localhost:5434/shop_db" in url
        assert rig.env()["APP_KEY"] == "base64:abc", "the rest of its environment is kept"
        assert len(rig.gate["restarts"]) == 1
        assert outcome.apps == [DOMAIN]

    def test_the_store_follows_the_database(self, rig: Rig) -> None:
        mover(rig).move(HOST, "shop_db", INSTANCE_KEY)

        moved = rig.store.get_database("shop_db", INSTANCE_KEY)
        left = rig.store.get_database("shop_db", HOST)
        assert moved is not None and moved.app_id == rig.app.id and moved.port == 5434
        assert left is not None and left.app_id is None, "the app's backups take the new one"
        [link] = DatabaseRecords(rig.store).links(app_id=rig.app.id)
        assert (link.engine, link.db_name, link.username) == (INSTANCE_KEY, "shop_db", "shop_user")

    def test_a_database_nobody_uses_moves_without_restarting_anything(self, rig: Rig) -> None:
        DatabaseRecords(rig.store).delete_link(rig.app.id or 0, HOST, "shop_db")
        row = rig.store.get_database("shop_db", HOST)
        assert row is not None
        row.app_id = None
        rig.store.update_database(row)

        outcome = mover(rig).move(HOST, "shop_db", INSTANCE_KEY)

        assert outcome.apps == [] and rig.gate["restarts"] == []

    def test_dropping_the_source_takes_a_last_dump_first(self, rig: Rig) -> None:
        outcome = mover(rig).move(HOST, "shop_db", INSTANCE_KEY, drop_source=True)

        assert outcome.source_dropped is True
        assert "shop_db" not in rig.host["dbs"]
        names = [call[0] for call in rig.host["calls"]]
        assert names.index("backup") < names.index("drop_database")
        assert rig.instance["dbs"]["shop_db"]["rows"] == "live"

    def test_replacing_keeps_a_safety_copy_of_what_was_there(self, rig: Rig) -> None:
        rig.instance["dbs"]["shop_db"] = {"owner": None, "rows": "stale"}

        outcome = mover(rig).move(HOST, "shop_db", INSTANCE_KEY, replace=True)

        assert outcome.safety_copy is not None
        assert rig.instance["dbs"]["shop_db"]["rows"] == "live"

    def test_the_backup_policy_follows_the_database(self, rig: Rig) -> None:
        policy = SimpleNamespace(
            schedule="*-*-* 03:00:00",
            retention_count=7,
            retention_days=30,
            retention_bytes=2 * 1024**3,
            destinations=[{"name": "offsite"}],
            dump_format=None,
            verify_restore=True,
            enabled=True,
        )
        policies = FakePolicies(policy)

        outcome = mover(rig, policies).move(HOST, "shop_db", INSTANCE_KEY)

        [(engine, database, kwargs)] = policies.set_calls
        assert (engine, database) == (INSTANCE_KEY, "shop_db")
        assert kwargs["retention_gb"] == 2.0 and kwargs["verify_restore"] is True
        assert policies.removed == [(HOST, "shop_db")]
        assert outcome.policies == ["*-*-* 03:00:00"]

    def test_a_policy_that_cannot_follow_is_left_where_it_is_and_said(self, rig: Rig) -> None:
        policies = FakePolicies(
            SimpleNamespace(
                schedule="daily",
                retention_count=7,
                retention_days=30,
                retention_bytes=None,
                destinations=[],
                dump_format=None,
                verify_restore=False,
                enabled=True,
            )
        )
        policies.fail = True

        outcome = mover(rig, policies).move(HOST, "shop_db", INSTANCE_KEY)

        assert policies.removed == []
        assert any("did not follow" in warning for warning in outcome.warnings)


# ======================================================================== a failure


class TestAMoveThatFails:
    def test_a_blocker_stops_it_before_anything_is_dumped(self, rig: Rig) -> None:
        with pytest.raises(ValidationError) as caught:
            mover(rig).move(HOST, "shop_db", HOST)

        assert "same engine" in caught.value.details
        assert rig.host["calls"] == []

    def test_an_application_that_does_not_come_up_puts_everything_back(self, rig: Rig) -> None:
        before = rig.env()
        rig.gate["results"] = [(False, "502 from /health")]

        with pytest.raises(DeploymentError) as caught:
            mover(rig).move(HOST, "shop_db", INSTANCE_KEY)

        assert "502 from /health" in caught.value.details
        assert rig.env() == before, "its previous variables are back"
        assert "shop_db" not in rig.instance["dbs"], "the copy made on the target is removed"
        assert "shop_user" not in rig.instance["users"] and "reporter" not in rig.instance["users"]
        assert rig.host["dbs"]["shop_db"]["rows"] == "live"
        left = rig.store.get_database("shop_db", HOST)
        assert left is not None and left.app_id == rig.app.id
        [link] = DatabaseRecords(rig.store).links(app_id=rig.app.id)
        assert link.engine == HOST

    def test_a_dump_that_does_not_load_leaves_the_target_without_the_database(
        self, rig: Rig
    ) -> None:
        rig.instance["fail_load"] = True

        with pytest.raises(Exception) as caught:
            mover(rig).move(HOST, "shop_db", INSTANCE_KEY)

        assert "pg_restore" in str(caught.value)
        assert "shop_user" not in rig.instance["users"], "the accounts it made are removed"
        assert rig.gate["restarts"] == [], "no application was touched"

    def test_a_second_application_failing_undoes_the_first(self, rig: Rig, tmp_path) -> None:
        second_path = tmp_path / "apps" / "blog-example-com"
        second_path.mkdir(parents=True)
        (second_path / ".env").write_text("OTHER=1\n")
        second = rig.store.create_app(App(domain="blog.example.com", app_path=str(second_path)))
        DatabaseRecords(rig.store).save_link(
            DatabaseLink(
                app_id=second.id or 0,
                engine=HOST,
                db_name="shop_db",
                username="shop_user",
                env_var="BLOG_DB",
            )
        )
        first_before = rig.env()
        rig.gate["results"] = [(True, ""), (False, "blog is down")]

        with pytest.raises(DeploymentError):
            mover(rig).move(HOST, "shop_db", INSTANCE_KEY)

        assert rig.env() == first_before, "the application changed first was put back"
        assert "BLOG_DB" not in (tmp_path / "apps" / "blog-example-com" / ".env").read_text()
