# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a server needs, declared once and judged by the doctor (checklist N-04).

The sizes are ``noust.managers.server.requirements``. The installer carries a generated copy and
the requirements document shows the same table, so what is pinned here is that the three agree,
that the three levels mean what they say, and that ``noust setup doctor`` turns a measurement
into the right status, the right command and the right holder of ports 80 and 443.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.deployers import compose_relay
from noust.managers import setup_doctor
from noust.managers.server import requirements
from noust.managers.server.requirements import (
    CPU,
    DISK,
    MEMORY,
    DiskFree,
    PortHolder,
    Resources,
)
from noust.managers.setup_doctor import DoctorEnvironment
from noust.validators import ssh as ssh_module

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "packaging" / "install.sh"
DOCUMENT = ROOT / "docs" / "requirements.md"


# ---------------------------------------------------------------------------
# The sizes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("requirement", requirements.REQUIREMENTS, ids=lambda r: r.key)
def test_each_resource_has_three_levels_that_do_not_run_backwards(
    requirement: requirements.Requirement,
) -> None:
    assert 0 < requirement.floor <= requirement.minimum <= requirement.recommended


def test_a_server_of_a_gigabyte_is_inside_the_minimum() -> None:
    """The claim made in the README: it fits in one gigabyte."""
    assert MEMORY.minimum <= 1024


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "ok"),
        (MEMORY.floor - 1, "below-floor"),
        (MEMORY.floor, "below-minimum"),
        (MEMORY.minimum - 1, "below-minimum"),
        (MEMORY.minimum, "ok"),
        (MEMORY.recommended * 4, "ok"),
    ],
)
def test_a_value_is_judged_against_the_floor_and_the_minimum(
    value: int | None, expected: str
) -> None:
    assert requirements.verdict(MEMORY, value) == expected


def test_a_machine_that_cannot_be_measured_is_not_refused_for_it() -> None:
    assert requirements.verdict(DISK, None) == "ok"


# ---------------------------------------------------------------------------
# Reading the machine
# ---------------------------------------------------------------------------


def test_the_memory_is_the_kernels_total_in_megabytes(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        2097152 kB\nMemFree: 1 kB\nSwapTotal:      1048576 kB\n")

    found = requirements.read_resources([tmp_path], meminfo=meminfo, cgroup_limits=[])

    assert found.memory_mb == 2048
    assert found.swap_mb == 1024


def test_a_container_limit_lower_than_the_hosts_memory_is_what_the_machine_has(
    tmp_path: Path,
) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16777216 kB\nSwapTotal: 0 kB\n")
    limit = tmp_path / "memory.max"
    limit.write_text(f"{512 * 1024 * 1024}\n")

    found = requirements.read_resources([tmp_path], meminfo=meminfo, cgroup_limits=[limit])

    assert found.memory_mb == 512


@pytest.mark.parametrize("text", ["max\n", str(2**63 - 4096) + "\n"])
def test_no_container_limit_leaves_the_kernels_total(tmp_path: Path, text: str) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        4194304 kB\n")
    limit = tmp_path / "memory.max"
    limit.write_text(text)

    found = requirements.read_resources([tmp_path], meminfo=meminfo, cgroup_limits=[limit])

    assert found.memory_mb == 4096


def test_an_unreadable_meminfo_is_unknown_not_zero(tmp_path: Path) -> None:
    found = requirements.read_resources([tmp_path], meminfo=tmp_path / "missing", cgroup_limits=[])

    assert found.memory_mb is None
    assert found.swap_mb is None


def test_a_directory_that_does_not_exist_yet_is_judged_by_its_closest_parent(
    tmp_path: Path,
) -> None:
    found = requirements.read_resources(
        [tmp_path / "var" / "www" / "apps"], meminfo=tmp_path / "x", cgroup_limits=[]
    )

    assert len(found.disks) == 1
    assert found.disks[0].path == str(tmp_path / "var" / "www" / "apps")
    assert found.disks[0].free_mb > 0


def test_two_directories_on_one_filesystem_are_one_disk(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    found = requirements.read_resources(
        [tmp_path / "a", tmp_path / "b"], meminfo=tmp_path / "x", cgroup_limits=[]
    )

    assert len(found.disks) == 1


def test_there_is_at_least_one_processor() -> None:
    assert (requirements.read_resources([]).cpus or 0) >= 1


# ---------------------------------------------------------------------------
# Who holds the web ports
# ---------------------------------------------------------------------------


def holders(*listeners: tuple[int, tuple[str, ...]]) -> dict[int, PortHolder]:
    return {h.port: h for h in requirements.classify_port_holders(listeners)}


def test_nothing_listening_is_free() -> None:
    assert [h.kind for h in holders().values()] == ["free", "free"]


def test_nginx_on_both_ports_is_the_web_server() -> None:
    found = holders((80, ("nginx",)), (443, ("nginx",)))

    assert found[80].kind == found[443].kind == "web-server"


def test_apache_counts_as_a_web_server_too() -> None:
    assert holders((80, ("apache2",)))[80].kind == "web-server"
    assert holders((80, ("httpd",)))[80].kind == "web-server"


def test_a_proxy_of_another_platform_is_named() -> None:
    found = holders((80, ("caddy",)), (443, ("traefik",)))

    assert (found[80].kind, found[80].processes) == ("other", ("caddy",))
    assert (found[443].kind, found[443].processes) == ("other", ("traefik",))


def test_a_port_a_container_publishes_is_not_a_program_on_the_host() -> None:
    found = holders((80, ("docker-proxy",)))

    assert found[80].kind == "container"


def test_the_web_server_sharing_a_port_with_something_else_names_the_something_else() -> None:
    found = holders((80, ("nginx", "caddy")))

    assert (found[80].kind, found[80].processes) == ("other", ("caddy",))


def test_a_socket_the_kernel_will_not_name_is_still_somebody() -> None:
    found = holders((443, ()))

    assert found[443].kind == "other"
    assert found[443].processes == ("another process",)


# ---------------------------------------------------------------------------
# The doctor
# ---------------------------------------------------------------------------


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """A server the test describes: its size and what listens, with the rest in place."""
    monkeypatch.setattr(compose_relay, "leftover_relays", lambda: [])
    monkeypatch.setattr(ssh_module, "ssh_key_exists", lambda: (True, "/root/.ssh/id_ed25519"))
    apps, logs, config = tmp_path / "apps", tmp_path / "log", tmp_path / "etc" / "config.yaml"
    for directory in (apps, logs, config.parent):
        directory.mkdir(parents=True)
    config.write_text("webserver: nginx", encoding="utf-8")
    state: dict[str, object] = {
        "resources": Resources(
            memory_mb=2048,
            swap_mb=0,
            cpus=2,
            disks=(DiskFree("/var/www/apps", 30 * 1024, 40 * 1024),),
        ),
        "listeners": [(80, ("nginx",)), (443, ("nginx",))],
    }

    def run(argv: list[str]) -> object:
        class Result:
            stdout = "active\n"

        return Result()

    def environment() -> DoctorEnvironment:
        return DoctorEnvironment(
            command_exists=lambda name: True,
            run=run,
            install_hint="apt-get install -y",
            apps_dir=apps,
            log_dir=logs,
            config_path=config,
            resources=lambda: state["resources"],  # type: ignore[arg-type,return-value]
            listeners=lambda: state["listeners"],  # type: ignore[arg-type,return-value]
        )

    state["environment"] = environment
    return state


def section(machine: dict[str, object]) -> list[setup_doctor.DoctorItem]:
    environment = machine["environment"]
    assert callable(environment)
    return list(setup_doctor.resources_section(environment()).items)


def test_a_comfortable_server_has_nothing_to_say(machine: dict[str, object]) -> None:
    found = section(machine)

    assert [i.status for i in found] == ["ok", "ok", "ok", "ok", "ok"]
    assert found[0].text == "Memory: 2 GB (minimum 1 GB)"
    assert "30 GB free of 40 GB at /var/www/apps" in found[2].text


def test_the_section_comes_right_after_the_platform(
    machine: dict[str, object], runner: FakeRunner
) -> None:
    environment = machine["environment"]
    assert callable(environment)

    report = setup_doctor.collect(environment())

    assert [s.title for s in report.sections][:2] == ["Platform", "Resources"]


def test_memory_under_the_minimum_warns_and_offers_swap(machine: dict[str, object]) -> None:
    machine["resources"] = Resources(768, 0, 2, ((DiskFree("/x", 30000, 40000),)))

    memory = section(machine)[0]

    assert memory.status == "warn"
    assert memory.text == "Memory: 768 MB, under the 1 GB a server is sized for"
    assert memory.fix == "sudo noust server swap create"


def test_memory_under_the_floor_is_a_failure(machine: dict[str, object]) -> None:
    machine["resources"] = Resources(256, 0, 2, ((DiskFree("/x", 30000, 40000),)))

    memory = section(machine)[0]

    assert memory.status == "fail"
    assert "under the 512 MB Noust needs to run" in memory.text


def test_swap_is_shown_next_to_the_memory(machine: dict[str, object]) -> None:
    machine["resources"] = Resources(1024, 2048, 2, ((DiskFree("/x", 30000, 40000),)))

    assert section(machine)[0].text == "Memory: 1 GB, swap 2 GB (minimum 1 GB)"


def test_disk_under_the_minimum_warns_and_points_at_what_takes_it(
    machine: dict[str, object],
) -> None:
    machine["resources"] = Resources(2048, 0, 2, (DiskFree("/var/www/apps", 6 * 1024, 20 * 1024),))

    disk = section(machine)[2]

    assert disk.status == "warn"
    assert disk.fix == "noust server storage"
    assert "6 GB free of 20 GB at /var/www/apps" in disk.text


def test_disk_under_the_floor_is_a_failure(machine: dict[str, object]) -> None:
    machine["resources"] = Resources(2048, 0, 2, (DiskFree("/var/www/apps", 1024, 20 * 1024),))

    assert section(machine)[2].status == "fail"


def test_each_filesystem_noust_writes_to_is_judged_on_its_own(machine: dict[str, object]) -> None:
    machine["resources"] = Resources(
        2048,
        0,
        2,
        (
            DiskFree("/var/www/apps", 50 * 1024, 100 * 1024),
            DiskFree("/var/lib/noust", 1024, 2 * 1024),
        ),
    )

    statuses = [i.status for i in section(machine) if i.text.startswith("Free disk")]

    assert statuses == ["ok", "fail"]


def test_what_cannot_be_measured_is_said_not_failed(machine: dict[str, object]) -> None:
    machine["resources"] = Resources(None, None, None, ())

    found = section(machine)

    assert [i.status for i in found[:3]] == ["info", "info", "info"]
    assert all(i.status != "fail" for i in found)


def test_ports_free_are_information_not_a_warning(machine: dict[str, object]) -> None:
    machine["listeners"] = []

    ports = section(machine)[3:]

    assert [(i.status, i.text.split(":")[0]) for i in ports] == [
        ("info", "Port 80"),
        ("info", "Port 443"),
    ]


def test_another_proxy_on_the_port_is_named_with_the_command_that_stops_it(
    machine: dict[str, object],
) -> None:
    machine["listeners"] = [(80, ("caddy",)), (443, ("caddy",))]

    port = section(machine)[3]

    assert port.status == "warn"
    assert port.text == "Port 80: held by caddy, not by the web server"
    assert port.fix == "sudo systemctl disable --now caddy"


def test_a_container_publishing_the_port_says_how_to_find_it(machine: dict[str, object]) -> None:
    machine["listeners"] = [(80, ("docker-proxy",)), (443, ("docker-proxy",))]

    port = section(machine)[3]

    assert port.status == "warn"
    assert port.fix == "docker ps --filter publish=80"


def test_a_warning_here_counts_in_the_summary(
    machine: dict[str, object], runner: FakeRunner
) -> None:
    machine["listeners"] = [(80, ("caddy",))]
    environment = machine["environment"]
    assert callable(environment)

    report = setup_doctor.collect(environment())

    assert report.warnings == 1
    assert report.ready


def test_a_failure_here_makes_the_server_not_ready(
    machine: dict[str, object], runner: FakeRunner
) -> None:
    machine["resources"] = Resources(2048, 0, 2, (DiskFree("/x", 100, 1000),))
    environment = machine["environment"]
    assert callable(environment)

    assert not setup_doctor.collect(environment()).ready


def test_the_default_environment_reads_the_real_machine_through_the_runner(
    tmp_path: Path, runner: FakeRunner
) -> None:
    runner.script(
        ["ss", "-ltnpH"],
        stdout='LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=1,fd=6))\n'
        'LISTEN 0 511 0.0.0.0:443 0.0.0.0:* users:(("nginx",pid=1,fd=7))\n',
    )
    environment = DoctorEnvironment(apps_dir=tmp_path, data_dir=tmp_path)

    found = setup_doctor.resources_section(environment).items

    assert [i.status for i in found if i.text.startswith("Port")] == ["ok", "ok"]
    assert ("ss", "-ltnpH") in runner.calls


# ---------------------------------------------------------------------------
# The installer and the document carry the same numbers
# ---------------------------------------------------------------------------


def test_the_installer_carries_the_current_sizes() -> None:
    """Edit the requirements, run scripts/sync_install_targets.py: the script is never behind."""
    text = INSTALLER.read_text(encoding="utf-8")

    assert requirements.replace_installer_block(text) == text


def test_the_document_shows_the_current_sizes() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")

    assert requirements.replace_document_table(text) == text


def test_the_installer_block_names_a_floor_and_a_minimum_for_each_resource() -> None:
    block = requirements.installer_block()

    for name, requirement in (("MEMORY_MB", MEMORY), ("CPUS", CPU), ("DISK_MB", DISK)):
        assert f"REQ_{name}_FLOOR={requirement.floor}\n" in block
        assert f"REQ_{name}_MIN={requirement.minimum}\n" in block


def test_the_cpu_requirement_is_spelled_as_processors() -> None:
    assert requirements.size_text(CPU, 1) == "1 processor"
    assert requirements.size_text(CPU, 2) == "2 processors"
