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
its own; ``rocky9``; ``leap``), and the runner starts a machine only when a selected scenario
needs it. Like the other runners it is not collected by pytest: it drives Docker.

Usage:
    .venv/bin/python tests/integration/engines_run.py --list
    .venv/bin/python tests/integration/engines_run.py --scenario remote_only_backups
    .venv/bin/python tests/integration/engines_run.py --dev --keep    # the working tree, mounted
    .venv/bin/python tests/integration/engines_run.py --dev --attach --scenario rpm_postgresql
    .venv/bin/python tests/integration/engines_run.py --purge         # remove everything it made

``--dev`` mounts the working tree read-only and imports Noust from it, so a change is tried
without building a wheel; every run that is reported as proof is a wheel run (the default).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]

#: Every name this runner creates starts with this, and every object carries the label.
PREFIX = "noust-test-ch"
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

#: The machines this run started, so that the end of the run removes them.
_STARTED: list[Machine] = []


def exists(kind: str, name: str) -> bool:
    """
    Say whether a Docker object of this runner's exists.

    Args:
        kind: ``container``, ``volume`` or ``image``.
        name: Its exact name.

    Returns:
        Whether it does.
    """
    return sh(["docker", kind, "inspect", name], timeout=30, check=False).returncode == 0


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
    sh(["docker", "cp", str(wheel), f"{box}:/tmp/{wheel.name}"], timeout=60)
    docker_exec(box, f"{machine.python} -m venv /opt/noust", timeout=60)
    docker_exec(box, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=180)
    docker_exec(
        box,
        f"/opt/noust/bin/pip install --quiet '/tmp/{wheel.name}[all]'",
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


def engines_scenario(name: str, *, machine: str = "ubuntu") -> Callable[[ScenarioFn], ScenarioFn]:
    """
    Register a scenario.

    Args:
        name: The name ``--scenario`` takes.
        machine: The key of the machine it runs on.

    Returns:
        The decorator.
    """

    def decorator(fn: ScenarioFn) -> ScenarioFn:
        SCENARIOS.append((name, machine, fn))
        return fn

    return decorator


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
    return sc.run(f"noust {arguments}", timeout=timeout, check=check, label=f"noust {arguments}")


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
    done = sc.run(f"{PSQL} -d {database} -c {shlex.quote(sql)}", label=f"psql {database}: {sql}")
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
