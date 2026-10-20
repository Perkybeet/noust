# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving to another server: ``prepare`` on the old one and ``check`` on the new one (3.4, H-30).

The managers that take and send the copies are replaced by recorders, so what is defended is the
orchestration, which is where a move goes wrong at night:

- **nothing is changed on the old server, and a piece that fails does not stop the rest**: one
  application without a good backup is reported and the other twenty are still copied, the bundle
  of Noust's own state last, so it is consistent with everything before it;
- **the plan refuses before copying anything** (a destination that does not exist or cannot be
  reached, an application that is not one), and says what the new server needs;
- **the check is the questions a person asks before moving DNS**: does it run, does it answer the
  way the deploy's gate asks, is its certificate there, are its databases up, and where does each
  name resolve now; a name still on the old server is *pending*, never wrong.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import audit
from noust.core.app_state import AppState
from noust.core.exceptions import BackupError, NoustError, ValidationError
from noust.core.store import App, Database, NoustStore
from noust.deployers.domains import DnsCheck
from noust.managers.database.records import DatabaseLink, DatabaseRecords
from noust.managers.server_migration import ServerMigration
from noust.monitor.reachability import AppProbe

HOST = "old-host"
NEW_ADDRESS = "203.0.113.10"
OLD_ADDRESS = "198.51.100.7"


class Recorder:
    """Stands in for every manager that takes or sends a copy, and remembers the order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], Exception] = {}
        self.targets: dict[str, list[str]] = {}
        self.destination_exists = True
        self.reachable = True
        self.passphrase: str | None = None
        self.backed_up: dict[str, str] = {}

    def failure(self, kind: str, name: str) -> None:
        error = self.fail.get((kind, name))
        if error is not None:
            raise error

    # --- BackupManager
    def create(self, domain: str, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["include_databases"] and kwargs["include_docker_volumes"]
        assert kwargs["tags"] == ["migration"]
        self.failure("application", domain)
        self.calls.append(("backup", domain))
        return SimpleNamespace(id=f"{domain}-b1", domain=domain, created_at="2026-10-13T10:00:00")

    def get_latest_backup(self, domain: str) -> SimpleNamespace | None:
        found = self.backed_up.get(domain)
        return SimpleNamespace(created_at=found) if found else None

    # --- VolumeBackupManager
    def targets_for(self, domain: str) -> list[str]:
        return self.targets.get(domain, [])

    def push_volume(self, backup_id: str, destination: str) -> dict[str, Any]:
        self.failure("push", backup_id)
        self.calls.append(("push", backup_id))
        return {}

    # --- BackupDestinationManager
    def get(self, name: str) -> object | None:
        return object() if self.destination_exists else None

    def test(self, name: str) -> dict[str, Any]:
        if not self.reachable:
            raise BackupError("Could not reach backup destination 'nas'", details="timeout")
        return {"ok": True}

    def push(self, metadata: Any, destination: str, **kwargs: Any) -> dict[str, Any]:
        self.failure("push", metadata.id)
        self.calls.append(("push", metadata.id))
        return {}

    # --- SelfBackupManager
    def host_slug(self) -> str:
        return HOST

    def stored_passphrase(self) -> str | None:
        return self.passphrase

    def seal(self, passphrase: str, *, kind: str) -> SimpleNamespace:
        self.failure("noust", HOST)
        self.calls.append(("seal", passphrase))
        return SimpleNamespace(path=Path(f"noust-self-{HOST}-20261013.tar.gz.enc"))

    def push_bundle(self, name: str, destination: str) -> dict[str, Any]:
        self.failure("noust-push", name)
        self.calls.append(("push", name))
        return {}


class FakeVolumes:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def targets(self, domain: str) -> list[str]:
        return self.recorder.targets_for(domain)

    def create(self, domain: str, **kwargs: Any) -> list[SimpleNamespace]:
        self.recorder.failure("data", domain)
        made = [
            SimpleNamespace(id=f"{domain}.{target}-v1", target=target)
            for target in self.recorder.targets_for(domain)
        ]
        self.recorder.calls.append(("data", domain))
        return made

    def push(self, backup_id: str, destination: str) -> dict[str, Any]:
        return self.recorder.push_volume(backup_id, destination)


class FakeSelf:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def host_slug(self) -> str:
        return self.recorder.host_slug()

    def stored_passphrase(self) -> str | None:
        return self.recorder.stored_passphrase()

    def create(self, passphrase: str, *, kind: str = "manual") -> SimpleNamespace:
        return self.recorder.seal(passphrase, kind=kind)

    def push(self, name: str, destination: str) -> dict[str, Any]:
        return self.recorder.push_bundle(name, destination)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    opened = NoustStore(tmp_path / "noust.db")
    yield opened
    opened.close()
    NoustStore.reset_instance()


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        audit, "record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


def add_app(store: NoustStore, domain: str, **fields: Any) -> App:
    return store.create_app(App(domain=domain, app_path=f"/var/www/apps/{domain}", **fields))


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def migration(
    store: NoustStore, recorder: Recorder, tmp_path: Path, audited: list[dict[str, Any]]
) -> ServerMigration:
    dumped: list[tuple[str, str, str]] = []

    def dump(engine: str, database: str, destination: str) -> str:
        recorder.failure("database", f"{engine}/{database}")
        dumped.append((engine, database, destination))
        recorder.calls.append(("dump", f"{engine}/{database}"))
        return f"{database}.sql.gz"

    return ServerMigration(
        store=store,
        backups=recorder,  # type: ignore[arg-type]
        volumes=FakeVolumes(recorder),  # type: ignore[arg-type]
        destinations=recorder,  # type: ignore[arg-type]
        self_backups=FakeSelf(recorder),  # type: ignore[arg-type]
        databases=dump,
    )


class TestThePlan:
    def test_it_lists_what_would_move_and_what_the_new_server_needs(
        self, store: NoustStore, migration: ServerMigration, recorder: Recorder
    ) -> None:
        shop = add_app(store, "shop.example.com")
        store.add_domain("shop.example.com", "www.shop.example.com", "alias")
        add_app(store, "blog.example.com")
        DatabaseRecords(store).save_link(
            DatabaseLink(app_id=shop.id or 0, engine="postgresql", db_name="shop_db")
        )
        store.create_database(Database(name="orphan", engine="mysql"))
        recorder.targets["shop.example.com"] = ["uploads", "volume:shop_data"]
        recorder.backed_up["shop.example.com"] = "2026-10-12T02:00:00"
        recorder.passphrase = "kept"

        plan = migration.plan("nas")

        by_domain = {item["domain"]: item for item in plan.applications}
        assert by_domain["shop.example.com"]["names"] == [
            "shop.example.com",
            "www.shop.example.com",
        ]
        assert by_domain["shop.example.com"]["databases"] == ["postgresql/shop_db"]
        assert by_domain["shop.example.com"]["data"] == 2
        assert by_domain["shop.example.com"]["last_backup"] == "2026-10-12T02:00:00"
        assert by_domain["blog.example.com"]["last_backup"] is None
        assert plan.databases == ["mysql/orphan"]
        assert plan.engines == ["mysql", "postgresql"]
        assert plan.passphrase_stored is True and plan.blockers == []
        assert plan.host == HOST

    def test_a_preview_of_a_pull_request_does_not_move(
        self, store: NoustStore, migration: ServerMigration
    ) -> None:
        add_app(store, "shop.example.com")
        add_app(store, "pr-7.shop.example.com", preview_parent="shop.example.com")

        plan = migration.plan("nas")

        assert [item["domain"] for item in plan.applications] == ["shop.example.com"]

    def test_a_destination_that_does_not_exist_blocks(
        self, migration: ServerMigration, recorder: Recorder
    ) -> None:
        recorder.destination_exists = False

        plan = migration.plan("nas")

        assert any(
            "does not exist" in blocker and "destination add" in blocker
            for blocker in plan.blockers
        )

    def test_a_destination_that_cannot_be_reached_blocks_with_the_reason(
        self, migration: ServerMigration, recorder: Recorder
    ) -> None:
        recorder.reachable = False

        plan = migration.plan("nas")

        assert any(
            "cannot be reached" in blocker and "Could not reach" in blocker
            for blocker in plan.blockers
        )

    def test_instances_are_said_to_be_made_again(
        self, store: NoustStore, migration: ServerMigration
    ) -> None:
        from noust.core.db_instance_store import DbInstanceStore

        DbInstanceStore(store).create(
            name="pg17",
            flavour="postgresql",
            version="17",
            image="postgres",
            digest="sha256:" + "a" * 64,
            host_port=5433,
            memory_limit_mb=512,
            created_by=None,
        )

        plan = migration.plan("nas")

        assert plan.instances == ["pg17"]
        assert any("noust db instance create" in warning for warning in plan.warnings)

    def test_a_server_without_applications_says_only_its_state_moves(
        self, migration: ServerMigration
    ) -> None:
        plan = migration.plan("nas")

        assert any("only its own state is copied" in warning for warning in plan.warnings)


class TestPreparing:
    def test_everything_is_copied_in_an_order_that_ends_with_the_state(
        self, store: NoustStore, migration: ServerMigration, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        add_app(store, "blog.example.com")
        store.create_database(Database(name="orphan", engine="postgresql"))
        recorder.targets["shop.example.com"] = ["uploads"]

        report = migration.prepare("nas", "pass phrase")

        assert [(step.kind, step.name, step.state) for step in report.steps] == [
            ("application", "blog.example.com", "done"),
            ("application", "shop.example.com", "done"),
            ("data", "shop.example.com/uploads", "done"),
            ("database", "postgresql/orphan", "done"),
            ("noust", HOST, "done"),
        ]
        assert [kind for kind, _name in recorder.calls] == [
            "backup",
            "push",
            "backup",
            "push",
            "data",
            "push",
            "dump",
            "seal",
            "push",
        ]
        assert report.ok and report.bundle == f"noust-self-{HOST}-20261013.tar.gz.enc"
        assert report.counts() == {"done": 5, "failed": 0, "skipped": 0}

    def test_the_commands_for_the_new_server_are_exact(
        self, store: NoustStore, migration: ServerMigration
    ) -> None:
        shop = add_app(store, "shop.example.com")
        DatabaseRecords(store).save_link(
            DatabaseLink(app_id=shop.id or 0, engine="postgresql", db_name="shop_db")
        )

        report = migration.prepare("nas", "pass phrase")

        assert (
            f"noust restore-server --install --from-destination nas --name {report.bundle} "
            f"--host {HOST}" in report.next_steps
        )
        assert any(
            "postgresql" in step and "installs the web server, Docker" in step
            for step in report.next_steps
        )
        assert report.next_steps[-2:] == [
            "noust server migrate check",
            "Point each name at the new server (the check lists them and where they resolve now), "
            "then 'noust cert create' for the ones without a certificate.",
        ]

    def test_an_application_that_fails_is_reported_and_the_rest_go_on(
        self, store: NoustStore, migration: ServerMigration, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        add_app(store, "blog.example.com")
        recorder.fail[("application", "shop.example.com")] = BackupError(
            "The backup could not be made", details="no space left"
        )

        report = migration.prepare("nas", "pass phrase")

        states = {step.name: step for step in report.steps}
        assert states["shop.example.com"].state == "failed"
        assert "no space left" in states["shop.example.com"].detail
        assert states["blog.example.com"].state == "done"
        assert report.bundle is not None, "the state is sealed whatever happened before"
        assert report.ok is False and report.counts()["failed"] == 1

    def test_a_piece_of_data_that_will_not_send_fails_alone(
        self, store: NoustStore, migration: ServerMigration, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        recorder.targets["shop.example.com"] = ["uploads", "media"]
        recorder.fail[("push", "shop.example.com.uploads-v1")] = BackupError(
            "the destination refused it"
        )

        report = migration.prepare("nas", "pass phrase")

        data = {step.name: step.state for step in report.steps if step.kind == "data"}
        assert data == {"shop.example.com/uploads": "failed", "shop.example.com/media": "done"}

    def test_a_state_that_cannot_be_sealed_says_how_to_do_it_by_hand(
        self, store: NoustStore, migration: ServerMigration, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        recorder.fail[("noust", HOST)] = BackupError("openssl is not installed")

        report = migration.prepare("nas", "pass phrase")

        assert report.bundle is None and report.ok is False
        assert any("self-backup create" in step for step in report.next_steps)
        assert all("restore-server" not in step for step in report.next_steps)

    def test_a_database_that_cannot_be_dumped_fails_alone(
        self, store: NoustStore, migration: ServerMigration, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        store.create_database(Database(name="orphan", engine="postgresql"))
        store.create_database(Database(name="other", engine="postgresql"))
        recorder.fail[("database", "postgresql/orphan")] = BackupError("the dump came out empty")

        report = migration.prepare("nas", "pass phrase")

        databases = {step.name: step.state for step in report.steps if step.kind == "database"}
        assert databases == {"postgresql/orphan": "failed", "postgresql/other": "done"}

    def test_only_the_applications_asked_for_move_but_the_state_always_does(
        self, store: NoustStore, migration: ServerMigration
    ) -> None:
        add_app(store, "shop.example.com")
        add_app(store, "blog.example.com")

        report = migration.prepare("nas", "pass phrase", only=["blog.example.com"])

        states = {step.name: step.state for step in report.steps}
        assert states["shop.example.com"] == "skipped" and states["blog.example.com"] == "done"
        assert states[HOST] == "done"

    def test_an_application_that_is_not_one_is_refused_before_anything_is_copied(
        self, store: NoustStore, migration: ServerMigration, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")

        with pytest.raises(ValidationError) as caught:
            migration.prepare("nas", "pass phrase", only=["nope.example.com"])

        assert caught.value.field == "only" and "shop.example.com" in caught.value.details
        assert recorder.calls == []

    def test_a_destination_that_cannot_be_used_is_refused_before_anything_is_copied(
        self, store: NoustStore, migration: ServerMigration, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        recorder.reachable = False

        with pytest.raises(ValidationError) as caught:
            migration.prepare("nas", "pass phrase")

        assert caught.value.field == "destination" and "cannot be reached" in caught.value.details
        assert recorder.calls == []

    def test_the_data_can_be_left_out(
        self, store: NoustStore, migration: ServerMigration, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        recorder.targets["shop.example.com"] = ["uploads"]

        report = migration.prepare("nas", "pass phrase", data=False)

        assert [step.kind for step in report.steps] == ["application", "noust"]

    def test_an_application_without_data_has_no_data_step(
        self, store: NoustStore, migration: ServerMigration
    ) -> None:
        add_app(store, "shop.example.com")

        report = migration.prepare("nas", "pass phrase")

        assert [step.kind for step in report.steps] == ["application", "noust"]

    def test_the_old_server_is_left_as_it_was_and_the_move_is_audited(
        self,
        store: NoustStore,
        migration: ServerMigration,
        audited: list[dict[str, Any]],
    ) -> None:
        add_app(store, "shop.example.com")

        migration.prepare("nas", "pass phrase")

        [event] = [item for item in audited if item["event"] == "server.migrate.prepare"]
        assert event["details"]["destination"] == "nas" and event["details"]["done"] == 2
        assert [app.domain for app in store.list_apps()] == ["shop.example.com"]

    def test_the_passphrase_is_never_in_the_report(
        self, store: NoustStore, migration: ServerMigration
    ) -> None:
        add_app(store, "shop.example.com")

        report = migration.prepare("nas", "correct horse battery staple")

        assert "correct horse" not in str(report.to_dict())

    def test_an_unexpected_failure_of_a_manager_is_a_failed_step_and_not_a_crash(
        self, store: NoustStore, migration: ServerMigration, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        recorder.fail[("application", "shop.example.com")] = NoustError("something odd")

        report = migration.prepare("nas", "pass phrase")

        assert next(step for step in report.steps if step.kind == "application").state == "failed"


# =================================================================== the check


def dns_for(addresses: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    def check(domain: str) -> DnsCheck:
        resolved = tuple(sorted(addresses.get(domain, [])))
        return DnsCheck(
            domain=domain,
            expected_addresses=(NEW_ADDRESS,),
            resolved_addresses=resolved,
            points_here=bool(resolved) and set(resolved) <= {NEW_ADDRESS},
        )

    return check


def checker(
    store: NoustStore,
    recorder: Recorder,
    *,
    states: dict[str, AppState] | None = None,
    probes: dict[str, AppProbe | None] | None = None,
    certificates: list[dict[str, Any]] | None = None,
    dns: dict[str, list[str]] | None = None,
    engines: dict[str, tuple[bool, str]] | None = None,
) -> ServerMigration:
    return ServerMigration(
        store=store,
        backups=recorder,  # type: ignore[arg-type]
        volumes=FakeVolumes(recorder),  # type: ignore[arg-type]
        destinations=recorder,  # type: ignore[arg-type]
        self_backups=FakeSelf(recorder),  # type: ignore[arg-type]
        dns=dns_for(dns or {}),
        probe=lambda app: (probes or {}).get(app.domain, AppProbe(ok=True, detail="")),
        states=lambda apps: {
            app.domain: (states or {}).get(app.domain, AppState("Running", True)) for app in apps
        },
        certificates=lambda: certificates or [],
        engine_up=lambda engine, database: (engines or {}).get(f"{engine}/{database}", (True, "")),
    )


class TestChecking:
    def test_an_application_that_runs_answers_and_has_everything_is_ok(
        self, store: NoustStore, recorder: Recorder
    ) -> None:
        shop = add_app(store, "shop.example.com")
        DatabaseRecords(store).save_link(
            DatabaseLink(app_id=shop.id or 0, engine="postgresql", db_name="shop_db")
        )
        migration = checker(
            store,
            recorder,
            certificates=[{"domains": ["shop.example.com"], "expiry": "2099-01-01T00:00:00+00:00"}],
            dns={"shop.example.com": [NEW_ADDRESS]},
        )

        report = migration.check()

        [item] = report.applications
        assert item.status == "ok" and item.answers is True and item.problems == []
        assert item.names[0]["resolves_here"] is True and item.names[0]["has_certificate"] is True
        assert item.databases == [
            {"engine": "postgresql", "database": "shop_db", "ok": True, "detail": ""}
        ]
        assert report.ok and report.pending == [] and report.addresses == [NEW_ADDRESS]

    def test_a_unit_that_does_not_run_is_down_with_its_reason(
        self, store: NoustStore, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        migration = checker(
            store,
            recorder,
            states={"shop.example.com": AppState("Stopped", False, "The unit is inactive")},
        )

        report = migration.check()

        [item] = report.applications
        assert item.status == "down" and "The unit is inactive" in item.problems
        assert report.ok is False

    def test_an_application_that_does_not_answer_shows_the_probe_verbatim(
        self, store: NoustStore, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        migration = checker(
            store,
            recorder,
            probes={
                "shop.example.com": AppProbe(
                    ok=False, detail="GET http://127.0.0.1:3000/ -> HTTP 502"
                )
            },
        )

        [item] = migration.check().applications

        assert item.status == "down" and item.answers is False
        assert item.probe == "GET http://127.0.0.1:3000/ -> HTTP 502"

    def test_a_static_site_has_nothing_to_ask(self, store: NoustStore, recorder: Recorder) -> None:
        add_app(store, "docs.example.com")
        migration = checker(store, recorder, probes={"docs.example.com": None})

        [item] = migration.check().applications

        assert item.answers is None and item.status != "down"

    def test_a_name_still_on_the_old_server_is_pending_and_not_wrong(
        self, store: NoustStore, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        store.add_domain("shop.example.com", "www.shop.example.com", "alias")
        migration = checker(
            store,
            recorder,
            dns={"shop.example.com": [OLD_ADDRESS], "www.shop.example.com": [NEW_ADDRESS]},
        )

        report = migration.check()

        assert report.ok is True
        assert report.pending == [
            {
                "name": "shop.example.com",
                "application": "shop.example.com",
                "resolves_to": [OLD_ADDRESS],
            }
        ]

    def test_a_name_that_resolves_nowhere_is_pending_too(
        self, store: NoustStore, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")

        report = checker(store, recorder).check()

        assert report.pending[0]["resolves_to"] == []

    def test_a_name_without_a_certificate_is_said_and_wants_attention(
        self, store: NoustStore, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")

        [item] = (
            checker(store, recorder, dns={"shop.example.com": [NEW_ADDRESS]}).check().applications
        )

        assert item.status == "attention"
        assert "shop.example.com has no certificate on this server yet" in item.problems

    def test_an_expired_certificate_is_said(self, store: NoustStore, recorder: Recorder) -> None:
        add_app(store, "shop.example.com")
        migration = checker(
            store,
            recorder,
            certificates=[{"domains": ["shop.example.com"], "expiry": "2020-01-01T00:00:00+00:00"}],
        )

        [item] = migration.check().applications

        assert any("expired" in problem for problem in item.problems)

    def test_a_database_that_is_not_there_puts_the_application_down(
        self, store: NoustStore, recorder: Recorder
    ) -> None:
        shop = add_app(store, "shop.example.com")
        DatabaseRecords(store).save_link(
            DatabaseLink(app_id=shop.id or 0, engine="postgresql", db_name="shop_db")
        )
        migration = checker(
            store,
            recorder,
            engines={
                "postgresql/shop_db": (
                    False,
                    "postgresql is not installed here: noust db install postgresql",
                )
            },
        )

        [item] = migration.check().applications

        assert item.status == "down"
        assert any("noust db install postgresql" in problem for problem in item.problems)

    def test_the_counts_and_the_json_say_how_it_went(
        self, store: NoustStore, recorder: Recorder
    ) -> None:
        import json

        add_app(store, "shop.example.com")
        add_app(store, "blog.example.com")
        migration = checker(
            store, recorder, states={"blog.example.com": AppState("Failed", False, "It crashed")}
        )

        report = migration.check()

        assert report.counts() == {"ok": 0, "attention": 1, "down": 1}
        assert json.loads(json.dumps(report.to_dict()))["host"] == HOST

    def test_a_resolver_that_fails_is_a_problem_of_that_name_only(
        self, store: NoustStore, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")

        def broken(domain: str) -> DnsCheck:
            raise NoustError("psutil is needed to list this server's addresses")

        migration = checker(store, recorder)
        migration._dns = broken

        [item] = migration.check().applications

        assert any("could not check DNS" in problem for problem in item.problems)
        assert item.names[0]["resolves_here"] is None
