# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust restore-server``: from a bundle to answering applications (3.4, checklist H-28).

The state is restored for real (over real openssl, from a real bundle of a
server with an application, its schedule, its data policy and its databases);
what is faked is what a machine is: the application backups and where they are,
the data backups, the web server, the health gate. What is pinned here is the
flow an operator trusts on the worst day:

- it says what the bundle would bring back before it brings it;
- the state comes back first, then each application from its newest backup
  (here or on a destination), then the data backups that are newer than it,
  then its site, then the health gate - and what the gate saw is kept verbatim;
- one application failing does not stop the next, nothing is claimed that did
  not happen, and the report says which are back, which are not and why;
- an application nothing backed up is reported as such, a missing database
  engine is named with the command that installs it, and a run that stopped can
  be taken up again with the state kept.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.central import RoleError
from noust.core.ens import envelope
from noust.core.ens.envelope import EnvelopeError
from noust.core.exceptions import BackupError
from noust.core.runner import set_runner
from noust.core.store import App, BackupScheduleRecord, Database, NoustStore, get_store
from noust.managers import restore_server
from noust.managers.backup_manager import BackupMetadata
from noust.managers.restore_server import RestoreServer
from tests.test_self_backup import PASSPHRASE, Runner, Server, origin, server

pytestmark = pytest.mark.allow_subprocess

NOW = datetime.now(timezone.utc)


def ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat(timespec="seconds")


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[Runner]:
    fake = Runner(tmp_path / "remote")
    fake.script(("systemctl", "is-active", "--quiet", "noust-web.service"), exit_code=3)
    fake.script(("systemctl", "is-active", "--quiet", "noust-monitor.service"), exit_code=3)
    fake.script(("systemctl", "is-active", "--quiet", "noust-previews.service"), exit_code=3)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


def meta(backup_id: str, domain: str, created: str) -> BackupMetadata:
    return BackupMetadata(
        id=backup_id,
        domain=domain,
        app_name=domain.replace(".", "-"),
        created_at=created,
        size_bytes=10,
        app_type="nodejs",
        version="2.0.0",
        description="",
        includes_env=True,
        includes_node_modules=False,
    )


@dataclass
class FakeBackups:
    """The application backups on this server, and what was restored from them."""

    local: dict[str, list[BackupMetadata]] = field(default_factory=dict)
    restored: list[tuple[str, str]] = field(default_factory=list)
    fail: dict[str, str] = field(default_factory=dict)

    def list_backups(self, domain: str | None = None, **_: Any) -> list[BackupMetadata]:
        return list(self.local.get(domain or "", []))

    def restore(self, backup_id: str, target_domain: str | None = None, **kwargs: Any) -> bool:
        if target_domain in self.fail:
            raise BackupError(f"Cannot restore {backup_id}", details=self.fail[target_domain])
        self.restored.append(("local", backup_id))
        self.options = kwargs
        return True


@dataclass
class FakeDestinations:
    """What destinations hold of each application, and what was restored from them."""

    held: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    unreachable: set[str] = field(default_factory=set)
    restored: list[tuple[str, str]] = field(default_factory=list)

    def remote_list(self, destination: str, app_name: str | None = None) -> dict[str, Any]:
        if destination in self.unreachable:
            raise BackupError(f"{destination} did not answer", details="connection refused")
        return {"apps": [], "backups": list(self.held.get((destination, app_name or ""), []))}

    def restore_remote(self, destination: str, backup_id: str, app_name: str, **kwargs: Any) -> str:
        self.restored.append((destination, backup_id))
        return str(kwargs.get("target_domain"))


@dataclass
class FakeVolumes:
    """The data policy and the data backups of each application."""

    policies: dict[str, Any] = field(default_factory=dict)
    local: dict[tuple[str, str], list[Any]] = field(default_factory=dict)
    remote: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    restored: list[str] = field(default_factory=list)
    fail: set[str] = field(default_factory=set)

    def get_policy(self, domain: str) -> Any:
        class View:
            policy = self.policies.get(domain)

        return View()

    def targets(self, domain: str) -> list[Any]:
        class Target:
            key = "path:uploads"

        return [Target()]

    def list_backups(self, domain: str, target: str | None = None) -> list[Any]:
        return list(self.local.get((domain, target or ""), []))

    def remote_backups(self, destination: str, domain: str) -> dict[str, list[dict[str, Any]]]:
        return self.remote.get((destination, domain), {})

    def restore(self, backup_id: str, **_: Any) -> dict[str, Any]:
        if backup_id in self.fail:
            raise BackupError("The data could not be put back", details="no space")
        self.restored.append(backup_id)
        return {"id": backup_id}

    def restore_remote(
        self, destination: str, domain: str, slug: str, file_name: str, **_: Any
    ) -> dict[str, Any]:
        if file_name in self.fail:
            raise BackupError("The data could not be put back", details="no space")
        self.restored.append(f"{destination}:{file_name}")
        return {"id": file_name}


@dataclass
class Policy:
    targets: list[str] = field(default_factory=list)
    destinations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Data:
    id: str
    created_at: str
    slug: str = "path-uploads"


def add_state(made: Server, *, apps: tuple[str, ...] = ()) -> None:
    """Give the origin server what a production server has: schedules and databases."""
    NoustStore.reset_instance()
    store = NoustStore(made.state / "noust.db")
    for domain in apps:
        store.create_app(App(domain=domain, app_type="nodejs", app_path=f"/srv/{domain}"))
    shop = store.get_app("shop.example.com")
    assert shop is not None
    store.save_backup_schedule(
        BackupScheduleRecord(
            app_domain="shop.example.com",
            schedule="daily",
            destinations=[{"name": "nas"}, {"name": "cloud"}],
        )
    )
    store.create_database(
        Database(name="shop", engine="postgresql", app_id=shop.id, username="shop")
    )
    store.close()
    NoustStore.reset_instance()


class Flow:
    """A target server, the flow over it, and every fake it runs with."""

    def __init__(self, base: Path, runner: Runner, old: Server) -> None:
        self.new = server(base, "new")
        self.backups = FakeBackups()
        self.destinations = FakeDestinations()
        self.volumes = FakeVolumes()
        self.sites: list[str] = []
        self.gates: dict[str, tuple[bool, str]] = {}
        self.installed: set[str] = {"postgresql"}
        self.manager = self.new.manager(runner)
        self.bundle = old.manager(runner).create(PASSPHRASE).path
        self.flow = RestoreServer(
            manager=self.manager,
            backups=self.backups,  # type: ignore[arg-type]
            destinations=self.destinations,  # type: ignore[arg-type]
            volumes=self.volumes,  # type: ignore[arg-type]
            gate=lambda app: self.gates.get(app.domain, (True, "")),
            refresh_site=lambda app: self.sites.append(app.domain),
            engine_installed=lambda engine: engine in self.installed,
        )

    def run(self, **kwargs: Any) -> restore_server.ServerRestoreReport:
        return self.flow.run(str(self.bundle), PASSPHRASE, **kwargs)


@pytest.fixture
def old(tmp_path: Path, runner: Runner) -> Iterator[Server]:
    made = origin(tmp_path)
    yield made
    for connection in made.held:
        connection.close()


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


def outcome(report: restore_server.ServerRestoreReport, domain: str) -> restore_server.AppOutcome:
    return next(o for o in report.apps if o.domain == domain)


# ============================================================== the plan


class TestPlan:
    def test_it_says_what_the_bundle_would_bring_back_and_changes_nothing(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old, apps=("blog.example.com",))
        flow = Flow(tmp_path, runner, old)
        flow.volumes.policies["shop.example.com"] = Policy()

        answer = flow.flow.plan(str(flow.bundle), PASSPHRASE)

        apps = {a["domain"]: a for a in answer["apps"]}
        assert set(apps) == {"shop.example.com", "blog.example.com"}
        assert apps["shop.example.com"]["destinations"] == ["nas", "cloud"]
        assert apps["blog.example.com"]["destinations"] == []
        assert answer["engines"] == ["postgresql"]
        assert answer["host"] and answer["noust_version"]
        assert list(flow.new.config.iterdir()) == []
        assert not [c for c in runner.calls if c[:2] == ("systemctl", "stop")]

    def test_a_wrong_passphrase_plans_nothing(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        flow = Flow(tmp_path, runner, old)

        with pytest.raises(EnvelopeError):
            flow.flow.plan(str(flow.bundle), "not the passphrase")


# ================================================================== the run


class TestRun:
    def test_the_state_comes_back_then_each_application_from_its_newest_backup(
        self, old: Server, runner: Runner, tmp_path: Path, audited: list[dict[str, Any]]
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)
        flow.backups.local["shop.example.com"] = [
            meta("shop-example-com_20260102_030000", "shop.example.com", ago(5)),
            meta("shop-example-com_20260101_030000", "shop.example.com", ago(29)),
        ]
        flow.backups.local["shop.example.com"].sort(key=lambda b: b.created_at, reverse=True)

        report = flow.run()

        assert report.state is not None and report.state.files > 0
        assert (flow.new.config / "config.yaml").is_file()
        shop = outcome(report, "shop.example.com")
        assert (shop.status, shop.source) == ("restored", "local")
        assert shop.backup_id == "shop-example-com_20260102_030000"
        assert shop.gate == "passed" and shop.healthy is True
        assert shop.site == "refreshed" and flow.sites == ["shop.example.com"]
        assert report.ok is True and report.counts() == {"restored": 1}
        event = next(e for e in audited if e["event"] == "restore_server.run")
        assert event["outcome"] == "ok" and event["details"]["counts"] == {"restored": 1}

    def test_a_backup_on_a_destination_that_is_newer_than_the_local_one_is_the_one_used(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)
        flow.backups.local["shop.example.com"] = [
            meta("shop-example-com_20260101_030000", "shop.example.com", ago(48))
        ]
        flow.destinations.held[("nas", "shop-example-com")] = [
            {"backup_id": "shop-example-com_20260103_030000", "modified": ago(2), "size": 5}
        ]

        report = flow.run()

        shop = outcome(report, "shop.example.com")
        assert shop.source == "nas" and shop.backup_id == "shop-example-com_20260103_030000"
        assert flow.destinations.restored == [("nas", "shop-example-com_20260103_030000")]
        assert flow.backups.restored == []

    def test_a_local_backup_newer_than_a_destinations_is_kept(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)
        flow.backups.local["shop.example.com"] = [
            meta("shop-example-com_20260103_030000", "shop.example.com", ago(1))
        ]
        flow.destinations.held[("nas", "shop-example-com")] = [
            {"backup_id": "shop-example-com_20260101_030000", "modified": ago(40), "size": 5}
        ]

        report = flow.run()

        assert outcome(report, "shop.example.com").source == "local"

    def test_an_unreachable_destination_is_skipped_for_the_next(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)
        flow.destinations.unreachable = {"nas"}
        flow.destinations.held[("cloud", "shop-example-com")] = [
            {"backup_id": "shop-example-com_20260103_030000", "modified": ago(3), "size": 5}
        ]

        report = flow.run()

        shop = outcome(report, "shop.example.com")
        assert shop.status == "restored" and shop.source == "cloud"

    def test_an_application_nothing_backed_up_is_reported_as_such_and_the_report_is_not_ok(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old, apps=("blog.example.com",))
        flow = Flow(tmp_path, runner, old)
        flow.backups.local["blog.example.com"] = [
            meta("blog-example-com_20260101_030000", "blog.example.com", ago(5))
        ]

        report = flow.run()

        shop = outcome(report, "shop.example.com")
        assert shop.status == "no_backup" and "deploy it again" in shop.notes[0]
        assert outcome(report, "blog.example.com").status == "restored"
        assert report.ok is False and any("no backup anywhere" in n for n in report.notes)
        assert flow.sites == ["blog.example.com"]

    def test_one_application_failing_does_not_stop_the_next(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old, apps=("blog.example.com",))
        flow = Flow(tmp_path, runner, old)
        for domain in ("shop.example.com", "blog.example.com"):
            flow.backups.local[domain] = [
                meta(f"{domain.replace('.', '-')}_20260101_030000", domain, ago(5))
            ]
        flow.backups.fail["blog.example.com"] = "the archive is damaged"

        report = flow.run()

        blog = outcome(report, "blog.example.com")
        assert blog.status == "failed" and "Cannot restore" in (blog.error or "")
        assert "the archive is damaged" in (blog.evidence or "")
        assert outcome(report, "shop.example.com").status == "restored"
        assert report.ok is False

    def test_a_health_gate_that_fails_keeps_what_it_saw_verbatim(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)
        flow.backups.local["shop.example.com"] = [
            meta("shop-example-com_20260101_030000", "shop.example.com", ago(5))
        ]
        flow.gates["shop.example.com"] = (False, "probe: 502\n\nJournal of shop:\nboom")

        report = flow.run()

        shop = outcome(report, "shop.example.com")
        assert shop.status == "restored" and shop.gate == "failed" and shop.healthy is False
        assert shop.evidence == "probe: 502\n\nJournal of shop:\nboom"
        assert report.ok is False

    def test_a_site_that_cannot_be_rendered_is_a_note_not_a_failure(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)
        flow.backups.local["shop.example.com"] = [
            meta("shop-example-com_20260101_030000", "shop.example.com", ago(5))
        ]

        def refuse(app: App) -> None:
            raise BackupError("nginx is not installed")

        flow.flow._refresh_site = refuse

        report = flow.run()

        shop = outcome(report, "shop.example.com")
        assert shop.status == "restored" and shop.healthy is True
        assert shop.site == "nginx is not installed"
        assert "noust update shop.example.com" in shop.notes[0]

    def test_only_and_skip_choose_the_applications(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old, apps=("blog.example.com", "wiki.example.com"))
        flow = Flow(tmp_path, runner, old)
        for domain in ("shop.example.com", "blog.example.com", "wiki.example.com"):
            flow.backups.local[domain] = [
                meta(f"{domain.replace('.', '-')}_20260101_030000", domain, ago(5))
            ]

        report = flow.run(only=("shop.example.com", "blog.example.com"), skip=("blog.example.com",))

        statuses = {o.domain: o.status for o in report.apps}
        assert statuses == {
            "shop.example.com": "restored",
            "blog.example.com": "skipped",
            "wiki.example.com": "skipped",
        }
        assert [r[1] for r in flow.backups.restored] == ["shop-example-com_20260101_030000"]
        assert report.ok is True

    def test_a_missing_engine_is_named_with_the_command_that_installs_it(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)
        flow.installed = set()

        report = flow.run()

        assert report.engines_missing == [
            {"engine": "postgresql", "command": "noust db install postgresql"}
        ]

    def test_the_schema_confirmation_is_the_operators_to_give(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)
        flow.backups.local["shop.example.com"] = [
            meta("shop-example-com_20260101_030000", "shop.example.com", ago(5))
        ]

        flow.run()
        assert flow.backups.options["schema_changed_ok"] is False
        flow.run(skip_state=True, accept_schema_changes=True)
        assert flow.backups.options["schema_changed_ok"] is True


class TestDataBackups:
    @pytest.fixture
    def flow(self, old: Server, runner: Runner, tmp_path: Path) -> Flow:
        add_state(old)
        made = Flow(tmp_path, runner, old)
        made.backups.local["shop.example.com"] = [
            meta("shop-example-com_20260101_030000", "shop.example.com", ago(24))
        ]
        made.volumes.policies["shop.example.com"] = Policy(destinations=[{"name": "nas"}])
        return made

    def test_a_data_backup_newer_than_the_application_backup_is_put_back_on_top(
        self, flow: Flow
    ) -> None:
        flow.volumes.local[("shop.example.com", "path:uploads")] = [
            Data("shop-example-com.path-uploads_20260101_150000", ago(2))
        ]

        report = flow.run()

        shop = outcome(report, "shop.example.com")
        assert shop.volumes == ["shop-example-com.path-uploads_20260101_150000"]
        assert flow.volumes.restored == ["shop-example-com.path-uploads_20260101_150000"]

    def test_one_older_than_the_application_backup_is_not(self, flow: Flow) -> None:
        flow.volumes.local[("shop.example.com", "path:uploads")] = [
            Data("shop-example-com.path-uploads_20251231_030000", ago(60))
        ]

        report = flow.run()

        assert outcome(report, "shop.example.com").volumes == []
        assert flow.volumes.restored == []

    def test_one_on_a_destination_is_fetched_when_it_is_the_newest(self, flow: Flow) -> None:
        flow.volumes.remote[("nas", "shop.example.com")] = {
            "path-uploads": [
                {
                    "name": "shop-example-com.path-uploads_20260101_200000.tar.gz",
                    "modified": ago(1),
                    "target": "path:uploads",
                }
            ]
        }

        report = flow.run()

        assert flow.volumes.restored == ["nas:shop-example-com.path-uploads_20260101_200000.tar.gz"]
        assert outcome(report, "shop.example.com").status == "restored"

    def test_data_that_cannot_be_put_back_is_a_note_and_the_application_is_still_back(
        self, flow: Flow
    ) -> None:
        flow.volumes.local[("shop.example.com", "path:uploads")] = [
            Data("shop-example-com.path-uploads_20260101_150000", ago(2))
        ]
        flow.volumes.fail = {"shop-example-com.path-uploads_20260101_150000"}

        report = flow.run()

        shop = outcome(report, "shop.example.com")
        assert shop.status == "restored" and shop.volumes == []
        assert any(
            "could not be restored" in note and "no space" not in note for note in shop.notes
        )


class TestResuming:
    def test_with_the_state_kept_only_the_applications_are_brought_up(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)
        flow.backups.local["shop.example.com"] = [
            meta("shop-example-com_20260101_030000", "shop.example.com", ago(5))
        ]
        first = flow.run()
        flow.backups.restored.clear()
        runner.calls.clear()

        again = flow.run(skip_state=True)

        assert first.state is not None and again.state is None
        assert outcome(again, "shop.example.com").status == "restored"
        assert not [c for c in runner.calls if c[:2] == ("systemctl", "stop")]

    def test_a_wrong_passphrase_attempts_no_application(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)

        with pytest.raises(EnvelopeError):
            flow.flow.run(str(flow.bundle), "not the passphrase")

        assert flow.backups.restored == [] and flow.sites == []

    def test_a_server_that_holds_state_is_not_overwritten_unasked(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        add_state(old)
        flow = Flow(tmp_path, runner, old)
        flow.run()  # the new server now holds the state

        with pytest.raises(BackupError, match="already holds Noust state"):
            flow.run()

        assert get_store().get_app("shop.example.com") is not None

    def test_a_hub_does_not_restore_servers(
        self, old: Server, runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        flow = Flow(tmp_path, runner, old)

        def hub(feature: str, config: Any = None) -> None:
            raise RoleError(f"{feature}: not available on this central, which is a hub")

        monkeypatch.setattr(restore_server, "require_server_role", hub)

        with pytest.raises(RoleError):
            flow.run()

        assert list(flow.new.config.iterdir()) == []
