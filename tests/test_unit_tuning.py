# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The tuning of an application's unit beyond its hard limits (checklist B-57, B-60).

Pinned here:

- what each option is, what it may be, and the unit lines it becomes;
- that the template and the edit of an existing unit say the same thing, after the sandbox's
  lines, and that the edit keeps what the operator wrote;
- that the unit of a tuned application is written tuned whoever writes it;
- that changing it restarts behind the health gate and, when the application does not answer,
  puts the previous unit and record back.
"""

# The service manager fixtures are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, PackageLoader

from noust.core import unit_tuning_store as tuning_store
from noust.core.exceptions import DeploymentError, ServiceError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import hardening as hardening_engine
from noust.deployers import unit_tuning as engine
from noust.managers import unit_hardening, unit_tuning
from noust.managers.service_manager import ServiceManager
from tests.test_service_manager import (  # noqa: F401  (pytest resolves fixtures by name)
    manager,
    store,
    unit_dirs,
)

DOMAIN = "shop.example.com"
ROOT = Path("/var/www/apps/shop-example-com")


def render(tuning: list[str] | None = None, hardening: list[str] | None = None) -> str:
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
            resource_limits=["MemoryMax=512M"],
            hardening=hardening or [],
            tuning=tuning or [],
        )
    )


# ---------------------------------------------------------------------------
# The options
# ---------------------------------------------------------------------------


def test_every_option_becomes_one_unit_line_in_a_fixed_order() -> None:
    tuning = unit_tuning.validate(
        {
            "io_weight": 50,
            "memory_high_mb": 384,
            "limit_nofile": 100000,
            "memory_swap_max_mb": 0,
            "cpu_weight": 200,
            "allowed_cpus": "0-1",
            "limit_nproc": 4096,
            "limit_memlock_mb": "infinity",
            "core_dumps": False,
        },
        cpus=4,
    )

    assert unit_tuning.directives(tuning) == [
        "MemoryHigh=384M",
        "MemorySwapMax=0M",
        "CPUWeight=200",
        "AllowedCPUs=0-1",
        "IOWeight=50",
        "LimitNOFILE=100000",
        "LimitNPROC=4096",
        "LimitMEMLOCK=infinity",
        "LimitCORE=0",
    ]


def test_nothing_set_is_no_tuning_and_writes_no_line() -> None:
    tuning = unit_tuning.validate({})

    assert tuning.is_empty() and unit_tuning.directives(tuning) == []
    assert tuning.to_dict() == {}


def test_the_options_come_back_as_they_went_in() -> None:
    options = {
        "memory_high_mb": 256,
        "capabilities": ["NET_BIND_SERVICE"],
        "devices": [{"path": "/dev/nvidia0", "mode": "rw"}],
    }

    assert unit_tuning.validate(options).to_dict() == options


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("memory_high_mb", 8),
        ("memory_swap_max_mb", -1),
        ("cpu_weight", 0),
        ("cpu_weight", 10001),
        ("io_weight", 0),
        ("limit_nofile", 10),
        ("limit_nofile", 2_000_000),
        ("limit_nproc", 1),
        ("limit_memlock_mb", -5),
        ("limit_memlock_mb", "lots"),
    ],
)
def test_a_value_out_of_its_range_is_refused_naming_the_option(name: str, value: object) -> None:
    with pytest.raises(ValidationError) as caught:
        unit_tuning.validate({name: value})

    assert caught.value.field == name


@pytest.mark.parametrize("value", [True, 1.5, "7", [1]])
def test_a_number_that_is_not_a_whole_number_is_refused(value: object) -> None:
    with pytest.raises(ValidationError, match="whole number"):
        unit_tuning.validate({"cpu_weight": value})


def test_an_unknown_option_is_refused_with_the_ones_there_are() -> None:
    with pytest.raises(ValidationError, match="shm_size") as caught:
        unit_tuning.validate({"shm_size": 64})

    assert "memory_high_mb" in caught.value.details


def test_core_dumps_are_a_yes_or_a_no() -> None:
    with pytest.raises(ValidationError, match="true or false"):
        unit_tuning.validate({"core_dumps": "yes"})
    assert "LimitCORE=infinity" in unit_tuning.directives(
        unit_tuning.validate({"core_dumps": True})
    )


@pytest.mark.parametrize("value", ["0-3", "0,2", "0-1,3", " 0 - 1 "])
def test_a_set_of_cpus_the_machine_has_is_accepted(value: str) -> None:
    assert unit_tuning.check_cpu_set(value, cpus=4) == value.replace(" ", "")


@pytest.mark.parametrize("value", ["", "a", "3-1", "0-8", "4", "0;1", "-1", 3])
def test_a_set_of_cpus_that_is_not_one_or_that_the_machine_lacks_is_refused(value: object) -> None:
    with pytest.raises(ValidationError) as caught:
        unit_tuning.check_cpu_set(value, cpus=4)

    assert caught.value.field == "allowed_cpus"


def test_a_soft_memory_limit_above_the_hard_one_is_refused() -> None:
    app = App(domain=DOMAIN, memory_max_mb=512)

    unit_tuning.check_against_limits(unit_tuning.validate({"memory_high_mb": 512}), app)
    with pytest.raises(ValidationError, match="above the hard one") as caught:
        unit_tuning.check_against_limits(unit_tuning.validate({"memory_high_mb": 600}), app)

    assert "noust app limits" in caught.value.details


def test_a_soft_limit_with_no_hard_one_is_fine() -> None:
    unit_tuning.check_against_limits(
        unit_tuning.validate({"memory_high_mb": 600}), App(domain=DOMAIN)
    )


# ---------------------------------------------------------------------------
# Capabilities and devices
# ---------------------------------------------------------------------------


def test_a_capability_is_given_to_the_bounding_set_and_to_the_process() -> None:
    tuning = unit_tuning.validate({"capabilities": ["net_bind_service", "CAP_IPC_LOCK"]})

    assert tuning.capabilities == ("NET_BIND_SERVICE", "IPC_LOCK")
    assert unit_tuning.directives(tuning) == [
        "CapabilityBoundingSet=CAP_NET_BIND_SERVICE CAP_IPC_LOCK",
        "AmbientCapabilities=CAP_NET_BIND_SERVICE CAP_IPC_LOCK",
    ]


@pytest.mark.parametrize("name", ["SYS_ADMIN", "SYS_PTRACE", "DAC_OVERRIDE", "SETUID", "ALL", ""])
def test_a_root_equivalent_capability_is_not_offered(name: str) -> None:
    with pytest.raises(ValidationError, match="not a capability Noust gives") as caught:
        unit_tuning.validate({"capabilities": [name]})

    assert "custom unit" in caught.value.details


def test_a_capability_listed_twice_is_given_once() -> None:
    assert unit_tuning.check_capabilities(["IPC_LOCK", "ipc_lock"]) == ("IPC_LOCK",)


def test_a_device_turns_the_private_dev_off_and_is_the_only_one_allowed() -> None:
    tuning = unit_tuning.validate(
        {"devices": [{"path": "/dev/nvidia0", "mode": "rw"}, {"path": "char-drm", "mode": "r"}]}
    )

    assert unit_tuning.directives(tuning) == [
        "PrivateDevices=false",
        "DevicePolicy=closed",
        "DeviceAllow=/dev/nvidia0 rw",
        "DeviceAllow=char-drm r",
    ]


def test_a_device_may_be_written_as_a_path_and_a_mode() -> None:
    assert unit_tuning.check_devices(["/dev/dri/renderD128:r", "/dev/kvm"]) == (
        unit_tuning.Device("/dev/dri/renderD128", "r"),
        unit_tuning.Device("/dev/kvm", "rw"),
    )


@pytest.mark.parametrize(
    "device",
    [
        "/etc/passwd",
        "/dev/../etc/shadow",
        "/dev/ nvidia",
        "/dev/",
        "nvidia0",
        "char-",
        "char-NVIDIA",
        "/dev/x\nDeviceAllow=/dev/mem rw",
        {"path": "/dev/null", "mode": "rwm"},
        {"path": "/dev/null", "mode": "x"},
        5,
    ],
)
def test_a_device_that_is_not_a_node_or_a_group_is_refused(device: object) -> None:
    with pytest.raises(ValidationError) as caught:
        unit_tuning.check_devices([device])

    assert caught.value.field == "devices"


def test_every_gpu_preset_is_made_of_device_groups() -> None:
    for groups in unit_tuning.GPU_PRESETS.values():
        assert unit_tuning.check_devices(list(groups))


# ---------------------------------------------------------------------------
# One spelling: the template and the edit
# ---------------------------------------------------------------------------


def lines_for(**options: Any) -> list[str]:
    return unit_tuning.directives(unit_tuning.validate(options, cpus=8))


def test_the_template_writes_the_block_after_everything_it_overrides() -> None:
    sandbox = unit_hardening.directives_for("default", family="node", app_root=ROOT)
    unit = render(lines_for(devices=["char-drm"], limit_nofile=1000), sandbox)

    block = unit.index("# Tuning: limits and options")
    assert unit.index("PrivateDevices=true") < unit.index("PrivateDevices=false")
    assert unit.index("MemoryMax=512M") < block < unit.index("[Install]")
    assert unit.index("LimitNOFILE=65535") < unit.index("LimitNOFILE=1000")


def test_a_unit_without_tuning_has_no_block() -> None:
    assert "Tuning" not in render([])


def test_editing_a_unit_gives_what_rendering_it_would() -> None:
    """In either direction, and every time: applying twice changes nothing."""
    lines = lines_for(memory_high_mb=256, capabilities=["NET_BIND_SERVICE"])

    assert unit_tuning.with_tuning(render([]), lines) == render(lines)
    assert unit_tuning.with_tuning(render(lines), []) == render([])
    once = unit_tuning.with_tuning(render([]), lines)
    assert unit_tuning.with_tuning(once, lines) == once


def test_changing_the_options_replaces_the_block() -> None:
    first = lines_for(memory_high_mb=256, cpu_weight=50)
    second = lines_for(allowed_cpus="0")

    assert unit_tuning.with_tuning(render(first), second) == render(second)


def test_the_template_default_of_a_ulimit_stays_when_the_tuning_goes() -> None:
    unit = unit_tuning.with_tuning(render(lines_for(limit_nofile=1000)), [])

    assert "LimitNOFILE=65535\n" in unit and "LimitNOFILE=1000" not in unit


def test_an_edited_unit_keeps_the_operators_own_lines() -> None:
    unit = (
        "# Generated by Noust\n[Service]\nExecStart=/bin/app\nNice=5\nMemoryHigh=1G\n\n[Install]\n"
        "WantedBy=multi-user.target\n"
    )

    edited = unit_tuning.with_tuning(unit, ["CPUWeight=20"])

    assert "Nice=5\n" in edited and "MemoryHigh=1G\n" in edited
    assert edited.index("CPUWeight=20") < edited.index("[Install]")


def test_a_unit_without_a_service_section_is_refused() -> None:
    with pytest.raises(ServiceError, match=r"\[Service\]"):
        unit_tuning.with_tuning("[Unit]\nDescription=x\n", ["CPUWeight=20"])


def test_the_blue_green_and_copy_templates_carry_the_block_too() -> None:
    env = Environment(  # noqa: S701 - unit files, escaped by their own macros
        loader=PackageLoader("noust", "templates/systemd"), trim_blocks=True, lstrip_blocks=True
    )
    for template, extra in (
        ("app@.service.j2", {"colors_directory": "/x/colors"}),
        ("replica@.service.j2", {"base": "shop-replica"}),
    ):
        text = env.get_template(template).render(
            name="shop",
            description="d",
            command="/usr/bin/true",
            working_directory="/srv",
            user="u",
            group="g",
            environment={},
            tuning=["CPUWeight=20"],
            **extra,
        )
        assert "CPUWeight=20" in text, template


def test_the_directives_pass_the_unit_values_check() -> None:
    from noust.validators.environment import validate_unit_value

    for line in lines_for(
        memory_high_mb=256,
        devices=["/dev/nvidia0:rw"],
        capabilities=["SYS_NICE"],
        allowed_cpus="0-3",
    ):
        validate_unit_value(line, field="Tuning")


# ---------------------------------------------------------------------------
# The service manager is the chokepoint
# ---------------------------------------------------------------------------


@pytest.fixture
def stored(store: Any) -> Any:
    """The service manager's fake store, holding the application."""
    app = App(domain=DOMAIN, app_type="nodejs", port=3100, app_path=str(ROOT), id=1)
    store.list_apps = lambda: [app]
    return store


def test_the_unit_of_a_tuned_application_is_written_tuned(
    manager: ServiceManager,
    unit_dirs: dict[str, Path],
    stored: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "noust.core.unit_tuning_store.get_record",
        lambda domain, **kw: tuning_store.TuningRecord({"cpu_weight": 20}),
    )

    manager.create_service(
        name="shop-example-com", command="/usr/bin/true", working_directory=str(ROOT / "current")
    )

    assert "CPUWeight=20" in (unit_dirs["managed"] / "shop-example-com.service").read_text()


def test_the_unit_of_an_application_never_tuned_is_written_as_it_was(
    manager: ServiceManager,
    unit_dirs: dict[str, Path],
    stored: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redeploy writes a new unit and must not tune what nobody asked to tune."""
    monkeypatch.setattr("noust.core.unit_tuning_store.get_record", lambda domain, **kw: None)

    manager.create_service(
        name="shop-example-com", command="/usr/bin/true", working_directory=str(ROOT / "current")
    )

    assert "Tuning" not in (unit_dirs["managed"] / "shop-example-com.service").read_text()


def test_a_unit_that_belongs_to_no_application_is_not_tuned(
    manager: ServiceManager, unit_dirs: dict[str, Path], stored: Any
) -> None:
    manager.create_service(name="worker", command="/usr/bin/true", working_directory="/srv")

    assert "Tuning" not in (unit_dirs["managed"] / "worker.service").read_text()


def test_the_blue_green_template_is_tuned_too(
    manager: ServiceManager,
    unit_dirs: dict[str, Path],
    stored: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "noust.core.unit_tuning_store.get_record",
        lambda domain, **kw: tuning_store.TuningRecord({"io_weight": 10}),
    )

    manager.install_instance_template(
        "shop-example-com",
        command="/usr/bin/true",
        colors_directory=str(ROOT / "colors"),
        environment={},
        environment_file=None,
        description="Noust: shop.example.com (nodejs)",
    )

    assert "IOWeight=10" in (unit_dirs["managed"] / "shop-example-com@.service").read_text()


def test_set_unit_tuning_rewrites_an_existing_unit_and_returns_the_old_one(
    manager: ServiceManager, unit_dirs: dict[str, Path], stored: Any
) -> None:
    manager.create_service(
        name="shop-example-com", command="/usr/bin/true", working_directory="/srv"
    )
    path = unit_dirs["managed"] / "shop-example-com.service"
    before = path.read_text()

    previous = manager.set_unit_tuning("shop-example-com", ["CPUWeight=20"])

    assert previous == before and "CPUWeight=20" in path.read_text()
    manager.set_unit_tuning("shop-example-com", [])
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
        manager.set_unit_tuning("shop-example-com", ["CPUWeight=1\nExecStartPre=/bin/evil"])

    assert path.read_text() == before


# ---------------------------------------------------------------------------
# What the store keeps
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_store(tmp_path: Path) -> Any:
    """A real store, with one application."""
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


def test_the_options_are_kept_and_read_back(sqlite_store: NoustStore) -> None:
    _app(sqlite_store)

    assert tuning_store.get_record(DOMAIN, store=sqlite_store) is None
    assert tuning_store.set_record(
        DOMAIN,
        {"cpu_weight": 20, "capabilities": ["IPC_LOCK"]},
        changed_by="ana",
        store=sqlite_store,
    )

    record = tuning_store.get_record(DOMAIN, store=sqlite_store)
    assert record is not None
    assert record.options == {"cpu_weight": 20, "capabilities": ["IPC_LOCK"]}
    assert record.changed_by == "ana" and record.changed_at


def test_an_application_that_does_not_exist_keeps_nothing(sqlite_store: NoustStore) -> None:
    assert (
        tuning_store.set_record(
            "ghost.example.com", {"cpu_weight": 5}, changed_by=None, store=sqlite_store
        )
        is False
    )


def test_setting_nothing_forgets_the_row(sqlite_store: NoustStore) -> None:
    _app(sqlite_store)
    tuning_store.set_record(DOMAIN, {"cpu_weight": 20}, changed_by="ana", store=sqlite_store)

    tuning_store.set_record(DOMAIN, {}, changed_by="ana", store=sqlite_store)

    assert tuning_store.get_record(DOMAIN, store=sqlite_store) is None


def test_the_row_goes_with_the_application_and_survives_a_redeploy(
    sqlite_store: NoustStore,
) -> None:
    app = _app(sqlite_store)
    tuning_store.set_record(DOMAIN, {"cpu_weight": 20}, changed_by="ana", store=sqlite_store)

    # A redeploy writes the whole row of the application again.
    app.port = 3101
    sqlite_store.update_app(app)
    assert tuning_store.get_record(DOMAIN, store=sqlite_store) is not None

    sqlite_store.delete_app(DOMAIN)
    with sqlite_store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) FROM app_unit_tuning")
        assert cursor.fetchone()[0] == 0


def test_a_stored_option_that_no_longer_validates_is_left_out_not_fatal() -> None:
    """The machine lost a CPU: every unit write must still work."""
    record = tuning_store.TuningRecord({"allowed_cpus": "0-99", "cpu_weight": 20})

    assert unit_tuning.from_record(record).to_dict() == {"cpu_weight": 20}


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

    def set_unit_tuning(self, name: str, lines: list[str]) -> str:
        if name == self.fail_on:
            raise ServiceError(f"Could not write {name}")
        previous = self.bodies[name]
        self.bodies[name] = "|".join(lines)
        return previous

    def update_config(self, name: str, content: str) -> str:
        previous = self.bodies[name]
        self.bodies[name] = content
        return previous

    def restart(self, name: str) -> None:
        self.restarts.append(name)


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
        return answer, "" if answer else "connection refused\njournal: cannot set affinity"


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the gate the restart builds; hand the test the answers it will give."""
    holder: dict[str, Any] = {"answers": [True]}

    def build(app: App, store: Any, log: Any, *, restart: Any = None) -> FakeGate:
        holder["gate"] = FakeGate(holder["answers"], restart)
        return holder["gate"]  # type: ignore[no-any-return]

    monkeypatch.setattr(hardening_engine, "health_gate_for", build)
    return holder


def test_the_units_are_rewritten_restarted_behind_the_gate_and_recorded(
    sqlite_store: NoustStore, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["shop-example-com"])

    change = engine.set_tuning(
        DOMAIN,
        {"memory_high_mb": 256},
        actor="ana",
        store=sqlite_store,
        services=units,  # type: ignore[arg-type]
    )

    assert change.restarted and change.options == {"memory_high_mb": 256}
    assert units.bodies["shop-example-com"] == "MemoryHigh=256M"
    assert units.restarts == ["shop-example-com"]
    record = tuning_store.get_record(DOMAIN, store=sqlite_store)
    assert record is not None and record.changed_by == "ana"


def test_an_application_that_does_not_answer_under_the_tuning_gets_the_old_unit_back(
    sqlite_store: NoustStore, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["shop-example-com"])
    gate["answers"] = [False, True]

    with pytest.raises(
        DeploymentError, match="new tuning; the previous one is running again"
    ) as caught:
        engine.set_tuning(DOMAIN, {"allowed_cpus": "0"}, store=sqlite_store, services=units)  # type: ignore[arg-type]

    assert units.bodies["shop-example-com"] == "original of shop-example-com"
    assert tuning_store.get_record(DOMAIN, store=sqlite_store) is None
    assert "cannot set affinity" in caught.value.details, "the probes, verbatim"


def test_the_previous_record_is_what_comes_back(
    sqlite_store: NoustStore, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    tuning_store.set_record(DOMAIN, {"cpu_weight": 30}, changed_by="ana", store=sqlite_store)
    units = FakeUnits(["shop-example-com"])
    gate["answers"] = [False, False]

    with pytest.raises(DeploymentError, match="not answering either"):
        engine.set_tuning(DOMAIN, {"io_weight": 5}, store=sqlite_store, services=units)  # type: ignore[arg-type]

    record = tuning_store.get_record(DOMAIN, store=sqlite_store)
    assert record is not None and record.options == {"cpu_weight": 30}
    assert record.changed_by == "ana"


def test_a_unit_that_cannot_be_written_leaves_every_unit_and_the_record_as_they_were(
    sqlite_store: NoustStore, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["a", "b"], fail_on="b")

    with pytest.raises(ServiceError):
        engine.set_tuning(DOMAIN, {"cpu_weight": 5}, store=sqlite_store, services=units)  # type: ignore[arg-type]

    assert units.bodies == {"a": "original of a", "b": "original of b"}
    assert tuning_store.get_record(DOMAIN, store=sqlite_store) is None
    assert units.restarts == []


def test_without_a_restart_the_unit_is_written_and_nothing_is_asked(
    sqlite_store: NoustStore, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["shop-example-com"])

    change = engine.set_tuning(
        DOMAIN,
        {"cpu_weight": 5},
        restart=False,
        store=sqlite_store,
        services=units,  # type: ignore[arg-type]
    )

    assert not change.restarted
    assert units.restarts == [] and "gate" not in gate


def test_an_empty_set_removes_the_tuning_and_the_row(
    sqlite_store: NoustStore, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    tuning_store.set_record(DOMAIN, {"cpu_weight": 30}, changed_by="ana", store=sqlite_store)
    units = FakeUnits(["shop-example-com"])

    engine.set_tuning(DOMAIN, {}, store=sqlite_store, services=units)  # type: ignore[arg-type]

    assert units.bodies["shop-example-com"] == ""
    assert tuning_store.get_record(DOMAIN, store=sqlite_store) is None


def test_a_stack_cannot_be_tuned_and_nothing_changes(
    sqlite_store: NoustStore, gate: dict[str, Any]
) -> None:
    _app(sqlite_store, domain="stack.example.com", app_type="docker-compose")
    units = FakeUnits(["stack"])

    with pytest.raises(ValidationError, match="cannot be tuned"):
        engine.set_tuning(
            "stack.example.com", {"cpu_weight": 5}, store=sqlite_store, services=units
        )  # type: ignore[arg-type]

    assert units.bodies == {"stack": "original of stack"}


def test_a_value_that_is_not_valid_is_refused_before_anything_is_written(
    sqlite_store: NoustStore, gate: dict[str, Any]
) -> None:
    _app(sqlite_store)
    units = FakeUnits(["shop-example-com"])

    with pytest.raises(ValidationError):
        engine.set_tuning(DOMAIN, {"cpu_weight": 0}, store=sqlite_store, services=units)  # type: ignore[arg-type]

    assert units.bodies == {"shop-example-com": "original of shop-example-com"}


def test_a_soft_limit_above_the_hard_one_is_refused_before_anything_is_written(
    sqlite_store: NoustStore, gate: dict[str, Any]
) -> None:
    _app(sqlite_store, memory_max_mb=256)
    units = FakeUnits(["shop-example-com"])

    with pytest.raises(ValidationError, match="above the hard one"):
        engine.set_tuning(DOMAIN, {"memory_high_mb": 512}, store=sqlite_store, services=units)  # type: ignore[arg-type]

    assert units.restarts == []


def test_an_unknown_application_is_refused(sqlite_store: NoustStore) -> None:
    with pytest.raises(Exception, match="not found"):
        engine.set_tuning("ghost.example.com", {}, store=sqlite_store)


def test_a_blue_green_application_is_written_once_into_its_template(
    sqlite_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app(sqlite_store, zero_downtime=True)
    units = FakeUnits(["shop-example-com@blue", "shop-example-com@green"])
    switched: list[str] = []
    monkeypatch.setattr(
        hardening_engine,
        "restart_blue_green",
        lambda app, store, log, put_back: switched.append(app.domain),
    )

    engine.set_tuning(DOMAIN, {"cpu_weight": 5}, store=sqlite_store, services=units)  # type: ignore[arg-type]

    assert units.bodies["shop-example-com@blue"] == "CPUWeight=5"
    assert units.bodies["shop-example-com@green"] == "original of shop-example-com@green"
    assert switched == [DOMAIN]


def test_the_status_of_an_application_never_tuned_says_nothing_is_set(
    sqlite_store: NoustStore,
) -> None:
    _app(sqlite_store)

    info = engine.status(DOMAIN, store=sqlite_store)

    assert (info.configured, info.eligible, info.options, info.directives) == (False, True, {}, ())
    assert "NET_BIND_SERVICE" in (info.capabilities or {})
    assert "nvidia" in (info.gpu_presets or {})


def test_the_status_of_a_stack_says_why_it_has_none(sqlite_store: NoustStore) -> None:
    _app(sqlite_store, domain="stack.example.com", app_type="docker-compose")

    info = engine.status("stack.example.com", store=sqlite_store)

    assert not info.eligible and "Docker" in (info.reason or "")


def test_the_status_shows_the_lines_the_unit_has(sqlite_store: NoustStore) -> None:
    _app(sqlite_store)
    tuning_store.set_record(DOMAIN, {"cpu_weight": 30}, changed_by="ana", store=sqlite_store)

    info = engine.status(DOMAIN, store=sqlite_store)

    assert info.configured and info.directives == ("CPUWeight=30",)


def test_a_change_is_audited(
    sqlite_store: NoustStore, gate: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _app(sqlite_store)
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        engine.audit, "record", lambda event, **kw: recorded.append((event, kw["details"]))
    )

    engine.set_tuning(
        DOMAIN,
        {"cpu_weight": 5},
        actor="ana",
        store=sqlite_store,
        services=FakeUnits(["shop-example-com"]),  # type: ignore[arg-type]
    )

    assert recorded[0][0] == "apps.tuning" and recorded[0][1]["by"] == "ana"
    assert recorded[0][1]["options"] == {"cpu_weight": 5}


def test_the_runner_never_leaves_the_tests(runner: FakeRunner) -> None:
    """The fakes above are the only machine: nothing here runs a process."""
    assert runner.calls == []
