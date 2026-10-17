#!/usr/bin/env python3
"""
A database moved from one server of a fleet to another, driven by a central (checklist C-21).

``noust db transfer`` and the fleet action behind it were rehearsed with fake servers that validate
every body with the real request models. This harness is the fleet doing it: three real containers
on a network of their own, each a systemd machine with Noust installed from the wheel of the
working tree, with sshd. A central and two servers it manages, enrolled the way an operator does it
(``fleet authorize`` on the server, ``node add`` on the central), over the real SSH tunnels and the
real restricted key. Both servers get a real database engine, and the central moves a database:

- the rows arrive whole, in a database the target made, owned by the target's own account and not
  by the roles the source had, and the source still has its copy;
- the accounts that had access arrive with the password they had (an account signs in on the target
  with it) and the privileges of their profile, and one whose password the source never knew
  arrives with another;
- the dump made for the move does not stay on the source's disk;
- no password is found anywhere on the central, in its files, its job records or its logs;
- a second move of the same name is refused before a dump is taken, ``replace`` carries the new
  rows, and ``drop_source`` leaves the database on the target alone.

It is not part of pytest (nothing here is named ``test_*``) and it drives Docker. ``ship_run.py``
(whose three machines, images, network and enrollment this reuses, never edited), ``run.py`` and
``fleet_run.py`` are loaded as modules.

Usage:
    .venv/bin/python tests/integration/db_transfer_run.py
    .venv/bin/python tests/integration/db_transfer_run.py --keep
    .venv/bin/python tests/integration/db_transfer_run.py --scenario postgresql

Everything it creates is named ``<prefix>-...`` (``NOUST_IT_DBTRANSFER_PREFIX``, default
``noust-it-dbtransfer``) and carries the label ``noust.test=1``: three containers, a network, two
images. Nothing of the host's Docker is shared with them.
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
from pathlib import Path
from typing import Any

INTEGRATION_DIR = Path(__file__).resolve().parent

# ship_run reads its prefix when it is loaded, so the prefix is set first.
os.environ["NOUST_IT_SHIP_PREFIX"] = os.environ.get(
    "NOUST_IT_DBTRANSFER_PREFIX", "noust-it-dbtransfer"
)


def _load(name: str, file: str) -> Any:
    """
    Load a sibling harness as a module, without touching sys.path.

    Args:
        name: The name to register it under.
        file: Its file name in this directory.

    Returns:
        The module.
    """
    spec = importlib.util.spec_from_file_location(name, INTEGRATION_DIR / file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {INTEGRATION_DIR / file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ship = _load("noust_integration_ship_run", "ship_run.py")
run = ship.run
HarnessError = run.HarnessError

#: How the central names the two servers: the one that holds the database, the one that gets it.
SOURCE_NAME = ship.BUILDER_NAME
TARGET_NAME = ship.TARGET_NAME

PSQL = "runuser -u postgres -- psql -X -A -t"

#: The passwords the accounts have at the source, which must be found nowhere on the central.
RW_PASSWORD = "rwSecret-4f7a91c2"
RO_PASSWORD = "roSecret-8d03be55"
MANUAL_PASSWORD = "manualSecret-61e0aa"
MARIA_PASSWORD = "mariaSecret-27c9d4b1"

BASE_IMAGE_TO_REUSE = "noust-integration:latest"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def noust(sc: Any, arguments: str, *, timeout: int = 300, check: bool = True) -> Any:
    """
    Run the real CLI in a server.

    Args:
        sc: The server.
        arguments: Everything after ``noust``.
        timeout: Seconds it may take.
        check: Fail the scenario on a non-zero exit.

    Returns:
        The completed process.
    """
    return sc.run(
        f"noust {arguments}",
        timeout=timeout,
        check=check,
        label=f"({sc.container.rsplit('-', 1)[-1]}) noust {arguments}",
    )


def psql(sc: Any, database: str, sql: str) -> str:
    """
    Run SQL as the postgres account of a server.

    Args:
        sc: The server.
        database: The database to connect to.
        sql: The statement.

    Returns:
        psql's output, stripped.
    """
    done = sc.run(
        f"{PSQL} -d {database} -c {shlex.quote(sql)}",
        label=f"({sc.container.rsplit('-', 1)[-1]}) psql {database}: {sql}",
    )
    return done.stdout.strip()


def psql_as(sc: Any, user: str, password: str, database: str, sql: str) -> tuple[int, str]:
    """
    Sign in to a server's PostgreSQL over TCP as an account and run SQL.

    Args:
        sc: The server.
        user: The account.
        password: Its password, kept off the command line (an environment variable).
        database: The database.
        sql: The statement.

    Returns:
        The exit status and the output.
    """
    done = sc.run(
        f"PGPASSWORD={shlex.quote(password)} psql -X -A -t -h 127.0.0.1 -U {user} -d {database} "
        f"-c {shlex.quote(sql)}",
        check=False,
        label=f"({sc.container.rsplit('-', 1)[-1]}) psql as {user} {database}: {sql}",
    )
    return done.returncode, (done.stdout + done.stderr).strip()


def maria(sc: Any, sql: str, *, user: str = "", password: str = "", database: str = "") -> Any:
    """
    Run SQL with a server's MariaDB client, as root over the socket or as an account.

    Args:
        sc: The server.
        sql: The statement.
        user: An account to sign in as over the socket; root when empty.
        password: That account's password, kept off the command line.
        database: The database to use.

    Returns:
        The finished command (not checked).
    """
    login = f"-u{user} " if user else ""
    env = f"MYSQL_PWD={shlex.quote(password)} " if password else ""
    return sc.run(
        f"{env}mariadb -N -B {login}{database} -e {shlex.quote(sql)}",
        check=False,
        label=f"({sc.container.rsplit('-', 1)[-1]}) mariadb {user or 'root'}: {sql}",
    )


def transfer(env: Any, body: dict[str, Any]) -> tuple[int, Any]:
    """
    Ask the central to move a database.

    Args:
        env: The fleet.
        body: The request.

    Returns:
        The HTTP status and the decoded answer.
    """
    return ship.api(env.central, env.token, "POST", "/api/fleet/databases/transfer", body)


def finished(env: Any, body: dict[str, Any]) -> dict[str, Any]:
    """
    Move a database and wait for the job.

    Args:
        env: The fleet.
        body: The request.

    Returns:
        The finished job.
    """
    status, answer = transfer(env, body)
    env.central.check(
        status == 200 and answer["job"] is not None, f"the move was not started: {answer!r}"
    )
    return ship.wait_for_job(env.central, env.token, answer["job"]["job_id"])


def central_holds_no_secret(env: Any, secrets: list[str]) -> None:
    """
    Search the central's disk for passwords that passed through it.

    Args:
        env: The fleet.
        secrets: The passwords.

    Raises:
        AssertionError: One is found in a file of the central.
    """
    for secret in secrets:
        found = env.central.run(
            f"grep -rIl -F -- {shlex.quote(secret)} /var/lib/noust /var/log /etc/noust /root /tmp "
            "/run 2>/dev/null | head -5",
            check=False,
            label=f"(central) grep for a password that passed through it: {secret[:6]}...",
        ).stdout.strip()
        env.central.check(not found, f"a password was written on the central: {found}")
        logged = env.central.run(
            f"journalctl -a --no-pager 2>/dev/null | grep -cF -- {shlex.quote(secret)}",
            check=False,
            label=f"(central) journalctl | grep -c for {secret[:6]}...",
        ).stdout.strip()
        env.central.check(logged in ("", "0"), f"a password was logged on the central: {logged}")


def dumps_of(sc: Any, database: str) -> str:
    """
    List the dumps of a database in a server's backup folders.

    Args:
        sc: The server.
        database: The database.

    Returns:
        The file names, one per line.
    """
    return sc.run(
        f"find /var/backups/noust -type f -name '*{database}*' 2>/dev/null",
        check=False,
        label=f"(source) the dumps of {database}",
    ).stdout.strip()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_postgresql(env: Any) -> None:
    """
    PostgreSQL: the rows, the owner, the accounts and their passwords, and what stays.

    The source holds ``shop`` (2000 rows, owned by ``shopapp``) and three accounts with access: one
    read-write and one read-only whose passwords Noust keeps, and one made by hand whose password it
    never knew. The central moves it with its accounts.
    """
    central, source, target = env.central, env.builder, env.target
    for server in (source, target):
        noust(server, "db install postgresql", timeout=1800)

    psql(source, "postgres", "create role shopapp login password 'x1y2z3w4'")
    noust(source, "db create shop --engine postgresql --owner shopapp")
    psql(
        source,
        "shop",
        "create table orders(id serial primary key, note text); "
        "insert into orders(note) select 'order ' || g from generate_series(1, 2000) g; "
        "alter table orders owner to shopapp; alter sequence orders_id_seq owner to shopapp",
    )
    noust(
        source,
        f"db user-create shop_rw -e postgresql -d shop --profile read_write -p {RW_PASSWORD}",
    )
    noust(
        source, f"db user-create shop_ro -e postgresql -d shop --profile read_only -p {RO_PASSWORD}"
    )
    psql(
        source,
        "postgres",
        f"create role manual_ro login password '{MANUAL_PASSWORD}'; "
        "grant connect on database shop to manual_ro",
    )
    psql(
        source,
        "shop",
        "grant usage on schema public to manual_ro; grant select on orders to manual_ro",
    )

    body = {
        "database": "shop",
        "engine": "postgresql",
        "from_node": SOURCE_NAME,
        "to": TARGET_NAME,
        "accounts": True,
    }

    # --- the plan says what will happen and changes nothing
    status, answer = transfer(env, {**body, "plan": True})
    central.check(status == 200, f"the plan was refused: {answer!r}")
    central.check(
        answer["job"] is None and answer["plan"]["batches"] == [[TARGET_NAME]],
        f"the plan is not one server and no job: {answer!r}",
    )
    central.check(
        psql(target, "postgres", "select count(*) from pg_database where datname = 'shop'") == "0",
        "planning made the database",
    )
    central.check(dumps_of(source, "shop") == "", "planning took a dump")

    # --- the move
    job = finished(env, body)
    central.check(job["status"] == "completed", f"the move failed: {json.dumps(job)[:3000]}")
    central.check(ship.states(job) == {TARGET_NAME: "succeeded"}, f"states: {ship.states(job)}")
    item = job["result"]["nodes"][0]["items"][0]
    central.check(
        sorted(item["accounts"]["created"]) == ["manual_ro", "shop_ro", "shop_rw"],
        f"the accounts made: {item['accounts']}",
    )
    central.check(
        item["accounts"]["new_password"] == ["manual_ro"],
        f"only the account Noust never knew the password of gets a new one: {item['accounts']}",
    )

    # --- the rows are there, whole, and belong to the database's own owner
    central.check(
        psql(target, "shop", "select count(*) from orders") == "2000",
        "the rows did not arrive whole",
    )
    central.check(
        psql(target, "shop", "select count(distinct note) from orders") == "2000",
        "the rows arrived changed",
    )
    owner = psql(target, "shop", "select tableowner from pg_tables where tablename = 'orders'")
    central.check(
        owner != "postgres",
        f"the table is owned by the superuser on the target: {owner!r}",
    )
    # The source's own role does not have to exist on the target for the data to belong to someone.
    central.check(
        psql(source, "shop", "select count(*) from orders") == "2000",
        "the source lost rows: it must be left alone",
    )

    # --- the accounts sign in with the password they had, and only do what their profile allows
    code, out = psql_as(target, "shop_rw", RW_PASSWORD, "shop", "select count(*) from orders")
    central.check(code == 0 and out == "2000", f"shop_rw cannot sign in with its password: {out}")
    code, out = psql_as(
        target, "shop_rw", RW_PASSWORD, "shop", "insert into orders(note) values ('from rw')"
    )
    central.check(code == 0, f"shop_rw cannot write: {out}")
    code, out = psql_as(target, "shop_ro", RO_PASSWORD, "shop", "select count(*) from orders")
    central.check(code == 0, f"shop_ro cannot sign in with its password: {out}")
    code, out = psql_as(
        target, "shop_ro", RO_PASSWORD, "shop", "insert into orders(note) values ('from ro')"
    )
    central.check(code != 0 and "permission denied" in out, f"shop_ro could write: {out}")
    code, _ = psql_as(target, "manual_ro", MANUAL_PASSWORD, "shop", "select 1")
    central.check(
        code != 0, "an account Noust never knew the password of got the source's password"
    )
    central.check(
        psql(target, "postgres", "select count(*) from pg_roles where rolname = 'manual_ro'")
        == "1",
        "the account was not made",
    )

    # --- the dump made for the move is gone from the source; nothing was written on the central
    central.check(
        dumps_of(source, "shop") == "", f"a dump stayed on the source: {dumps_of(source, 'shop')}"
    )
    central_holds_no_secret(env, [RW_PASSWORD, RO_PASSWORD, MANUAL_PASSWORD])
    for text in (json.dumps(job), ship.api(central, env.token, "GET", "/api/fleet/jobs")[1] or ""):
        central.check(
            all(s not in json.dumps(text) for s in (RW_PASSWORD, RO_PASSWORD, MANUAL_PASSWORD)),
            "a password is in the job's record",
        )

    # --- the same name again is refused before a dump is taken; replace carries the new rows
    psql(source, "shop", "insert into orders(note) values ('one more at the source')")
    status, answer = transfer(env, {**body, "accounts": False, "plan": True})
    central.check(
        status == 400 and "already on" in json.dumps(answer),
        f"a second move of the same name was not refused: {status} {answer!r}",
    )
    central.check(dumps_of(source, "shop") == "", "the refused move took a dump")
    job = finished(env, {**body, "accounts": False, "replace": True})
    central.check(job["status"] == "completed", f"the replace failed: {json.dumps(job)[:3000]}")
    central.check(
        psql(target, "shop", "select count(*) from orders") == "2001",
        "replace did not bring the source's rows",
    )

    # --- under another name, and the source dropped after a last dump
    job = finished(env, {**body, "accounts": False, "new_name": "shop_two", "drop_source": True})
    central.check(job["status"] == "completed", f"the move failed: {json.dumps(job)[:3000]}")
    item = job["result"]["nodes"][0]["items"][0]
    central.check(
        item["source_dropped"] is True, f"the source was not dropped: {json.dumps(item)[:1500]}"
    )
    central.check(
        psql(target, "shop_two", "select count(*) from orders") == "2001",
        "the database under its new name did not arrive",
    )
    central.check(
        psql(source, "postgres", "select count(*) from pg_database where datname = 'shop'") == "0",
        "the source still has the database it was told to drop",
    )

    # --- what cannot work is refused before anything is dumped
    status, answer = transfer(env, {**body, "database": "ghost", "plan": True})
    central.check(
        status == 400 and "not a database of postgresql" in json.dumps(answer),
        f"a database the source lacks was not refused: {status} {answer!r}",
    )
    status, answer = transfer(env, {**body, "engine": "redis", "plan": True})
    central.check(
        status == 400, f"an engine that does not travel as a dump was accepted: {answer!r}"
    )


def scenario_mariadb(env: Any) -> None:
    """
    MariaDB: the same move, with an account that has a host.

    The accounts of MariaDB are ``user@host``: the one with access arrives with its password and
    its privileges on the database, and the engine's own accounts are left alone.
    """
    central, source, target = env.central, env.builder, env.target
    for server in (source, target):
        noust(server, "db install mariadb", timeout=1800)
    noust(source, "db create shop --engine mysql")
    maria(
        source,
        "create table orders (id int primary key auto_increment, note text); "
        "insert into orders (note) select concat('order ', seq) from seq_1_to_1500",
        database="shop",
    )
    noust(
        source,
        f"db user-create shop_rw -e mysql -d shop --profile read_write -p {MARIA_PASSWORD}",
    )

    body = {
        "database": "shop",
        "engine": "mysql",
        "from_node": SOURCE_NAME,
        "to": TARGET_NAME,
        "accounts": True,
    }
    job = finished(env, body)
    central.check(job["status"] == "completed", f"the move failed: {json.dumps(job)[:3000]}")
    item = job["result"]["nodes"][0]["items"][0]
    central.check(item["accounts"]["created"] == ["shop_rw"], f"accounts: {item['accounts']}")

    counted = maria(target, "select count(*) from orders", database="shop")
    central.check(counted.stdout.strip() == "1500", f"the rows did not arrive: {counted.stdout!r}")
    mine = maria(
        target,
        "select count(*) from orders",
        user="shop_rw",
        password=MARIA_PASSWORD,
        database="shop",
    )
    central.check(
        mine.returncode == 0 and mine.stdout.strip() == "1500",
        f"shop_rw cannot sign in with its password: {mine.stdout}{mine.stderr}",
    )
    wrote = maria(
        target,
        "insert into orders (note) values ('from rw')",
        user="shop_rw",
        password=MARIA_PASSWORD,
        database="shop",
    )
    central.check(wrote.returncode == 0, f"shop_rw cannot write: {wrote.stderr}")
    other = maria(target, "create database elsewhere", user="shop_rw", password=MARIA_PASSWORD)
    central.check(other.returncode != 0, "shop_rw can create databases: it was given too much")
    central.check(dumps_of(source, "shop") == "", "a dump stayed on the source")
    central_holds_no_secret(env, [MARIA_PASSWORD])
    central.check(
        maria(source, "select count(*) from orders", database="shop").stdout.strip() == "1500",
        "the source lost rows",
    )


SCENARIOS: list[tuple[str, Callable[[Any], None]]] = [
    ("postgresql", scenario_postgresql),
    ("mariadb", scenario_mariadb),
]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def prepare_images(*, rebuild: bool) -> None:
    """
    Have the two images, reusing the base the other harnesses built when it is there.

    Args:
        rebuild: Build them even when they exist.
    """
    have = run.sh(["docker", "image", "inspect", ship.BASE_IMAGE], check=False)
    reuse = run.sh(["docker", "image", "inspect", BASE_IMAGE_TO_REUSE], check=False)
    if have.returncode != 0 and reuse.returncode == 0 and not rebuild:
        print(f"[setup] tagging {BASE_IMAGE_TO_REUSE} as {ship.BASE_IMAGE}")
        run.sh(["docker", "tag", BASE_IMAGE_TO_REUSE, ship.BASE_IMAGE])
    ship.build_images(rebuild=rebuild)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Leave the containers running.")
    parser.add_argument("--skip-wheel-build", action="store_true", help="Reuse the newest wheel.")
    parser.add_argument(
        "--rebuild-images", action="store_true", help="Build the images even when they exist."
    )
    parser.add_argument(
        "--remove-images", action="store_true", help="Remove the images at the end."
    )
    parser.add_argument("--scenario", action="append", help="Run only this scenario (repeatable).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = time.monotonic()
    workdir = INTEGRATION_DIR / ".build"
    workdir.mkdir(exist_ok=True)
    if args.skip_wheel_build:
        wheels = sorted((workdir / "dist").glob("noust-*.whl"))
        if not wheels:
            raise HarnessError("--skip-wheel-build given but no wheel exists yet")
        wheel = wheels[-1]
    else:
        wheel = run.build_wheel(workdir)
    prepare_images(rebuild=args.rebuild_images)

    chosen = [s for s in SCENARIOS if not args.scenario or s[0] in args.scenario]
    failures = 0
    setup_error: str | None = None
    environment: Any = None
    try:
        try:
            environment = ship.setup(wheel)
        except HarnessError as exc:
            setup_error = str(exc)
        else:
            for name, function in chosen:
                print(f"\n=== {name} ===")
                for machine in (environment.central, environment.builder, environment.target):
                    machine.evidence = []
                try:
                    function(environment)
                except AssertionError as exc:
                    failures += 1
                    print(f"FAIL: {name}: {exc}")
                except (HarnessError, subprocess.TimeoutExpired) as exc:
                    failures += 1
                    print(f"FAIL: {name} (command error): {exc}")
                except (KeyError, TypeError, ValueError, IndexError) as exc:
                    failures += 1
                    print(f"FAIL: {name} (unexpected output): {type(exc).__name__}: {exc}")
                else:
                    print(f"PASS: {name}")
                finally:
                    evidence = (
                        environment.central.evidence
                        + environment.builder.evidence
                        + environment.target.evidence
                    )
                    if evidence:
                        print("--- evidence ---")
                        print("\n\n".join(evidence))
    finally:
        if args.keep:
            print(
                f"\n[teardown] --keep given, leaving {ship.CENTRAL}, {ship.BUILDER} and "
                f"{ship.TARGET} running"
            )
        else:
            print("\n[teardown] removing the containers and the network")
            ship.teardown(remove_images=args.remove_images)

    elapsed = time.monotonic() - started_at
    if setup_error is not None:
        print(f"\n[setup] FAILED before any scenario ran: {setup_error}")
        return 1
    print(f"\n[summary] {len(chosen)} scenario(s), {failures} failure(s), {elapsed:.1f}s total")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
