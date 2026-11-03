# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The software manager: what is installed, who depends on it, installing and removing it (item 118).

One test per guard, because the manager is where the guards are (rule 4): a component that is never
removed, one something depends on, one whose removal would take another along, an origin Noust did
not make, a hub, a system whose packages are not Noust's. And what a removal is not: never a purge,
never a repository file that is not Noust's, never anything under ``--dry-run``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.config import Config
from noust.core.exceptions import EngineInUseError, ValidationError
from noust.core.fs import DryRunFileSystem, RecordingFileSystem, set_fs
from noust.core.runner import DryRunRunner, FakeRunner
from noust.core.software_ledger import SoftwareLedger
from noust.core.store import App, get_store
from noust.managers.server.component_dependents import Dependent
from noust.managers.server.components import COMPONENTS, Component, Installer, Severity
from noust.managers.server.errors import (
    ConfirmationRequiredError,
    ServerError,
    SoftwareRefusedError,
    UnsupportedHostError,
)
from noust.managers.server.facts import FactCache
from noust.managers.server.software import InstallOptions, SoftwareManager
from tests.support.server_support import SequencedRunner, fixture, make_host, platform_for

SOURCES = "etc/apt/sources.list.d"
KEYRINGS = "usr/share/keyrings"


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A configuration read from an empty file, whose role is a server's."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make this Noust a hub, the way the container image does."""
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    Config.reset_instance()


@pytest.fixture
def runner(runner: FakeRunner) -> FakeRunner:
    """A machine with no program on its path, so what is installed is what a test says."""
    runner.only_knows()
    return runner


@pytest.fixture
def host(tmp_path: Path):
    root = make_host(tmp_path / "root")
    for directory in (SOURCES, KEYRINGS, "etc/yum.repos.d"):
        (root.root / directory).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kwargs: recorded.append((event, kwargs)))
    return recorded


def machine(runner: FakeRunner, host, family: str = "apt", **kwargs: Any) -> SoftwareManager:
    return SoftwareManager(
        runner=runner,
        fs=RecordingFileSystem(),
        host=host,
        platform=platform_for(family),
        **kwargs,
    )


def installed(runner: FakeRunner, **versions: str) -> None:
    """The packages dpkg says are installed, with their versions."""
    runner.script(
        ["dpkg-query", "-W"],
        stdout="".join(
            f"{name.replace('_', '-')} ii  {version}\n" for name, version in versions.items()
        ),
    )


def webapp(domain: str) -> None:
    get_store().create_app(
        App(domain=domain, app_type="static", app_path=f"/var/www/apps/{domain}", webserver="nginx")
    )


def removals(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[:2] == ("apt-get", "remove")]


class TestTheList:
    def test_a_server_with_nothing_lists_every_component_not_installed(
        self, runner: FakeRunner, host
    ) -> None:
        inventory = machine(runner, host).inventory()

        assert inventory.managed and inventory.family == "apt"
        assert not any(item.installed for item in inventory.items)
        by_id = {item.id: item for item in inventory.items}
        assert by_id["nginx"].installable
        assert by_id["nginx"].version is None and by_id["nginx"].origin is None
        assert by_id["nginx"].purpose.code == "software.nginx.purpose"

    def test_an_installed_package_says_its_version(self, runner: FakeRunner, host) -> None:
        installed(runner, nginx="1.24.0-2ubuntu7.1")

        item = {i.id: i for i in machine(runner, host).inventory().items}["nginx"]

        assert (item.installed, item.version, item.packages) == (
            True,
            "1.24.0-2ubuntu7.1",
            ("nginx",),
        )

    def test_a_program_on_the_path_without_a_package_is_installed_too(
        self, runner: FakeRunner, host
    ) -> None:
        runner.only_knows("git")

        item = {i.id: i for i in machine(runner, host).inventory().items}["git"]

        assert item.installed and item.version is None

    def test_who_put_it_there_is_known_by_the_ledger_or_by_the_marks_and_never_assumed(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner, nginx="1", certbot="2", fail2ban="3")
        ledger = SoftwareLedger()
        ledger.record("nginx", packages=["nginx"], via="package", actor="cli:root")
        # certbot: no row, no mark. fail2ban: no row, but a file Noust writes is there.
        (host.root / SOURCES / "noust-cloudflared.list").write_text("deb x\n")
        runner.only_knows("cloudflared")

        by_id = {i.id: i for i in machine(runner, host).inventory().items}

        assert by_id["nginx"].origin == "noust"
        assert (
            by_id["nginx"].installed_by == "cli:root" and by_id["nginx"].installed_via == "package"
        )
        assert by_id["certbot"].origin == "unknown"
        assert by_id["fail2ban"].origin == "unknown"
        assert by_id["cloudflared"].origin == "noust"

    def test_what_depends_on_a_piece_is_listed_with_it_and_it_is_not_removable_while_so(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner, nginx="1", fail2ban="3")
        webapp("shop.example.com")

        by_id = {i.id: i for i in machine(runner, host).inventory().items}

        assert by_id["nginx"].dependents == (Dependent("application", ("shop.example.com",)),)
        assert by_id["nginx"].removable is False
        assert by_id["fail2ban"].removable is True

    def test_a_never_is_listed_and_is_not_removable(self, runner: FakeRunner, host) -> None:
        runner.only_knows("git")

        item = {i.id: i for i in machine(runner, host).inventory().items}["git"]

        assert item.severity == "never" and item.removable is False

    def test_a_dependent_that_cannot_be_asked_is_not_none(self, runner: FakeRunner, host) -> None:
        def broken() -> list[Dependent]:
            raise sqlite3.OperationalError("database is locked")

        item = Component(
            "widget",
            "software.nginx.purpose",
            Severity.CRITICAL_WHILE,
            packages={},
            binaries=("widget",),
            dependents=broken,
        )
        runner.only_knows("widget")

        found = machine(runner, host, components=[item]).inventory().items[0]

        assert found.installed and found.removable is False

    def test_the_answer_is_kept_for_a_while_and_an_install_or_a_removal_forgets_it(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner, fail2ban="3")
        software = machine(runner, host, cache=FactCache(background=False))
        software.inventory()
        software.inventory()
        assert len([c for c in runner.calls if c[0] == "dpkg-query"]) == 1

        software.inventory(refresh=True)
        assert len([c for c in runner.calls if c[0] == "dpkg-query"]) == 2

        runner.script(["apt-get", "-s"], stdout="")
        software.remove("fail2ban", confirm_unknown_origin=True)
        software.inventory()
        assert len([c for c in runner.calls if c[0] == "dpkg-query"]) > 2

    def test_a_hub_has_no_software_of_its_own(self, runner: FakeRunner, host, hub: None) -> None:
        inventory = machine(runner, host).inventory()

        assert inventory.items == () and inventory.managed is False
        assert "hub" in (inventory.reason or "")

    def test_an_image_based_system_lists_what_it_has_and_says_nothing_can_change(
        self, runner: FakeRunner, host
    ) -> None:
        runner.only_knows("git")
        software = SoftwareManager(
            runner=runner,
            host=host,
            platform=platform_for("dnf", transactional=True),
        )

        inventory = software.inventory()

        assert inventory.managed is False and inventory.reason
        assert {i.id: i for i in inventory.items}["git"].installed


class TestWhatARemovalWouldDo:
    def test_it_names_the_packages_what_else_goes_and_the_files_noust_would_take_back(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner, cloudflared="2025.1")
        runner.script(
            ["apt-get", "-s"],
            stdout="Remv cloudflared [2025.1]\nRemv some-library [1]\n",
        )
        source = host.root / SOURCES / "noust-cloudflared.list"
        key = host.root / KEYRINGS / "noust-cloudflared.gpg"
        source.write_text("deb x\n")
        key.write_bytes(b"k")

        impact = machine(runner, host).impact("cloudflared")

        assert impact.removable
        assert impact.removes == ("cloudflared",)
        assert impact.also_removes == ("some-library",)
        assert impact.repository_files == (str(source), str(key))
        assert impact.origin == "noust" and impact.needs_confirmation is False
        assert not removals(runner)

    def test_a_piece_nobody_depends_on_that_noust_did_not_install_needs_a_yes(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")

        impact = machine(runner, host).impact("fail2ban")

        assert impact.removable and impact.needs_confirmation and impact.origin == "unknown"

    def test_the_asking_changes_nothing(self, runner: FakeRunner, host) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")

        machine(runner, host).impact("fail2ban")

        assert [c[:3] for c in runner.calls if c[0] == "apt-get"] == [("apt-get", "-s", "-o")]

    def test_a_component_that_is_not_there_has_one_reason(self, runner: FakeRunner, host) -> None:
        impact = machine(runner, host).impact("nginx")

        assert [b.reason for b in impact.blockers] == ["not_installed"]
        assert impact.installed is False


class TestEveryGuardRefusesWithItsReason:
    def refused(
        self, software: SoftwareManager, component: str, **kwargs: Any
    ) -> SoftwareRefusedError:
        with pytest.raises(SoftwareRefusedError) as raised:
            software.remove(component, **kwargs)
        return raised.value

    def test_what_is_never_removed_is_not_whatever_the_operator_confirms(
        self, runner: FakeRunner, host, events: list
    ) -> None:
        installed(runner, git="2.43")

        error = self.refused(machine(runner, host), "git", confirm_unknown_origin=True)

        assert error.reason == "never"
        assert "Every deployment from a repository needs it" in error.message
        assert error.coded is not None and error.coded.code == "software.git.never"
        assert not removals(runner)
        assert [(e, k["outcome"]) for e, k in events] == [("server.software_remove", "denied")]

    @pytest.mark.parametrize(
        "component", ["noust", "systemd", "sshd", "firewall", "git", "curl", "gnupg"]
    )
    def test_none_of_the_pieces_the_machine_stands_on_is_removed(
        self, component: str, runner: FakeRunner, host
    ) -> None:
        runner.only_knows(*{program for item in COMPONENTS for program in item.binaries})

        error = self.refused(machine(runner, host), component, confirm_unknown_origin=True)

        assert error.reason == "never"
        assert not removals(runner)

    def test_while_something_depends_on_it_it_stays_and_the_error_names_what(
        self, runner: FakeRunner, host, events: list
    ) -> None:
        installed(runner, nginx="1.24")
        webapp("shop.example.com")
        webapp("blog.example.com")

        error = self.refused(machine(runner, host), "nginx", confirm_unknown_origin=True)

        assert error.reason == "dependents"
        assert error.dependents == [
            {"kind": "application", "names": ["blog.example.com", "shop.example.com"]}
        ]
        assert "blog.example.com, shop.example.com" in error.message
        assert not removals(runner)
        assert (
            events[0][1]["outcome"] == "denied"
            and events[0][1]["details"]["reason"] == "dependents"
        )

    def test_a_question_that_cannot_be_answered_is_a_refusal_not_a_yes(
        self, runner: FakeRunner, host
    ) -> None:
        def broken() -> list[Dependent]:
            raise sqlite3.OperationalError("database is locked")

        widget = Component(
            "widget",
            "software.nginx.purpose",
            Severity.CRITICAL_WHILE,
            packages={},
            binaries=("widget",),
            dependents=broken,
        )
        runner.only_knows("widget")

        error = self.refused(machine(runner, host, components=[widget]), "widget")

        assert error.reason == "cannot_tell"
        assert "database is locked" in (error.details or "")

    def test_a_removal_that_would_take_a_never_along_is_refused(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner, certbot="2.9", noust="4.2.0")
        runner.script(["apt-get", "-s"], stdout="Remv certbot [2.9]\nRemv noust [4.2.0]\n")

        error = self.refused(machine(runner, host), "certbot", confirm_unknown_origin=True)

        assert error.reason == "takes_never"
        assert "noust" in error.message
        assert not removals(runner)

    def test_a_removal_that_would_take_a_piece_in_use_along_is_refused(
        self, runner: FakeRunner, host
    ) -> None:
        from noust.core.stream_store import StreamExposure, StreamStore

        installed(runner, certbot="2.9")
        runner.script(
            ["apt-get", "-s"], stdout="Remv certbot [2.9]\nRemv libnginx-mod-stream [1.24]\n"
        )
        StreamStore().create(StreamExposure("pg", "tcp", 5433, "10.0.0.5", 5432))

        error = self.refused(machine(runner, host), "certbot", confirm_unknown_origin=True)

        assert error.reason == "takes_in_use"
        assert error.dependents == [{"kind": "published_port", "names": ["pg"]}]
        assert not removals(runner)

    def test_a_simulation_that_fails_is_a_refusal_with_its_words(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stderr="E: Could not get lock", exit_code=100)

        error = self.refused(machine(runner, host), "fail2ban", confirm_unknown_origin=True)

        assert error.reason == "cannot_tell"
        assert not removals(runner)

    def test_what_is_not_installed_is_not_removed(self, runner: FakeRunner, host) -> None:
        assert self.refused(machine(runner, host), "nginx").reason == "not_installed"

    def test_an_id_the_registry_does_not_have_is_not_found(self, runner: FakeRunner, host) -> None:
        from noust.core.teams.model import ResourceNotFoundError

        with pytest.raises(ResourceNotFoundError, match="no software called"):
            machine(runner, host).remove("nginx; rm -rf /")

    def test_a_hub_has_nothing_to_remove(self, runner: FakeRunner, host, hub: None) -> None:
        from noust.central import RoleError

        with pytest.raises(RoleError, match="Software"):
            machine(runner, host).remove("fail2ban")

    def test_a_system_whose_packages_are_not_noust_s_has_nothing_to_remove(
        self, runner: FakeRunner, host
    ) -> None:
        software = SoftwareManager(
            runner=runner, host=host, platform=platform_for("dnf", transactional=True)
        )

        with pytest.raises(UnsupportedHostError):
            software.remove("fail2ban")
        with pytest.raises(UnsupportedHostError):
            software.install("fail2ban")


class TestOriginUnknown:
    def test_it_is_removed_only_with_the_operators_yes(
        self, runner: FakeRunner, host, events: list
    ) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")
        software = machine(runner, host)

        with pytest.raises(ConfirmationRequiredError) as asked:
            software.remove("fail2ban")

        assert asked.value.required == {"unknown_origin": True, "removes": ["fail2ban"]}
        assert asked.value.coded is not None
        assert asked.value.coded.code == "software.refused.unknown_origin"
        assert not removals(runner)
        assert events[-1][1]["outcome"] == "denied"
        assert events[-1][1]["details"]["reason"] == "unknown_origin"

        software.remove("fail2ban", confirm_unknown_origin=True)

        assert removals(runner) == [("apt-get", "remove", "-y", "fail2ban")]

    def test_what_noust_installed_needs_no_confirmation(self, runner: FakeRunner, host) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")
        SoftwareLedger().record("fail2ban", packages=["fail2ban"], via="fail2ban")

        machine(runner, host).remove("fail2ban")

        assert removals(runner)


class TestRemoving:
    def test_it_removes_the_installed_packages_and_never_purges(
        self, runner: FakeRunner, host, events: list
    ) -> None:
        installed(runner, nginx="1.24", nginx_core="1.24", fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv nginx [1.24]\nRemv nginx-core [1.24]\n")
        SoftwareLedger().record("nginx", packages=["nginx"], via="package", actor="cli:root")

        result = machine(runner, host).remove("nginx")

        assert removals(runner) == [("apt-get", "remove", "-y", "nginx", "nginx-core")]
        assert not [c for c in runner.calls if "purge" in c or "autoremove" in c]
        assert result.removed == ("nginx", "nginx-core")
        assert [(e, k["outcome"], k["target"]) for e, k in events] == [
            ("server.software_remove", "ok", "software:nginx")
        ]
        assert events[0][1]["details"]["packages"] == ["nginx", "nginx-core"]

    def test_the_removal_runs_in_the_package_managers_environment(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")
        SoftwareLedger().record("fail2ban", packages=["fail2ban"], via="fail2ban")

        machine(runner, host).remove("fail2ban")

        index = runner.calls.index(removals(runner)[0])
        env = runner.envs[index]
        assert env is not None and env["DEBIAN_FRONTEND"] == "noninteractive"

    def test_the_repository_and_the_key_noust_added_go_with_it_and_the_ledger_forgets(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner, cloudflared="2025.1")
        runner.script(["apt-get", "-s"], stdout="Remv cloudflared [2025.1]\n")
        source = host.root / SOURCES / "noust-cloudflared.list"
        key = host.root / KEYRINGS / "noust-cloudflared.gpg"
        operators = host.root / SOURCES / "operators.list"
        for path in (source, key, operators):
            path.write_text("x")
        SoftwareLedger().record(
            "cloudflared",
            packages=["cloudflared"],
            repository_files=[
                "/etc/apt/sources.list.d/noust-cloudflared.list",
                "/usr/share/keyrings/noust-cloudflared.gpg",
            ],
            via="cloudflared",
        )

        result = machine(runner, host).remove("cloudflared")

        assert not source.exists() and not key.exists()
        assert operators.exists()
        assert result.withdrawn == (
            "/etc/apt/sources.list.d/noust-cloudflared.list",
            "/usr/share/keyrings/noust-cloudflared.gpg",
        )
        assert SoftwareLedger().get("cloudflared") is None

    @pytest.mark.parametrize(
        "named",
        [
            "/etc/shadow",
            "/etc/apt/sources.list.d/operators.list",
            "/etc/apt/sources.list.d/noust-x/../../../shadow",
            "/usr/share/keyrings/other.gpg",
        ],
    )
    def test_a_row_cannot_make_it_delete_what_is_not_a_noust_repository_file(
        self, named: str, runner: FakeRunner, host
    ) -> None:
        installed(runner, cloudflared="2025.1")
        runner.script(["apt-get", "-s"], stdout="Remv cloudflared [2025.1]\n")
        target = host.root / named.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("precious")
        # A row can only carry a path the ledger accepts; one it accepts is still not deleted
        # unless it is a Noust repository file, which is the manager's own guard.
        row = SoftwareLedger()
        row.record("cloudflared", packages=["cloudflared"], via="package")
        if named == "/etc/apt/sources.list.d/noust-x/../../../shadow":
            with pytest.raises(ValidationError):
                row.record("cloudflared", repository_files=[named], via="package")
        else:
            row.record("cloudflared", repository_files=[named], via="package")

        machine(runner, host).remove("cloudflared")

        assert target.exists() and target.read_text() == "precious"

    def test_a_failed_removal_changes_nothing_else_and_says_what_the_tool_said(
        self, runner: FakeRunner, host, events: list
    ) -> None:
        installed(runner, cloudflared="2025.1")
        runner.script(["apt-get", "-s"], stdout="Remv cloudflared [2025.1]\n")
        runner.script(["apt-get", "remove"], stderr="E: dpkg was interrupted", exit_code=100)
        source = host.root / SOURCES / "noust-cloudflared.list"
        source.write_text("deb x\n")
        SoftwareLedger().record(
            "cloudflared",
            packages=["cloudflared"],
            repository_files=["/etc/apt/sources.list.d/noust-cloudflared.list"],
            via="cloudflared",
        )

        with pytest.raises(ServerError) as raised:
            machine(runner, host).remove("cloudflared")

        assert "dpkg was interrupted" in (raised.value.output or "")
        assert source.exists()
        assert SoftwareLedger().get("cloudflared") is not None
        assert [(e, k["outcome"]) for e, k in events] == [("server.software_remove", "failure")]

    def test_it_streams_what_the_package_manager_prints(self, runner: FakeRunner, host) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")
        runner.script(["apt-get", "remove"], stdout="Removing fail2ban (1.0) ...\n")
        lines: list[str] = []

        machine(runner, host).remove(
            "fail2ban", confirm_unknown_origin=True, on_output=lines.append
        )

        assert lines == ["Removing fail2ban (1.0) ..."]

    def test_a_database_engine_is_removed_by_its_own_manager_behind_its_own_guard(
        self, runner: FakeRunner, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[tuple[str, bool]] = []

        class FakeEngine:
            ENGINE_NAME = "postgresql"

            def is_installed(self) -> bool:
                return True

            def get_version(self) -> str:
                return "16.3"

            def uninstall(self, purge: bool = False) -> None:
                called.append((self.ENGINE_NAME, purge))

        monkeypatch.setattr(
            "noust.managers.database.registry.get_db_manager",
            lambda engine, verbose=False: FakeEngine(),
        )
        monkeypatch.setattr(
            "noust.managers.server.component_dependents._databases_of", lambda engine: []
        )
        SoftwareLedger().record("postgresql", via="database_engine")
        software = machine(runner, host)

        software.remove("postgresql")

        assert called == [("postgresql", False)]
        assert not removals(runner)
        assert SoftwareLedger().get("postgresql") is None

    def test_an_engine_that_still_holds_databases_stays_whatever_the_dependents_said(
        self, runner: FakeRunner, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeEngine:
            ENGINE_NAME = "postgresql"

            def is_installed(self) -> bool:
                return True

            def get_version(self) -> str:
                return "16.3"

            def uninstall(self, purge: bool = False) -> None:
                raise EngineInUseError("PostgreSQL still holds 1 database: shop")

        monkeypatch.setattr(
            "noust.managers.database.registry.get_db_manager",
            lambda engine, verbose=False: FakeEngine(),
        )
        monkeypatch.setattr(
            "noust.managers.server.component_dependents._databases_of", lambda engine: []
        )
        SoftwareLedger().record("postgresql", via="database_engine")

        with pytest.raises(EngineInUseError, match="shop"):
            machine(runner, host).remove("postgresql")

        # What was refused is still there, and still the ledger's.
        assert SoftwareLedger().get("postgresql") is not None

    def test_under_a_rehearsal_nothing_is_removed_recorded_or_forgotten(
        self, runner: FakeRunner, host, events: list
    ) -> None:
        installed(runner, cloudflared="2025.1")
        runner.script(["apt-get", "-s"], stdout="Remv cloudflared [2025.1]\n")
        source = host.root / SOURCES / "noust-cloudflared.list"
        source.write_text("deb x\n")
        SoftwareLedger().record(
            "cloudflared",
            packages=["cloudflared"],
            repository_files=["/etc/apt/sources.list.d/noust-cloudflared.list"],
            via="cloudflared",
        )
        rehearsal = DryRunRunner(runner)
        set_fs(DryRunFileSystem())
        try:
            software = SoftwareManager(
                runner=rehearsal, host=host, platform=platform_for("apt"), fs=None
            )

            result = software.remove("cloudflared")
        finally:
            set_fs(None)

        assert result.rehearsal is True
        assert result.removed == ("cloudflared",)
        assert source.exists()
        assert SoftwareLedger().get("cloudflared") is not None
        assert events == []
        assert rehearsal.skipped and rehearsal.skipped[0][:2] == ("apt-get", "remove")


class TestInstalling:
    def test_it_installs_through_the_package_manager_and_writes_the_ledger(
        self, host, events: list
    ) -> None:
        # dpkg says nothing is installed before apt runs and the package is there after it.
        runner = SequencedRunner()
        runner.sequence(
            ["dpkg-query"], [{"stdout": ""}, {"stdout": "libnginx-mod-stream ii  1.24.0\n"}]
        )

        result = machine(runner, host).install("nginx_stream_module")

        assert result.installed_now and result.packages == ("libnginx-mod-stream",)
        assert ("apt-get", "install", "-y", "libnginx-mod-stream") in runner.calls
        row = SoftwareLedger().get("nginx_stream_module")
        assert row is not None and row.via == "package" and row.packages == ("libnginx-mod-stream",)
        assert [(e, k["outcome"]) for e, k in events] == [("server.software_install", "ok")]

    def test_what_is_there_is_not_installed_again(self, runner: FakeRunner, host) -> None:
        installed(runner, nginx="1.24")

        result = machine(runner, host).install("nginx")

        assert result.installed_now is False
        assert not [c for c in runner.calls if c[:2] == ("apt-get", "install")]
        assert SoftwareLedger().get("nginx") is None

    def test_where_noust_installs_nothing_it_says_so_and_records_the_refusal(
        self, runner: FakeRunner, host, events: list
    ) -> None:
        software = machine(runner, host, family="zypper")

        with pytest.raises(SoftwareRefusedError) as raised:
            software.install("nginx_stream_module")

        assert raised.value.reason == "not_installable"
        assert [(e, k["outcome"]) for e, k in events] == [("server.software_install", "denied")]

    def test_a_failed_install_carries_the_package_managers_words_and_writes_no_row(
        self, runner: FakeRunner, host, events: list
    ) -> None:
        installed(runner)
        runner.script(
            ["apt-get", "install"], stderr="E: Unable to locate package nginx", exit_code=100
        )

        with pytest.raises(ServerError) as raised:
            machine(runner, host).install("nginx")

        assert "Unable to locate package nginx" in (raised.value.output or "")
        assert SoftwareLedger().get("nginx") is None
        assert [(e, k["outcome"]) for e, k in events] == [("server.software_install", "failure")]

    def test_an_installer_of_its_own_is_called_and_the_files_it_added_are_the_ledgers(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner)
        called: list[tuple[str, InstallOptions]] = []

        def fake_cloudflared(
            manager: SoftwareManager, component: Component, options: InstallOptions
        ) -> None:
            called.append((component.id, options))
            (host.root / SOURCES / "noust-cloudflared.list").write_text("deb x\n")
            (host.root / KEYRINGS / "noust-cloudflared.gpg").write_bytes(b"k")
            installed(runner, cloudflared="2025.1")

        software = machine(runner, host, adapters={Installer.CLOUDFLARED: fake_cloudflared})

        result = software.install("cloudflared", InstallOptions(epel=True, ignore=("203.0.113.9",)))

        assert called == [("cloudflared", InstallOptions(epel=True, ignore=("203.0.113.9",)))]
        assert result.repository_files == (
            "/etc/apt/sources.list.d/noust-cloudflared.list",
            "/usr/share/keyrings/noust-cloudflared.gpg",
        )
        row = SoftwareLedger().get("cloudflared")
        assert row is not None and row.via == "cloudflared"
        assert row.repository_files == result.repository_files

    def test_a_file_that_was_there_before_is_not_claimed_by_the_install(
        self, runner: FakeRunner, host
    ) -> None:
        installed(runner)
        (host.root / SOURCES / "noust-cloudflared.list").write_text("deb x\n")

        def fake(manager: SoftwareManager, component: Component, options: InstallOptions) -> None:
            (host.root / KEYRINGS / "noust-cloudflared.gpg").write_bytes(b"k")
            installed(runner, cloudflared="1")

        result = machine(runner, host, adapters={Installer.CLOUDFLARED: fake}).install(
            "cloudflared"
        )

        assert result.repository_files == ("/usr/share/keyrings/noust-cloudflared.gpg",)

    def test_a_ledger_that_cannot_be_written_does_not_undo_an_install_that_worked(
        self, runner: FakeRunner, host
    ) -> None:
        class BrokenLedger:
            def record(self, *args: Any, **kwargs: Any) -> None:
                raise sqlite3.OperationalError("database is locked")

            def get(self, component: str) -> None:
                return None

            def all(self) -> dict:
                return {}

            def forget(self, component: str) -> bool:
                return False

        installed(runner)

        def fake(manager: SoftwareManager, component: Component, options: InstallOptions) -> None:
            installed(runner, cloudflared="1")

        software = machine(
            runner,
            host,
            ledger=BrokenLedger(),
            adapters={Installer.CLOUDFLARED: fake},  # type: ignore[arg-type]
        )

        assert software.install("cloudflared").installed_now is True

    def test_an_unknown_installer_is_refused_not_guessed(self, runner: FakeRunner, host) -> None:
        installed(runner)

        with pytest.raises(UnsupportedHostError, match="no installer"):
            machine(runner, host, adapters={}).install("cloudflared")

    def test_the_real_installers_are_the_ones_that_already_exist(self) -> None:
        from noust.managers.server import software_installers

        assert set(software_installers.ADAPTERS) == {
            Installer.CLOUDFLARED,
            Installer.WAF,
            Installer.FAIL2BAN,
            Installer.KNOT,
            Installer.DOCKER,
            Installer.DATABASE_ENGINE,
        }


def test_the_fixture_the_other_backends_share_is_the_one_the_removal_tests_read() -> None:
    assert "Remv nginx" in fixture("apt", "remove-simulation.txt")
