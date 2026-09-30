# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the sampling plan: one answer per kind of application.

The collector, the API and the console used to work out separately which
applications could be measured and why not (the client even re-derived "static"
and "Compose" by hand, and a PHP application had no answer at all). These tests
pin the one place that decides. What is defended:

- **Every kind of application has a plan or a reason**: an in-place unit, a
  legacy ``wasm-*`` unit, a blue/green pair, a monorepo, a Compose stack, a
  PHP-FPM pool, a static site.
- **The cgroup path is asked of systemd**, not computed: a ``Slice=`` in a
  drop-in is honoured; the computed path is only the fallback when systemd
  cannot be asked.
- **systemd is asked once for every unit of every application.**
- **A reason is machine-readable, carries the fix and quotes the system**, and
  is never a paraphrase of what systemd said.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App
from noust.monitor import plan as plan_module
from noust.monitor.plan import (
    PLAN_PROPERTIES,
    PlanBuilder,
    find_access_log,
    is_cgroup_v2,
    target_status,
    unit_cgroup_path,
)

CONTAINER = "0123456789abcdef" * 4


class FakeManager:
    """Names units and answers ``systemctl show`` without systemd."""

    def __init__(
        self,
        units: dict[str, list[str]],
        described: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.units = units
        self.described = described or {}
        self.asked: list[tuple[list[str], tuple[str, ...]]] = []
        self.store = SimpleNamespace(list_services=lambda: [])

    def app_units(self, app: Any, **kwargs: Any) -> list[str]:
        return self.units.get(app.domain, [])

    def describe_units(
        self, names: list[str], properties: tuple[str, ...] = ()
    ) -> dict[str, dict[str, str]]:
        self.asked.append((list(names), tuple(properties)))
        return {name: self.described[name] for name in names if name in self.described}


@pytest.fixture
def mount(tmp_path: Path) -> Path:
    """A fake cgroup v2 mount."""
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpu memory\n")
    return root


def app(domain: str, **fields: Any) -> App:
    """An application row that was never stored."""
    values: dict[str, Any] = {
        "domain": domain,
        "app_type": "nextjs",
        "app_path": f"/var/www/apps/{domain.replace('.', '-')}",
        "webserver": "nginx",
    }
    values.update(fields)
    return App(**values)


def lay_unit(mount: Path, control_group: str, *, memory: int | None = 4096) -> Path:
    """Create a unit's cgroup directory with the files systemd gives it."""
    directory = mount / control_group.lstrip("/")
    directory.mkdir(parents=True, exist_ok=True)
    if memory is not None:
        (directory / "memory.current").write_text(f"{memory}\n")
    (directory / "cpu.stat").write_text("usage_usec 1\n")
    return directory


def running(unit: str, control_group: str, **extra: str) -> dict[str, str]:
    """What systemd says about a running unit."""
    return {
        "Id": f"{unit}.service",
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "ControlGroup": control_group,
        "MemoryAccounting": "yes",
        "CPUAccounting": "yes",
        **extra,
    }


def builder(
    mount: Path,
    manager: FakeManager,
    runner: FakeRunner | None = None,
) -> PlanBuilder:
    """A plan builder over the fake tree and the fake manager."""
    return PlanBuilder(runner=runner or FakeRunner(), cgroup_mount=mount, service_manager=manager)


# --------------------------------------------------------------------- units


def test_an_in_place_unit_is_read_where_systemd_says(mount: Path) -> None:
    """The plan points at the ControlGroup systemd reported: no path is computed."""
    lay_unit(mount, "/system.slice/shop-example-com.service")
    manager = FakeManager(
        {"shop.example.com": ["shop-example-com"]},
        {"shop-example-com": running("shop-example-com", "/system.slice/shop-example-com.service")},
    )

    plans = builder(mount, manager).build([app("shop.example.com")])

    plan = plans["shop.example.com"]
    assert plan.kind == "unit"
    assert plan.source == "cgroup"
    assert plan.reason is None and plan.measures_resources
    assert plan.targets[0].cgroup == mount / "system.slice/shop-example-com.service"


def test_a_custom_slice_in_a_drop_in_is_honoured(mount: Path) -> None:
    """The computed path would miss it; ControlGroup is authoritative."""
    lay_unit(mount, "/apps.slice/shop-example-com.service")
    manager = FakeManager(
        {"shop.example.com": ["shop-example-com"]},
        {"shop-example-com": running("shop-example-com", "/apps.slice/shop-example-com.service")},
    )

    plan = builder(mount, manager).build([app("shop.example.com")])["shop.example.com"]

    assert plan.reason is None
    assert plan.targets[0].cgroup == mount / "apps.slice/shop-example-com.service"


def test_a_legacy_prefixed_unit_has_its_own_kind(mount: Path) -> None:
    """Units from before 0.14.1 keep their wasm- prefix, and are still measured."""
    lay_unit(mount, "/system.slice/wasm-old-example-com.service")
    manager = FakeManager(
        {"old.example.com": ["wasm-old-example-com"]},
        {
            "wasm-old-example-com": running(
                "wasm-old-example-com", "/system.slice/wasm-old-example-com.service"
            )
        },
    )

    plan = builder(mount, manager).build([app("old.example.com")])["old.example.com"]

    assert plan.kind == "legacy"
    assert plan.reason is None


def test_a_blue_green_pair_is_both_instances_in_the_template_slice(mount: Path) -> None:
    """The instances sit in system-<prefix>.slice; the idle one may be stopped."""
    green = "/system.slice/system-bg\\x2dexample\\x2dcom.slice/bg-example-com@green.service"
    lay_unit(mount, green, memory=3000)
    manager = FakeManager(
        {"bg.example.com": ["bg-example-com@green", "bg-example-com@blue"]},
        {
            "bg-example-com@green": running("bg-example-com@green", green),
            "bg-example-com@blue": {
                "Id": "bg-example-com@blue.service",
                "LoadState": "loaded",
                "ActiveState": "inactive",
                "SubState": "dead",
                "ControlGroup": "",
            },
        },
    )

    plan = builder(mount, manager).build([app("bg.example.com", zero_downtime=True)])[
        "bg.example.com"
    ]

    assert plan.kind == "blue_green"
    assert plan.reason is None, "an idle instance stopped by design says nothing"
    assert [t.name for t in plan.targets] == ["bg-example-com@green", "bg-example-com@blue"]


def test_a_monorepo_is_the_sum_of_its_workspaces(mount: Path) -> None:
    """One unit per workspace; the plan carries them all."""
    for unit in ("mono-web", "mono-api"):
        lay_unit(mount, f"/system.slice/{unit}.service")
    manager = FakeManager(
        {"mono.example.com": ["mono-web", "mono-api"]},
        {unit: running(unit, f"/system.slice/{unit}.service") for unit in ("mono-web", "mono-api")},
    )

    plan = builder(mount, manager).build([app("mono.example.com", app_type="monorepo")])[
        "mono.example.com"
    ]

    assert plan.kind == "monorepo"
    assert len(plan.targets) == 2
    assert plan.reason is None


def test_systemd_is_asked_once_for_every_unit_of_every_application(mount: Path) -> None:
    """One systemctl show, not one per application: it runs every half minute."""
    manager = FakeManager({"a.example.com": ["a-example-com"], "b.example.com": ["b-example-com"]})

    builder(mount, manager).build([app("a.example.com"), app("b.example.com")])

    assert len(manager.asked) == 1
    names, properties = manager.asked[0]
    assert sorted(names) == ["a-example-com", "b-example-com"]
    assert properties == PLAN_PROPERTIES
    assert "ControlGroup" in properties


# ------------------------------------------------------------------- reasons


def test_a_stopped_unit_says_stopped_with_systemds_own_words(mount: Path) -> None:
    """The reason quotes ActiveState and when it went inactive."""
    manager = FakeManager(
        {"shop.example.com": ["shop-example-com"]},
        {
            "shop-example-com": {
                "Id": "shop-example-com.service",
                "LoadState": "loaded",
                "ActiveState": "inactive",
                "SubState": "dead",
                "ControlGroup": "",
                "InactiveEnterTimestamp": "Tue 2026-09-29 03:12:00 UTC",
            }
        },
    )

    plan = builder(mount, manager).build([app("shop.example.com")])["shop.example.com"]

    assert plan.reason is not None and plan.reason.code == "stopped"
    assert plan.reason.fix == "noust service start shop-example-com"
    assert plan.reason.evidence is not None and "ActiveState=inactive" in plan.reason.evidence
    assert plan.reason.params["since"] == "Tue 2026-09-29 03:12:00 UTC"
    assert not plan.measures_resources


def test_a_failed_unit_points_at_the_journal(mount: Path) -> None:
    """A unit systemd gave up on is not 'stopped': the fix is the log."""
    manager = FakeManager(
        {"shop.example.com": ["shop-example-com"]},
        {
            "shop-example-com": {
                "Id": "shop-example-com.service",
                "LoadState": "loaded",
                "ActiveState": "failed",
                "ControlGroup": "",
            }
        },
    )

    reason = builder(mount, manager).build([app("shop.example.com")])["shop.example.com"].reason

    assert reason is not None and reason.code == "failed"
    assert reason.fix == "journalctl -u shop-example-com -n 50"


def test_a_unit_systemd_does_not_know_is_unit_missing(mount: Path) -> None:
    """LoadState=not-found: the fix is to redeploy, which writes the unit again."""
    manager = FakeManager(
        {"shop.example.com": ["shop-example-com"]},
        {
            "shop-example-com": {
                "Id": "shop-example-com.service",
                "LoadState": "not-found",
                "ActiveState": "inactive",
            }
        },
    )

    reason = builder(mount, manager).build([app("shop.example.com")])["shop.example.com"].reason

    assert reason is not None and reason.code == "unit_missing"
    assert "noust app update shop.example.com" in (reason.fix or "")


def test_an_application_with_no_recorded_unit_is_unit_missing(mount: Path) -> None:
    """Nothing to ask systemd about."""
    reason = (
        builder(mount, FakeManager({})).build([app("shop.example.com")])["shop.example.com"].reason
    )

    assert reason is not None and reason.code == "unit_missing"


def test_accounting_off_is_named_with_its_fix(mount: Path) -> None:
    """The cgroup is there but memory.current is not: the controller is not counting."""
    lay_unit(mount, "/system.slice/shop-example-com.service", memory=None)
    manager = FakeManager(
        {"shop.example.com": ["shop-example-com"]},
        {
            "shop-example-com": running(
                "shop-example-com",
                "/system.slice/shop-example-com.service",
                MemoryAccounting="no",
            )
        },
    )

    reason = builder(mount, manager).build([app("shop.example.com")])["shop.example.com"].reason

    assert reason is not None and reason.code == "accounting_off"
    assert "MemoryAccounting=yes" in (reason.fix or "")
    assert reason.evidence is not None and "MemoryAccounting=no" in reason.evidence


def test_a_running_unit_whose_cgroup_is_missing_says_so(mount: Path) -> None:
    """Active, but the directory systemd named is not on this filesystem."""
    manager = FakeManager(
        {"shop.example.com": ["shop-example-com"]},
        {"shop-example-com": running("shop-example-com", "/system.slice/shop-example-com.service")},
    )

    reason = builder(mount, manager).build([app("shop.example.com")])["shop.example.com"].reason

    assert reason is not None and reason.code == "cgroup_missing"


def test_a_host_without_the_unified_hierarchy_says_cgroup_v1(tmp_path: Path) -> None:
    """Noust reads cgroup v2; on v1 or hybrid it says that instead of drawing nothing."""
    v1 = tmp_path / "v1"
    v1.mkdir()
    manager = FakeManager(
        {"shop.example.com": ["shop-example-com"]},
        {"shop-example-com": running("shop-example-com", "/system.slice/shop-example-com.service")},
    )

    reason = builder(v1, manager).build([app("shop.example.com")])["shop.example.com"].reason

    assert not is_cgroup_v2(v1)
    assert reason is not None and reason.code == "cgroup_v1"


def test_when_systemd_cannot_be_asked_the_computed_path_is_the_fallback(mount: Path) -> None:
    """No answer from systemctl: the path is computed, and a readable one still measures."""
    lay_unit(mount, "/system.slice/shop-example-com.service")
    manager = FakeManager({"shop.example.com": ["shop-example-com"]}, {})

    plan = builder(mount, manager).build([app("shop.example.com")])["shop.example.com"]

    assert plan.reason is None
    assert plan.targets[0].cgroup == unit_cgroup_path(mount / "system.slice", "shop-example-com")


def test_the_computed_path_escapes_a_template_prefix(tmp_path: Path) -> None:
    """systemd puts <prefix>@<instance> in system-<escaped prefix>.slice; a dash is \\x2d."""
    assert unit_cgroup_path(tmp_path, "shop-example-com") == tmp_path / "shop-example-com.service"
    assert unit_cgroup_path(tmp_path, "bg-example-com@green") == (
        tmp_path / "system-bg\\x2dexample\\x2dcom.slice" / "bg-example-com@green.service"
    )


# -------------------------------------------------------------------- static


def test_a_static_site_has_no_process_and_its_traffic_is_counted(
    mount: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason is 'static' with nothing to fix; the access log is the data."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "docs.example.com.access.log").write_text("")
    monkeypatch.setattr(plan_module, "ACCESS_LOG_DIRS", {"nginx": (logs,)})

    plan = builder(mount, FakeManager({})).build(
        [app("docs.example.com", app_type="static", is_static=True)]
    )["docs.example.com"]

    assert plan.kind == "static"
    assert plan.source == "none"
    assert plan.reason is not None and plan.reason.code == "static"
    assert plan.reason.fix is None
    assert plan.traffic_log == logs / "docs.example.com.access.log"
    assert plan.traffic_reason is None


def test_a_static_site_without_a_log_says_where_it_looked(
    mount: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The access log is created on the first request after a reload: not there yet."""
    monkeypatch.setattr(plan_module, "ACCESS_LOG_DIRS", {"nginx": (tmp_path / "empty",)})

    plan = builder(mount, FakeManager({})).build(
        [app("docs.example.com", app_type="static", is_static=True)]
    )["docs.example.com"]

    assert plan.traffic_log is None
    assert plan.traffic_reason is not None and plan.traffic_reason.code == "access_log_missing"
    assert "docs.example.com.access.log" in (plan.traffic_reason.evidence or "")


def test_an_apache_site_is_found_in_either_distribution_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Debian writes /var/log/apache2, RHEL /var/log/httpd; the file name is the same."""
    debian, rhel = tmp_path / "apache2", tmp_path / "httpd"
    rhel.mkdir()
    (rhel / "docs.example.com.access.log").write_text("")
    monkeypatch.setattr(plan_module, "ACCESS_LOG_DIRS", {"apache": (debian, rhel)})

    assert find_access_log("docs.example.com", "apache") == rhel / "docs.example.com.access.log"
    assert find_access_log("other.example.com", "apache") is None


# ------------------------------------------------------------------- Compose


def test_a_compose_stack_is_the_cgroup_of_each_container(mount: Path) -> None:
    """Per container through the runner, not through the oneshot unit."""
    lay_unit(mount, f"/system.slice/docker-{CONTAINER}.scope")
    runner = FakeRunner().script(["docker", "ps"], stdout=f"{CONTAINER}\tstack-web-1\n")

    plan = builder(mount, FakeManager({}), runner).build(
        [app("stack.example.com", app_type="docker-compose")]
    )["stack.example.com"]

    assert plan.kind == "compose"
    assert plan.source == "docker"
    assert plan.reason is None
    assert plan.targets[0].kind == "container"
    assert plan.targets[0].name == "stack-web-1"
    assert plan.targets[0].cgroup == mount / f"system.slice/docker-{CONTAINER}.scope"
    ps = next(call for call in runner.calls if call[:2] == ("docker", "ps"))
    assert "--no-trunc" in ps
    assert any("com.docker.compose.project.working_dir=" in part for part in ps)


def test_a_compose_stack_under_the_cgroupfs_driver_is_found_too(mount: Path) -> None:
    """Docker without the systemd cgroup driver puts containers in docker/<id>."""
    lay_unit(mount, f"/docker/{CONTAINER}")
    runner = FakeRunner().script(["docker", "ps"], stdout=f"{CONTAINER}\tstack-web-1\n")

    plan = builder(mount, FakeManager({}), runner).build(
        [app("stack.example.com", app_type="docker-compose")]
    )["stack.example.com"]

    assert plan.reason is None
    assert plan.targets[0].cgroup == mount / f"docker/{CONTAINER}"


def test_a_compose_stack_without_docker_says_so(mount: Path) -> None:
    """No docker command: the reason, not an empty chart."""
    runner = FakeRunner().only_knows("systemctl")

    plan = builder(mount, FakeManager({}), runner).build(
        [app("stack.example.com", app_type="docker-compose")]
    )["stack.example.com"]

    assert plan.reason is not None and plan.reason.code == "compose_docker_unavailable"


def test_a_docker_daemon_that_does_not_answer_is_quoted_verbatim(mount: Path) -> None:
    """The daemon's own error is the evidence."""
    runner = FakeRunner().script(
        ["docker", "ps"],
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
        exit_code=1,
    )

    plan = builder(mount, FakeManager({}), runner).build(
        [app("stack.example.com", app_type="docker-compose")]
    )["stack.example.com"]

    assert plan.reason is not None and plan.reason.code == "compose_docker_unavailable"
    assert (
        plan.reason.evidence == "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
    )


def test_a_stack_with_no_running_container_is_stopped(mount: Path) -> None:
    """Nothing runs, so nothing is measured, and the fix is to start it."""
    runner = FakeRunner().script(["docker", "ps"], stdout="")

    plan = builder(mount, FakeManager({}), runner).build(
        [app("stack.example.com", app_type="docker-compose")]
    )["stack.example.com"]

    assert plan.reason is not None and plan.reason.code == "stopped"


def test_containers_whose_cgroups_are_not_where_docker_puts_them_say_so(mount: Path) -> None:
    """Running, but unreadable: the reason lists where it looked."""
    runner = FakeRunner().script(["docker", "ps"], stdout=f"{CONTAINER}\tstack-web-1\n")

    plan = builder(mount, FakeManager({}), runner).build(
        [app("stack.example.com", app_type="docker-compose")]
    )["stack.example.com"]

    assert plan.reason is not None and plan.reason.code == "compose_cgroup_unreadable"
    assert f"docker-{CONTAINER}.scope" in (plan.reason.evidence or "")
    assert plan.reason.fix == "Use 'docker stats' for their usage."


# ------------------------------------------------------------------- PHP-FPM


@pytest.fixture
def fpm(mount: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Pretend PHP 8.3's FPM is installed; return its master's cgroup."""
    from noust.deployers.helpers.php_fpm import FpmInstallation

    pools = tmp_path / "pool.d"
    pools.mkdir()
    installation = FpmInstallation(
        pool_dir=pools,
        service="php8.3-fpm",
        binary="/usr/sbin/php-fpm8.3",
        socket_dir=tmp_path / "run",
        version="8.3",
    )
    monkeypatch.setattr(
        "noust.deployers.helpers.php_fpm.find_fpm", lambda root=Path("/"): installation
    )
    return lay_unit(mount, "/system.slice/php8.3-fpm.service")


def test_a_php_application_is_its_pool_inside_the_shared_fpm_service(
    mount: Path, fpm: Path
) -> None:
    """The plan names the pool and points at the FPM master's cgroup."""
    manager = FakeManager(
        {},
        {"php8.3-fpm": running("php8.3-fpm", "/system.slice/php8.3-fpm.service")},
    )

    plan = builder(mount, manager).build(
        [app("blog.example.com", app_type="php-fpm", is_static=True)]
    )["blog.example.com"]

    assert plan.kind == "php_fpm"
    assert plan.source == "fpm"
    assert plan.pool == "noust-blog-example-com"
    assert plan.fpm_service == "php8.3-fpm"
    assert plan.reason is None
    assert plan.targets[0].cgroup == fpm


def test_a_stopped_fpm_service_is_stopped_for_every_pool(mount: Path, fpm: Path) -> None:
    """No master, no workers: the reason names the service to start."""
    manager = FakeManager(
        {},
        {
            "php8.3-fpm": {
                "Id": "php8.3-fpm.service",
                "LoadState": "loaded",
                "ActiveState": "inactive",
                "ControlGroup": "",
            }
        },
    )

    plan = builder(mount, manager).build(
        [app("blog.example.com", app_type="php-fpm", is_static=True)]
    )["blog.example.com"]

    assert plan.reason is not None and plan.reason.code == "stopped"
    assert plan.reason.fix == "systemctl start php8.3-fpm"


def test_without_php_fpm_installed_the_reason_says_so(
    mount: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PHP application on a server that has no FPM."""
    from noust.core.exceptions import DeploymentError

    def none(root: Path = Path("/")) -> Any:
        raise DeploymentError("PHP-FPM is not installed", details="apt install php-fpm")

    monkeypatch.setattr("noust.deployers.helpers.php_fpm.find_fpm", none)

    plan = builder(mount, FakeManager({})).build(
        [app("blog.example.com", app_type="php-fpm", is_static=True)]
    )["blog.example.com"]

    assert plan.reason is not None and plan.reason.code == "php_fpm_missing"


# ---------------------------------------------------------------------- misc


def test_a_target_reports_what_the_files_say_now(mount: Path) -> None:
    """The API's per-unit view reads the cgroup at the moment it is asked."""
    directory = lay_unit(mount, "/system.slice/shop-example-com.service", memory=8192)
    manager = FakeManager(
        {"shop.example.com": ["shop-example-com"]},
        {"shop-example-com": running("shop-example-com", "/system.slice/shop-example-com.service")},
    )
    plan = builder(mount, manager).build([app("shop.example.com")])["shop.example.com"]

    status = target_status(plan.targets[0])

    assert status.cgroup_exists and status.cpu_stat
    assert status.memory_current == 8192
    assert status.control_group == "/system.slice/shop-example-com.service"
    assert status.active_state == "active"

    (directory / "memory.current").unlink()
    assert target_status(plan.targets[0]).memory_current is None


def test_a_reason_serialises_for_the_api() -> None:
    """The console reads code, message, fix, evidence and params, nothing else."""
    reason = plan_module.Reason(
        code="stopped", message="m", fix="f", evidence="e", params={"unit": "u"}
    )

    assert reason.to_dict() == {
        "code": "stopped",
        "message": "m",
        "fix": "f",
        "evidence": "e",
        "params": {"unit": "u"},
    }


def test_a_row_without_a_domain_is_not_planned(mount: Path) -> None:
    """A half-written row must not put an empty key in the plans."""
    plans = builder(mount, FakeManager({})).build([app("")])

    assert plans == {}
