# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving an engine to a newer major version (3.4, C-13).

The machine is :class:`~tests.database_instance_support.DockerHost`, so the new
instance is really made: its Compose file, its unit, its container. The engines
are :class:`~tests.database_move_support.TwinEngine`, so a database really leaves
one server and arrives in the other. What is pinned:

- the plan names everything that will happen and refuses what cannot;
- a move leaves the engine it came from exactly as it was;
- a move that fails at any step leaves no new instance behind and the
  applications where they were;
- a finished move can be rolled back, keeps what was written since, and says it
  did not carry it across.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.db_migration_store import DONE, FAILED, ROLLED_BACK, DbMigrationStore
from noust.core.exceptions import (
    DatabaseEngineError,
    DatabaseNotFoundError,
    DeploymentError,
    ValidationError,
)
from noust.managers.database import instance_layout as layout
from noust.managers.database.records import DatabaseRecords
from tests.database_migration_support import Lab, build_lab
from tests.database_move_support import APP_PASSWORD, DOMAIN, HOST


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    yield from build_lab(tmp_path, monkeypatch)


# ====================================================================== planning


class TestThePlan:
    def test_it_says_what_will_be_made_moved_and_restarted(self, lab: Lab) -> None:
        plan = lab.migrations.plan(HOST, "17", memory="1g")

        assert plan.blockers == []
        assert (plan.from_version, plan.to_version) == ("16.4", "17")
        assert plan.target == "postgresql17"
        assert plan.new_instance["memory_limit_mb"] == 1024
        assert [entry["database"] for entry in plan.databases] == ["shop_db"]
        assert plan.apps == [DOMAIN]
        assert any("rolled back" in warning for warning in plan.warnings)

    def test_planning_makes_nothing(self, lab: Lab) -> None:
        lab.migrations.plan(HOST, "17", memory="1g")

        assert lab.world.host.containers == {}
        assert lab.managed.records.all() == []
        assert DbMigrationStore(lab.world.store).all() == []

    def test_a_version_that_is_not_newer_is_a_blocker(self, lab: Lab) -> None:
        plan = lab.migrations.plan(HOST, "16", memory="1g")

        assert any("not newer" in blocker for blocker in plan.blockers)

    def test_a_version_the_catalog_lacks_is_refused_with_what_it_offers(self, lab: Lab) -> None:
        with pytest.raises(ValidationError) as caught:
            lab.migrations.plan(HOST, "99", memory="1g")

        assert "15, 16, 17, 18" in caught.value.details

    def test_what_blocks_a_database_is_said_with_its_name(self, lab: Lab) -> None:
        DatabaseRecords(lab.world.store).delete_link(lab.app.id or 0, HOST, "shop_db")

        plan = lab.migrations.plan(HOST, "17", memory="1g")

        assert any(
            blocker.startswith("shop_db: ") and "without a link" in blocker
            for blocker in plan.blockers
        )

    def test_an_engine_that_holds_no_database_still_gets_its_instance(self, lab: Lab) -> None:
        lab.host["dbs"].clear()

        plan = lab.migrations.plan(HOST, "17", memory="1g")

        assert plan.blockers == [] and plan.databases == []
        assert any("holds no database" in warning for warning in plan.warnings)


# ============================================================================ run


class TestAMoveThatSucceeds:
    def test_a_new_instance_is_made_and_the_database_is_on_it(self, lab: Lab) -> None:
        outcome = lab.migrations.run(HOST, "17", memory="1g")

        record = lab.managed.get("postgresql17")
        assert (record.version, record.state) == ("17", "ready")
        assert "noust-db-postgresql17" in {
            name.removesuffix(".service") for name in lab.world.host.enabled
        }
        assert lab.server("postgresql@postgresql17")["dbs"]["shop_db"]["rows"] == "live"
        assert outcome.moved[0]["apps"] == [DOMAIN]

    def test_the_engine_it_left_is_exactly_as_it_was(self, lab: Lab) -> None:
        lab.migrations.run(HOST, "17", memory="1g")

        assert lab.host["dbs"]["shop_db"]["rows"] == "live"
        assert lab.host["users"]["shop_user"] == APP_PASSWORD
        assert ("drop_database", "shop_db") not in lab.host["calls"]

    def test_the_application_points_at_the_new_instance(self, lab: Lab) -> None:
        lab.migrations.run(HOST, "17", memory="1g")

        port = lab.managed.get("postgresql17").host_port
        assert f"@localhost:{port}/shop_db" in lab.env()["DATABASE_URL"]
        assert len(lab.gate["restarts"]) == 1

    def test_the_move_is_written_down_with_what_went_across(self, lab: Lab) -> None:
        outcome = lab.migrations.run(HOST, "17", memory="1g")

        [row] = DbMigrationStore(lab.world.store).all()
        assert row.id == outcome.migration and row.state == DONE
        assert (row.source_key, row.target_name, row.to_version) == (HOST, "postgresql17", "17")
        assert [(entry.database, entry.apps) for entry in row.moved] == [("shop_db", (DOMAIN,))]

    def test_a_chosen_name_is_the_instances(self, lab: Lab) -> None:
        lab.migrations.run(HOST, "17", name="pg17", memory="1g")

        assert lab.managed.get("pg17").version == "17"

    def test_from_an_instance_the_old_one_is_stopped_not_removed(self, lab: Lab) -> None:
        lab.managed.create("pg16", "postgresql", version="16", memory="1g")
        lab.service.create("postgresql@pg16", "blog_db")
        lab.service.refresh_instances()

        lab.migrations.run("pg16", "17", name="pg17")

        old = lab.managed.describe(lab.managed.get("pg16"))
        assert old["status"] == "stopped" and old["state"] == "ready"
        assert lab.server("postgresql@pg17")["dbs"]["blog_db"] is not None
        assert layout.directory("pg16").exists(), "its files stay for the way back"


# ======================================================================== failure


class TestAMoveThatFails:
    def test_a_blocker_makes_nothing(self, lab: Lab) -> None:
        DatabaseRecords(lab.world.store).delete_link(lab.app.id or 0, HOST, "shop_db")

        with pytest.raises(ValidationError) as caught:
            lab.migrations.run(HOST, "17", memory="1g")

        assert "without a link" in caught.value.details
        assert lab.world.host.containers == {} and lab.managed.records.all() == []

    def test_an_application_that_does_not_come_up_leaves_no_instance_behind(self, lab: Lab) -> None:
        before = lab.env()
        lab.gate["results"] = [(False, "502 from /health")]

        with pytest.raises(DeploymentError) as caught:
            lab.migrations.run(HOST, "17", memory="1g")

        assert "502 from /health" in caught.value.details
        assert lab.env() == before
        assert lab.managed.records.all() == [], "the instance that was made is removed"
        assert lab.world.host.containers == {} and lab.world.host.volumes == set()
        [row] = DbMigrationStore(lab.world.store).all()
        assert row.state == FAILED
        assert lab.host["dbs"]["shop_db"]["rows"] == "live"

    def test_a_later_database_failing_points_the_first_one_back(self, lab: Lab, tmp_path) -> None:
        lab.host["dbs"]["zz_second"] = {"owner": "shop_user", "rows": "two"}
        lab.host["profiles"][("shop_user", "zz_second")] = "owner"
        from noust.core.store import App, Database
        from noust.managers.database.records import DatabaseLink

        path = tmp_path / "apps" / "zz-example-com"
        path.mkdir(parents=True)
        (path / ".env").write_text("OTHER=1\n")
        second = lab.world.store.create_app(App(domain="zz.example.com", app_path=str(path)))
        lab.world.store.create_database(
            Database(app_id=second.id, name="zz_second", engine=HOST, username="shop_user")
        )
        DatabaseRecords(lab.world.store).save_link(
            DatabaseLink(
                app_id=second.id or 0, engine=HOST, db_name="zz_second", username="shop_user"
            )
        )
        before = lab.env()
        lab.gate["results"] = [(True, ""), (False, "zz is down")]

        with pytest.raises(DeploymentError):
            lab.migrations.run(HOST, "17", memory="1g")

        assert lab.env() == before, "the application moved first is pointed back"
        assert lab.managed.records.all() == []
        [link] = DatabaseRecords(lab.world.store).links(app_id=lab.app.id)
        assert link.engine == HOST

    def test_an_instance_that_cannot_be_made_is_the_errors_to_say(self, lab: Lab) -> None:
        lab.world.host.fail[("docker", "pull")] = "toomanyrequests"

        with pytest.raises(DatabaseEngineError) as caught:
            lab.migrations.run(HOST, "17", memory="1g")

        assert "toomanyrequests" in (caught.value.output or "")
        assert lab.managed.records.all() == []
        [row] = DbMigrationStore(lab.world.store).all()
        assert row.state == FAILED


# ===================================================================== rollback


class TestRollback:
    def test_the_applications_go_back_to_the_engine_they_left(self, lab: Lab) -> None:
        before = lab.env()
        done = lab.migrations.run(HOST, "17", memory="1g")
        assert lab.env() != before

        outcome = lab.migrations.rollback(done.migration)

        assert lab.env()["DATABASE_URL"].split("@")[1].startswith("localhost:5432/")
        assert outcome.repointed == [{"database": "shop_db", "apps": [DOMAIN]}]
        [link] = DatabaseRecords(lab.world.store).links(app_id=lab.app.id)
        assert (link.engine, link.db_name) == (HOST, "shop_db")
        assert DbMigrationStore(lab.world.store).get(done.migration).state == ROLLED_BACK

    def test_what_was_written_since_is_dumped_first_and_said_not_to_be_carried(
        self, lab: Lab
    ) -> None:
        done = lab.migrations.run(HOST, "17", name="pg17", memory="1g")
        lab.server("postgresql@pg17")["dbs"]["shop_db"]["rows"] = "written after the move"

        outcome = lab.migrations.rollback("pg17")

        assert outcome.dumps, "a dump of the new instance was taken"
        assert lab.host["dbs"]["shop_db"]["rows"] == "live", "the old copy is not overwritten"
        assert any("not on postgresql" in warning for warning in outcome.warnings)
        assert done.migration == outcome.migration

    def test_the_new_instance_is_stopped_not_removed(self, lab: Lab) -> None:
        lab.migrations.run(HOST, "17", name="pg17", memory="1g")

        lab.migrations.rollback("pg17")

        described = lab.managed.describe(lab.managed.get("pg17"))
        assert described["status"] == "stopped"
        assert layout.directory("pg17").exists()

    def test_a_move_from_an_instance_starts_it_again(self, lab: Lab) -> None:
        lab.managed.create("pg16", "postgresql", version="16", memory="1g")
        lab.service.create("postgresql@pg16", "blog_db")
        done = lab.migrations.run("pg16", "17", name="pg17")
        assert lab.managed.describe(lab.managed.get("pg16"))["status"] == "stopped"

        lab.migrations.rollback(done.migration)

        assert lab.managed.describe(lab.managed.get("pg16"))["status"] == "running"

    def test_it_cannot_be_done_twice(self, lab: Lab) -> None:
        done = lab.migrations.run(HOST, "17", memory="1g")
        lab.migrations.rollback(done.migration)

        with pytest.raises(DatabaseEngineError) as caught:
            lab.migrations.rollback(done.migration)

        assert "rolled_back" in caught.value.message

    def test_it_cannot_be_done_once_the_old_instance_is_removed(self, lab: Lab) -> None:
        lab.managed.create("pg16", "postgresql", version="16", memory="1g")
        done = lab.migrations.run("pg16", "17", name="pg17")
        lab.managed.remove("pg16", confirm=True)

        with pytest.raises(DatabaseEngineError):
            lab.migrations.rollback(done.migration)
        assert [entry["can_roll_back"] for entry in lab.migrations.all()] == [False]

    def test_an_unknown_move_is_not_found(self, lab: Lab) -> None:
        with pytest.raises(DatabaseNotFoundError):
            lab.migrations.rollback(99)

    def test_a_finished_move_is_listed_as_one_that_can_be_rolled_back(self, lab: Lab) -> None:
        lab.migrations.run(HOST, "17", memory="1g")

        [entry] = lab.migrations.all()

        assert entry["state"] == "done" and entry["can_roll_back"] is True
