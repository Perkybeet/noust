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
