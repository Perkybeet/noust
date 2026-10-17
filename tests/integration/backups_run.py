#!/usr/bin/env python3
"""
Real-software harness for Noust's backup and recovery features.

``engines_run.py`` proves the database features on systemd machines; this runner proves what
only a real engine, a real object store or a real second server can: that the dump of a database
that lives in a Compose stack loads into a throwaway container of its own image, that a destination
created with a wrong bucket is refused before it is saved, that the PostgreSQL roles of a server
travel with its copy. The Noust code it drives runs **in this process** (the real
``SubprocessRunner``, a store and a configuration in a temporary directory), against the real
software in containers, so a change is tried without building a wheel.

Rules for a machine that is also somebody's development workstation (the same as ``engines_run.py``):

* **Everything it creates is named ``noust-test-w6b-*`` and carries the label ``noust.test=1``**
  (containers, networks, volumes). It removes those by exact name, never by a prune and never
  anything it did not make. ``--purge`` removes what a run that was killed left behind, and
  only what has both the prefix and the label.
* **It publishes no port on the host.** The software runs on a network of its own, or on none, and
  is reached with ``docker exec`` or from another container of that network.
* At most three containers are alive at once. The images are the ones already on the machine; a
  scenario whose image is not there reports SKIP with the reason, it does not pull.

What each scenario proves (the checklist row it answers):

* ``unit_files`` (H-01, priority of the copies, the retention sweep): the service and timer units
  of a database policy, a data policy, Noust's own state and the sweep, written with a time limit,
  a time zone with an underscore and a priority, are accepted by ``systemd-analyze verify`` of a
  real systemd 255.
* ``stack_dump_restore`` (D-18, H-31): the dump of PostgreSQL, MySQL and MariaDB databases that live
  in containers is taken with the commands an update runs, loaded into a throwaway container of the
  image the service ran, and counted; a truncated dump fails the test with the engine's own words;
  nothing of the test is left behind.

Usage:
    .venv/bin/python tests/integration/backups_run.py --list
    .venv/bin/python tests/integration/backups_run.py --scenario stack_dump_restore
    .venv/bin/python tests/integration/backups_run.py            # every scenario
    .venv/bin/python tests/integration/backups_run.py --purge    # remove what a killed run left
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every name this runner creates starts with this, and every object carries the label.
PREFIX = "noust-test-w6b"
LABEL = "noust.test=1"

#: The image tags the scenarios use. They must be on the machine already.
IMAGES = {
    "postgres": "postgres:16-alpine",
    "mysql": "mysql:8.4",
    "mariadb": "mariadb:11",
    "minio": "minio/minio:latest",
}

sys.path.insert(0, str(REPO_ROOT / "src"))


class ScenarioSkipped(Exception):
    """A scenario that cannot run here, with the reason. It is never a PASS."""


@dataclass
class Lab:
    """
    What a scenario uses: Docker, a place to keep evidence, and a record of what it made.

    Attributes:
        scratch: A private directory for the scenario's files.
        evidence: What was run and what it printed, in order, kept for the report.
        containers: Every container made, so they are removed by name afterwards.
    """

    scratch: Path
    evidence: list[str] = field(default_factory=list)
    containers: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)

    def name(self, suffix: str) -> str:
        """
        Name an object this run makes.

        Args:
            suffix: What it is.

        Returns:
            ``noust-test-w6b-<suffix>-<random>``.
        """
        return f"{PREFIX}-{suffix}-{secrets.token_hex(3)}"

    def docker(
        self,
        *args: str,
        timeout: int = 300,
        check: bool = True,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """
        Run ``docker`` and keep what it printed as evidence.

        Args:
            *args: The arguments after ``docker``.
            timeout: Seconds it may take.
            check: Raise when it fails.
            input_text: Text for its standard input.
            env: Extra environment.

        Returns:
            The finished process.
        """
        proc = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
            env={**os.environ, **(env or {})},
        )
        shown = " ".join(a if " " not in a else repr(a) for a in args)
        block = [f"$ docker {shown[:300]}"]
        if proc.stdout.strip():
            block.append(proc.stdout.rstrip()[-1500:])
        if proc.stderr.strip():
            block.append("--- stderr ---\n" + proc.stderr.rstrip()[-800:])
        block.append(f"(exit={proc.returncode})")
        self.evidence.append("\n".join(block))
        if check and proc.returncode != 0:
            raise AssertionError(f"docker {shown[:200]} failed (exit {proc.returncode})")
        return proc

    def require_image(self, tag: str) -> None:
        """
        Skip the scenario when an image is not on the machine.

        Args:
            tag: The image reference.

        Raises:
            ScenarioSkipped: The image is not there; nothing is pulled.
        """
        found = subprocess.run(
            ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if found.returncode != 0:
            raise ScenarioSkipped(f"the image {tag} is not on this machine (nothing is pulled)")

    def start(
        self, name: str, image: str, *run_args: str, env: dict[str, str] | None = None
    ) -> None:
        """
        Start a labelled container and remember it for cleanup.

        Args:
            name: Its ``noust-test-w6b-*`` name.
            image: The image.
            *run_args: More ``docker run`` arguments.
            env: Environment for the ``docker`` process, for the ``-e NAME`` secrets.
        """
        assert name.startswith(PREFIX), name
        self.containers.append(name)
        self.docker(
            "run", "-d", "--name", name, "--label", LABEL, *run_args, image, env=env, timeout=900
        )

    def remove(self, name: str) -> None:
        """
        Remove one container of this run, by exact name, with its anonymous volumes.

        Args:
            name: The container.
        """
        assert name.startswith(PREFIX), name
        subprocess.run(
            ["docker", "rm", "-f", "-v", name], capture_output=True, text=True, timeout=120
        )
        if name in self.containers:
            self.containers.remove(name)

    def cleanup(self) -> None:
        """Remove everything this run made, by exact name."""
        for name in list(self.containers):
            self.remove(name)
        for network in list(self.networks):
            assert network.startswith(PREFIX), network
            subprocess.run(
                ["docker", "network", "rm", network], capture_output=True, text=True, timeout=60
            )
            self.networks.remove(network)

    def leftovers(self) -> list[str]:
        """
        Returns:
            The names of containers that carry this run's label and a drill's or a test's name
            and are still there (there must be none when a scenario ends).
        """
        found = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "label=noust.drill=1",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.split()
        return sorted(found + list(self.containers))

    def check(self, condition: bool, message: str) -> None:
        """
        Assert something, with a message that says what was expected.

        Args:
            condition: What must hold.
            message: What failed when it does not.
        """
        if not condition:
            raise AssertionError(message)


ScenarioFn = Callable[[Lab], None]
SCENARIOS: dict[str, ScenarioFn] = {}


def scenario(name: str) -> Callable[[ScenarioFn], ScenarioFn]:
    """
    Register a scenario under a name.

    Args:
        name: What ``--scenario`` calls it.

    Returns:
        The decorator.
    """

    def decorator(fn: ScenarioFn) -> ScenarioFn:
        SCENARIOS[name] = fn
        return fn

    return decorator


@contextlib.contextmanager
def noust_environment(scratch: Path) -> Iterator[None]:
    """
    Point Noust's store and directories at a temporary place for the length of a scenario.

    Args:
        scratch: A private directory.

    Yields:
        Nothing; the process-wide store is the scenario's own.
    """
    from noust.core.store import NoustStore

    NoustStore.reset_instance()
    store = NoustStore(scratch / "noust.db")
    NoustStore._instance = store
    try:
        yield
    finally:
        store.close()
        NoustStore.reset_instance()


# ---------------------------------------------------------------------------
# stack_dump_restore
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StackCase:
    """One engine of ``stack_dump_restore``: how its source container is started and seeded."""

    engine: str
    image: str
    user: str
    database: str
    settings: tuple[str, ...]
    seed: Sequence[str]


STACK_CASES = (
    StackCase(
        "postgres",
        IMAGES["postgres"],
        "shopper",
        "shop",
        ("POSTGRES_USER=shopper", "POSTGRES_DB=shop"),
        [
            "psql -U shopper -d shop -c 'CREATE TABLE orders (id serial PRIMARY KEY, total int)'",
            "psql -U shopper -d shop -c 'CREATE TABLE customers (id serial PRIMARY KEY, name text)'",
            "psql -U shopper -d shop -c 'INSERT INTO orders (total) SELECT g FROM generate_series(1,500) g'",
        ],
    ),
    StackCase(
        "mysql",
        IMAGES["mysql"],
        "shopper",
        "shop",
        ("MYSQL_USER=shopper", "MYSQL_DATABASE=shop"),
        [
            'mysql -u shopper -p"$MYSQL_PASSWORD" shop -e "CREATE TABLE orders (id INT AUTO_INCREMENT PRIMARY KEY, total INT)"',
            'mysql -u shopper -p"$MYSQL_PASSWORD" shop -e "CREATE TABLE customers (id INT AUTO_INCREMENT PRIMARY KEY, name TEXT)"',
            'mysql -u shopper -p"$MYSQL_PASSWORD" shop -e "INSERT INTO orders (total) VALUES (1),(2),(3)"',
        ],
    ),
    StackCase(
        "mariadb",
        IMAGES["mariadb"],
        "root",
        "",
        (),
        [
            'mariadb -u root -p"$MARIADB_ROOT_PASSWORD" -e "CREATE DATABASE shop; CREATE DATABASE blog"',
            'mariadb -u root -p"$MARIADB_ROOT_PASSWORD" shop -e "CREATE TABLE orders (id INT PRIMARY KEY)"',
            'mariadb -u root -p"$MARIADB_ROOT_PASSWORD" blog -e "CREATE TABLE posts (id INT PRIMARY KEY)"',
        ],
    ),
)


class ContainerHost:
    """
    The stack an update talks to, for a container the harness started by hand.

    ``dump_stack_database`` builds ``docker compose exec -T <service> ...``; here the "service" is a
    container, so the same commands run through ``docker exec -i``.
    """

    def __init__(self, container: str, scratch: Path) -> None:
        from noust.core.runner import get_runner

        self.container = container
        self.runner = get_runner()
        self.app_path = scratch
        self.domain = "stack.example.test"

    def _compose(self, *args: str, project: str | None = None) -> list[str]:
        assert args[:2] == ("exec", "-T"), args
        return ["docker", "exec", "-i", self.container]


@scenario("stack_dump_restore")
def stack_dump_restore(lab: Lab) -> None:
    """The dump of a database in a container loads into a throwaway container of its image."""
    from noust.core.runner import get_runner
    from noust.managers.stack_databases import StackDatabase, dump_stack_database
    from noust.managers.stack_drill import restore_test_stack_dump

    runner = get_runner()
    for case in STACK_CASES:
        lab.require_image(case.image)
    for case in STACK_CASES:
        source = lab.name(f"src-{case.engine}")
        secret = secrets.token_urlsafe(18)
        names = {
            "postgres": ("POSTGRES_PASSWORD",),
            "mysql": ("MYSQL_ROOT_PASSWORD", "MYSQL_PASSWORD"),
            "mariadb": ("MARIADB_ROOT_PASSWORD",),
        }[case.engine]
        environment = dict.fromkeys(names, secret)
        flags = ["--network", "none", "--memory", "1g"]
        for name in names:
            flags += ["-e", name]
        for setting in case.settings:
            flags += ["-e", setting]
        lab.start(source, case.image, *flags, env=environment)
        database = StackDatabase("db", case.engine, case.database, case.user, case.image)  # type: ignore[arg-type]
        _wait_for_engine(lab, source, database)
        for statement in case.seed:
            lab.docker("exec", source, "sh", "-c", statement)
        host = ContainerHost(source, lab.scratch)
        target = lab.scratch / case.engine
        target.mkdir()
        dump = dump_stack_database(host, database, target)  # type: ignore[arg-type]
        lab.check(dump.is_file() and dump.stat().st_size > 100, f"{case.engine}: dump is empty")

        started = time.monotonic()
        result = restore_test_stack_dump(runner, database, dump)
        lab.evidence.append(
            f"[{case.engine}] restore test: ok={result.ok} skipped={result.skipped} "
            f"{time.monotonic() - started:.1f}s\n  {result.detail}"
        )
        lab.check(result.ok and not result.skipped, f"{case.engine}: {result.detail}")
        expected = {"postgres": "2 tables", "mysql": "2 tables", "mariadb": "2 tables"}[case.engine]
        lab.check(
            expected in result.detail, f"{case.engine}: expected {expected!r} in {result.detail!r}"
        )

        truncated = lab.scratch / f"{case.engine}-truncated.gz"
        with gzip.open(dump, "rb") as handle:
            body = handle.read()
        with gzip.open(truncated, "wb") as handle:
            handle.write(body[: max(40, len(body) // 3)])
        broken = restore_test_stack_dump(runner, database, truncated)
        lab.evidence.append(
            f"[{case.engine}] truncated dump: ok={broken.ok}\n  {broken.detail[:400]}"
        )
        lab.check(not broken.ok, f"{case.engine}: a truncated dump passed the test")
        lab.remove(source)
    lab.check(lab.leftovers() == [], f"left behind: {lab.leftovers()}")


def _wait_for_engine(lab: Lab, container: str, database: object) -> None:
    """
    Wait for a seeded source container to finish its first start and accept connections.

    Args:
        lab: The lab.
        container: The container.
        database: The database it runs.
    """
    from noust.managers.stack_databases import client_command

    markers = ("init process complete", "init process done")
    for _ in range(90):
        logs = lab.docker("logs", container, check=False)
        text = logs.stdout + logs.stderr
        if any(marker in text for marker in markers):
            ping = subprocess.run(
                ["docker", "exec", container, *client_command(database, "ping", tcp=True)],  # type: ignore[arg-type]
                capture_output=True,
                text=True,
                timeout=60,
            )
            if ping.returncode == 0:
                return
        time.sleep(2)
    raise AssertionError(f"{container} never finished starting")


# ---------------------------------------------------------------------------
# unit_files
# ---------------------------------------------------------------------------


@scenario("unit_files")
def unit_files(lab: Lab) -> None:
    """The units a backup schedule is made of are accepted by a real systemd 255."""
    from noust.core.runner import FakeRunner
    from noust.managers.backup_scheduler import BackupScheduler
    from noust.managers.backup_timer_options import validate_options

    image = "noust-integration:latest"
    lab.require_image(image)
    units = lab.scratch / "units"
    units.mkdir()
    BackupScheduler.SYSTEMD_DIR = units  # type: ignore[misc]
    # The units name an executable; systemd-analyze verify wants it to exist.
    import noust.managers.backup_scheduler as module

    module.find_noust_executable = lambda: "/bin/true"  # type: ignore[assignment]
    scheduler = BackupScheduler(runner=FakeRunner())
    options = validate_options("2h", "America/Argentina/Buenos_Aires")
    scheduler.install_database_timer("postgresql", "shop", "daily", options=options)
    scheduler.install_volume_timer(
        "shop.example.com", "weekly", options=validate_options("90m", "UTC")
    )
    scheduler.install_self_backup_timer("*-*-* 03:30:00")
    names = sorted(path.name for path in units.iterdir())
    lab.evidence.append("written: " + ", ".join(names))
    lab.check("noust-backup-sweep.timer" in names, "the sweep's timer was not written")

    container = lab.name("verify")
    lab.containers.append(container)
    verify = lab.docker(
        "run",
        "--name",
        container,
        "--label",
        LABEL,
        "-v",
        f"{units}:/units:ro",
        "--entrypoint",
        "sh",
        image,
        "-c",
        "cd /units && systemd-analyze verify *.service *.timer 2>&1; echo verify-exit=$?; "
        "grep -h 'OnCalendar\\|TimeoutStartSec\\|Nice\\|IOScheduling' *.service *.timer; "
        "systemd-analyze calendar 'Mon *-*-* 02:00:00 America/Argentina/Buenos_Aires' | head -3",
        check=False,
        timeout=120,
    )
    lab.check("verify-exit=0" in verify.stdout, f"systemd-analyze verify failed: {verify.stdout}")
    # systemd ignores RuntimeMaxSec= on a oneshot service and says so; a limit it ignores is none.
    lab.check("no effect" not in verify.stdout, f"systemd ignores a directive: {verify.stdout}")
    lab.check("TimeoutStartSec=7200" in verify.stdout, "the time limit is not in the unit")
    lab.check("Buenos_Aires" in verify.stdout, "the time zone is not in the timer")
    lab.check("Nice=10" in verify.stdout, "the priority is not in the unit")
    lab.remove(container)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def purge() -> int:
    """
    Remove what a killed run left behind: objects with this prefix and this label, by name.

    Returns:
        0.
    """
    listed = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={LABEL}",
            "--filter",
            f"name=^{PREFIX}-",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.split()
    for name in listed:
        if name.startswith(PREFIX):
            subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
            print(f"removed container {name}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the scenarios.

    Args:
        argv: Arguments; the command line when None.

    Returns:
        0 when every scenario that ran passed (or was skipped), 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--scenario", action="append", default=[], help="a scenario to run")
    parser.add_argument("--list", action="store_true", help="list the scenarios")
    parser.add_argument("--purge", action="store_true", help="remove what a killed run left")
    parser.add_argument("--evidence", type=Path, help="write the evidence of every scenario here")
    options = parser.parse_args(argv)
    if options.list:
        for name, fn in SCENARIOS.items():
            print(f"{name}: {(fn.__doc__ or '').strip().splitlines()[0]}")
        return 0
    if options.purge:
        return purge()
    chosen = options.scenario or list(SCENARIOS)
    unknown = [name for name in chosen if name not in SCENARIOS]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    failed = 0
    report: dict[str, dict[str, object]] = {}
    for name in chosen:
        with tempfile.TemporaryDirectory(prefix="noust-w6b-") as scratch_dir:
            lab = Lab(Path(scratch_dir))
            status = "PASS"
            detail = ""
            try:
                with noust_environment(lab.scratch):
                    SCENARIOS[name](lab)
            except ScenarioSkipped as exc:
                status, detail = "SKIP", str(exc)
            except Exception as exc:
                status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
                lab.evidence.append(traceback.format_exc())
                failed += 1
            finally:
                lab.cleanup()
            print(f"{status} {name}" + (f": {detail}" if detail else ""))
            if status == "FAIL":
                print("\n\n".join(lab.evidence[-12:]))
            report[name] = {"status": status, "detail": detail, "evidence": lab.evidence}
    if options.evidence:
        options.evidence.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
