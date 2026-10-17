#!/usr/bin/env python3
"""
Real-engine harness for Noust's database features.

Where ``run.py`` proves deploys and ``fleet_run.py`` proves the fleet, this runner proves what
only a real database engine can: that PostgreSQL archives its write-ahead log to the place
Noust says and can be recovered to an instant, that a replica follows its primary and can be
promoted, that an engine serves TLS with a certificate Noust installed and reloads it, that a
backup encrypted on the server's disk restores, and that an engine installs on a system that
is not Debian's.

It reuses ``run.py``'s systemd-under-Docker image and helpers, and lays two rules on top of it
for a machine that is also somebody's development workstation:

* **Everything it creates is named ``noust-test-ch-*`` and carries the label
  ``noust.test=1``**: the containers, the volume a container's own Docker keeps its data in,
  and every image it builds. It removes those by exact name, never by a prune, and never touches
  a container, volume, network or image it did not make.
* **It publishes no port on the host.** The databases run in a container's *own* Docker
  daemon (a daemon per data volume, never the host's ``docker.sock``), or on the container's own
  systemd, and are reached with ``docker exec``.

A scenario names the machine it runs on (``ubuntu``: Ubuntu 24.04 with systemd and a Docker of
its own; ``rocky9``; ``leap``; ``leap16``), and the runner starts a machine only when a selected
scenario needs it. Like the other runners it is not collected by pytest: it drives Docker.

A scenario that needs two servers (a primary and its replica) also asks for ``replica``, a second
systemd machine; both are put on one network, ``noust-test-ch-net``, under the names
``primary.noust.test`` and ``replica.noust.test``, and the scenario drives the second with
``peer(sc, "replica")`` (its commands are tagged ``[replica]`` in the evidence). The network is
removed with the machines. At most three containers are alive at once: run the machines of
different distributions in separate invocations.

What each scenario proves (the checklist row it answers):

* ``apt_postgresql``, ``rpm_el9``, ``rpm_leap16``, ``rpm_leap156`` (C-11): engines install,
  start and uninstall with apt, dnf and zypper, from the repositories whose keys are pinned.
* ``remote_only_backups`` (H-13): schedules that keep nothing on the server.
* ``pitr_postgresql`` (H-32): archived log, base backups, recovery to an instant, rollback,
  retention, and recovery from a destination.
* ``host_tls`` (C-27): PostgreSQL and MariaDB present a real certificate and load a renewal.
* ``sealed_backups`` (H-08): backups sealed on the server's disk, opened by one door.
* ``replication_postgresql``, ``replication_mariadb``, ``replication_mysql`` (C-14): a replica on
  another server, over TLS, with an account of its own.
* ``dragonfly_facts``: Dragonfly's snapshot format and ACL, the facts the guide states.

Usage:
    .venv/bin/python tests/integration/engines_run.py --list
    .venv/bin/python tests/integration/engines_run.py --scenario remote_only_backups
    .venv/bin/python tests/integration/engines_run.py --dev --keep    # the working tree, mounted
    .venv/bin/python tests/integration/engines_run.py --dev --attach --scenario rpm_el9
    .venv/bin/python tests/integration/engines_run.py --scenario replication_mariadb
    .venv/bin/python tests/integration/engines_run.py --purge         # remove everything it made

``--dev`` mounts the working tree read-only and imports Noust from it, so a change is tried
without building a wheel; every run that is reported as proof is a wheel run (the default).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]

#: Every name this runner creates starts with this, and every object carries the label.
#: ``NOUST_ENGINES_PREFIX`` names another family (``noust-test-w6d``) for a run that must not meet
#: the containers of another lane's run on the same machine.
PREFIX = os.environ.get("NOUST_ENGINES_PREFIX", "noust-test-ch")
LABEL = "noust.test=1"

#: The working tree as seen inside a container with ``--dev``.
SOURCE_MOUNT = "/src"

#: Where the engines' scenarios keep their scratch files inside a container.
SCRATCH = "/root/engines"


def _load_run() -> Any:
    """
    Load tests/integration/run.py as a module, without touching sys.path.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        "noust_integration_run", INTEGRATION_DIR / "run.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {INTEGRATION_DIR / 'run.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run = _load_run()
sh = run.sh
docker_exec = run.docker_exec
HarnessError = run.HarnessError
Scenario = run.Scenario
ScenarioSkipped = run.ScenarioSkipped


# ---------------------------------------------------------------------------
# The machines
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Machine:
    """
    A systemd container a scenario runs on.

    Attributes:
        key: The name a scenario gives (``ubuntu``).
        container: The container's name.
        image: The image it runs.
        python: The interpreter whose venv holds Noust.
        base: The distribution image to build ``image`` from; None when ``image`` exists
            already (the harness image of run.py).
        packages: The shell command that installs what Noust and the scenarios need on ``base``.
        inner_docker: Whether the container runs a Docker daemon of its own.
    """

    key: str
    container: str
    image: str
    python: str = "python3"
    base: str | None = None
    packages: str = ""
    inner_docker: bool = False

    @property
    def volume(self) -> str:
        """The Docker volume the container's own daemon keeps its data in."""
        return f"{PREFIX}-docker"


MACHINES: dict[str, Machine] = {
    "ubuntu": Machine(
        key="ubuntu",
        container=f"{PREFIX}-engines",
        image=run.IMAGE_TAG,
        inner_docker=True,
    ),
    # Rocky Linux 9 stands for the RHEL 9 family: AlmaLinux, RHEL and CentOS Stream 9 are
    # rebuilds of the same sources and share its repositories.
    "rocky9": Machine(
        key="rocky9",
        container=f"{PREFIX}-rocky9",
        image=f"{PREFIX}-rocky9:1",
        python="python3.12",
        base="rockylinux:9",
        packages=(
            "dnf -y --allowerasing update && dnf -y --allowerasing install systemd python3.12 python3.12-pip procps-ng iproute findutils which "
            "tar gzip xz curl ca-certificates gnupg2 openssl hostname glibc-langpack-en "
            "diffutils util-linux-core && dnf clean all"
        ),
    ),
    "leap": Machine(
        key="leap",
        container=f"{PREFIX}-leap",
        image=f"{PREFIX}-leap:1",
        python="python3.11",
        base="opensuse/leap:15.6",
        packages=(
            "zypper -n install --no-recommends systemd systemd-sysvinit python311 python311-pip "
            "procps iproute2 findutils which tar gzip xz curl ca-certificates gpg2 openssl "
            "hostname glibc-locale-base timezone util-linux && zypper clean -a"
        ),
    ),
    # Leap 16.0 is the supported openSUSE release (docs/PLATFORMS.md); 15.6 is past its end of life.
    "leap16": Machine(
        key="leap16",
        container=f"{PREFIX}-leap16",
        image=f"{PREFIX}-leap16:1",
        python="python3.13",
        base="opensuse/leap:16.0",
        packages=(
            "zypper -n install --no-recommends systemd python313 python313-pip procps iproute2 "
            "findutils which tar gzip xz curl ca-certificates gpg2 openssl hostname "
            "util-linux && zypper clean -a"
        ),
    ),
}

# A second server for the scenarios that need two (a primary and its replica): the same image,
# no Docker of its own.
MACHINES["replica"] = Machine(
    key="replica",
    container=f"{PREFIX}-replica",
    image=run.IMAGE_TAG,
)

#: The network the machines of a two-server scenario share, with the names they reach each other by.
NETWORK = f"{PREFIX}-net"
ALIASES = {"ubuntu": "primary.noust.test", "replica": "replica.noust.test"}

#: The machines this run started, so that the end of the run removes them.
_STARTED: list[Machine] = []


def exists(kind: str, name: str) -> bool:
    """
    Say whether a Docker object of this runner's exists.

    Args:
        kind: ``container``, ``volume``, ``image`` or ``network``.
        name: Its exact name.

    Returns:
        Whether it does.
    """
    return sh(["docker", kind, "inspect", name], timeout=30, check=False).returncode == 0


def join_network(machine: Machine) -> None:
    """
    Put a machine on the runner's network, under the name the other machines reach it by.

    The network is made once, labelled, and removed with the machines (or by ``--purge``); a
    machine already on it is left alone.

    Args:
        machine: The machine.
    """
    if not exists("network", NETWORK):
        sh(["docker", "network", "create", "--label", LABEL, NETWORK], timeout=30)
    attached = sh(
        ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", machine.container],
        timeout=30,
    ).stdout
    if NETWORK not in attached:
        sh(
            [
                "docker",
                "network",
                "connect",
                "--alias",
                ALIASES[machine.key],
                NETWORK,
                machine.container,
            ],
            timeout=30,
        )


def address_on_network(machine: Machine) -> str:
    """
    Args:
        machine: A machine on the runner's network.

    Returns:
        Its address there.
    """
    return sh(
        [
            "docker",
            "inspect",
            "-f",
            '{{(index .NetworkSettings.Networks "' + NETWORK + '").IPAddress}}',
            machine.container,
        ],
        timeout=30,
    ).stdout.strip()


def build_image(machine: Machine) -> None:
    """
    Build the systemd image of a distribution from its base, once.

    Args:
        machine: The machine whose ``base`` and ``packages`` say what to build.

    Raises:
        HarnessError: The build failed.
    """
    if machine.base is None or exists("image", machine.image):
        return
    print(f"[setup] building {machine.image} from {machine.base}")
    dockerfile = (
        f"FROM {machine.base}\n"
        f"RUN {machine.packages}\n"
        "RUN systemctl mask getty.target console-getty.service systemd-udevd.service "
        "systemd-udev-trigger.service systemd-udevd-kernel.socket systemd-udevd-control.socket "
        "systemd-modules-load.service systemd-random-seed.service sys-kernel-config.mount "
        "sys-kernel-debug.mount sys-kernel-tracing.mount systemd-remount-fs.service "
        "|| true\n"
        "STOPSIGNAL SIGRTMIN+3\n"
        'CMD ["/sbin/init"]\n'
    )
    proc = subprocess.run(
        ["docker", "build", "--label", LABEL, "-t", machine.image, "-"],
        input=dockerfile,
        capture_output=True,
        text=True,
        timeout=1200,
    )
    if proc.returncode != 0:
        raise HarnessError(
            f"the image {machine.image} did not build:\n{proc.stdout}\n{proc.stderr}"
        )


def start_machine(machine: Machine, *, dev: bool) -> None:
    """
    Start a systemd container, labelled, with the working tree mounted when asked.

    Args:
        machine: The machine.
        dev: Mount the working tree read-only at :data:`SOURCE_MOUNT`.

    Raises:
        HarnessError: The container exists already, or does not start.
    """
    if exists("container", machine.container):
        raise HarnessError(
            f"{machine.container} already exists: use --attach, or --purge to remove it"
        )
    build_image(machine)
    argv = [
        "docker",
        "run",
        "-d",
        "--name",
        machine.container,
        "--label",
        LABEL,
        "--privileged",
        "--cgroupns=host",
        "-v",
        "/sys/fs/cgroup:/sys/fs/cgroup:rw",
        "--tmpfs",
        "/run",
        "--tmpfs",
        "/run/lock",
    ]
    if machine.inner_docker:
        if not exists("volume", machine.volume):
            sh(["docker", "volume", "create", "--label", LABEL, machine.volume], timeout=30)
        argv += ["-v", f"{machine.volume}:/var/lib/docker"]
    if dev:
        argv += ["-v", f"{REPO_ROOT}:{SOURCE_MOUNT}:ro"]
    argv.append(machine.image)
    print(f"[setup] starting {machine.container}")
    sh(argv, timeout=60)
    _STARTED.append(machine)


def install_noust_dev(machine: Machine) -> None:
    """
    Install Noust's dependencies and import its code from the mounted working tree.

    The dependencies are read from ``pyproject.toml`` (the tree is mounted read-only, so it
    cannot be built in place), and ``noust`` becomes a path file that points at the mount: a
    change to the tree is the next ``noust`` the scenario runs.

    Args:
        machine: The machine.

    Raises:
        HarnessError: pip could not install the dependencies.
    """
    print(f"[setup] {machine.key}: installing the dependencies and pointing Noust at the tree")
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        project = tomllib.load(handle)["project"]
    requirements = [*project["dependencies"], *project["optional-dependencies"]["all"]]
    box = machine.container
    docker_exec(box, f"{machine.python} -m venv /opt/noust", timeout=60)
    docker_exec(box, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=180)
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            box,
            "/opt/noust/bin/pip",
            "install",
            "--quiet",
            "-r",
            "/dev/stdin",
        ],
        input="\n".join(requirements) + "\n",
        capture_output=True,
        text=True,
        timeout=run.PIP_INSTALL_TIMEOUT,
    )
    if proc.returncode != 0:
        raise HarnessError(f"pip could not install the dependencies:\n{proc.stderr}")
    site = docker_exec(
        box, "/opt/noust/bin/python -c 'import site; print(site.getsitepackages()[0])'", timeout=30
    ).stdout.strip()
    docker_exec(box, f"echo {SOURCE_MOUNT}/src > {site}/noust-dev.pth", timeout=15)
    wrapper = '#!/bin/sh\\nexec /opt/noust/bin/python -m noust "$@"\\n'
    for name in ("noust", "wasm"):
        docker_exec(
            box,
            f"printf '{wrapper}' > /usr/local/bin/{name} && chmod 755 /usr/local/bin/{name}",
            timeout=15,
        )
    print(docker_exec(box, "noust --version", timeout=30).stdout.strip())


def prepare(machine: Machine, *, dev: bool, wheel: Path | None, attach: bool) -> None:
    """
    Bring a machine to the point where a scenario can drive Noust on it.

    Args:
        machine: The machine.
        dev: Run Noust from the mounted tree.
        wheel: The wheel to install when not ``dev``.
        attach: Use the container a previous ``--keep`` left running.

    Raises:
        HarnessError: Any step failed.
    """
    box = machine.container
    if attach:
        if not exists("container", box):
            raise HarnessError(f"--attach: {box} is not there (run once with --dev --keep)")
    else:
        start_machine(machine, dev=dev)
        run.wait_for_systemd(box)
        if machine.inner_docker:
            run.prepare_inner_docker(box)
        if dev:
            install_noust_dev(machine)
        else:
            assert wheel is not None
            if machine.inner_docker:
                run.install_noust(box, wheel)
            else:
                install_noust_wheel(machine, wheel)
    docker_exec(box, f"mkdir -p {SCRATCH}", timeout=15)
    if machine.inner_docker and docker_exec(box, "test -d /root/fixtures", check=False).returncode:
        run.install_fixtures(box)


def install_noust_wheel(machine: Machine, wheel: Path) -> None:
    """
    Install the wheel into a venv, exactly as an operator would with pip.

    Args:
        machine: The machine.
        wheel: The wheel built from the working tree.
    """
    box = machine.container
    sh(["docker", "cp", str(wheel), f"{box}:/root/{wheel.name}"], timeout=60)
    docker_exec(box, f"{machine.python} -m venv /opt/noust", timeout=60)
    docker_exec(box, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=180)
    docker_exec(
        box,
        f"/opt/noust/bin/pip install --quiet '/root/{wheel.name}[all]'",
        timeout=run.PIP_INSTALL_TIMEOUT,
    )
    for name in ("noust", "wasm"):
        docker_exec(box, f"ln -sf /opt/noust/bin/{name} /usr/local/bin/{name}", timeout=15)


def remove_machine(machine: Machine) -> None:
    """
    Remove a machine's container, by its exact name.

    Args:
        machine: The machine.
    """
    if not exists("container", machine.container):
        return
    if machine.inner_docker:
        subprocess.run(
            [
                "docker",
                "exec",
                machine.container,
                "bash",
                "-lc",
                f"{run.INNER_DOCKER_CLEAR}; systemctl stop docker.socket docker.service",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    sh(["docker", "rm", "-f", machine.container], timeout=90, check=False)


def purge(*, images: bool) -> None:
    """
    Remove what this runner made, by exact name.

    Args:
        images: Also remove the volume of the container's own Docker (every image it pulled)
            and the distribution images this runner built.
    """
    for machine in MACHINES.values():
        remove_machine(machine)
    if exists("network", NETWORK):
        sh(["docker", "network", "rm", NETWORK], timeout=60, check=False)
    if not images:
        return
    for machine in MACHINES.values():
        if machine.inner_docker and exists("volume", machine.volume):
            sh(["docker", "volume", "rm", machine.volume], timeout=90, check=False)
        if machine.base is not None and exists("image", machine.image):
            sh(["docker", "rmi", machine.image], timeout=90, check=False)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

ScenarioFn = Callable[[Any], None]
SCENARIOS: list[tuple[str, str, ScenarioFn]] = []

#: The other machines a scenario needs besides the one it runs on, by scenario name.
ALSO: dict[str, tuple[str, ...]] = {}


def engines_scenario(
    name: str, *, machine: str = "ubuntu", also: tuple[str, ...] = ()
) -> Callable[[ScenarioFn], ScenarioFn]:
    """
    Register a scenario.

    Args:
        name: The name ``--scenario`` takes.
        machine: The key of the machine it runs on.
        also: The keys of other machines it needs; they are started, put on the runner's network
            and reachable with :func:`peer`.

    Returns:
        The decorator.
    """

    def decorator(fn: ScenarioFn) -> ScenarioFn:
        SCENARIOS.append((name, machine, fn))
        ALSO[name] = also
        return fn

    return decorator


def peer(sc: Any, key: str) -> Any:
    """
    Drive another machine of a two-server scenario.

    Args:
        sc: The scenario of the machine it runs on; the evidence is shared, in order.
        key: The machine's key (``replica``).

    Returns:
        A scenario bound to that machine's container, whose commands are tagged in the evidence.
    """
    other = Scenario(container=MACHINES[key].container, evidence=sc.evidence)
    other.tag = f"[{key}] "
    return other


def tag(sc: Any) -> str:
    """
    Args:
        sc: A scenario.

    Returns:
        The prefix its evidence lines carry: the machine's key for a peer, nothing otherwise.
    """
    return str(getattr(sc, "tag", ""))


def noust(sc: Any, arguments: str, *, timeout: int = 120, check: bool = True) -> Any:
    """
    Run the real CLI in the scenario's container.

    Args:
        sc: The scenario.
        arguments: Everything after ``noust``.
        timeout: Seconds it may take.
        check: Fail the scenario on a non-zero exit.

    Returns:
        The completed process.
    """
    return sc.run(
        f"noust {arguments}", timeout=timeout, check=check, label=f"{tag(sc)}noust {arguments}"
    )


def pull(sc: Any, image: str) -> None:
    """
    Pull an image into the container's own Docker once; it stays in the data volume.

    Args:
        sc: The scenario.
        image: The reference.

    Raises:
        ScenarioSkipped: The registry cannot be reached.
    """
    run.pull_images(sc, image)


def json_of(proc: Any, what: str) -> Any:
    """
    Args:
        proc: A finished command whose stdout is JSON.
        what: What it was, for the failure.

    Returns:
        The parsed output.

    Raises:
        AssertionError: The output is not JSON.
    """
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{what} did not print JSON: {proc.stdout[:300]!r}") from exc


@engines_scenario("smoke")
def scenario_smoke(sc: Any) -> None:
    """The container runs Noust, systemd and a Docker of its own."""
    version = noust(sc, "--version")
    sc.check("noust" in version.stdout.lower() or "wasm" in version.stdout.lower(), "version")
    sc.run("docker info --format '{{.ServerVersion}}'", label="the container's own Docker")
    sc.run("systemctl is-system-running || true", label="systemd")


@engines_scenario("smoke_rocky9", machine="rocky9")
def scenario_smoke_rocky9(sc: Any) -> None:
    """Rocky Linux 9 runs Noust and systemd."""
    noust(sc, "--version")
    sc.run("cat /etc/os-release | head -3; systemctl is-system-running || true", label="the OS")


@engines_scenario("smoke_leap", machine="leap")
def scenario_smoke_leap(sc: Any) -> None:
    """openSUSE Leap runs Noust and systemd."""
    noust(sc, "--version")
    sc.run("cat /etc/os-release | head -3; systemctl is-system-running || true", label="the OS")


#: How each SQL engine's own client reads a number out of a database, for the checks that the
#: data a backup holds is the data that comes back.
PG_COUNT = "runuser -u postgres -- psql -d {db} -tAc 'select count(*) from t'"
MY_COUNT = "mysql -N -e 'select count(*) from {db}.t'"


def engine_cycle(
    sc: Any,
    flavour: str,
    version: str | None,
    *,
    engine: str,
    expect_version: str,
    sql: str | None = None,
) -> None:
    """
    Install an engine with the system's package manager, use it through Noust, and remove it.

    The whole point of C-11 in one function: the plan is the one the catalog offers, the
    packages come from the real repositories, the engine starts, Noust manages it (a database,
    a dump, a restore of that dump into a new database with the data in it) and takes it out
    again with its data.

    Args:
        sc: The scenario.
        flavour: What to install (``postgresql``, ``mariadb``...).
        version: The version asked for; None for the distribution's own.
        engine: The engine's key (``postgresql``, ``mysql``).
        expect_version: What ``noust db status`` must say runs, as a prefix.
        sql: ``pg`` or ``my`` to also create a database, dump it and restore it.
    """
    flag = f" --version {version}" if version else ""
    # A run that stopped half way leaves its engine installed; start from none.
    noust(sc, f"db uninstall {engine} --purge -f", timeout=900, check=False)
    noust(sc, f"db install {flavour}{flag}", timeout=1800)
    status = noust(sc, f"db status {engine} --json", check=False)
    shown = status.stdout
    sc.check(expect_version in shown, f"noust db status should say {expect_version}: {shown[:400]}")
    if sql:
        noust(sc, f"db create shopdb --engine {engine}")
        if sql == "pg":
            sc.run(
                "runuser -u postgres -- psql -d shopdb -c "
                "'create table t(i int); insert into t select generate_series(1, 42)'"
            )
        else:
            sc.run(
                "mysql -e 'create table shopdb.t(i int); insert into shopdb.t values (1),(2),(3)'"
            )
        dump = f"{SCRATCH}/{flavour}-{version or 'default'}.dump"
        noust(sc, f"db backup shopdb --engine {engine} --no-compress --output {dump}")
        noust(sc, f"db restore shopdb {dump} --engine {engine} --as-new shopdb2 -f")
        count = sc.run((PG_COUNT if sql == "pg" else MY_COUNT).format(db="shopdb2"))
        sc.check(
            count.stdout.strip() == ("42" if sql == "pg" else "3"),
            f"the restored database should hold the data: {count.stdout!r}",
        )
        noust(sc, f"db drop shopdb --engine {engine} -f", check=False)
        noust(sc, f"db drop shopdb2 --engine {engine} -f", check=False)
    noust(sc, f"db uninstall {engine} --purge -f", timeout=900)
    gone = noust(sc, f"db status {engine} --json", check=False)
    sc.check('"running": true' not in gone.stdout, "the engine should be gone after uninstall")


@engines_scenario("apt_postgresql")
def scenario_apt_postgresql(sc: Any) -> None:
    """The same cycle on Ubuntu 24.04, which the RPM scenarios are measured against."""
    engine_cycle(sc, "postgresql", None, engine="postgresql", expect_version="16", sql="pg")


@engines_scenario("rpm_el9", machine="rocky9")
def scenario_rpm_el9(sc: Any) -> None:
    """C-11 on Rocky Linux 9: PostgreSQL, MariaDB, MySQL, Redis, Valkey and MongoDB install."""
    noust(sc, "db catalog")
    # (flavour, version asked, engine key, version it must run, SQL client or None)
    cycles: list[tuple[str, str | None, str, str, str | None]] = [
        ("postgresql", None, "postgresql", "13", "pg"),  # AppStream's plain package
        ("postgresql", "16", "postgresql", "16", "pg"),  # an AppStream module stream
        ("postgresql", "17", "postgresql", "17", "pg"),  # PGDG, with the pinned key
        ("postgresql", "18", "postgresql", "18", "pg"),
        ("mariadb", None, "mysql", "10.5", "my"),
        ("mariadb", "11.8", "mysql", "11.8", "my"),
        ("mariadb", "11.4", "mysql", "11.4", "my"),  # MariaDB's own repository
        ("mysql", "8.0", "mysql", "8.0", "my"),
        ("mysql", "8.4", "mysql", "8.4", "my"),
        ("redis", None, "redis", "6.2", None),
        ("redis", "7.2", "redis", "7.2", None),
        ("valkey", None, "redis", "8.0", None),
        ("mongodb", "8.0", "mongodb", "8.0", None),
    ]
    only = {name for name in os.environ.get("CH_ONLY", "").split(",") if name}
    for flavour, version, engine, expect, sql in cycles:
        if only and flavour not in only:
            continue
        engine_cycle(sc, flavour, version, engine=engine, expect_version=expect, sql=sql)


def leap_cycles(sc: Any, mariadb: str) -> None:
    """
    The cycles openSUSE Leap is covered for: PostgreSQL in several majors and MariaDB.

    Args:
        sc: The scenario.
        mariadb: The MariaDB the release ships.
    """
    noust(sc, "db catalog")
    cycles: list[tuple[str, str | None, str, str, str | None]] = [
        ("postgresql", None, "postgresql", "18", "pg"),  # the meta packages
        ("postgresql", "17", "postgresql", "17", "pg"),
        ("postgresql", "14", "postgresql", "14", "pg"),
        ("mariadb", None, "mysql", mariadb, "my"),
    ]
    only = {name for name in os.environ.get("CH_ONLY", "").split(",") if name}
    for flavour, version, engine, expect, sql in cycles:
        if only and flavour not in only:
            continue
        engine_cycle(sc, flavour, version, engine=engine, expect_version=expect, sql=sql)
    # What Leap does not cover is refused before anything is touched, with the way that works.
    for refused in ("mongodb", "valkey", "redis"):
        out = noust(sc, f"db install {refused}", check=False)
        sc.check(out.returncode != 0, f"{refused} is not installed on Leap by Noust yet")
        sc.check(
            "instance" in (out.stdout + out.stderr),
            f"the refusal of {refused} should name an instance: {out.stdout} {out.stderr}",
        )


@engines_scenario("rpm_leap16", machine="leap16")
def scenario_rpm_leap16(sc: Any) -> None:
    """C-11 on openSUSE Leap 16.0: PostgreSQL and MariaDB install with zypper."""
    leap_cycles(sc, "11.8")


@engines_scenario("rpm_leap156", machine="leap")
def scenario_rpm_leap156(sc: Any) -> None:
    """C-11 on openSUSE Leap 15.6 (past its end of life, its repositories still served)."""
    leap_cycles(sc, "10.11")


@engines_scenario("smoke_leap16", machine="leap16")
def scenario_smoke_leap16(sc: Any) -> None:
    """openSUSE Leap 16.0 runs Noust and systemd."""
    noust(sc, "--version")
    sc.run("cat /etc/os-release | head -3; systemctl is-system-running || true", label="the OS")


BK_DOMAIN = "bk.test"
BK_APP = "bk-test"
BK_REMOTE = "/srv/noust-test-ch-remote"


@engines_scenario("remote_only_backups")
def scenario_remote_only_backups(sc: Any) -> None:
    """
    H-13: schedules that keep nothing on the server, against a real rclone destination.

    An application's backup, an application's data and Noust's own state are each taken by their
    schedule, sent to a directory destination and checked on arrival, and then gone from the
    server's disk; the copy on the destination restores. With the destination unreachable the
    local archive is kept and the run fails.
    """
    sc.check(
        sc.run("rclone version | head -1", check=False).returncode == 0, "rclone is not installed"
    )
    create = f"noust create -d {BK_DOMAIN} -s /root/fixtures/static-site -t static --no-ssl"
    sc.run(create, timeout=run.DEPLOY_TIMEOUT, label=create)
    try:
        sc.run(f"rm -rf {BK_REMOTE} && mkdir -p {BK_REMOTE}")
        noust(sc, f"backup destination add chremote --type local --field path={BK_REMOTE}")

        # --- an application's own schedule
        refused = noust(
            sc, f"backup schedule create {BK_DOMAIN} --schedule daily --remote-only", check=False
        )
        sc.check(refused.returncode != 0, "remote-only without a destination must be refused")
        noust(
            sc,
            f"backup schedule create {BK_DOMAIN} --schedule daily --destination chremote "
            "--remote-only",
        )
        noust(sc, f"backup run-schedule {BK_DOMAIN}", timeout=300)
        local = sc.run(
            f"find /var/backups/noust -name '{BK_APP}_*.tar.gz' | wc -l", label="local archives"
        )
        sc.check(local.stdout.strip() == "0", f"the server kept a local archive: {local.stdout}")
        remote = sc.run(f"ls -1 {BK_REMOTE}/{BK_APP}", label="what the destination holds")
        names = remote.stdout.split()
        sc.check(
            any(n.endswith(".tar.gz") for n in names) and any(n.endswith(".json") for n in names),
            f"the destination should hold the archive and its sidecar: {names}",
        )
        archive = next(n for n in names if n.endswith(".tar.gz")).removesuffix(".tar.gz")

        # The copy that remains is the one a restore uses.
        root = sc.run(
            f"readlink -e /var/www/apps/{BK_APP}/current || echo /var/www/apps/{BK_APP}"
        ).stdout.strip()
        sc.run(f"echo tampered > {root}/index.html")
        noust(sc, f"backup restore {archive} --from chremote -f", timeout=300)
        page = sc.run(f"curl -fsS -H 'Host: {BK_DOMAIN}' http://127.0.0.1/").stdout
        sc.check("tampered" not in page, f"the restore did not bring the page back: {page[:200]}")

        # --- the application's data: a directory the application keeps its uploads in
        sc.run(
            f"mkdir -p /var/www/apps/{BK_APP}/shared/uploads && "
            f"echo kept > /var/www/apps/{BK_APP}/shared/uploads/a.txt"
        )
        noust(
            sc,
            f"backup volumes schedule set {BK_DOMAIN} --path uploads --to chremote --remote-only",
        )
        noust(sc, f"backup volumes run {BK_DOMAIN}", timeout=300)
        listed = sc.run(f"noust backup volumes list {BK_DOMAIN} --json", label="data backups here")
        sc.check(
            json_of(listed, "volumes list") in ([], {"backups": [], "total": 0}), listed.stdout
        )
        held = sc.run(f"find {BK_REMOTE}/volumes -name '*.tar.gz' | wc -l").stdout.strip()
        sc.check(held == "1", f"the destination should hold the data backup: {held}")

        # --- Noust's own state, sealed by a passphrase the destination never sees
        sc.run("printf 'correct horse battery staple\\n' > /root/pass && chmod 600 /root/pass")
        noust(
            sc, "self-backup schedule set --to chremote --remote-only --passphrase-file /root/pass"
        )
        noust(sc, "self-backup run", timeout=300)
        here = sc.run("ls /var/backups/noust/self 2>/dev/null | wc -l").stdout.strip()
        sc.check(here == "0", f"the server kept a bundle of its own state: {here}")
        held = sc.run(f"find {BK_REMOTE}/self-backups -name '*.enc' | wc -l").stdout.strip()
        sc.check(held == "1", f"the destination should hold the bundle: {held}")
        leaked = sc.run(
            f"grep -rl 'correct horse' {BK_REMOTE} | wc -l", check=False, label="passphrase sent?"
        )
        sc.check(leaked.stdout.strip() == "0", "the passphrase reached the destination")

        # --- the destination goes away: the archive stays and the run fails
        noust(sc, f"backup volumes schedule remove {BK_DOMAIN} -f")
        noust(sc, "self-backup schedule remove -f")
        noust(sc, "backup destination remove chremote -f")
        sc.run("touch /root/not-a-directory")
        noust(
            sc,
            "backup destination add chbroken --type local --field path=/root/not-a-directory/x",
        )
        noust(
            sc,
            f"backup schedule create {BK_DOMAIN} --schedule daily --destination chbroken "
            "--remote-only",
        )
        failed = noust(sc, f"backup run-schedule {BK_DOMAIN}", timeout=300, check=False)
        sc.check(failed.returncode != 0, "a run that reached no destination must fail")
        kept = sc.run(
            f"find /var/backups/noust -name '{BK_APP}_*.tar.gz' | wc -l", label="local archives"
        )
        sc.check(kept.stdout.strip() == "1", f"the failed run should keep its archive: {kept}")
        noust(sc, "backup destination remove chbroken -f")
    finally:
        sc.run(
            f"noust backup schedule delete {BK_DOMAIN}; "
            f"noust backup volumes schedule remove {BK_DOMAIN} -f; "
            "noust self-backup schedule remove -f; "
            "noust backup destination remove chremote -f; "
            "noust backup destination remove chbroken -f; "
            f"noust delete {BK_DOMAIN} -f; rm -rf {BK_REMOTE} /root/not-a-directory "
            f"/var/backups/noust/{BK_APP}",
            timeout=180,
            check=False,
            label="cleanup",
        )


PSQL = "runuser -u postgres -- psql -X -tA"
PITR_REMOTE = "/srv/noust-test-ch-pitr"


def psql(sc: Any, database: str, sql: str) -> str:
    """
    Run SQL as the postgres account of the scenario's machine.

    Args:
        sc: The scenario.
        database: The database to connect to.
        sql: The statement.

    Returns:
        psql's output, stripped.
    """
    done = sc.run(
        f"{PSQL} -d {database} -c {shlex.quote(sql)}",
        label=f"{tag(sc)}psql {database}: {sql}",
    )
    return done.stdout.strip()


def utc_now(sc: Any) -> str:
    """
    Args:
        sc: The scenario.

    Returns:
        The container's clock as ``YYYY-MM-DD HH:MM:SS`` (UTC): the instant a recovery names.
    """
    return sc.run("date -u +'%Y-%m-%d %H:%M:%S'", label="the time").stdout.strip()


def wait_archived(sc: Any, *, timeout: int = 150) -> None:
    """
    Make the engine finish its segment and wait until the archive holds it.

    Args:
        sc: The scenario.
        timeout: Seconds to wait.

    Raises:
        AssertionError: Nothing new reached the archive.
    """
    newest = "ls -t /var/lib/postgresql/noust-wal/*/ | head -n 1"
    before = sc.run(newest, label="newest archived file").stdout.strip()
    sc.run(f"{PSQL} -c \"select pg_create_restore_point('chtest'); select pg_switch_wal()\"")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        now = sc.run(newest, label="newest archived file").stdout.strip()
        if now != before:
            return
        time.sleep(3)
    raise AssertionError("the engine did not archive the segment it was asked to switch")


def pitr_status(sc: Any) -> dict[str, Any]:
    """
    Args:
        sc: The scenario.

    Returns:
        ``noust db pitr status --json``, parsed.
    """
    data = json_of(noust(sc, "db pitr status --json"), "db pitr status")
    assert isinstance(data, dict)
    return data


@engines_scenario("pitr_postgresql")
def scenario_pitr_postgresql(sc: Any) -> None:
    """
    H-32: a PostgreSQL recovered to an instant, from the log it archived and a base backup.

    The real engine, installed by Noust: archiving is turned on (one restart) and proved; a
    mistake is made after an instant; the cluster is recovered to that instant and holds exactly
    what it held then; a recovery that fails puts the previous data back; the same instant can
    be asked for again; retention drops the oldest base backup and the log only it needed; the
    archive, sent to a destination, brings a server whose archive is gone back to the instant.
    """
    try:
        sc.run("noust db pitr disable -y --delete-archive", check=False, timeout=300)
        noust(sc, "db uninstall postgresql --purge -f", timeout=900, check=False)
        noust(sc, "db install postgresql", timeout=1800)
        sc.run(f"rm -rf {PITR_REMOTE} && mkdir -p {PITR_REMOTE}")
        noust(sc, f"backup destination add chpitr --type local --field path={PITR_REMOTE}")

        # --- off: nothing to recover from, and the refusal says what to do
        sc.check(pitr_status(sc)["enabled"] is False, "archiving should start off")
        refused = noust(sc, "db pitr restore --latest -y", check=False)
        sc.check(refused.returncode != 0, "a recovery with no archive must be refused")
        sc.check(
            "noust db pitr enable" in refused.stdout + refused.stderr,
            f"the refusal should name the command: {refused.stdout} {refused.stderr}",
        )

        # --- on: one restart, proved by the engine's own archiver
        planned = json_of(noust(sc, "db pitr enable --plan --json"), "enable --plan")
        sc.check(planned["restart"] is True, "turning archiving on restarts the engine")
        noust(sc, "db pitr enable --to chpitr:2 --keep 2 --keep-days none -y", timeout=600)
        sc.check(psql(sc, "postgres", "show archive_mode") == "on", "archive_mode should be on")
        status = pitr_status(sc)
        sc.check(status["enabled"] and len(status["bases"]) == 1, f"one base backup: {status}")
        sc.check(status["archiver"]["failed"] == 0, f"the archiver failed: {status['archiver']}")
        sc.check(not status["warnings"], f"a fresh setup should warn of nothing: {status}")
        owner = sc.run(f"stat -c '%U %a' {status['archive']['path']}").stdout.strip()
        sc.check(owner == "postgres 700", f"the archive belongs to the engine alone: {owner}")
        base_file = status["bases"][0]["start_wal_file"]
        archive = status["archive"]["path"]

        # --- the data, an instant, and a mistake
        noust(sc, "db create shop --engine postgresql")
        psql(sc, "shop", "create table t(id int, tag text)")
        psql(sc, "shop", "insert into t select g, 'a' from generate_series(1, 100) g")
        psql(sc, "shop", "insert into t select g, 'b' from generate_series(1, 100) g")
        time.sleep(3)
        instant = utc_now(sc)
        time.sleep(3)
        psql(sc, "shop", "insert into t select g, 'c' from generate_series(1, 100) g")
        time.sleep(2)
        psql(sc, "shop", "delete from t")
        sc.check(psql(sc, "shop", "select count(*) from t") == "0", "the mistake was made")
        wait_archived(sc)

        # --- the plan, and what is refused before anything is touched
        plan = json_of(noust(sc, f"db pitr restore --time '{instant}' --plan --json"), "plan")
        sc.check(
            plan["missing"] == [] and plan["segments"] >= 2, f"the log should be whole: {plan}"
        )
        sc.check(plan["base"]["id"] == status["bases"][0]["id"], "the only base backup is used")
        early = noust(sc, "db pitr restore --time '2000-01-01 00:00:00' --plan", check=False)
        sc.check(
            early.returncode != 0 and "No base backup is old enough" in early.stdout + early.stderr,
            "an instant before the first base backup is refused",
        )
        late = noust(sc, "db pitr restore --time '2999-01-01 00:00:00' --plan", check=False)
        sc.check(
            late.returncode != 0 and "later than the archive reaches" in late.stdout + late.stderr,
            "an instant after the archive is refused",
        )
        sc.check(
            noust(sc, "db pitr restore --plan", check=False).returncode != 0,
            "a recovery with neither --time nor --latest is refused",
        )
        sc.check(psql(sc, "shop", "select count(*) from t") == "0", "refusals changed nothing")

        # --- a recovery that cannot finish puts the previous data back
        sc.run(f"cp {archive}/{base_file} /root/first-segment")
        sc.run(f"truncate -s 1048576 {archive}/{base_file}")
        broken = noust(sc, f"db pitr restore --time '{instant}' -y", timeout=900, check=False)
        sc.run(f"cp /root/first-segment {archive}/{base_file}")
        sc.run(f"chown postgres:postgres {archive}/{base_file} && chmod 600 {archive}/{base_file}")
        sc.check(broken.returncode != 0, "a recovery over a truncated segment must fail")
        sc.check(
            "the previous data is back" in broken.stdout + broken.stderr,
            f"the failure should say the data is back: {broken.stdout} {broken.stderr}",
        )
        sc.check(
            psql(sc, "shop", "select count(*) from t") == "0",
            "the engine should run on what it held before the attempt",
        )
        kept = sc.run("ls -d /var/lib/postgresql/16/main.noust-failed-recovery").returncode
        sc.check(kept == 0, "the failed attempt should be kept for inspection")

        # --- the recovery
        noust(sc, f"db pitr restore --time '{instant}' -y", timeout=900)
        rows = psql(sc, "shop", "select tag || ':' || count(*) from t group by tag order by tag")
        sc.check(rows.split() == ["a:100", "b:100"], f"the cluster should hold a and b: {rows!r}")
        sc.check(psql(sc, "postgres", "select pg_is_in_recovery()") == "f", "recovery ended")
        sc.check(
            psql(sc, "postgres", "select timeline_id from pg_control_checkpoint()") == "2",
            "a new timeline",
        )
        settings = sc.run(
            "grep -c recovery_target /var/lib/postgresql/16/main/postgresql.auto.conf", check=False
        )
        sc.check(settings.stdout.strip() == "0", "the recovery settings should be gone afterwards")
        saved = sc.run("ls -d /var/lib/postgresql/16/main.noust-before-recovery-*").stdout.split()
        sc.check(len(saved) >= 1, "the replaced data directory should be kept")
        sc.run(
            "rm -rf /var/lib/postgresql/16/main.noust-before-recovery-* /var/lib/postgresql/16/main.noust-failed-recovery"
        )

        # --- archiving goes on, on the new timeline, and the same instant can be asked for again
        psql(sc, "shop", "insert into t select g, 'd' from generate_series(1, 10) g")
        wait_archived(sc)
        newest = sc.run(f"ls {archive}").stdout.split()
        sc.check(any(n.startswith("00000002") for n in newest), f"timeline 2 is archived: {newest}")
        noust(sc, f"db pitr restore --time '{instant}' -y", timeout=900)
        rows = psql(sc, "shop", "select tag || ':' || count(*) from t group by tag order by tag")
        sc.check(rows.split() == ["a:100", "b:100"], f"the same instant again: {rows!r}")
        sc.check(
            psql(sc, "postgres", "select timeline_id from pg_control_checkpoint()") == "3",
            "timeline 3",
        )
        sc.run("rm -rf /var/lib/postgresql/16/main.noust-before-recovery-*")

        # --- more base backups: retention drops the oldest and the log only it needed
        first = pitr_status(sc)["bases"][0]
        psql(sc, "shop", "insert into t select g, 'e' from generate_series(1, 20) g")
        noust(sc, "db pitr base", timeout=900)
        time.sleep(2)
        psql(sc, "shop", "insert into t select g, 'f' from generate_series(1, 30) g")
        time.sleep(3)
        second_instant = utc_now(sc)
        time.sleep(3)
        psql(sc, "shop", "delete from t where tag = 'f'")
        noust(sc, "db pitr base", timeout=900)
        wait_archived(sc)
        status = pitr_status(sc)
        ids = [b["id"] for b in status["bases"]]
        sc.check(len(ids) == 2 and first["id"] not in ids, f"keep 2 drops the oldest: {ids}")
        left = sc.run(f"ls {archive}").stdout.split()
        sc.check(first["start_wal_file"] not in left, "the log only the oldest base needed is gone")
        sc.check(any(n.endswith(".history") for n in left), "timeline histories are never pruned")

        # --- the archive on the destination, and a server that lost its own
        report = json_of(noust(sc, "db pitr sync --json", timeout=900), "sync")
        sc.check("chpitr" in report, f"the destination was sent to: {report}")
        remote_bases = sc.run(f"ls {PITR_REMOTE}/databases/postgresql/pitr/*/base").stdout.split()
        sc.check(
            sorted(n for n in remote_bases if n.endswith(".tar.gz"))
            == sorted(f"{i}.tar.gz" for i in ids),
            f"the destination keeps the same base backups: {remote_bases}",
        )
        sc.run(f"rm -rf {archive}/* /var/backups/noust/databases/pitr/postgresql/*/*")
        fetched = json_of(noust(sc, "db pitr fetch chpitr --json", timeout=900), "fetch")
        sc.check(len(fetched["bases"]) == 2 and fetched["segments"], f"fetched: {fetched}")
        noust(sc, f"db pitr restore --time '{second_instant}' -y", timeout=900)
        rows = psql(sc, "shop", "select tag || ':' || count(*) from t group by tag order by tag")
        sc.check(
            rows.split() == ["a:100", "b:100", "e:20", "f:30"],
            f"recovered from the destination to the instant: {rows!r}",
        )

        # --- off again: the engine is as it was, and the destination is cleared
        noust(sc, "db pitr disable -y --delete-archive", timeout=600)
        sc.check(psql(sc, "postgres", "show archive_mode") == "off", "archive_mode should be off")
        sc.check(pitr_status(sc)["enabled"] is False, "archiving should be off")
        left = sc.run(f"find {PITR_REMOTE} -type f | wc -l").stdout.strip()
        sc.check(left == "0", f"the destination should be empty: {left}")
        sc.check(
            sc.run("systemctl list-timers --all | grep -c pitr", check=False).stdout.strip() == "0",
            "the upkeep timer should be gone",
        )
    finally:
        sc.run(
            "noust db pitr disable -y --delete-archive; "
            "noust backup destination remove chpitr -f; "
            f"rm -rf {PITR_REMOTE} /root/first-segment "
            "/var/lib/postgresql/16/main.noust-before-recovery-* "
            "/var/lib/postgresql/16/main.noust-failed-recovery",
            timeout=300,
            check=False,
            label="cleanup",
        )


TLS_HOST = "db.noust.test"
TLS_LINEAGE = f"/etc/letsencrypt/live/{TLS_HOST}"


def issue_certificate(sc: Any, *, directory: str, days: int = 30, names: str = TLS_HOST) -> None:
    """
    Issue a certificate for the scenario's name from a private authority, as a certbot lineage.

    The authority is made once (``/root/tls-ca``) and every call signs a new key with it, so a
    "renewal" is a new certificate the same clients still verify.

    Args:
        sc: The scenario.
        directory: Where ``fullchain.pem`` and ``privkey.pem`` are written.
        days: How long it is valid.
        names: The subject alternative name.
    """
    sc.run(
        "test -f /root/tls-ca/ca.pem || { mkdir -p /root/tls-ca && cd /root/tls-ca && "
        "openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 365 "
        "-subj '/CN=Noust test CA' 2>/dev/null; }",
        label="the private authority",
    )
    sc.run(
        f"mkdir -p {directory} && cd /root/tls-ca && "
        f"openssl req -newkey rsa:2048 -nodes -keyout {directory}/privkey.pem -out leaf.csr "
        f"-subj '/CN={names}' 2>/dev/null && "
        f"printf 'subjectAltName=DNS:{names}\\n' > leaf.ext && "
        f"openssl x509 -req -in leaf.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days {days} "
        f"-extfile leaf.ext -out leaf.pem 2>/dev/null && "
        f"cat leaf.pem ca.pem > {directory}/fullchain.pem && chmod 600 {directory}/privkey.pem",
        label=f"a certificate for {names}",
    )


def presented(sc: Any, port: int, protocol: str) -> str:
    """
    Ask an engine what certificate it presents, as a client that verifies would.

    Args:
        sc: The scenario.
        port: The engine's port.
        protocol: ``postgres`` or ``mysql``.

    Returns:
        The leaf's SHA-256 fingerprint, once the handshake verified against the authority and
        the name.
    """
    done = sc.run(
        f"echo | openssl s_client -connect 127.0.0.1:{port} -starttls {protocol} "
        f"-servername {TLS_HOST} -CAfile /root/tls-ca/ca.pem -verify_return_error "
        f"-verify_hostname {TLS_HOST} 2>&1 | tee /root/handshake.txt | "
        "openssl x509 -noout -fingerprint -sha256",
        label=f"what the {protocol} engine presents",
    )
    sc.check(
        "Verify return code: 0 (ok)" in sc.run("cat /root/handshake.txt").stdout,
        "a client that verifies the authority and the name should accept the engine",
    )
    return done.stdout.strip().split("=", 1)[1].replace(":", "").lower()


def lineage_fingerprint(sc: Any) -> str:
    """
    Args:
        sc: The scenario.

    Returns:
        The SHA-256 fingerprint of the leaf of the scenario's lineage.
    """
    done = sc.run(
        f"openssl x509 -in {TLS_LINEAGE}/fullchain.pem -noout -fingerprint -sha256",
        label="the lineage's leaf",
    )
    return done.stdout.strip().split("=", 1)[1].replace(":", "").lower()


@engines_scenario("host_tls")
def scenario_host_tls(sc: Any) -> None:
    """
    C-27: PostgreSQL and MariaDB on the host present a real certificate, and a renewal reaches them.

    The certificate is a certbot-shaped lineage signed by a private authority; the engines are
    installed by Noust. A client that verifies the authority and the name is accepted, a name the
    certificate does not cover is refused before the engine is touched, and when the lineage is
    replaced, certbot's own deploy hook (the one Noust installs) makes both engines present the new
    certificate: PostgreSQL by a reload and MariaDB by FLUSH SSL, neither restarted.
    """
    try:
        noust(sc, "db uninstall postgresql --purge -f", timeout=900, check=False)
        noust(sc, "db uninstall mysql --purge -f", timeout=900, check=False)
        noust(sc, "db install postgresql", timeout=1800)
        noust(sc, "db install mariadb", timeout=1800)
        sc.run(f"grep -q {TLS_HOST} /etc/hosts || echo '127.0.0.1 {TLS_HOST}' >> /etc/hosts")
        issue_certificate(sc, directory=TLS_LINEAGE)
        first = lineage_fingerprint(sc)

        # --- refused before anything is touched
        wrong_name = noust(
            sc,
            "db tls enable -e postgresql --host other.noust.test --source lineage "
            f"--certificate {TLS_HOST} --plan",
            check=False,
        )
        sc.check(
            wrong_name.returncode != 0
            and "does not cover other.noust.test" in wrong_name.stdout + wrong_name.stderr,
            f"a certificate that does not cover the name is refused: {wrong_name.stdout}",
        )
        sc.run(
            "mkdir -p /etc/letsencrypt/live/mismatch && cd /etc/letsencrypt/live/mismatch && "
            f"cp {TLS_LINEAGE}/fullchain.pem . && openssl genrsa -out privkey.pem 2048 2>/dev/null"
        )
        mismatch = noust(
            sc,
            "db tls enable -e postgresql --host db.noust.test --source lineage "
            "--certificate mismatch --plan",
            check=False,
        )
        sc.check(
            mismatch.returncode != 0
            and "not the certificate's" in mismatch.stdout + mismatch.stderr,
            "a key that is not the certificate's is refused",
        )
        sc.check(
            psql(sc, "postgres", "show ssl_cert_file")
            != "/etc/ssl/noust-db/postgresql/fullchain.pem",
            "the refusals changed nothing",
        )

        # --- PostgreSQL: a reload, and a client that verifies is accepted
        pg_start = psql(sc, "postgres", "select pg_postmaster_start_time()")
        noust(sc, f"db tls enable -e postgresql --host {TLS_HOST} --yes", timeout=300)
        port = int(psql(sc, "postgres", "show port"))
        sc.check(
            presented(sc, port, "postgres") == first,
            "PostgreSQL should present the lineage's certificate",
        )
        sc.run(f"{PSQL} -c \"create role tlsuser login password 'tlspass'\"")
        who = sc.run(
            f"PGPASSWORD=tlspass psql -X -tA 'host={TLS_HOST} dbname=postgres user=tlsuser "  # noqa: S608
            "sslmode=verify-full sslrootcert=/root/tls-ca/ca.pem' "
            "-c 'select ssl from pg_stat_ssl where pid = pg_backend_pid()'",
            label="psql with sslmode=verify-full",
        ).stdout.strip()
        sc.check(who == "t", f"a verify-full client should connect over TLS: {who!r}")
        sc.check(
            psql(sc, "postgres", "select pg_postmaster_start_time()") == pg_start, "no restart"
        )

        # --- MariaDB: one restart, then the same
        noust(sc, f"db tls enable -e mysql --host {TLS_HOST} --yes", timeout=600)
        my_port = int(sc.run("mysql -N -e 'select @@port'").stdout.strip())
        sc.check(
            presented(sc, my_port, "mysql") == first,
            "MariaDB should present the lineage's certificate",
        )
        sc.run(
            "mysql -e \"create user if not exists 'tlsuser'@'127.0.0.1' identified by 'tlspass'; "
            "grant select on mysql.* to 'tlsuser'@'127.0.0.1'\""
        )
        cipher = sc.run(
            f"mysql -N -h {TLS_HOST} -u tlsuser -ptlspass --ssl --ssl-verify-server-cert "
            "--ssl-ca=/root/tls-ca/ca.pem -e \"show status like 'Ssl_cipher'\"",
            label="mariadb client verifying the certificate and the name",
        ).stdout
        sc.check("TLS" in cipher, f"a verify-identity client should connect over TLS: {cipher!r}")
        status = json_of(noust(sc, "db tls status --json"), "db tls status")
        sc.check(
            sorted(v["engine"] for v in status if v["enabled"]) == ["mysql", "postgresql"]
            and all(not v["warnings"] for v in status if v["enabled"]),
            f"both engines are given the certificate and nothing is behind: {status}",
        )

        # --- the renewal: certbot replaces the lineage and runs its deploy hook
        uptime = int(sc.run("mysql -N -e \"show global status like 'Uptime'\"").stdout.split()[1])
        issue_certificate(sc, directory=TLS_LINEAGE)
        second = lineage_fingerprint(sc)
        sc.check(second != first, "a renewal is a new certificate")
        behind = json_of(noust(sc, "db tls status --json"), "status")
        sc.check(
            any(v["enabled"] and v.get("source_changed") for v in behind),
            "status should say the copy is behind its source",
        )
        hook = "/etc/letsencrypt/renewal-hooks/deploy/noust-reload"
        sc.check(
            sc.run(f"grep -c 'noust db tls refresh' {hook}", check=False).stdout.strip() == "1",
            "certbot's deploy hook should refresh the engines",
        )
        sc.run(f"RENEWED_LINEAGE={TLS_LINEAGE} {hook}", label="certbot's deploy hook")
        sc.check(
            presented(sc, port, "postgres") == second,
            "PostgreSQL should present the renewed certificate",
        )
        sc.check(
            presented(sc, my_port, "mysql") == second,
            "MariaDB should present the renewed certificate",
        )
        sc.check(
            psql(sc, "postgres", "select pg_postmaster_start_time()") == pg_start,
            "PostgreSQL loaded the renewed certificate without a restart",
        )
        now = int(sc.run("mysql -N -e \"show global status like 'Uptime'\"").stdout.split()[1])
        sc.check(now >= uptime, "MariaDB loaded the renewed certificate without a restart")

        # --- off: the settings are back and the copies are gone
        noust(sc, "db tls disable -e postgresql", timeout=300)
        noust(sc, "db tls disable -e mysql --yes", timeout=600)
        sc.check(
            psql(sc, "postgres", "show ssl_cert_file")
            != "/etc/ssl/noust-db/postgresql/fullchain.pem",
            "PostgreSQL's settings are back",
        )
        sc.check(
            sc.run("find /etc/ssl/noust-db -type f 2>/dev/null | wc -l", check=False).stdout.strip()
            == "0",
            "the copies are removed",
        )
        sc.check(
            sc.run(
                "ls /etc/mysql/*/98-noust-tls.cnf 2>/dev/null | wc -l", check=False
            ).stdout.strip()
            == "0",
            "MariaDB's include is removed",
        )
        off = json_of(noust(sc, "db tls status --json"), "status")
        sc.check(not any(v["enabled"] for v in off), "nothing is given a certificate any more")
    finally:
        sc.run(
            "noust db tls disable -e postgresql; noust db tls disable -e mysql --yes; "
            "runuser -u postgres -- psql -X -c 'drop role if exists tlsuser'; "
            "mysql -e \"drop user if exists 'tlsuser'@'127.0.0.1'\"; "
            f"rm -rf /etc/letsencrypt/live/mismatch {TLS_LINEAGE}; "
            f"sed -i '/{TLS_HOST}/d' /etc/hosts",
            timeout=600,
            check=False,
            label="cleanup",
        )


SEAL_MARKER = "customer-secret-marker-7731"
SEAL_DOMAIN = "seal.test"
SEAL_APP = "seal-test"
MAGIC_LINE = "NOUST-ENVELOPE 1"


def newest(sc: Any, pattern: str) -> str:
    """
    Args:
        sc: The scenario.
        pattern: A shell glob.

    Returns:
        The newest file matching it.
    """
    return sc.run(f"ls -t {pattern} | head -n 1", label=f"newest {pattern}").stdout.strip()


def stored_as(sc: Any, path: str) -> str:
    """
    Args:
        sc: The scenario.
        path: A file on the machine.

    Returns:
        ``sealed`` when it is an envelope, ``clear`` otherwise.
    """
    first = sc.run(f"head -c 16 {path}", label=f"first bytes of {path}").stdout
    return "sealed" if first.startswith(MAGIC_LINE[:16]) else "clear"


@engines_scenario("sealed_backups")
def scenario_sealed_backups(sc: Any) -> None:
    """
    H-08: backups sealed on the server's disk, opened by one door, with real engines.

    A PostgreSQL dump, an application's archive, an application's data, and the safety copy a
    restore takes are envelopes that do not contain their plaintext; each is checked, restored and
    sent to a destination as the stored file; a dump made before sealing still restores; a backup
    whose key is gone is refused with the fix and opens again when the key is put back.
    """
    key_file = "/root/kept-elsewhere"
    try:
        noust(sc, "db uninstall postgresql --purge -f", timeout=900, check=False)
        noust(sc, "db install postgresql", timeout=1800)
        noust(sc, "db create shop --engine postgresql")
        psql(
            sc,
            "shop",
            f"create table t(note text); insert into t select '{SEAL_MARKER}-' || g "  # noqa: S608
            "from generate_series(1, 50) g",
        )

        # --- before: a dump is plain text anyone with the disk can read
        noust(sc, "db backup shop --engine postgresql --format plain --no-compress")
        before = newest(sc, "/var/backups/noust/databases/postgresql-shop-*.sql")
        sc.check(stored_as(sc, before) == "clear", "a dump made before sealing is in clear")
        sc.check(int(sc.run(f"grep -c {SEAL_MARKER} {before}").stdout) > 0, "and readable")
        status = json_of(noust(sc, "backup encryption status --json"), "status")
        sc.check(
            status["enabled"] is False and status["clear"] >= 1, f"nothing sealed yet: {status}"
        )

        # --- on: the key is shown once and kept
        enabled = noust(sc, "backup encryption enable --yes")
        key_id = next(
            line.split()[1].rstrip(":")
            for line in enabled.stdout.splitlines()
            if line.startswith("Key ")
        )
        passphrase = next(
            line.strip() for line in enabled.stdout.splitlines() if len(line.strip()) == 48
        )
        sc.run(f"printf '%s\\n' '{passphrase}' > {key_file} && chmod 600 {key_file}")

        # --- a dump an engine makes is sealed whatever its format
        noust(sc, "db backup shop --engine postgresql --format plain --no-compress")
        plain_dump = newest(sc, "/var/backups/noust/databases/postgresql-shop-*.sql")
        sc.check(stored_as(sc, plain_dump) == "sealed", "a plain dump made now is sealed")
        sc.check(
            sc.run(f"grep -c {SEAL_MARKER} {plain_dump}", check=False).stdout.strip() == "0",
            "and holds none of the plaintext",
        )
        noust(sc, "db backup shop --engine postgresql")
        custom_dump = newest(sc, "/var/backups/noust/databases/postgresql-shop-*.dump*")
        sc.check(stored_as(sc, custom_dump) == "sealed", "a custom-format dump is sealed too")
        status = json_of(noust(sc, "backup encryption status --json"), "status")
        sc.check(status["sealed"] >= 2 and status["current_key"] == key_id, f"counted: {status}")

        # --- checked and restored through the one door
        verified = noust(
            sc,
            f"db backup-verify {Path(custom_dump).name} --engine postgresql --restore-test",
            timeout=300,
        )
        sc.check("ok" in verified.stdout.lower() or verified.returncode == 0, verified.stdout)
        psql(sc, "shop", "delete from t")
        sc.check(psql(sc, "shop", "select count(*) from t") == "0", "the data was lost")
        noust(
            sc,
            f"db restore shop {custom_dump} --engine postgresql --drop -f",
            timeout=600,
        )
        sc.check(psql(sc, "shop", "select count(*) from t") == "50", "a sealed dump restores")
        # Taken after the delete, so it holds an empty table: it proves sealing, not the data.
        safety = newest(sc, "/var/backups/noust/databases/postgresql-shop-*.dump*")
        sc.check(stored_as(sc, safety) == "sealed", "the safety copy a restore takes is sealed")
        sc.check(
            int(sc.run("find /var/backups/noust/.open -type f 2>/dev/null | wc -l").stdout) == 0,
            "no decrypted copy is left behind",
        )

        # --- and the dump made before sealing was turned on is still opened the same way
        noust(sc, f"db restore shop {before} --engine postgresql --drop -f", timeout=600)
        sc.check(
            psql(sc, "shop", "select count(*) from t") == "50", "a dump in clear still restores"
        )

        # --- an application and its data
        create = f"noust create -d {SEAL_DOMAIN} -s /root/fixtures/static-site -t static --no-ssl"
        sc.run(create, timeout=run.DEPLOY_TIMEOUT, label=create)
        sc.run(
            f"mkdir -p /var/www/apps/{SEAL_APP}/shared/uploads && "
            f"echo '{SEAL_MARKER}' > /var/www/apps/{SEAL_APP}/shared/uploads/a.txt"
        )
        noust(sc, f"backup create {SEAL_DOMAIN}", timeout=300)
        archive = newest(sc, f"/var/backups/noust/{SEAL_APP}/*.tar.gz")
        sc.check(stored_as(sc, archive) == "sealed", "an application's archive is sealed")
        backup_id = Path(archive).name.removesuffix(".tar.gz")
        noust(sc, f"backup verify {backup_id}", timeout=300)
        root = sc.run(
            f"readlink -e /var/www/apps/{SEAL_APP}/current || echo /var/www/apps/{SEAL_APP}"
        ).stdout.strip()
        sc.run(f"echo tampered > {root}/index.html")
        noust(sc, f"backup restore {backup_id} -f", timeout=300)
        page = sc.run(f"curl -fsS -H 'Host: {SEAL_DOMAIN}' http://127.0.0.1/").stdout
        sc.check(
            "tampered" not in page,
            f"the restore from a sealed archive brought the page back: {page[:120]}",
        )

        noust(sc, f"backup volumes create {SEAL_DOMAIN} --path uploads", timeout=300)
        data = sc.run(
            f"find /var/backups/noust -name '{SEAL_APP}.path-uploads_*.tar.gz' | sort | tail -n 1",
            label="the data backup",
        ).stdout.strip()
        sc.check(stored_as(sc, data) == "sealed", "an application's data backup is sealed")
        data_id = Path(data).name.removesuffix(".tar.gz")
        noust(sc, f"backup volumes verify {data_id} --deep", timeout=300)
        sc.run(f"echo lost > /var/www/apps/{SEAL_APP}/shared/uploads/a.txt")
        noust(sc, f"backup volumes restore {data_id} -f", timeout=300)
        sc.check(
            sc.run(f"cat /var/www/apps/{SEAL_APP}/shared/uploads/a.txt").stdout.strip()
            == SEAL_MARKER,
            "the data came back from a sealed archive",
        )

        # --- sent as it is stored, and restored from the destination on a server with no copy
        sc.run("rm -rf /srv/noust-test-ch-seal && mkdir -p /srv/noust-test-ch-seal")
        noust(sc, "backup destination add chseal --type local --field path=/srv/noust-test-ch-seal")
        noust(
            sc,
            f"db backup-push {Path(custom_dump).name} --engine postgresql --to chseal",
            timeout=300,
        )
        remote = sc.run(
            "find /srv/noust-test-ch-seal -name 'postgresql-shop-*.dump*' ! -name '*.json'"
        ).stdout.split()
        sc.check(
            len(remote) == 1 and stored_as(sc, remote[0]) == "sealed",
            f"the destination holds the stored file: {remote}",
        )
        sc.run(f"rm -f {custom_dump}")
        psql(sc, "shop", "delete from t")
        noust(
            sc,
            f"db restore-remote shop {Path(custom_dump).name} --engine postgresql --from chseal --drop -f",
            timeout=600,
        )
        sc.check(
            psql(sc, "shop", "select count(*) from t") == "50",
            "a sealed dump restores from a destination",
        )

        # --- the key is gone: refused with the fix, and the key put back opens it again
        # (the first dump was deleted above to prove the restore from a destination)
        noust(sc, "db backup shop --engine postgresql")
        custom_dump = newest(sc, "/var/backups/noust/databases/postgresql-shop-*.dump*")
        sc.check(stored_as(sc, custom_dump) == "sealed", "the dump to lose the key for is sealed")
        # The store sits beside the account's data directory when no unit runs the command, so
        # the key is looked for rather than assumed at the system path.
        key_path = sc.run(
            f"find / -xdev -path '*/secrets/backups/local-encryption/{key_id}' | head -n 1"
        ).stdout.strip()
        sc.check(bool(key_path), "the key is a file of the secret store")
        sc.run(f"mv {key_path} /root/key-moved")
        gone = noust(
            sc,
            f"db restore shop {custom_dump} --engine postgresql --drop -f",
            timeout=600,
            check=False,
        )
        text = gone.stdout + gone.stderr
        sc.check(
            gone.returncode != 0
            and "does not hold its key" in text
            and f"import-key {key_id}" in text,
            f"a backup whose key is gone is refused with the fix: {text}",
        )
        sc.check(psql(sc, "shop", "select count(*) from t") == "50", "nothing was touched")
        noust(sc, f"backup encryption import-key {key_id} --passphrase-file {key_file}")
        noust(sc, f"db restore shop {custom_dump} --engine postgresql --drop -f", timeout=600)
        sc.check(psql(sc, "shop", "select count(*) from t") == "50", "the key put back opens it")

        # --- off again: nothing new is sealed, and everything sealed still opens
        noust(sc, "backup encryption disable --yes")
        noust(sc, "db backup shop --engine postgresql --format plain --no-compress")
        later = newest(sc, "/var/backups/noust/databases/postgresql-shop-*.sql")
        sc.check(stored_as(sc, later) == "clear", "with sealing off a new dump is in clear")
        noust(sc, f"db restore shop {custom_dump} --engine postgresql --drop -f", timeout=600)
        sc.check(psql(sc, "shop", "select count(*) from t") == "50", "what was sealed still opens")
    finally:
        sc.run(
            "noust backup encryption disable --yes; "
            "noust backup destination remove chseal -f; "
            f"noust delete {SEAL_DOMAIN} -f; "
            f"rm -rf /srv/noust-test-ch-seal {key_file} /root/key-moved /var/backups/noust/{SEAL_APP} "
            f"/var/backups/noust/volumes/{SEAL_APP}",
            timeout=300,
            check=False,
            label="cleanup",
        )


# ---------------------------------------------------------------------------
# C-14: replicas
# ---------------------------------------------------------------------------

REPLICA_PRIMARY_HOST = "primary.noust.test"
REPLICA_LINEAGE = f"/etc/letsencrypt/live/{REPLICA_PRIMARY_HOST}"


def carry(sc: Any, source_box: str, target_box: str, path: str) -> None:
    """
    Copy a file from one machine to another through the host, without printing it.

    Args:
        sc: The scenario, for the evidence line.
        source_box: The container it is in.
        target_box: The container it goes to.
        path: Its path on both.
    """
    with tempfile.TemporaryDirectory() as scratch:
        local = Path(scratch) / "carried"
        sh(["docker", "cp", f"{source_box}:{path}", str(local)], timeout=60)
        sh(["docker", "exec", target_box, "mkdir", "-p", str(Path(path).parent)], timeout=30)
        sh(["docker", "cp", str(local), f"{target_box}:{path}"], timeout=60)
    sc.evidence.append(f"$ carried {path} from {source_box} to {target_box}\n(exit=0)")


def rewrite_token(sc: Any, path: str, target: str, edit: str) -> None:
    """
    Make a damaged copy of a join code, on the machine that will read it.

    Args:
        sc: The scenario of that machine.
        path: The code to start from.
        target: Where the damaged copy goes.
        edit: Python that changes the decoded object ``body`` in place.
    """
    script = (
        "import base64, json, pathlib\n"
        f"raw = pathlib.Path({path!r}).read_text().strip().split(':', 2)[2]\n"
        "body = json.loads(base64.urlsafe_b64decode(raw))\n"
        f"{edit}\n"
        "text = base64.urlsafe_b64encode(json.dumps(body, separators=(',', ':'), "
        "sort_keys=True).encode()).decode()\n"
        f"pathlib.Path({target!r}).write_text('noust-replica:v1:' + text + '\\n')\n"
    )
    sc.run(f"python3 -c {shlex.quote(script)}", label=f"damage a copy of the join code: {edit}")


@engines_scenario("replication_postgresql", also=("replica",))
def scenario_replication_postgresql(sc: Any) -> None:
    """
    C-14: a PostgreSQL replica on another server, with an account of its own, over TLS.

    Two real servers on one network, each with PostgreSQL installed by Noust. The primary gets a
    certificate for the name the replica uses and is prepared (an account that can only replicate,
    a slot, one pg_hba.conf line); a connection without TLS, or with a wrong password, is refused
    by the engine; the replica joins with the code, copies the cluster, and streams: its data is
    the primary's, it refuses writes, and the primary's pg_stat_ssl says the walsender is on TLS. A
    join that cannot trust the primary's certificate, or has a wrong password, changes nothing; one
    whose standby cannot start puts the previous data directory back. Then the replica is promoted,
    accepts writes on its own, and removing the replica from the primary undoes everything.
    """
    other = peer(sc, "replica")
    boxes = (sc, other)
    token = "/root/join.token"
    replica_ip = address_on_network(MACHINES["replica"])
    primary_ip = address_on_network(MACHINES["ubuntu"])
    try:
        for box in boxes:
            # What an earlier run left, before the engine under it is replaced.
            box.run(
                "noust db replica promote --yes; noust db replica remove reports --yes",
                check=False,
                timeout=300,
            )
            noust(box, "db uninstall postgresql --purge -f", timeout=900, check=False)
            noust(box, "db install postgresql", timeout=1800)
            box.run(
                "grep -v noust.test /etc/hosts > /tmp/hosts.new; "
                f"echo '{primary_ip} {REPLICA_PRIMARY_HOST}' >> /tmp/hosts.new; "
                "cat /tmp/hosts.new > /etc/hosts"
            )
        port = int(psql(sc, "postgres", "show port"))
        sc.run(f'{PSQL} -c "create database shop"')
        psql(sc, "shop", "create table t as select g as id from generate_series(1, 100) g")
        sc.run(f"{PSQL} -c \"create role app login password 'apppass'\"")
        psql(other, "postgres", "create database local_marker")

        # --- the primary refuses to be prepared while it cannot be reached or trusted
        no_tls = noust(
            sc,
            f"db replica prepare reports --from {replica_ip} --host {REPLICA_PRIMARY_HOST} "
            "--listen '*' --plan",
            check=False,
        )
        sc.check(
            no_tls.returncode != 0 and "db tls enable" in no_tls.stdout + no_tls.stderr,
            f"a primary that serves no certificate is refused with the fix: {no_tls.stdout}",
        )
        issue_certificate(sc, directory=REPLICA_LINEAGE, names=REPLICA_PRIMARY_HOST)
        noust(sc, f"db tls enable -e postgresql --host {REPLICA_PRIMARY_HOST} --yes", timeout=300)
        sc.run(f'{PSQL} -c "show ssl"')
        loopback = noust(
            sc,
            f"db replica prepare reports --from {replica_ip} --host {REPLICA_PRIMARY_HOST} "
            "--ca-file /root/tls-ca/ca.pem --plan",
            check=False,
        )
        sc.check(
            loopback.returncode != 0 and "listens on" in loopback.stdout + loopback.stderr,
            f"a primary that listens on the loopback only says so: {loopback.stdout}",
        )
        wrong_name = noust(
            sc,
            f"db replica prepare reports --from {replica_ip} --host other.noust.test "
            "--listen '*' --ca-file /root/tls-ca/ca.pem --plan",
            check=False,
        )
        sc.check(
            wrong_name.returncode != 0
            and "would not be accepted" in wrong_name.stdout + wrong_name.stderr,
            f"a name the certificate does not cover is refused: {wrong_name.stdout}",
        )
        untrusted = noust(
            sc,
            f"db replica prepare reports --from {replica_ip} --host {REPLICA_PRIMARY_HOST} "
            "--listen '*' --plan",
            check=False,
        )
        sc.check(
            untrusted.returncode != 0 and "would not be accepted" in untrusted.stdout,
            f"an authority the replica does not trust is refused: {untrusted.stdout}",
        )
        sc.check(
            psql(sc, "postgres", "select count(*) from pg_roles where rolname like 'noust_repl%'")
            == "0",
            "the refusals made nothing",
        )

        # --- prepared: an account that can only replicate, a slot, one line, a code
        noust(
            sc,
            f"db replica prepare reports --from {replica_ip} --host {REPLICA_PRIMARY_HOST} "
            f"--listen '*' --ca-file /root/tls-ca/ca.pem --open-firewall --token-file {token} --yes",
            timeout=600,
        )
        sc.check(
            psql(
                sc,
                "postgres",
                "select rolreplication, rolsuper, rolcanlogin from pg_roles "
                "where rolname = 'noust_repl_reports'",
            )
            == "t|f|t",
            "the account replicates, logs in, and is not a superuser",
        )
        sc.check(
            psql(sc, "postgres", "select slot_name from pg_replication_slots") == "noust_reports",
            "the slot exists",
        )
        sc.check(
            psql(sc, "postgres", "show listen_addresses") == "*",
            "PostgreSQL listens where the replica can reach it",
        )
        hba = sc.run(
            "grep -h -A2 'BEGIN noust replication' $(runuser -u postgres -- psql -X -tA "
            "-c 'show hba_file')"
        ).stdout
        sc.check(
            f"hostssl replication noust_repl_reports {replica_ip}/32 scram-sha-256" in hba,
            f"one hostssl line for that account and address: {hba}",
        )

        # --- the engine itself refuses what is not TLS, the wrong password and any other use
        carry(sc, MACHINES["ubuntu"].container, MACHINES["replica"].container, token)
        password = (
            'PW=$(python3 -c "import base64,json;'
            f"print(json.loads(base64.urlsafe_b64decode(open('{token}').read().strip()"
            ".split(':',2)[2]))['w'])\"); "
        )
        conn = (
            f"host={REPLICA_PRIMARY_HOST} port={port} user=noust_repl_reports "
            "replication=true connect_timeout=10"
        )
        tls = "sslmode=verify-full sslrootcert=/root/tls-ca/ca.pem"
        carry(
            sc, MACHINES["ubuntu"].container, MACHINES["replica"].container, "/root/tls-ca/ca.pem"
        )
        clear = other.run(
            f"{password} PGPASSWORD=$PW psql -X -tA '{conn} sslmode=disable' -c IDENTIFY_SYSTEM",
            check=False,
            label="a replication connection without TLS (password hidden)",
        )
        sc.check(
            clear.returncode != 0 and "no pg_hba.conf entry" in clear.stderr,
            f"a connection without TLS finds no line: {clear.stderr}",
        )
        wrong = other.run(
            f"PGPASSWORD=nope psql -X -tA '{conn} {tls}' -c IDENTIFY_SYSTEM",
            check=False,
            label="a replication connection with the wrong password",
        )
        sc.check(
            wrong.returncode != 0 and "password authentication failed" in wrong.stderr,
            f"a wrong password is refused: {wrong.stderr}",
        )
        data = other.run(
            f"{password} PGPASSWORD=$PW psql -X -tA "  # noqa: S608
            f"'host={REPLICA_PRIMARY_HOST} port={port} user=noust_repl_reports dbname=shop {tls}' "
            "-c 'select count(*) from t'",
            check=False,
            label="the replication account reading a table (password hidden)",
        )
        sc.check(
            data.returncode != 0 and "no pg_hba.conf entry" in data.stderr,
            f"the account cannot read data: {data.stderr}",
        )
        good = other.run(
            f"{password} PGPASSWORD=$PW psql -X -tA '{conn} {tls}' -c IDENTIFY_SYSTEM",
            label="a replication connection over verified TLS (password hidden)",
        )
        sc.check("|" in good.stdout, "the right account, over verified TLS, is let in")

        # --- a join that cannot work changes nothing
        rewrite_token(other, token, "/root/bad-password.token", "body['w'] = 'x' * 40")
        planned_bad = noust(
            other, "db replica join --token-file /root/bad-password.token --plan", check=False
        )
        sc.check(planned_bad.returncode == 0, "the plan of a join with a wrong password is fine")
        refused = noust(
            other,
            "db replica join --token-file /root/bad-password.token --replace-data",
            timeout=300,
            check=False,
        )
        sc.check(
            refused.returncode != 0
            and "password authentication failed" in refused.stdout + refused.stderr,
            f"the engine's own words reach the operator: {refused.stdout} {refused.stderr}",
        )
        other.run(
            "mkdir -p /root/other-ca && cd /root/other-ca && openssl req -x509 -newkey rsa:2048 "
            "-nodes -keyout k.pem -out ca.pem -days 2 -subj '/CN=Another CA' 2>/dev/null"
        )
        rewrite_token(
            other,
            token,
            "/root/bad-ca.token",
            "body['c'] = open('/root/other-ca/ca.pem').read()",
        )
        untrusted_join = noust(
            other,
            "db replica join --token-file /root/bad-ca.token --replace-data",
            timeout=300,
            check=False,
        )
        sc.check(
            untrusted_join.returncode != 0
            and "certificate verify failed" in untrusted_join.stdout + untrusted_join.stderr,
            f"a primary whose certificate is not trusted is refused: {untrusted_join.stdout}",
        )
        sc.check(
            psql(
                other, "postgres", "select count(*) from pg_database where datname = 'local_marker'"
            )
            == "1"
            and psql(other, "postgres", "select pg_is_in_recovery()") == "f",
            "the refused joins changed nothing",
        )
        leftovers = other.run(
            "ls -d /var/lib/postgresql/*/main.noust-* /var/lib/postgresql/.noust-replica/* "
            "2>/dev/null | wc -l"
        ).stdout.strip()
        sc.check(leftovers == "0", f"and left nothing behind: {leftovers}")

        # --- a standby that cannot start puts the previous data directory back
        sc.run(f'{PSQL} -c "alter system set max_connections = 300"')
        sc.run("systemctl restart postgresql && sleep 3")
        wedged = noust(
            other,
            f"db replica join --token-file {token} --replace-data",
            timeout=900,
            check=False,
        )
        sc.check(
            wedged.returncode != 0 and "previous data is back" in wedged.stdout + wedged.stderr,
            f"a replica that cannot start says the previous data is back: {wedged.stdout}",
        )
        sc.check(
            psql(
                other, "postgres", "select count(*) from pg_database where datname = 'local_marker'"
            )
            == "1"
            and psql(other, "postgres", "select pg_is_in_recovery()") == "f",
            "this server runs on what it held before",
        )
        sc.check(
            other.run(
                "ls -d /var/lib/postgresql/*/main.noust-failed-replica | wc -l"
            ).stdout.strip()
            == "1",
            "the failed attempt is kept for inspection",
        )
        sc.run(f'{PSQL} -c "alter system reset max_connections"')
        sc.run("systemctl restart postgresql && sleep 3")
        other.run("rm -rf /var/lib/postgresql/*/main.noust-failed-replica")

        # --- the join: a copy of the primary, streaming over TLS
        planned_join = json_of(
            noust(other, f"db replica join --token-file {token} --plan --json"), "join --plan"
        )
        sc.check(
            planned_join["ok"] and "local_marker" in planned_join["databases"], str(planned_join)
        )
        noust(other, f"db replica join --token-file {token} --replace-data", timeout=1800)
        sc.check(psql(other, "postgres", "select pg_is_in_recovery()") == "t", "it is a standby")
        sc.check(
            psql(other, "shop", "select count(*) from t") == "100", "it holds the primary's data"
        )
        sc.check(
            psql(other, "postgres", "select count(*) from pg_roles where rolname = 'app'") == "1",
            "the primary's roles came with it",
        )
        sc.check(
            psql(
                other, "postgres", "select count(*) from pg_database where datname = 'local_marker'"
            )
            == "0",
            "and its own databases are gone from the running server",
        )
        read_only = other.run(f"{PSQL} -d shop -c 'insert into t values (1000)'", check=False)  # noqa: S608
        sc.check(
            read_only.returncode != 0 and "read-only" in read_only.stderr,
            f"the replica refuses writes: {read_only.stderr}",
        )
        conf = other.run(f"{PSQL} -c 'show primary_conninfo'").stdout
        sc.check(
            "sslmode='verify-full'" in conf and "passfile=" in conf and "sslrootcert=" in conf, conf
        )
        sc.check("password=" not in conf, "the password is not in the connection string")
        sc.check(
            other.run(
                "ls -d /var/lib/postgresql/*/main.noust-before-replica | wc -l"
            ).stdout.strip()
            == "1",
            "the replica's own data directory was set aside",
        )

        # --- the primary sees it: streaming, on TLS
        sc.run(f"{PSQL} -d shop -c 'insert into t select g from generate_series(101, 150) g'")  # noqa: S608
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if psql(other, "shop", "select count(*) from t") == "150":
                break
            time.sleep(2)
        sc.check(
            psql(other, "shop", "select count(*) from t") == "150", "what is written there arrives"
        )
        seen = sc.run(
            f'{PSQL} -c "select r.state, s.ssl, s.version from pg_stat_replication r '  # noqa: S608
            'join pg_stat_ssl s on s.pid = r.pid"'
        ).stdout.strip()
        sc.check(seen.startswith("streaming|t|TLS"), f"the walsender is on TLS: {seen}")
        view = json_of(noust(sc, "db replica status -e postgresql --json"), "status on the primary")
        entry = view["replicas"][0]
        sc.check(
            view["role"] == "primary"
            and entry["connected"]
            and entry["tls"] is True
            and entry["state"] == "streaming",
            str(view),
        )
        following = json_of(
            noust(other, "db replica status -e postgresql --json"), "status on the replica"
        )
        sc.check(
            following["role"] == "replica"
            and following["follows"]["state"] == "streaming"
            and following["follows"]["sslmode"] == "verify-full",
            str(following),
        )

        # --- promoted, it stands on its own; removed, the primary is as it was
        noust(other, "db replica promote --yes", timeout=300)
        sc.check(psql(other, "postgres", "select pg_is_in_recovery()") == "f", "it accepts writes")
        psql(other, "shop", "insert into t values (2000)")
        sc.check(
            psql(sc, "shop", "select count(*) from t where id = 2000") == "0",
            "and no longer follows",
        )
        sc.check(
            other.run("ls /var/lib/postgresql/.noust-replica/ 2>/dev/null | wc -l").stdout.strip()
            == "0",
            "its passfile is gone",
        )
        noust(sc, "db replica remove reports --restore-listen --yes", timeout=600)
        sc.check(
            psql(sc, "postgres", "select count(*) from pg_replication_slots") == "0"
            and psql(
                sc, "postgres", "select count(*) from pg_roles where rolname like 'noust_repl%'"
            )
            == "0",
            "the slot and the account are gone",
        )
        sc.check(
            sc.run(
                "grep -c 'noust replication' $(runuser -u postgres -- psql -X -tA "
                "-c 'show hba_file')",
                check=False,
            ).stdout.strip()
            == "0",
            "and so is the pg_hba.conf line",
        )
        sc.check(
            psql(sc, "postgres", "show listen_addresses") == "localhost", "listening as before"
        )
    finally:
        for box in boxes:
            box.run(
                "noust db replica promote --yes; noust db replica remove reports --yes; "
                "noust db tls disable -e postgresql; "
                f"rm -rf {token} /root/bad-*.token /root/other-ca /var/lib/postgresql/*/main.noust-* "
                f"{REPLICA_LINEAGE} /var/lib/postgresql/.noust-replica",
                timeout=300,
                check=False,
                label=f"{tag(box)}cleanup",
            )


def mysql_flags(flavour: str, mode: str, authority: str = "") -> str:
    """
    Write the client options of a TLS mode, in the words of the flavour.

    Args:
        flavour: ``mariadb`` or ``mysql``.
        mode: ``plain`` (TLS off) or ``verify`` (TLS, the authority and the name checked).
        authority: The authority file, for ``verify``.

    Returns:
        The options.
    """
    if mode == "plain":
        return "--skip-ssl" if flavour == "mariadb" else "--ssl-mode=DISABLED"
    if flavour == "mariadb":
        return f"--ssl --ssl-ca={authority} --ssl-verify-server-cert"
    return f"--ssl-mode=VERIFY_IDENTITY --ssl-ca={authority}"


def sql(sc: Any, statement: str) -> str:
    """
    Run SQL on the machine's MySQL or MariaDB as its administrator.

    Args:
        sc: The scenario.
        statement: The statement.

    Returns:
        The client's output, stripped.
    """
    done = sc.run(f"mysql -N -B -e {shlex.quote(statement)}", label=f"{tag(sc)}mysql: {statement}")
    return done.stdout.strip()


def squeeze_packets(sc: Any, flavour: str, *, on: bool) -> None:
    """
    Make a server refuse packets above 1 MB, in a file that survives the restart a join may do.

    Args:
        sc: The scenario of the server.
        flavour: ``mariadb`` or ``mysql``.
        on: Turn it on; off removes the file.
    """
    folder = "mariadb.conf.d" if flavour == "mariadb" else "mysql.conf.d"
    path = f"/etc/mysql/{folder}/zz-noust-test.cnf"
    unit = "mariadb" if flavour == "mariadb" else "mysql"
    change = f"printf '[mysqld]\\nmax_allowed_packet = 1M\\n' > {path}" if on else f"rm -f {path}"
    sc.run(
        f"{change} && systemctl restart {unit} && sleep 3",
        timeout=300,
        label=f"{tag(sc)}{'limit' if on else 'unlimit'} the packets of the server",
    )


def logical_replication(sc: Any, flavour: str) -> None:
    """
    C-14 for MySQL or MariaDB: two real servers on a network, an account that requires TLS.

    The primary gets a certificate for the name the replica uses and is prepared (binary
    logging, GTIDs and the address to listen on in a file of Noust's, an account that requires
    TLS and is allowed from one address); the engine itself refuses that account without TLS and
    with a wrong password; the replica joins with the code: it dumps the primary over verified TLS,
    loads it and follows; a join that cannot trust the certificate or has a wrong password changes
    nothing, and a load the server refuses puts the previous databases back. The replica streams
    on verified TLS and refuses writes; promoted, it stands on its own; removed, the primary is as
    it was.

    Args:
        sc: The scenario of the primary.
        flavour: ``mariadb`` or ``mysql``.
    """
    other = peer(sc, "replica")
    boxes = (sc, other)
    token = "/root/join.token"
    replica_ip = address_on_network(MACHINES["replica"])
    primary_ip = address_on_network(MACHINES["ubuntu"])
    status_sql = "show slave status\\G" if flavour == "mariadb" else "show replica status\\G"
    running = "Slave_IO_Running" if flavour == "mariadb" else "Replica_IO_Running"
    try:
        for box in boxes:
            box.run(
                "noust db replica promote -e mysql --yes; "
                "noust db replica remove reports -e mysql --yes",
                check=False,
                timeout=300,
                label=f"{tag(box)}what an earlier run left",
            )
            # Oracle's repository is checked with gpg, which a minimal image does not have.
            box.run(
                "command -v gpg >/dev/null || { apt-get update -qq && apt-get install -y -qq gnupg; }",
                timeout=300,
                label=f"{tag(box)}gpg, for the signing key of the repository",
            )
            # The generic name installs whichever the distribution ships, MariaDB first: Oracle's
            # MySQL is asked for by version.
            for installed in ("mysql", "mariadb"):
                noust(box, f"db uninstall {installed} --purge -f", timeout=900, check=False)
            noust(
                box,
                "db install mariadb" if flavour == "mariadb" else "db install mysql --version 8.4",
                timeout=1800,
            )
            box.run(
                "grep -v noust.test /etc/hosts > /tmp/hosts.new; "
                f"echo '{primary_ip} {REPLICA_PRIMARY_HOST}' >> /tmp/hosts.new; "
                "cat /tmp/hosts.new > /etc/hosts"
            )
        sc.check(
            sql(sc, "select @@log_bin") == "0" or flavour == "mysql",
            "MariaDB starts without a binary log; Noust turns it on",
        )
        sql(sc, "create database shop")
        sql(sc, "create table shop.t (id int primary key, note varchar(20))")
        sql(
            sc,
            "insert into shop.t (id, note) with recursive n(i) as (select 1 union all "
            "select i + 1 from n where i < 100) select i, concat('row', i) from n",
        )
        sql(sc, "create database blog")
        sql(sc, "create table blog.posts (id int primary key, body longblob)")
        sql(sc, "create function blog.seven() returns int deterministic return 7")
        sql(other, "create database local_marker")

        # --- refused while it cannot be reached or trusted, before anything is made
        prepare = f"db replica prepare reports -e mysql --from {replica_ip} --host {REPLICA_PRIMARY_HOST} "
        no_tls = noust(sc, f"{prepare} --listen '*' --plan", check=False)
        sc.check(
            no_tls.returncode != 0 and "db tls enable" in no_tls.stdout + no_tls.stderr,
            f"a primary whose certificate nobody can verify is refused with the fix: {no_tls.stdout}",
        )
        issue_certificate(sc, directory=REPLICA_LINEAGE, names=REPLICA_PRIMARY_HOST)
        noust(sc, f"db tls enable -e mysql --host {REPLICA_PRIMARY_HOST} --yes", timeout=600)
        if flavour == "mariadb":  # MySQL listens on every address by default
            loopback = noust(sc, f"{prepare} --ca-file /root/tls-ca/ca.pem --plan", check=False)
            sc.check(
                loopback.returncode != 0 and "listens on" in loopback.stdout + loopback.stderr,
                f"a primary on the loopback says so: {loopback.stdout}",
            )
        wrong_name = noust(
            sc,
            f"db replica prepare reports -e mysql --from {replica_ip} --host other.noust.test "
            "--listen '*' --ca-file /root/tls-ca/ca.pem --plan",
            check=False,
        )
        sc.check(
            wrong_name.returncode != 0
            and "would not be accepted" in wrong_name.stdout + wrong_name.stderr,
            f"a name the certificate does not cover is refused: {wrong_name.stdout}",
        )
        sc.check(
            sql(sc, "select count(*) from mysql.user where user like 'noust_repl%'") == "0",
            "the refusals made nothing",
        )

        # --- prepared
        planned = json_of(
            noust(sc, f"{prepare} --listen '*' --ca-file /root/tls-ca/ca.pem --plan --json"),
            "prepare --plan",
        )
        sc.check(planned["restart"] is True and planned["ok"], str(planned))
        noust(
            sc,
            f"{prepare} --listen '*' --ca-file /root/tls-ca/ca.pem --open-firewall "
            f"--token-file {token} --yes",
            timeout=900,
        )
        sc.check(sql(sc, "select @@log_bin") == "1", "binary logging is on")
        sc.check(
            sql(sc, "select @@bind_address") in ("0.0.0.0", "*", "::"),  # noqa: S104
            "the server listens where the replica can reach it",
        )
        sc.check(
            sql(sc, "select ssl_type from mysql.user where user = 'noust_repl_reports'") == "ANY",
            "the account requires TLS",
        )
        sc.check(
            sql(sc, "select host from mysql.user where user = 'noust_repl_reports'") == replica_ip,
            "and is allowed from the replica's address only",
        )
        if flavour == "mysql":
            sc.check(sql(sc, "select @@gtid_mode") == "ON", "MySQL has GTIDs on")

        # --- the engine itself refuses what is not TLS, and a wrong password
        carry(sc, MACHINES["ubuntu"].container, MACHINES["replica"].container, token)
        carry(
            sc, MACHINES["ubuntu"].container, MACHINES["replica"].container, "/root/tls-ca/ca.pem"
        )
        password = (
            'PW=$(python3 -c "import base64,json;'
            f"print(json.loads(base64.urlsafe_b64decode(open('{token}').read().strip()"
            ".split(':',2)[2]))['w'])\"); "
        )
        remote = f"-h {REPLICA_PRIMARY_HOST} -P 3306 -u noust_repl_reports"
        verified = mysql_flags(flavour, "verify", "/root/tls-ca/ca.pem")
        clear = other.run(
            f"{password} MYSQL_PWD=$PW mysql {remote} {mysql_flags(flavour, 'plain')} -e 'select 1'",
            check=False,
            label=f"{tag(other)}the account without TLS (password hidden)",
        )
        sc.check(
            clear.returncode != 0
            and re.search(r"Access denied|requires secure connection", clear.stderr),
            f"the engine refuses the account without TLS: {clear.stderr}",
        )
        wrong = other.run(
            f"MYSQL_PWD=nope mysql {remote} {verified} -e 'select 1'",
            check=False,
            label=f"{tag(other)}the account with a wrong password",
        )
        sc.check(
            wrong.returncode != 0 and "Access denied" in wrong.stderr,
            f"a wrong password is refused: {wrong.stderr}",
        )
        reads = other.run(
            f"{password} MYSQL_PWD=$PW mysql {remote} {verified} -N "  # noqa: S608
            "-e 'select count(*) from shop.t'",
            label=f"{tag(other)}the account over verified TLS (password hidden)",
        )
        sc.check(reads.stdout.strip() == "100", "over verified TLS it reads, for the first copy")
        writes = other.run(
            f"{password} MYSQL_PWD=$PW mysql {remote} {verified} -e 'create table shop.x (i int)'",
            check=False,
            label=f"{tag(other)}the account writing (password hidden)",
        )
        sc.check(
            writes.returncode != 0 and "denied" in writes.stderr,
            f"and it cannot write: {writes.stderr}",
        )

        # --- a join that cannot work changes nothing
        rewrite_token(other, token, "/root/bad-password.token", "body['w'] = 'x' * 40")
        refused = noust(
            other,
            "db replica join --token-file /root/bad-password.token --replace-data",
            timeout=600,
            check=False,
        )
        sc.check(
            refused.returncode != 0 and "Access denied" in refused.stdout + refused.stderr,
            f"the engine's own words reach the operator: {refused.stdout} {refused.stderr}",
        )
        other.run(
            "mkdir -p /root/other-ca && cd /root/other-ca && openssl req -x509 -newkey rsa:2048 "
            "-nodes -keyout k.pem -out ca.pem -days 2 -subj '/CN=Another CA' 2>/dev/null"
        )
        rewrite_token(
            other, token, "/root/bad-ca.token", "body['c'] = open('/root/other-ca/ca.pem').read()"
        )
        untrusted_join = noust(
            other,
            "db replica join --token-file /root/bad-ca.token --replace-data",
            timeout=600,
            check=False,
        )
        text = untrusted_join.stdout + untrusted_join.stderr
        sc.check(
            untrusted_join.returncode != 0
            and re.search(r"certificate verify failed|SSL connection error|TLS/SSL error", text),
            f"a primary whose certificate is not trusted is refused: {text}",
        )
        sc.check(
            sql(
                other,
                "select count(*) from information_schema.schemata where schema_name = 'local_marker'",
            )
            == "1"
            and other.run(f"mysql -e '{status_sql}'").stdout.strip() == "",
            "the refused joins changed nothing",
        )
        sc.check(
            other.run(
                "find /etc/mysql /etc/ssl/noust-db/mysql-replica \\( -name 99-noust-replica.cnf "
                "-o -name ca.pem \\) 2>/dev/null | wc -l"
            ).stdout.strip()
            == "0",
            "and left nothing behind",
        )

        # --- a load the server refuses puts the previous databases back
        sql(sc, "insert into blog.posts values (1, repeat('x', 3000000))")
        squeeze_packets(other, flavour, on=True)
        failed = noust(
            other,
            "db replica join --token-file /root/join.token --replace-data",
            timeout=1200,
            check=False,
        )
        text = failed.stdout + failed.stderr
        sc.check(
            failed.returncode != 0 and "previous databases are back" in text,
            f"a load the server refuses says the previous databases are back: {text}",
        )
        sc.check(
            sql(
                other,
                "select count(*) from information_schema.schemata where schema_name = 'local_marker'",
            )
            == "1"
            and other.run(f"mysql -e '{status_sql}'").stdout.strip() == "",
            "this server holds what it held before",
        )
        sc.check(sql(other, "select @@read_only") == "0", "and accepts writes as before")
        squeeze_packets(other, flavour, on=False)
        sql(sc, "delete from blog.posts")

        # --- the join
        planned_join = json_of(
            noust(other, "db replica join --token-file /root/join.token --plan --json"),
            "join --plan",
        )
        sc.check(
            planned_join["ok"] and "local_marker" in planned_join["databases"], str(planned_join)
        )
        noust(other, "db replica join --token-file /root/join.token --replace-data", timeout=1800)
        sc.check(sql(other, "select count(*) from shop.t") == "100", "it holds the primary's data")
        sc.check(sql(other, "select blog.seven()") == "7", "and its routines")
        sc.check(
            sql(
                other,
                "select count(*) from information_schema.schemata where schema_name = 'local_marker'",
            )
            == "0",
            "its own databases are replaced",
        )
        sc.check(
            int(other.run("ls /var/backups/noust/databases/*local_marker* | wc -l").stdout) >= 1,
            "after being dumped with the ordinary backup",
        )
        sc.check(sql(other, "select @@read_only") == "1", "the replica refuses ordinary writes")
        # An account made on the primary after the join reaches the replica with everything else.
        sql(sc, "create user 'app'@'localhost' identified by 'apppass'")
        sql(sc, "grant select, insert on shop.* to 'app'@'localhost'")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if sql(other, "select count(*) from mysql.user where user = 'app'") == "1":
                break
            time.sleep(2)
        sc.check(
            sql(other, "select count(*) from mysql.user where user = 'app'") == "1",
            "an account made on the primary afterwards replicates",
        )
        refused_write = other.run(
            "mysql -u app -papppass -e 'insert into shop.t values (1000, \"x\")'", check=False
        )
        sc.check(
            refused_write.returncode != 0 and "read-only" in refused_write.stderr,
            f"an ordinary account cannot write on the replica: {refused_write.stderr}",
        )
        if flavour == "mysql":
            super_write = other.run(
                "mysql -e 'insert into shop.t values (1000, \"x\")'", check=False
            )
            sc.check(
                super_write.returncode != 0 and "read-only" in super_write.stderr,
                f"MySQL's super_read_only refuses even the administrator: {super_write.stderr}",
            )
        verified_status = other.run(f"mysql -e '{status_sql}'").stdout
        sc.check(
            f"{running}: Yes" in verified_status
            and re.search(r"(Master|Source)_SSL_Verify_Server_Cert: Yes", verified_status)
            is not None
            and re.search(r"(Master|Source)_SSL_Allowed: Yes", verified_status) is not None,
            "the replica follows over verified TLS",
        )
        sql(sc, "insert into shop.t values (101, 'row101'), (102, 'row102')")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if sql(other, "select count(*) from shop.t") == "102":
                break
            time.sleep(2)
        sc.check(
            sql(other, "select count(*) from shop.t") == "102", "what is written there arrives"
        )
        view = json_of(noust(sc, "db replica status -e mysql --json"), "status on the primary")
        entry = view["replicas"][0]
        sc.check(
            view["role"] == "primary" and entry["connected"] and entry["tls"] is True, str(view)
        )
        following = json_of(
            noust(other, "db replica status -e mysql --json"), "status on the replica"
        )
        sc.check(
            following["role"] == "replica"
            and following["follows"]["state"] == "streaming"
            and following["follows"]["sslmode"] == "verify-full",
            str(following),
        )

        # --- promoted, it stands on its own; removed, the primary is as it was
        noust(other, "db replica promote -e mysql --yes", timeout=300)
        sc.check(sql(other, "select @@read_only") == "0", "it accepts writes")
        sql(other, "insert into shop.t values (2000, 'own')")
        sc.check(
            sql(sc, "select count(*) from shop.t where id = 2000") == "0", "and no longer follows"
        )
        sc.check(other.run(f"mysql -e '{status_sql}'").stdout.strip() == "", "it follows nothing")
        noust(sc, "db replica remove reports -e mysql --restore-listen --yes", timeout=900)
        sc.check(
            sql(sc, "select count(*) from mysql.user where user like 'noust_repl%'") == "0",
            "the account is gone",
        )
        sc.check(
            sc.run("ls /etc/mysql/*/99-noust-replication.cnf 2>/dev/null | wc -l").stdout.strip()
            == "0",
            "and so are Noust's settings",
        )
        if flavour == "mariadb":
            sc.check(sql(sc, "select @@bind_address") == "127.0.0.1", "listening as before")
        else:
            sc.check(sql(sc, "select @@gtid_mode") == "OFF", "GTIDs as before")
    finally:
        for box in boxes:
            box.run(
                "noust db replica promote -e mysql --yes; "
                "noust db replica remove reports -e mysql --yes; "
                "noust db tls disable -e mysql --yes; "
                f"rm -rf {token} /root/bad-*.token /root/other-ca {REPLICA_LINEAGE} "
                "/etc/mysql/*/99-noust-repl*.cnf /etc/ssl/noust-db/mysql-replica",
                timeout=600,
                check=False,
                label=f"{tag(box)}cleanup",
            )


@engines_scenario("replication_mariadb", also=("replica",))
def scenario_replication_mariadb(sc: Any) -> None:
    """C-14: a MariaDB replica on another server, over verified TLS (see logical_replication)."""
    logical_replication(sc, "mariadb")


@engines_scenario("replication_mysql", also=("replica",))
def scenario_replication_mysql(sc: Any) -> None:
    """C-14: a MySQL replica on another server, over verified TLS (see logical_replication)."""
    logical_replication(sc, "mysql")


# ---------------------------------------------------------------------------
# Dragonfly: what decides whether it can be offered
# ---------------------------------------------------------------------------

DRAGONFLY_IMAGE = "docker.dragonflydb.io/dragonflydb/dragonfly:latest"
DRAGONFLY_BOX = f"{PREFIX}-df"


def dragonfly(sc: Any, *flags: str) -> None:
    """
    Start a Dragonfly in the machine's own Docker with its data in a bind-mounted directory.

    Args:
        sc: The scenario.
        *flags: Command-line flags of the server.
    """
    sc.run(
        f"docker rm -f {DRAGONFLY_BOX} >/dev/null 2>&1; rm -rf /tmp/dfdata; mkdir -p /tmp/dfdata; "
        f"chmod 777 /tmp/dfdata; docker run -d --name {DRAGONFLY_BOX} --label {LABEL} "
        f"--ulimit memlock=-1 -v /tmp/dfdata:/data {DRAGONFLY_IMAGE} "
        f"--requirepass sekret --dir /data --dbfilename dump {' '.join(flags)} && sleep 5",
        timeout=120,
        label=f"start Dragonfly {' '.join(flags)}",
    )


def df(sc: Any, *args: str, user: str = "", stdin: str = "") -> Any:
    """
    Run redis-cli against the scenario's Dragonfly.

    Args:
        sc: The scenario.
        *args: The command.
        user: An account to sign in as ("name:password"); the administrator when empty.
        stdin: Commands to send on the client's standard input.

    Returns:
        The finished command.
    """
    auth = "-a sekret"
    if user:
        name, _, password = user.partition(":")
        auth = f"--user {name} --pass {password}"
    quoted = " ".join(shlex.quote(item) for item in args)
    feed = f"printf %b {shlex.quote(stdin)} | " if stdin else ""
    flag = "-i" if stdin else ""
    return sc.run(
        f"{feed}docker exec {flag} {DRAGONFLY_BOX} redis-cli {auth} --no-auth-warning {quoted}",
        check=False,
        label=f"redis-cli {'(as ' + user.split(':')[0] + ') ' if user else ''}{quoted or stdin.strip()}",
    )


@engines_scenario("dragonfly_facts")
def scenario_dragonfly_facts(sc: Any) -> None:
    """
    Dragonfly's snapshot format and ACL, checked against the real server (the facts docs/databases.md states).

    Its default snapshot is a family of ``.dfs`` files; with ``--df_snapshot_format=false`` it is
    one RDB file that a Redis tool reads and that a restore brings back. It stores passwords as
    hashes but refuses a hash as input, and keeps its accounts across a restart in the file it is
    given.
    """
    pull(sc, DRAGONFLY_IMAGE)
    pull(sc, "redis:7")
    try:
        dragonfly(sc)
        df(sc, "SET", "k1", "v1")
        sc.check(df(sc, "SAVE").stdout.strip() == "OK", "SAVE should work")
        files = sc.run("ls /tmp/dfdata").stdout.split()
        sc.check(
            len(files) > 2 and all(name.endswith(".dfs") for name in files),
            f"by default a snapshot is a family of .dfs files: {files}",
        )

        dragonfly(sc, "--df_snapshot_format=false", "--aclfile=/data/users.acl")
        for key in ("k1", "k2", "k3"):
            df(sc, "SET", key, "v")
        info = df(sc, "INFO", "persistence").stdout
        sc.check(
            "rdb_bgsave_in_progress:0" in info, "INFO persistence has the field Noust waits on"
        )
        sc.check(df(sc, "BGSAVE").stdout.strip() == "OK", "BGSAVE should work")
        time.sleep(2)
        sc.check(sc.run("ls /tmp/dfdata").stdout.split() == ["dump.rdb"], "one RDB file")
        checked = sc.run(
            f"docker run --rm --label {LABEL} -v /tmp/dfdata:/d redis:7 redis-check-rdb /d/dump.rdb 2>&1",
            timeout=120,
        ).stdout
        sc.check("3 keys read" in checked, f"a Redis tool reads the snapshot: {checked[-300:]}")
        rdb = df(sc, "--rdb", "/tmp/remote.rdb")
        sc.check(
            "SYNC" in rdb.stdout + rdb.stderr and rdb.returncode != 0,
            "redis-cli --rdb needs SYNC, which Dragonfly does not have",
        )

        digest = sc.run("printf pw1 | sha256sum | cut -d' ' -f1").stdout.strip()
        hashed = df(sc, "ACL", "SETUSER", "hashed", "on", f"#{digest}", "~*", "+@all")
        sc.check(
            "Unrecognized parameter" in hashed.stdout + hashed.stderr,
            f"a password already hashed is refused: {hashed.stdout}",
        )
        made = df(sc, stdin="ACL SETUSER alice on >pw1 ~* +@read\nACL SAVE\n")
        sc.check(
            made.stdout.split() == ["OK", "OK"], f"a plaintext password is accepted: {made.stdout}"
        )
        sc.check(df(sc, "GET", "k1", user="alice:pw1").stdout.strip() == "v", "and it signs in")
        denied = df(sc, "SET", "x", "y", user="alice:pw1")
        sc.check("NOPERM" in denied.stdout + denied.stderr, "with the rules given (read only)")
        wrong = df(sc, "GET", "k1", user="alice:nope")
        sc.check(
            wrong.stdout.strip().startswith("NOAUTH") or "AUTH failed" in wrong.stderr,
            "a wrong password is refused",
        )
        listed = df(sc, "ACL", "LIST").stdout
        sc.check(
            digest[:15] in listed and digest not in listed,
            "ACL LIST shows only the start of a hash",
        )
        saved = sc.run("cat /tmp/dfdata/users.acl").stdout
        sc.check(digest in saved.lower(), "and the file keeps all of it")

        sc.run(f"docker restart {DRAGONFLY_BOX} && sleep 5", timeout=60)
        sc.check(
            df(sc, "GET", "k1", user="alice:pw1").stdout.strip() == "v",
            "the account and its password survive a restart",
        )
        sc.check(df(sc, "DBSIZE").stdout.strip() == "3", "and so does the data")

        df(sc, "SET", "only_in_snapshot", "yes")
        df(sc, "BGSAVE")
        time.sleep(2)
        sc.run("cp /tmp/dfdata/dump.rdb /tmp/dfdata.keep")
        df(sc, "DEL", "only_in_snapshot")
        sc.run(
            f"docker stop {DRAGONFLY_BOX} && cp /tmp/dfdata.keep /tmp/dfdata/dump.rdb && "
            f"docker start {DRAGONFLY_BOX} && sleep 5",
            timeout=60,
        )
        sc.check(
            df(sc, "GET", "only_in_snapshot").stdout.strip() == "yes",
            "replacing the snapshot and starting restores it",
        )
    finally:
        sc.run(
            f"docker rm -f {DRAGONFLY_BOX}; rm -rf /tmp/dfdata /tmp/dfdata.keep",
            check=False,
            label="cleanup",
        )


# ---------------------------------------------------------------------------
# 4.0 (W6-DB): a dump is not a superuser's script
# ---------------------------------------------------------------------------

#: What the dumps below try to do on the server: leave a file only a program could.
H19_MARK = "/tmp/noust-h19-ran"


def write_file(sc: Any, path: str, text: str) -> None:
    """
    Write a file inside the scenario's machine, dressed as a pg_dump (header and trailer).

    Args:
        sc: The scenario.
        path: Where.
        text: What it holds between them.
    """
    sc.run(
        f"cat > {path} <<'NOUST_EOF'\n-- PostgreSQL database dump\n{text}\n"
        "-- PostgreSQL database dump complete\nNOUST_EOF",
        label=f"write {path}",
    )


def loader_roles(sc: Any) -> str:
    """
    Args:
        sc: The scenario.

    Returns:
        The logins of Noust's loaders the server still holds, one per line.
    """
    return sc.run(
        f"{PSQL} -tAc \"select rolname from pg_roles where rolname like 'noust\\_load\\_%'\"",  # noqa: S608 - fixed text
        label="loader logins left",
    ).stdout.strip()


@engines_scenario("restore_unprivileged")
def scenario_restore_unprivileged(sc: Any) -> None:
    """
    H-19: a dump from elsewhere is loaded as the database's owner, so it cannot run a program.

    Against a real PostgreSQL: ``COPY ... FROM PROGRAM`` is refused (nothing runs, the database is
    put back, no loader login is left), a dump that tries ``RESET ROLE`` first is refused too,
    ordinary dumps (owners, ACL, a trusted extension) load and their objects belong to the
    database's owner, a database owned by the superuser is loaded by a login that owns what it
    makes and hands it over, Noust's own backup of a database with an extension that needs a
    superuser still restores, and ``--as-superuser`` is the one explicit way to let a dump run a
    program.
    """
    noust(sc, "db uninstall postgresql --purge -f", timeout=900, check=False)
    noust(sc, "db install postgresql", timeout=1800)
    sc.run(f"rm -f {H19_MARK}")
    psql(sc, "postgres", "create role shopapp login password 'x1y2z3w4'")
    noust(sc, "db create shop --engine postgresql --owner shopapp")
    psql(sc, "shop", "create table keep(i int); insert into keep select generate_series(1, 5)")
    psql(sc, "shop", "alter table keep owner to shopapp")

    # --- COPY ... FROM PROGRAM is refused, and nothing is left behind
    write_file(
        sc,
        f"{SCRATCH}/evil.sql",
        "SET standard_conforming_strings = on;\n"
        "CREATE TABLE loot (line text);\n"
        f"COPY loot FROM PROGRAM 'touch {H19_MARK}';\n",
    )
    refused = noust(
        sc, f"db import shop {SCRATCH}/evil.sql -e postgresql -f", check=False, timeout=300
    )
    said = refused.stdout + refused.stderr
    sc.check(refused.returncode != 0, f"a dump that runs a program must be refused: {said[:400]}")
    sc.check("--as-superuser" in said, f"the refusal should say how to repeat it: {said[-600:]}")
    sc.check(
        sc.run(f"test ! -e {H19_MARK}", check=False).returncode == 0,
        "the program ran on the server",
    )
    sc.check(psql(sc, "shop", "select count(*) from keep") == "5", "the database was not put back")
    sc.check(loader_roles(sc) == "", f"a loader login was left behind: {loader_roles(sc)}")

    # --- the superuser cannot be handed back from inside the script
    write_file(
        sc,
        f"{SCRATCH}/reset.sql",
        "SET standard_conforming_strings = on;\n"
        "RESET ROLE;\nRESET SESSION AUTHORIZATION;\n"
        "CREATE TABLE loot2 (line text);\n"
        f"COPY loot2 FROM PROGRAM 'touch {H19_MARK}';\n",
    )
    reset = noust(
        sc, f"db import shop {SCRATCH}/reset.sql -e postgresql -f", check=False, timeout=300
    )
    sc.check(reset.returncode != 0, "RESET ROLE must not give the superuser back")
    sc.check(sc.run(f"test ! -e {H19_MARK}", check=False).returncode == 0, "RESET ROLE worked")
    write_file(
        sc,
        f"{SCRATCH}/auth.sql",
        "SET standard_conforming_strings = on;\n"
        "SET SESSION AUTHORIZATION postgres;\n"
        "CREATE TABLE loot3 (line text);\n",
    )
    auth = noust(
        sc, f"db import shop {SCRATCH}/auth.sql -e postgresql -f", check=False, timeout=300
    )
    sc.check(auth.returncode != 0, "SET SESSION AUTHORIZATION postgres must be refused")

    # --- an ordinary dump of another database loads, and its objects belong to the owner
    psql(sc, "postgres", "create role legacy login")
    psql(sc, "postgres", "create database origin owner legacy")
    psql(
        sc,
        "origin",
        "create extension pg_trgm; create table people(id serial primary key, name text); "
        "insert into people(name) select 'p' || g from generate_series(1, 30) g; "
        "alter table people owner to legacy; grant select on people to public",
    )
    sc.run(f"runuser -u postgres -- pg_dump origin > {SCRATCH}/origin.sql", label="pg_dump (plain)")
    sc.run(
        f"runuser -u postgres -- pg_dump -Fc origin > {SCRATCH}/origin.dump", label="pg_dump -Fc"
    )
    noust(sc, f"db import shop {SCRATCH}/origin.sql -e postgresql -f", timeout=300)
    sc.check(psql(sc, "shop", "select count(*) from people") == "30", "the plain dump loaded")
    sc.check(
        psql(sc, "shop", "select tableowner from pg_tables where tablename = 'people'")
        == "shopapp",
        "the objects of the dump belong to the database's owner, not to the roles it came from",
    )
    noust(sc, f"db import shop2 {SCRATCH}/origin.dump -e postgresql -f", timeout=300)
    sc.check(psql(sc, "shop2", "select count(*) from people") == "30", "the archive loaded")
    owner = psql(sc, "shop2", "select tableowner from pg_tables where tablename = 'people'")
    sc.check(
        owner == "postgres", f"a database made by the import is owned as it always was: {owner}"
    )
    sc.check(loader_roles(sc) == "", f"a loader login was left behind: {loader_roles(sc)}")

    # --- Noust's own backup of a database with an extension that needs a superuser comes back
    psql(sc, "shop", "create extension file_fdw")
    noust(sc, "db backup shop --engine postgresql", timeout=300)
    psql(sc, "shop", "drop extension file_fdw cascade; delete from keep")
    newest_dump = sc.run(
        "ls -1t /var/backups/noust/databases/postgresql-shop-* | head -1", label="newest backup"
    ).stdout.strip()
    noust(sc, f"db restore shop {newest_dump} -e postgresql --drop -f", timeout=300)
    sc.check(
        psql(sc, "shop", "select count(*) from pg_extension where extname = 'file_fdw'") == "1",
        "Noust's own backup should put an extension that needs a superuser back",
    )
    # The same objects in a dump from anywhere else are not Noust's to vouch for.
    sc.run(f"runuser -u postgres -- pg_dump shop > {SCRATCH}/shop-elsewhere.sql", label="pg_dump")
    psql(sc, "shop", "drop extension file_fdw cascade")
    elsewhere = noust(
        sc,
        f"db import shop {SCRATCH}/shop-elsewhere.sql -e postgresql -f",
        check=False,
        timeout=300,
    )
    sc.check(
        elsewhere.returncode != 0 and "--as-superuser" in elsewhere.stdout + elsewhere.stderr,
        f"an extension that needs a superuser needs the operator to say so: {elsewhere.stdout[-500:]}",
    )

    # --- the explicit way: audited, and a program does run
    explicit = noust(
        sc,
        f"db import shop {SCRATCH}/evil.sql -e postgresql -f --as-superuser",
        timeout=300,
        check=False,
    )
    sc.check(explicit.returncode == 0, f"--as-superuser should load it: {explicit.stdout[-400:]}")
    sc.check(
        sc.run(f"test -e {H19_MARK}", check=False).returncode == 0,
        "--as-superuser should let the program run (that is what it asks for)",
    )
    trail = noust(sc, "audit list --action db.restore.superuser --limit 5", check=False)
    sc.check(
        "db.restore.superuser" in trail.stdout, "the superuser load should be on the audit trail"
    )

    # --- a login left by a process that died is swept by the next load
    psql(
        sc,
        "postgres",
        "create role noust_load_deadbeef login valid until '2020-01-01'",
    )
    noust(sc, f"db import shop2 {SCRATCH}/origin.dump -e postgresql -f --replace", timeout=300)
    sc.check(
        loader_roles(sc) == "", f"the dead loader login should have been swept: {loader_roles(sc)}"
    )

    sc.run(f"rm -f {H19_MARK}")
    for name in ("shop", "shop2", "origin"):
        noust(sc, f"db drop {name} --engine postgresql -f", check=False)
    noust(sc, "db uninstall postgresql --purge -f", timeout=900)


#: The pg_dumpall of PostgreSQL 16 the unit tests read, written by a real server: four accounts
#: (one a superuser), a membership and three databases, two with rows.
CLUSTER_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "pg_dumpall" / "all16.sql"
GLOBALS_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "pg_dumpall" / "globals16.sql"
H18_MARK = "/tmp/noust-h18-ran"


def drop_everything_of_the_fixture(sc: Any) -> None:
    """
    Leave the server without the databases and accounts the cluster fixture names.

    Args:
        sc: The scenario.
    """
    for name in ("shop", "blog", "evil"):
        sc.run(f"{PSQL} -c 'drop database if exists {name}'", check=False, label=f"drop {name}")
    for name in ("shopapp", "reporting", "legacy", "bossman"):
        sc.run(f"{PSQL} -c 'drop role if exists {name}'", check=False, label=f"drop role {name}")


@engines_scenario("import_cluster_postgresql")
def scenario_import_cluster_postgresql(sc: Any) -> None:
    """
    H-18: a pg_dumpall of another server comes in: every database, and its accounts when asked.

    The file is what a real PostgreSQL 16 wrote for four accounts and three databases. Without
    ``--users`` the databases are created and loaded and no account is made; with it, the accounts
    come with the password hash they had (the old password signs in), without the superuser
    power one of them had, and the databases belong to them with the privileges the dump gave.
    An account that exists is never touched. A database of a hostile file that runs a program
    fails alone, and the others load.
    """
    noust(sc, "db uninstall postgresql --purge -f", timeout=900, check=False)
    noust(sc, "db install postgresql", timeout=1800)
    sc.run(f"rm -f {H18_MARK}")
    sh(["docker", "cp", str(CLUSTER_FIXTURE), f"{sc.container}:{SCRATCH}/all16.sql"], timeout=60)
    sh(
        ["docker", "cp", str(GLOBALS_FIXTURE), f"{sc.container}:{SCRATCH}/globals16.sql"],
        timeout=60,
    )
    dump = f"{SCRATCH}/all16.sql"

    # --- the plan changes nothing and says what would happen
    plan = json_of(noust(sc, f"db import-all {dump} -e postgresql --users --plan --json"), "plan")
    actions = {d["name"]: d["action"] for d in plan["databases"]}
    sc.check(
        actions == {"template1": "skip", "blog": "create", "postgres": "skip", "shop": "create"},
        f"the plan should create the two user databases: {actions}",
    )
    roles = {r["name"]: r for r in plan["roles"]}
    sc.check(
        roles["bossman"]["dropped"] == ["SUPERUSER"] and roles["postgres"]["action"] == "skip",
        f"the plan should name what is not carried: {roles}",
    )
    sc.check(
        psql(sc, "postgres", "select count(*) from pg_database where datname in ('shop', 'blog')")
        == "0",
        "planning created a database",
    )

    # --- without --users: the databases, no accounts
    noust(sc, f"db import-all {dump} -e postgresql -f", timeout=300)
    sc.check(psql(sc, "shop", "select count(*) from orders") == "4", "shop was not loaded")
    sc.check(psql(sc, "blog", "select count(*) from posts") == "2", "blog was not loaded whole")
    sc.check(
        psql(sc, "postgres", "select count(*) from pg_roles where rolname = 'shopapp'") == "0",
        "an account was made without asking for them",
    )
    sc.check(
        psql(sc, "shop", "select tableowner from pg_tables where tablename = 'orders'")
        == "postgres",
        "without the accounts everything belongs to the server's own",
    )

    # --- with --users: the accounts, their passwords and their databases
    refused = noust(
        sc, f"db import-all {dump} -e postgresql --users --replace", check=False, timeout=60
    )
    sc.check(refused.returncode == 0, "declining by closing stdin should be a quiet no")
    sc.check(
        psql(sc, "postgres", "select count(*) from pg_roles where rolname = 'shopapp'") == "0",
        "the accounts were made without being accepted",
    )
    noust(sc, f"db import-all {dump} -e postgresql --users --replace -f", timeout=300)
    sc.check(
        psql(
            sc,
            "postgres",
            "select rolname || ':' || rolsuper || ':' || rolcanlogin || ':' || rolconnlimit "
            "from pg_roles where rolname in ('bossman', 'legacy', 'reporting', 'shopapp') "
            "order by 1",
        )
        == "bossman:false:true:-1\nlegacy:false:true:5\nreporting:false:false:-1\nshopapp:false:true:-1",
        "the accounts should be there, none a superuser",
    )
    signed_in = sc.run(
        "PGPASSWORD=secretpw psql -h 127.0.0.1 -U shopapp -d shop -tAc 'select count(*) from orders'",
        label="sign in as shopapp with the password it had",
    )
    sc.check(signed_in.stdout.strip() == "4", "the password hash should have come with the account")
    sc.check(
        psql(sc, "shop", "select tableowner from pg_tables where tablename = 'orders'")
        == "shopapp",
        "the table should belong to the account that owned it",
    )
    sc.check(
        psql(sc, "postgres", "select datdba::regrole from pg_database where datname = 'blog'")
        == "legacy",
        "the database should belong to its account",
    )
    sc.check(
        psql(sc, "shop", "select has_table_privilege('reporting', 'orders', 'select')") == "t",
        "the privilege the dump gave should be kept",
    )
    sc.check(
        psql(sc, "postgres", "select pg_has_role('shopapp', 'reporting', 'member')") == "t",
        "the membership between the accounts should be kept",
    )
    sc.check(
        psql(sc, "shop", "select proowner::regrole from pg_proc where proname = 'f'") == "shopapp",
        "what the source's superuser owned goes to the database's owner",
    )
    sc.check(loader_roles(sc) == "", f"a loader login was left behind: {loader_roles(sc)}")

    # --- an account that exists is never touched
    drop_everything_of_the_fixture(sc)
    psql(sc, "postgres", "create role shopapp login password 'mine-not-theirs'")
    noust(sc, f"db import-all {dump} -e postgresql --users -f", timeout=300)
    still = sc.run(
        "PGPASSWORD=mine-not-theirs psql -h 127.0.0.1 -U shopapp -d postgres -tAc 'select 1'",
        check=False,
        label="the password of the account that was already here",
    )
    sc.check(still.stdout.strip() == "1", "the account that existed lost its password")
    sc.check(
        psql(sc, "postgres", "select pg_has_role('shopapp', 'reporting', 'member')") == "f",
        "a membership was granted to an account that was not made now",
    )

    # --- a hostile file: its database fails alone, the program does not run
    sc.run(f"cp {dump} {SCRATCH}/hostile.sql")
    sc.run(
        f"cat >> {SCRATCH}/hostile.sql <<'NOUST_EOF'\n--\n-- Database \"evil\" dump\n--\n\n"
        "CREATE DATABASE evil WITH TEMPLATE = template0 ENCODING = 'UTF8';\n\n"
        "\\connect evil\n\nSET standard_conforming_strings = on;\nCREATE TABLE loot (l text);\n"
        f"COPY loot FROM PROGRAM 'touch {H18_MARK}';\n-- PostgreSQL database dump complete\n"
        "NOUST_EOF",
        label="a database of the file that runs a program",
    )
    drop_everything_of_the_fixture(sc)
    hostile = noust(
        sc, f"db import-all {SCRATCH}/hostile.sql -e postgresql -f", check=False, timeout=300
    )
    said = hostile.stdout + hostile.stderr
    sc.check(
        hostile.returncode == 1, f"a database that fails should be the exit status: {said[-500:]}"
    )
    sc.check("evil was not imported" in said, f"the failure should be told: {said[-500:]}")
    sc.check(
        sc.run(f"test ! -e {H18_MARK}", check=False).returncode == 0,
        "the program of the hostile database ran",
    )
    sc.check(psql(sc, "shop", "select count(*) from orders") == "4", "the others should load")
    sc.check(
        psql(sc, "postgres", "select count(*) from pg_database where datname = 'evil'") == "0",
        "the database that failed should not stay behind",
    )

    # --- a file of accounts alone
    drop_everything_of_the_fixture(sc)
    noust(sc, f"db import-all {SCRATCH}/globals16.sql -e postgresql --users -f", timeout=120)
    sc.check(
        psql(sc, "postgres", "select count(*) from pg_roles where rolname in ('shopapp', 'legacy')")
        == "2",
        "a file of accounts should make them",
    )
    sc.check(
        psql(sc, "postgres", "select count(*) from pg_database where datname = 'shop'") == "0",
        "and no database",
    )

    drop_everything_of_the_fixture(sc)
    sc.run(f"rm -f {H18_MARK}")
    noust(sc, "db uninstall postgresql --purge -f", timeout=900)


#: The Redis family as instances: the flavour, the version asked, the client its image ships and the
#: variable its password is in.
FAMILY = (
    ("redis", "7.4", "redis-cli", "REDIS_PASSWORD"),
    ("valkey", "8", "valkey-cli", "VALKEY_PASSWORD"),
    ("keydb", "6.3", "keydb-cli", "KEYDB_PASSWORD"),
)


def in_container(sc: Any, container: str, command: str, *, check: bool = True) -> Any:
    """
    Run a command inside an instance's container, in the machine's own Docker.

    Args:
        sc: The scenario.
        container: The container.
        command: A shell command, run by ``sh -c`` inside it.
        check: Fail the scenario on a non-zero exit.

    Returns:
        The finished command.
    """
    return sc.run(
        f"docker exec {container} sh -c {shlex.quote(command)}",
        check=check,
        timeout=120,
        label=f"in {container}: {command}",
    )


@engines_scenario("redis_family_tls")
def scenario_redis_family_tls(sc: Any) -> None:
    """
    C-25 and C-08: Redis, Valkey and KeyDB as instances serve TLS on their one port, and run.

    Each is made from its pinned image in the machine's own Docker, given a key, restarted into
    ``required`` (its plaintext port closed, a client that does not speak TLS refused, one that
    does reads the key it had), reached through Noust's own client the whole time, and put back to
    plaintext. KeyDB is run for real here for the first time: its image, its configuration file,
    its client, and the TLS its image is built with.
    """
    for flavour, version, cli, variable in FAMILY:
        name = f"w6d-{flavour}"
        box = f"noust-db-{name}"
        key = f"redis@{name}"
        noust(sc, f"db instance remove {name} --yes", check=False, timeout=300)
        noust(
            sc,
            f"db instance create {name} -e {flavour} --version {version} --memory 256m --yes",
            timeout=900,
        )
        auth = f'REDISCLI_AUTH="${variable}"'
        wrote = in_container(sc, box, f"{auth} {cli} set tlskey one")
        sc.check("OK" in wrote.stdout, f"{flavour} should take a write: {wrote.stdout}")

        # --- Noust's client works in the clear
        listed = noust(sc, f"db list --engine {key} --json", check=False)
        sc.check(listed.returncode == 0, f"{flavour}: listing before TLS: {listed.stdout[-300:]}")

        # --- required: the one port serves TLS
        plan = noust(sc, f"db instance tls {name} --mode required --plan --json")
        sc.check(json_of(plan, "plan")["blockers"] == [], f"{flavour}: nothing should block TLS")
        refused = noust(sc, f"db instance tls {name} --mode optional --yes", check=False)
        sc.check(
            refused.returncode != 0 and "port of their own" in refused.stdout + refused.stderr,
            f"{flavour}: optional should be refused with the reason: {refused.stdout[-300:]}",
        )
        noust(sc, f"db instance tls {name} --mode required --yes", timeout=600)

        plain = in_container(sc, box, f"{auth} {cli} -h 127.0.0.1 ping", check=False)
        sc.check(
            "PONG" not in plain.stdout,
            f"{flavour}: a client that does not speak TLS must not get in: {plain.stdout!r}",
        )
        secure = in_container(
            sc,
            box,
            f"{auth} {cli} --tls --cacert /etc/noust-tls/ca.crt -h localhost get tlskey",
        )
        sc.check(
            secure.stdout.strip() == "one", f"{flavour}: the key should survive: {secure.stdout!r}"
        )
        # --- the certificate chain, from the machine's side of the published port
        port = json_of(noust(sc, f"db instance show {name} --json"), "show")["host_port"]
        chain = sc.run(
            f"openssl s_client -connect 127.0.0.1:{port} -CAfile /etc/noust/tls/ca.crt "
            "-verify_hostname localhost </dev/null 2>&1 | grep -E 'Verify return code|Verification'",
            check=False,
            label=f"{flavour}: the chain the published port presents",
        )
        sc.check(
            "Verify return code: 0" in chain.stdout or "Verification: OK" in chain.stdout,
            f"{flavour}: Noust's authority should verify it: {chain.stdout!r}",
        )
        other = sc.run(
            f"openssl s_client -connect 127.0.0.1:{port} -CAfile /etc/noust/tls/ca.crt "
            "-verify_hostname not-its-name.example </dev/null 2>&1 | grep -E 'Verify return code'",
            check=False,
            label=f"{flavour}: a name the certificate does not carry",
        )
        sc.check(
            "Verify return code: 0" not in other.stdout and "mismatch" in other.stdout,
            f"{flavour}: a name the certificate does not cover must not verify: {other.stdout!r}",
        )

        # --- Noust's own client, the whole time
        status = noust(sc, f"db instance show {name} --json")
        sc.check(json_of(status, "show")["state"] == "ready", f"{flavour} should be ready")
        listed = noust(sc, f"db list --engine {key} --json", check=False)
        sc.check(listed.returncode == 0, f"{flavour}: listing through TLS: {listed.stdout[-300:]}")
        dump = f"{SCRATCH}/{flavour}.rdb"
        noust(sc, f"db backup 0 --engine {key} --no-compress --output {dump}", timeout=300)
        sc.check(
            sc.run(f"test -s {dump}", check=False).returncode == 0,
            f"{flavour}: the backup through TLS should be a file",
        )

        # --- it survives a restart on TLS, and goes back to plaintext
        noust(sc, f"db instance stop {name}", timeout=300)
        noust(sc, f"db instance start {name}", timeout=300)
        again = in_container(
            sc, box, f"{auth} {cli} --tls --cacert /etc/noust-tls/ca.crt -h localhost get tlskey"
        )
        sc.check(again.stdout.strip() == "one", f"{flavour}: restart on TLS: {again.stdout!r}")
        noust(sc, f"db instance tls {name} --mode off --yes", timeout=600)
        back = in_container(sc, box, f"{auth} {cli} get tlskey")
        sc.check(back.stdout.strip() == "one", f"{flavour}: back in the clear: {back.stdout!r}")
        noust(sc, f"db instance remove {name} --yes", timeout=300)


@engines_scenario("clickhouse_instance")
def scenario_clickhouse_instance(sc: Any) -> None:
    """
    C-06: ClickHouse as an instance, started from its pinned image and driven end to end.

    A database and an account are made through Noust, rows are inserted with ClickHouse's own client,
    the database is backed up with its own BACKUP to the disk Noust configures, restored as a new
    database with the data in it, and the instance is stopped, started and removed.
    """
    name = "w6d-ch"
    box = f"noust-db-{name}"
    key = f"clickhouse@{name}"
    noust(sc, f"db instance remove {name} --yes", check=False, timeout=300)
    noust(sc, f"db instance create {name} -e clickhouse --memory 2g --yes", timeout=1200)
    shown = json_of(noust(sc, f"db instance show {name} --json"), "show")
    sc.check(shown["state"] == "ready", f"ClickHouse should be ready: {shown}")

    noust(sc, f"db create shop --engine {key}")
    client = (
        'clickhouse-client --user noust_admin --password "$CLICKHOUSE_PASSWORD" --query {query}'
    )

    def query(text: str) -> str:
        return in_container(sc, box, client.format(query=shlex.quote(text))).stdout.strip()

    query("CREATE TABLE shop.orders (id UInt32, note String) ENGINE = MergeTree ORDER BY id")
    query("INSERT INTO shop.orders SELECT number, concat('o', toString(number)) FROM numbers(25)")
    sc.check(query("SELECT count() FROM shop.orders") == "25", "the rows should be there")

    made = noust(sc, f"db user-create reader --engine {key} --database shop")
    found = re.search(r"Password:\s+(\S+)", made.stdout)
    sc.check(found is not None, f"the account's password should be shown once: {made.stdout!r}")
    password = found.group(1) if found else ""
    users = json_of(noust(sc, f"db user-list --engine {key} --json"), "user-list")
    sc.check(
        {u["username"] for u in users} == {"noust_admin", "reader"},
        f"both accounts should be listed: {users}",
    )
    signed_in = in_container(
        sc,
        box,
        f"clickhouse-client --user reader --password {shlex.quote(password)} "  # noqa: S608 - a quoted word
        "--query 'SELECT count() FROM shop.orders'",
    )
    sc.check(signed_in.stdout.strip() == "25", "the account Noust made should read the table")

    noust(sc, f"db backup shop --engine {key}", timeout=600)
    newest = sc.run(
        "ls -1t /var/backups/noust/databases/clickhouse*shop* | head -1", label="newest backup"
    ).stdout.strip()
    sc.check(bool(newest), "ClickHouse's backup should be a file Noust keeps")
    noust(sc, f"db restore shop {newest} --engine {key} --as-new shop_copy -f", timeout=600)
    sc.check(
        query("SELECT count() FROM shop_copy.orders") == "25",
        "the restored database should hold the data",
    )

    noust(sc, f"db instance stop {name}", timeout=300)
    noust(sc, f"db instance start {name}", timeout=600)
    sc.check(query("SELECT count() FROM shop.orders") == "25", "the data should survive a restart")
    noust(sc, f"db drop shop_copy --engine {key} -f", check=False)
    noust(sc, f"db drop shop --engine {key} -f", check=False)
    noust(sc, f"db instance remove {name} --yes", timeout=300)


MY_SQL = "mariadb -N -B"


def my(sc: Any, sql: str, *, user: str = "", password: str = "", check: bool = True) -> Any:
    """
    Run SQL with the machine's MariaDB client, as root over the socket or as an account over TCP.

    Args:
        sc: The scenario.
        sql: The statement.
        user: An account to sign in as over 127.0.0.1; root over the socket when empty.
        password: That account's password.
        check: Fail the scenario on a non-zero exit.

    Returns:
        The finished command.
    """
    login = f"-u{user} -p{shlex.quote(password)} -h127.0.0.1" if user else ""
    return sc.run(
        f"{MY_SQL} {login} -e {shlex.quote(sql)}",
        check=check,
        label=f"mariadb {user or 'root'}: {sql}",
    )


@engines_scenario("import_cluster_mariadb")
def scenario_import_cluster_mariadb(sc: Any) -> None:
    """
    H-18 on MariaDB: a mysqldump of every database comes in, and its accounts from a file.

    A real MariaDB takes two databases (one latin1, one holding a view that mysqldump writes in a
    second pass, and rows that look like the markers the dump is cut at), three accounts (one root-like)
    and is dumped with ``mariadb-dump --all-databases``. It is emptied, and ``noust db import-all`` puts
    the databases back whole, the views included and the server's own schema untouched; with ``--users``
    and the grants file the accounts come back with the password they had and without the power they
    were not to have, and an account that already exists is left as it is.
    """
    noust(sc, "db uninstall mysql --purge -f", timeout=900, check=False)
    noust(sc, "db install mariadb", timeout=1800)
    my(sc, "create database shop character set utf8mb4; create database blog character set latin1")
    my(
        sc,
        "use shop; create table orders (id int primary key auto_increment, note text); "
        "insert into orders (note) values ('o1'), ('o2'), ('line\\none -- not a marker'), "
        "('-- Current Database: `blog`'); create view v_orders as select id from orders; "
        "use blog; create table posts (id int, body text); insert into posts values (1, 'x')",
    )
    my(
        sc,
        "create user 'shopapp'@'%' identified by 'secretpw'; "
        "create user 'reader'@'10.0.%' identified by 'readpw'; "
        "create user 'admin2'@'localhost' identified by 'adminpw'; "
        "grant select, insert, update, delete on shop.* to 'shopapp'@'%'; "
        "grant select on shop.orders to 'reader'@'10.0.%'; "
        "grant all privileges on *.* to 'admin2'@'localhost' with grant option",
    )
    dump = f"{SCRATCH}/all-mariadb.sql"
    grants = f"{SCRATCH}/grants-mariadb.sql"
    sc.run(
        f"mariadb-dump --all-databases --routines --triggers --events > {dump}",
        label="mariadb-dump",
    )
    sc.run(
        "mariadb -N -B -e \"select concat(quote(user), '@', quote(host)) from mysql.user where "  # noqa: S608 - fixed text
        "user in ('shopapp', 'reader', 'admin2')\" | while read -r account; do "
        'mariadb -N -B -e "SHOW CREATE USER $account" | sed "s/$/;/"; '
        'mariadb -N -B -e "SHOW GRANTS FOR $account" | sed "s/$/;/"; '
        f"done > {grants}",
        label="the accounts, as SHOW CREATE USER and SHOW GRANTS print them",
    )
    for account in ("'shopapp'@'%'", "'reader'@'10.0.%'", "'admin2'@'localhost'"):
        my(sc, f"drop user {account}")
    my(sc, "drop database shop; drop database blog")

    # --- the plan says what would happen and changes nothing
    plan = json_of(
        noust(sc, f"db import-all {dump} -e mysql --users --accounts-file {grants} --plan --json"),
        "plan",
    )
    actions = {d["name"]: d["action"] for d in plan["databases"]}
    sc.check(
        actions == {"blog": "create", "mysql": "skip", "shop": "create"},
        f"the plan should create the two user databases and skip the server's own: {actions}",
    )
    names = {r["name"]: r for r in plan["roles"]}
    sc.check(
        set(names) == {"shopapp@%", "reader@10.0.%", "admin2@localhost"}
        and "ALL PRIVILEGES ON *.*" in names["admin2@localhost"]["dropped"],
        f"the plan should name the accounts and what is not carried: {names}",
    )
    sc.check(
        my(sc, "show databases").stdout.split()
        and "shop" not in my(sc, "show databases").stdout.split(),
        "planning created a database",
    )

    # --- the databases alone
    noust(sc, f"db import-all {dump} -e mysql -f", timeout=300)
    sc.check(
        my(sc, "select count(*) from shop.orders").stdout.strip() == "4",
        "shop was not loaded whole",
    )
    sc.check(
        my(sc, "select count(*) from shop.v_orders").stdout.strip() == "4",
        "the view of the second pass should be there",
    )
    sc.check(my(sc, "select count(*) from blog.posts").stdout.strip() == "1", "blog was not loaded")
    sc.check(
        my(
            sc,
            "select default_character_set_name from information_schema.schemata where schema_name = 'blog'",
        ).stdout.strip()
        == "latin1",
        "a database keeps its character set",
    )
    sc.check(
        my(sc, "select count(*) from mysql.user where user = 'shopapp'").stdout.strip() == "0",
        "an account was made without asking for them",
    )

    # --- the accounts, with the password they had and without the power they were not to have
    my(sc, "drop database shop; drop database blog")
    noust(
        sc,
        f"db import-all {dump} -e mysql --users --accounts-file {grants} -f",
        timeout=300,
    )
    signed_in = my(sc, "select count(*) from shop.orders", user="shopapp", password="secretpw")
    sc.check(signed_in.stdout.strip() == "4", "the password the account had should sign in")
    reader = my(
        sc, "select count(*) from shop.orders", user="reader", password="readpw", check=False
    )
    sc.check(reader.returncode != 0 or "4" in reader.stdout, "reader's host pattern is its own")
    cannot = my(sc, "create database zzz", user="admin2", password="adminpw", check=False)
    sc.check(cannot.returncode != 0, "admin2 must not have the power it had at the source")
    sc.check(
        "ALL PRIVILEGES" not in my(sc, "show grants for 'admin2'@'localhost'").stdout,
        "the root-like grant should not have been carried",
    )

    # --- an account that exists is left as it is
    for account in ("'shopapp'@'%'", "'reader'@'10.0.%'", "'admin2'@'localhost'"):
        my(sc, f"drop user {account}")
    my(sc, "create user 'shopapp'@'%' identified by 'mine-not-theirs'")
    my(sc, "drop database shop; drop database blog")
    noust(
        sc,
        f"db import-all {dump} -e mysql --users --accounts-file {grants} -f",
        timeout=300,
    )
    still = my(sc, "select 1", user="shopapp", password="mine-not-theirs", check=False)
    sc.check(still.stdout.strip() == "1", "the account that existed lost its password")

    for account in ("'shopapp'@'%'", "'reader'@'10.0.%'", "'admin2'@'localhost'"):
        my(sc, f"drop user if exists {account}", check=False)
    my(sc, "drop database if exists shop; drop database if exists blog", check=False)
    noust(sc, "db uninstall mysql --purge -f", timeout=900)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Args:
        argv: Arguments; the process's when None.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenario", action="append", help="run only this scenario (repeatable)")
    parser.add_argument("--list", action="store_true", help="list the scenarios and exit")
    parser.add_argument("--dev", action="store_true", help="mount the working tree, build no wheel")
    parser.add_argument("--keep", action="store_true", help="leave the containers running")
    parser.add_argument(
        "--attach", action="store_true", help="use the containers --keep left running (--dev)"
    )
    parser.add_argument("--purge", action="store_true", help="remove everything this runner made")
    parser.add_argument("--skip-wheel-build", action="store_true", help="reuse the built wheel")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """
    Run the scenarios.

    Args:
        argv: Arguments; the process's when None.

    Returns:
        The exit status: 0 when every scenario that ran passed.
    """
    args = parse_args(argv)
    if args.list:
        for name, machine, fn in SCENARIOS:
            first = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
            print(f"{name} [{machine}]: {first}")
        return 0
    if args.purge:
        purge(images=True)
        print("[purge] removed the containers, the volume and the images of this runner")
        return 0

    selected = [s for s in SCENARIOS if not args.scenario or s[0] in args.scenario]
    if args.scenario:
        unknown = set(args.scenario) - {name for name, _, _ in SCENARIOS}
        if unknown:
            print(f"unknown scenario(s): {sorted(unknown)}")
            return 2

    started = time.monotonic()
    workdir = INTEGRATION_DIR / ".build"
    workdir.mkdir(exist_ok=True)
    wheel: Path | None = None
    if not args.dev and not args.attach:
        if args.skip_wheel_build:
            wheels = sorted((workdir / "dist").glob("noust-*.whl"))
            if not wheels:
                raise HarnessError("--skip-wheel-build given but no wheel exists yet")
            wheel = wheels[-1]
        else:
            wheel = run.build_wheel(workdir)

    failures = 0
    attempted = 0
    skipped: list[tuple[str, str]] = []
    setup_errors: list[str] = []
    ready: set[str] = set()
    try:
        for name, key, fn in selected:
            machine = MACHINES[key]
            if key not in ready:
                try:
                    prepare(machine, dev=args.dev, wheel=wheel, attach=args.attach)
                except HarnessError as exc:
                    setup_errors.append(f"{key}: {exc}")
                    continue
                ready.add(key)
            try:
                for other_key in ALSO.get(name, ()):
                    other = MACHINES[other_key]
                    if other_key not in ready:
                        prepare(
                            other,
                            dev=args.dev,
                            wheel=wheel,
                            attach=args.attach and exists("container", other.container),
                        )
                        ready.add(other_key)
                    join_network(other)
                if ALSO.get(name):
                    join_network(machine)
            except HarnessError as exc:
                setup_errors.append(f"{name}: {exc}")
                continue
            print(f"\n=== {name} [{key}] ===")
            attempted += 1
            sc = Scenario(container=machine.container)
            try:
                fn(sc)
            except ScenarioSkipped as exc:
                skipped.append((name, str(exc)))
                print(f"SKIP: {name}: {exc}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL: {name}: {exc}")
            except HarnessError as exc:
                failures += 1
                print(f"FAIL: {name} (command error): {exc}")
            except Exception as exc:
                failures += 1
                print(f"FAIL: {name} (harness error): {type(exc).__name__}: {exc}")
            else:
                print(f"PASS: {name}")
            finally:
                if sc.evidence:
                    print("--- evidence ---")
                    print("\n\n".join(sc.evidence))
    finally:
        if args.keep or args.attach:
            print("\n[teardown] --keep given, the containers stay")
        else:
            for machine in list(_STARTED):
                remove_machine(machine)
            if exists("network", NETWORK):
                sh(["docker", "network", "rm", NETWORK], timeout=60, check=False)

    elapsed = time.monotonic() - started
    for problem in setup_errors:
        print(f"\n[setup] FAILED: {problem}")
    for name, reason in skipped:
        print(f"[skipped] {name}: {reason}")
    print(
        f"\n[summary] {attempted} scenario(s) run, {failures} failure(s), "
        f"{len(skipped)} skipped, {len(setup_errors)} setup error(s), {elapsed:.1f}s total"
    )
    return 1 if failures or setup_errors else 0


if __name__ == "__main__":
    sys.exit(main())
