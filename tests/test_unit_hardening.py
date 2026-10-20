# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sandbox of an application's unit (checklist B-58).

Pinned here:

- what each profile writes, for each runtime family, and that the template and
  the edit of an existing unit say the same thing;
- that only what an application's own unit runs is hardened, and a redeploy
  never hardens an application that predates the feature;
- that the directories an operator declares are held to what a unit may write;
- that changing the sandbox restarts behind the health gate and, when the
  application does not answer, puts the previous unit and record back.
"""

# The service manager fixtures are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, PackageLoader

from noust.core import unit_hardening_store as hardening_store
from noust.core.exceptions import DeploymentError, ServiceError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import hardening as engine
from noust.managers import unit_hardening
from noust.managers.service_manager import ServiceManager
from tests.test_service_manager import (  # noqa: F401  (pytest resolves fixtures by name)
    manager,
    store,
    unit_dirs,
)

DOMAIN = "shop.example.com"
ROOT = Path("/var/www/apps/shop-example-com")


def render(directives: list[str] | None = None) -> str:
    """Render the application unit template the way the service manager does."""
    return (
        Environment(  # noqa: S701 - unit files, escaped by their own macros
            loader=PackageLoader("noust", "templates/systemd"), trim_blocks=True, lstrip_blocks=True
        )
        .get_template("app.service.j2")
        .render(
            name="shop-example-com",
            description="Noust: shop.example.com (nodejs)",
            command="/usr/bin/npm run start",
            working_directory=f"{ROOT}/current",
            user="noust-app-shop",
            group="noust-app-shop",
            environment={"PORT": "3100"},
            hardening=directives or [],
        )
    )


# ---------------------------------------------------------------------------
# The profiles
# ---------------------------------------------------------------------------


def test_off_is_the_unit_as_it_was() -> None:
    assert unit_hardening.directives_for("off", family="node", app_root=ROOT) == []


def test_the_default_profile_takes_away_what_a_web_application_does_not_need() -> None:
    lines = unit_hardening.directives_for("default", family="node", app_root=ROOT)

    for wanted in (
        "ProtectSystem=strict",
        f"ReadWritePaths=-{ROOT}",
        "ProtectHome=true",
        "PrivateDevices=true",
        "ProtectKernelTunables=true",
        "ProtectKernelModules=true",
        "ProtectKernelLogs=true",
        "ProtectControlGroups=true",
        "ProtectClock=true",
        "ProtectHostname=true",
        "LockPersonality=true",
        "RestrictRealtime=true",
        "RestrictSUIDSGID=true",
        "RestrictNamespaces=true",
        "SystemCallArchitectures=native",
        "SystemCallFilter=@system-service",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
    ):
        assert wanted in lines, wanted


def test_a_blocked_call_fails_instead_of_killing_the_process() -> None:
    lines = unit_hardening.directives_for("default", family="node", app_root=ROOT)

    assert "SystemCallErrorNumber=EPERM" in lines


def test_node_can_still_ask_the_machine_for_its_interfaces() -> None:
    """os.networkInterfaces() needs AF_NETLINK; every framework calls it."""
    lines = unit_hardening.directives_for("default", family="node", app_root=ROOT)

    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK" in lines


def test_strict_adds_the_privileged_calls_and_memory_protection_where_there_is_no_jit() -> None:
    node = unit_hardening.directives_for("strict", family="node", app_root=ROOT)
    python = unit_hardening.directives_for("strict", family="python", app_root=ROOT)

    assert "SystemCallFilter=~@privileged @resources" in node
    assert "MemoryDenyWriteExecute=true" not in node, "V8 maps writable executable memory"
    assert "MemoryDenyWriteExecute=true" in python


def test_an_application_that_lives_under_home_is_not_hidden_from_itself() -> None:
    lines = unit_hardening.directives_for("default", family="node", app_root=Path("/home/ana/shop"))

    assert "ProtectHome=true" not in lines
    assert "ProtectSystem=strict" in lines


def test_declared_directories_are_writable_beside_the_applications_own() -> None:
    lines = unit_hardening.directives_for(
        "default", family="node", app_root=ROOT, extra_writable=["/var/lib/shop", "/srv/uploads"]
    )

    assert f"ReadWritePaths=-{ROOT} -/var/lib/shop -/srv/uploads" in lines


def test_an_unknown_profile_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        unit_hardening.directives_for("paranoid", family="node", app_root=ROOT)
    assert caught.value.field == "profile"


@pytest.mark.parametrize(
    ("kind", "family"),
    [
        ("nodejs", "node"),
        ("nextjs", "node"),
        ("vite", "node"),
        ("python", "python"),
        ("unknown", "generic"),
    ],
)
def test_the_family_follows_the_type(kind: str, family: str) -> None:
    assert unit_hardening.family_of(kind) == family


@pytest.mark.parametrize("kind", ["docker-compose", "container", "php-fpm", "static"])
def test_only_what_the_units_own_code_runs_is_hardened(kind: str) -> None:
    assert unit_hardening.ineligible(App(domain=DOMAIN, app_type=kind)) is not None


def test_a_monorepos_workspaces_run_their_own_code_and_are_hardened_like_node() -> None:
    assert unit_hardening.ineligible(App(domain=DOMAIN, app_type="monorepo")) is None
    assert unit_hardening.family_of("monorepo") == "node"


def test_a_node_application_can_be_hardened() -> None:
    assert unit_hardening.ineligible(App(domain=DOMAIN, app_type="nodejs")) is None


# ---------------------------------------------------------------------------
# What a unit may be allowed to write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/var/lib/shop", "/srv/uploads/", "/data", "/var/log/shop"])
def test_a_directory_of_the_applications_own_can_be_declared(path: str) -> None:
    assert unit_hardening.check_writable_path(path) == path.rstrip("/")


@pytest.mark.parametrize(
    "path",
    [
        "relative/dir",
        "",
        "/",
        "/etc",
        "/etc/nginx",
        "/usr/local/bin",
        "/var",
        "/var/lib",
        "/proc/1",
        "/dev/shm/../etc",
        "/srv/with space",
        "/srv/100%",
        "/var/lib/noust/secrets",
        "/etc/noust",
        "/var/backups/noust/x",
    ],
)
def test_the_systems_and_nousts_own_directories_cannot_be_made_writable(path: str) -> None:
    with pytest.raises(ValidationError):
        unit_hardening.check_writable_path(path)


def test_declared_directories_are_listed_once() -> None:
    assert unit_hardening.check_writable_paths(["/srv/a", "/srv/a/", "/srv/b"]) == (
        "/srv/a",
        "/srv/b",
    )


# ---------------------------------------------------------------------------
# One spelling: the template and the edit
# ---------------------------------------------------------------------------


def test_the_template_writes_the_block_after_the_security_lines() -> None:
    lines = unit_hardening.directives_for("default", family="node", app_root=ROOT)
    unit = render(lines)

    block = unit.index("# Hardening: a read-only filesystem")
    assert unit.index("PrivateTmp=true") < block < unit.index("# Logging")
    assert "ProtectSystem=strict\n" in unit


def test_a_unit_without_directives_has_no_block() -> None:
    assert "Hardening" not in render([])
    assert "ProtectSystem" not in render([])


def test_editing_a_unit_gives_what_rendering_it_would() -> None:
    """In either direction, and every time: applying twice changes nothing."""
    lines = unit_hardening.directives_for("default", family="node", app_root=ROOT)

    assert unit_hardening.with_hardening(render([]), lines) == render(lines)
    assert unit_hardening.with_hardening(render(lines), []) == render([])
    once = unit_hardening.with_hardening(render([]), lines)
    assert unit_hardening.with_hardening(once, lines) == once


def test_changing_the_profile_replaces_the_block() -> None:
    default = unit_hardening.directives_for("default", family="python", app_root=ROOT)
    strict = unit_hardening.directives_for("strict", family="python", app_root=ROOT)

    assert unit_hardening.with_hardening(render(default), strict) == render(strict)
    assert unit_hardening.with_hardening(render(strict), default) == render(default)


def test_an_edited_unit_keeps_the_operators_own_lines() -> None:
    unit = "# Generated by Noust\n[Service]\nExecStart=/bin/app\nNice=5\nProtectSystem=full\n\n[Install]\n"
    lines = ["ProtectSystem=strict", "PrivateDevices=true"]

    edited = unit_hardening.with_hardening(unit, lines)

    assert "Nice=5\n" in edited
    assert "ProtectSystem=full" not in edited
    assert edited.count("ProtectSystem=strict") == 1
    assert edited.index("[Install]") > edited.index("PrivateDevices=true")


def test_a_unit_without_a_service_section_is_refused() -> None:
    with pytest.raises(ServiceError, match=r"\[Service\]"):
        unit_hardening.with_hardening("[Unit]\nDescription=x\n", ["ProtectSystem=strict"])


def test_the_sandbox_directives_are_the_ones_the_edit_knows_how_to_remove() -> None:
    """A directive the profile writes and the edit does not own would survive a switch to off."""
    for profile in ("default", "strict"):
        for family in ("node", "python"):
            for line in unit_hardening.directives_for(profile, family=family, app_root=ROOT):
                assert line.split("=", 1)[0] in unit_hardening.HARDENING_KEYS, line


# ---------------------------------------------------------------------------
# The service manager is the chokepoint
# ---------------------------------------------------------------------------


@pytest.fixture
def stored(store: Any) -> Any:
    """The service manager's fake store, holding the application."""
    app = App(domain=DOMAIN, app_type="nodejs", port=3100, app_path=str(ROOT), id=1)
    store.list_apps = lambda: [app]
    return store


def test_the_unit_of_a_hardened_application_is_written_hardened(
    manager: ServiceManager,
    unit_dirs: dict[str, Path],
    stored: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        unit_hardening,
        "get_record",
        lambda domain, **kw: hardening_store.HardeningRecord(profile="default"),
    )

    manager.create_service(
        name="shop-example-com", command="/usr/bin/true", working_directory=str(ROOT / "current")
    )

    text = (unit_dirs["managed"] / "shop-example-com.service").read_text()
    assert "ProtectSystem=strict" in text
    assert f"ReadWritePaths=-{ROOT}" in text


def test_every_workspace_unit_of_a_monorepo_is_written_hardened(
    manager: ServiceManager,
    unit_dirs: dict[str, Path],
    store: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = App(domain=DOMAIN, app_type="monorepo", port=3100, app_path=str(ROOT), id=1)
    store.list_apps = lambda: [app]
    monkeypatch.setattr(
        unit_hardening,
        "get_record",
        lambda domain, **kw: hardening_store.HardeningRecord(profile="strict"),
    )

    for workspace in ("web", "api"):
        manager.create_service(
            name=f"shop-example-com-{workspace}",
            command="/usr/bin/true",
            working_directory=str(ROOT / "current" / "apps" / workspace),
        )

    for workspace in ("web", "api"):
        text = (unit_dirs["managed"] / f"shop-example-com-{workspace}.service").read_text()
        assert "ProtectSystem=strict" in text
        assert f"ReadWritePaths=-{ROOT}" in text
        # Next.js and the rest of a monorepo are Node: V8 maps writable executable memory.
        assert "MemoryDenyWriteExecute" not in text


def test_a_unit_named_like_a_monorepos_workspace_but_of_another_application_is_not_its_own(
    manager: ServiceManager,
    unit_dirs: dict[str, Path],
    store: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``shop-example-com-api`` could be the monorepo's workspace or an application of its own."""
    monorepo = App(domain=DOMAIN, app_type="monorepo", port=3100, app_path=str(ROOT), id=1)
    other = App(
        domain="shop-example-com-api.example.com",
        app_type="nodejs",
        port=3101,
        app_path=str(ROOT.parent / "shop-example-com-api"),
        id=2,
    )
    store.list_apps = lambda: [monorepo, other]
    profiles = {DOMAIN: "strict", other.domain: "off"}
    monkeypatch.setattr(
        unit_hardening,
        "get_record",
        lambda domain, **kw: hardening_store.HardeningRecord(profile=profiles[domain]),
    )

    manager.create_service(
        name="shop-example-com-api", command="/usr/bin/true", working_directory="/srv"
    )

    assert (
        "ProtectSystem" not in (unit_dirs["managed"] / "shop-example-com-api.service").read_text()
    )


def test_the_unit_of_an_application_that_predates_hardening_is_written_as_it_was(
    manager: ServiceManager,
    unit_dirs: dict[str, Path],
    stored: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redeploy writes a new unit and must not harden what nobody asked to harden."""
    monkeypatch.setattr(unit_hardening, "get_record", lambda domain, **kw: None)

    manager.create_service(
        name="shop-example-com", command="/usr/bin/true", working_directory=str(ROOT / "current")
    )

    text = (unit_dirs["managed"] / "shop-example-com.service").read_text()
    assert "ProtectSystem" not in text and "Hardening" not in text


def test_a_unit_that_belongs_to_no_application_is_not_hardened(
    manager: ServiceManager, unit_dirs: dict[str, Path], stored: Any
) -> None:
    manager.create_service(name="worker", command="/usr/bin/true", working_directory="/srv")

    assert "ProtectSystem" not in (unit_dirs["managed"] / "worker.service").read_text()


def test_the_blue_green_template_is_hardened_too(
    manager: ServiceManager,
    unit_dirs: dict[str, Path],
    stored: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        unit_hardening,
        "get_record",
        lambda domain, **kw: hardening_store.HardeningRecord(profile="strict"),
    )

    manager.install_instance_template(
        "shop-example-com",
        command="/usr/bin/true",
        colors_directory=str(ROOT / "colors"),
        environment={},
        environment_file=None,
        description="Noust: shop.example.com (nodejs)",
    )

    text = (unit_dirs["managed"] / "shop-example-com@.service").read_text()
    assert "SystemCallFilter=~@privileged @resources" in text


def test_set_unit_hardening_rewrites_an_existing_unit_and_returns_the_old_one(
    manager: ServiceManager, unit_dirs: dict[str, Path], stored: Any
) -> None:
    manager.create_service(
        name="shop-example-com", command="/usr/bin/true", working_directory="/srv"
    )
    path = unit_dirs["managed"] / "shop-example-com.service"
    before = path.read_text()

    previous = manager.set_unit_hardening("shop-example-com", ["ProtectSystem=strict"])

    assert previous == before
    assert "ProtectSystem=strict" in path.read_text()
    manager.set_unit_hardening("shop-example-com", [])
    assert path.read_text() == before


def test_a_directive_with_a_newline_is_refused_and_writes_nothing(
    manager: ServiceManager, unit_dirs: dict[str, Path], stored: Any
) -> None:
    manager.create_service(
        name="shop-example-com", command="/usr/bin/true", working_directory="/srv"
    )
    path = unit_dirs["managed"] / "shop-example-com.service"
    before = path.read_text()

    with pytest.raises(ValidationError):
        manager.set_unit_hardening("shop-example-com", ["ProtectSystem=strict\nExecStart=/bin/sh"])

    assert path.read_text() == before


# ---------------------------------------------------------------------------
# New applications
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real store, where the engine and the manager look for it."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def _app(store: NoustStore, **fields: Any) -> App:
    values: dict[str, Any] = {
        "domain": DOMAIN,
        "app_type": "nodejs",
        "port": 3100,
        "app_path": str(ROOT),
    }
    values.update(fields)
    return store.create_app(App(**values))


def test_a_new_application_is_hardened_from_its_first_deployment(sqlite_store: NoustStore) -> None:
    _app(sqlite_store)

    record = unit_hardening.adopt_new_app(DOMAIN, store=sqlite_store)

    assert record is not None and record.profile == "default"
    assert record.changed_by == "noust"


def test_an_operators_choice_survives_the_first_deployment_code_running_again(
    sqlite_store: NoustStore,
) -> None:
    _app(sqlite_store)
    hardening_store.set_record(DOMAIN, "off", (), changed_by="ana", store=sqlite_store)

    record = unit_hardening.adopt_new_app(DOMAIN, store=sqlite_store)

    assert record is not None and record.profile == "off" and record.changed_by == "ana"


@pytest.mark.parametrize("kind", ["docker-compose", "container", "php-fpm"])
def test_an_application_that_cannot_be_hardened_gets_no_record(
    sqlite_store: NoustStore, kind: str
) -> None:
    _app(sqlite_store, app_type=kind)

    assert unit_hardening.adopt_new_app(DOMAIN, store=sqlite_store) is None
    assert hardening_store.get_record(DOMAIN, store=sqlite_store) is None


def test_the_profile_a_server_gives_new_applications_is_its_setting(
    sqlite_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.config import Config

    _app(sqlite_store)
    real = Config.get
    monkeypatch.setattr(
        Config,
        "get",
        lambda self, key, default=None: (
            "strict" if key == "deploy.hardening" else real(self, key, default)
        ),
    )

    record = unit_hardening.adopt_new_app(DOMAIN, store=sqlite_store)

    assert record is not None and record.profile == "strict"


def test_a_setting_that_is_not_a_profile_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.core.config import Config

    monkeypatch.setattr(Config, "get", lambda self, key, default=None: "nonsense")

    assert unit_hardening.default_profile() == "default"


def test_the_row_goes_with_the_application_and_survives_a_redeploy(
    sqlite_store: NoustStore,
) -> None:
    app = _app(sqlite_store)
    hardening_store.set_record(DOMAIN, "strict", ("/srv/a",), changed_by="ana", store=sqlite_store)

    app.status = "running"
    sqlite_store.update_app(app)
    record = hardening_store.get_record(DOMAIN, store=sqlite_store)
    assert record is not None and record.writable_paths == ("/srv/a",)

    sqlite_store.delete_app(DOMAIN)
    with sqlite_store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) FROM app_hardening")
        assert cursor.fetchone()[0] == 0


def test_a_profile_that_does_not_exist_is_refused_by_the_store(sqlite_store: NoustStore) -> None:
    _app(sqlite_store)

    with pytest.raises(ValidationError):
        hardening_store.set_record(DOMAIN, "paranoid", (), changed_by="ana", store=sqlite_store)


# ---------------------------------------------------------------------------
# Reading systemd's verdict
# ---------------------------------------------------------------------------

ANALYZE_OUTPUT = """\
  NAME                                                        DESCRIPTION                                                              EXPOSURE
✗ PrivateNetwork=                                             Service has access to the host's network                                      0.5
✓ ProtectSystem=                                              Service has strict read-only access to the OS file hierarchy

→ Overall exposure level for shop-example-com.service: 3.8 OK 🙂
"""


def test_the_verdict_is_read_off_the_end_of_the_report() -> None:
    exposure = unit_hardening.parse_exposure(ANALYZE_OUTPUT)

    assert exposure == unit_hardening.Exposure("shop-example-com.service", 3.8, "OK")


def test_a_report_without_a_verdict_is_none() -> None:
    assert unit_hardening.parse_exposure("Failed to load unit") is None


def test_the_score_is_asked_with_the_read_only_probe(runner: FakeRunner) -> None:
    runner.script(("systemd-analyze", "security"), stdout=ANALYZE_OUTPUT)

    exposure = unit_hardening.exposure_of("shop-example-com", runner)

    assert exposure is not None and exposure.score == 3.8
    assert runner.ran("systemd-analyze", "security", "--no-pager", "shop-example-com.service")


def test_an_older_systemd_without_the_analysis_is_no_score(runner: FakeRunner) -> None:
    runner.script(("systemd-analyze", "security"), exit_code=1, stderr="Unknown verb 'security'")

    assert unit_hardening.exposure_of("shop-example-com", runner) is None


def test_the_probe_is_declared_read_only() -> None:
    from noust.core.runner import is_read_only

    assert is_read_only(["systemd-analyze", "security", "--no-pager", "shop-example-com.service"])
    assert not is_read_only(["systemd-analyze", "set-log-level", "debug"])


# ---------------------------------------------------------------------------
# Changing it
# ---------------------------------------------------------------------------


class FakeUnits:
    """Stands in for ServiceManager: records rewrites and restarts."""

    def __init__(self, units: list[str], *, fail_on: str | None = None) -> None:
        self.units = units
        self.bodies = {unit: f"original of {unit}" for unit in units}
        self.restarts: list[str] = []
        self.fail_on = fail_on

    def app_units(self, app: Any) -> list[str]:
        return list(self.units)

    def serving_units(self, app: Any) -> list[str]:
        return list(self.units)

    def set_unit_hardening(self, name: str, directives: list[str]) -> str:
        if name == self.fail_on:
            raise ServiceError(f"Could not write {name}")
        previous = self.bodies[name]
        self.bodies[name] = "|".join(directives)
        return previous

    def update_config(self, name: str, content: str) -> str:
        previous = self.bodies[name]
        self.bodies[name] = content
        return previous

    def restart(self, name: str) -> None:
        self.restarts.append(name)

    def logs(self, name: str, lines: int = 50) -> str:
        return "shop: operation not permitted"


class FakeGate:
    """A health gate that answers as the test says, one answer per restart."""

    def __init__(self, answers: list[bool], restart: Any = None) -> None:
        self.answers = answers
        self.calls = 0
        self.restart = restart

    def restart_and_probe(self) -> tuple[bool, str]:
        if self.restart is not None:
            self.restart()
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return answer, "" if answer else "connection refused\njournal: operation not permitted"


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the gate the engine builds; hand the test the answers it will give."""
    holder: dict[str, Any] = {"answers": [True]}

    def build(app: App, store: Any, log: Any, *, restart: Any = None) -> FakeGate:
        holder["gate"] = FakeGate(holder["answers"], restart)
        return holder["gate"]  # type: ignore[no-any-return]

    monkeypatch.setattr(engine, "health_gate_for", build)
    return holder


def test_the_units_are_rewritten_restarted_behind_the_gate_and_recorded(
    sqlite_store: NoustStore, runner: FakeRunner, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["shop-example-com"])

    change = engine.set_hardening(
        DOMAIN,
        "default",
        actor="ana",
        store=sqlite_store,
        services=units,
        runner=runner,  # type: ignore[arg-type]
    )

    assert change.restarted and change.profile == "default"
    assert "ProtectSystem=strict" in units.bodies["shop-example-com"]
    assert units.restarts == ["shop-example-com"]
    record = hardening_store.get_record(DOMAIN, store=sqlite_store)
    assert record is not None and (record.profile, record.changed_by) == ("default", "ana")


def test_every_unit_of_a_monorepo_is_rewritten_and_all_of_them_restarted_behind_one_gate(
    sqlite_store: NoustStore, runner: FakeRunner, gate: dict[str, Any]
) -> None:
    _app(sqlite_store, app_type="monorepo")
    names = ["shop-example-com-api", "shop-example-com-web"]
    units = FakeUnits(names)

    change = engine.set_hardening(
        DOMAIN,
        "default",
        store=sqlite_store,
        services=units,
        runner=runner,  # type: ignore[arg-type]
    )

    assert change.units == tuple(names)
    assert all("ProtectSystem=strict" in units.bodies[name] for name in names)
    assert units.restarts == names


def test_a_monorepo_whose_workspace_does_not_answer_in_the_new_sandbox_gets_every_unit_back(
    sqlite_store: NoustStore, runner: FakeRunner, gate: dict[str, Any]
) -> None:
    _app(sqlite_store, app_type="monorepo")
    names = ["shop-example-com-api", "shop-example-com-web"]
    units = FakeUnits(names)
    gate["answers"] = [False, True]

    with pytest.raises(DeploymentError, match="did not answer"):
        engine.set_hardening(
            DOMAIN,
            "strict",
            store=sqlite_store,
            services=units,
            runner=runner,  # type: ignore[arg-type]
        )

    assert units.bodies == {name: f"original of {name}" for name in names}
    assert hardening_store.get_record(DOMAIN, store=sqlite_store) is None


def test_an_application_that_does_not_answer_in_the_new_sandbox_gets_the_old_one_back(
    sqlite_store: NoustStore, runner: FakeRunner, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["shop-example-com"])
    gate["answers"] = [False, True]

    with pytest.raises(DeploymentError, match="previous one is running again") as caught:
        engine.set_hardening(
            DOMAIN,
            "default",
            store=sqlite_store,
            services=units,
            runner=runner,  # type: ignore[arg-type]
        )

    assert units.bodies["shop-example-com"] == "original of shop-example-com"
    assert hardening_store.get_record(DOMAIN, store=sqlite_store) is None
    assert "operation not permitted" in caught.value.details, "the probes, verbatim"


def test_the_previous_record_is_what_comes_back(
    sqlite_store: NoustStore, runner: FakeRunner, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    hardening_store.set_record(DOMAIN, "default", ("/srv/a",), changed_by="ana", store=sqlite_store)
    units = FakeUnits(["shop-example-com"])
    gate["answers"] = [False, False]

    with pytest.raises(DeploymentError, match="not answering either"):
        engine.set_hardening(
            DOMAIN,
            "strict",
            store=sqlite_store,
            services=units,
            runner=runner,  # type: ignore[arg-type]
        )

    record = hardening_store.get_record(DOMAIN, store=sqlite_store)
    assert record is not None and (record.profile, record.writable_paths) == (
        "default",
        ("/srv/a",),
    )


def test_a_unit_that_cannot_be_written_leaves_every_unit_and_the_record_as_they_were(
    sqlite_store: NoustStore, runner: FakeRunner, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["a", "b"], fail_on="b")

    with pytest.raises(ServiceError):
        engine.set_hardening(
            DOMAIN,
            "default",
            store=sqlite_store,
            services=units,
            runner=runner,  # type: ignore[arg-type]
        )

    assert units.bodies == {"a": "original of a", "b": "original of b"}
    assert hardening_store.get_record(DOMAIN, store=sqlite_store) is None
    assert units.restarts == []


def test_without_a_restart_the_unit_is_written_and_nothing_is_asked(
    sqlite_store: NoustStore, runner: FakeRunner, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["shop-example-com"])

    change = engine.set_hardening(
        DOMAIN,
        "strict",
        restart=False,
        store=sqlite_store,
        services=units,
        runner=runner,  # type: ignore[arg-type]
    )

    assert not change.restarted
    assert units.restarts == [] and "gate" not in gate


def test_off_removes_the_block_and_remembers_the_choice(
    sqlite_store: NoustStore, runner: FakeRunner, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["shop-example-com"])

    engine.set_hardening(
        DOMAIN,
        "off",
        store=sqlite_store,
        services=units,
        runner=runner,  # type: ignore[arg-type]
    )

    assert units.bodies["shop-example-com"] == ""
    record = hardening_store.get_record(DOMAIN, store=sqlite_store)
    assert record is not None and record.profile == "off"


def test_a_stack_cannot_be_hardened_and_nothing_changes(
    sqlite_store: NoustStore, runner: FakeRunner, gate: dict[str, Any]
) -> None:
    _app(sqlite_store, app_type="docker-compose")
    units = FakeUnits(["shop-example-com"])

    with pytest.raises(ValidationError, match="cannot be hardened"):
        engine.set_hardening(
            DOMAIN,
            "default",
            store=sqlite_store,
            services=units,
            runner=runner,  # type: ignore[arg-type]
        )

    assert units.bodies == {"shop-example-com": "original of shop-example-com"}


def test_a_path_the_system_owns_is_refused_before_anything_is_written(
    sqlite_store: NoustStore, runner: FakeRunner, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["shop-example-com"])

    with pytest.raises(ValidationError):
        engine.set_hardening(
            DOMAIN,
            "default",
            writable_paths=["/etc"],
            store=sqlite_store,
            services=units,  # type: ignore[arg-type]
            runner=runner,
        )

    assert units.bodies == {"shop-example-com": "original of shop-example-com"}


def test_an_unknown_application_is_refused(sqlite_store: NoustStore, runner: FakeRunner) -> None:
    with pytest.raises(Exception, match="not found"):
        engine.set_hardening(
            "nobody.example.com", "default", store=sqlite_store, runner=runner, logger=Logger()
        )


def test_a_blue_green_application_is_written_once_into_its_template(
    sqlite_store: NoustStore,
    runner: FakeRunner,
    gate: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _app(sqlite_store, zero_downtime=True, active_color="blue")
    units = FakeUnits(["shop-example-com@blue", "shop-example-com@green"])
    switched: list[str] = []
    monkeypatch.setattr(
        engine, "restart_blue_green", lambda app, store, log, put_back: switched.append(app.domain)
    )

    change = engine.set_hardening(
        DOMAIN,
        "default",
        store=sqlite_store,
        services=units,
        runner=runner,  # type: ignore[arg-type]
    )

    assert switched == [DOMAIN]
    assert units.bodies["shop-example-com@green"] == "original of shop-example-com@green"
    assert "ProtectSystem=strict" in units.bodies["shop-example-com@blue"]
    assert change.units == ("shop-example-com@blue", "shop-example-com@green")


def test_the_status_of_an_application_that_predates_hardening_says_so(
    sqlite_store: NoustStore, runner: FakeRunner
) -> None:
    _app(sqlite_store)
    runner.script(("systemd-analyze", "security"), stdout=ANALYZE_OUTPUT)
    units = FakeUnits(["shop-example-com"])

    info = engine.status(DOMAIN, store=sqlite_store, runner=runner, services=units)  # type: ignore[arg-type]

    assert (info.profile, info.configured, info.eligible) == ("off", False, True)
    assert info.directives == ()
    assert (info.exposure, info.exposure_label) == (3.8, "OK")


def test_the_status_of_a_stack_says_why_it_has_none(sqlite_store: NoustStore) -> None:
    _app(sqlite_store, app_type="docker-compose")

    info = engine.status(DOMAIN, store=sqlite_store, analyze=False)

    assert not info.eligible and info.reason is not None and "Docker" in info.reason
