# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A database moved to another server of the fleet: the fleet action behind ``noust db transfer`` (C-21).

A dump and a restore between two servers was possible by hand (dump here, a remote destination,
``restore-remote`` there); this does it in one step, as a bulk action like the others
(:mod:`noust.web.fleet_jobs`): a plan first, a record of what was done, the node's own words when
something fails. What it adds is only what is specific to moving a database, and every step is a
call to a server's own API (rule 3):

- **The source dumps**: ``POST /api/databases/backups`` on the server that holds the database,
  the same dump its own backups take, checked and hashed there.
- **The dump crosses the central in pieces** (:mod:`noust.web.fleet_carry`), never held whole and
  written nowhere on the central: read from the source by ranges, written to the target as an upload
  that can resume. A request through a tunnel is cut after a few minutes, and a dump is gigabytes.
- **The target loads it with its own import**, the one any dump from elsewhere takes: as the
  database's owner, never as the superuser. Whoever moves a database cannot make the target run a
  program as root through the dump, because the superuser is a decision (``--as-superuser``) an
  operator takes on the target, not one a central takes for them.
- **The accounts follow when asked.** Those with access to the database on the source are made on
  the target with the password the source holds for each, which passes through this central and is
  kept nowhere (it reaches no log, no job record, no audit event), and the profile they had; one the
  source holds no password for is made with a new one the target keeps.
- **The source is left as it is**, unless the operator asked for it to be dropped, which the source
  does itself after a last dump, and refuses while an application uses it.

The dump the move made is removed from the source when it ends, whatever the outcome. The copy is
the database at the moment of the dump: writes after it stay on the source, so an application that
must not lose any is stopped first. The applications that use it are not pointed at the new server:
that is a decision about a network, not about a database.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from noust.core.exceptions import NoustError, ValidationError
from noust.fleet.aggregate import Asker, Failure, NodeSource
from noust.fleet.models import validate_node_name
from noust.managers.database.base import NAME_PATTERN
from noust.managers.database.instances import engine_of, is_instance_key, parse_instance_key
from noust.managers.database.moves import MOVABLE, release_of
from noust.managers.database.registry import DatabaseRegistry
from noust.web.fleet_carry import Carriage, carry
from noust.web.fleet_jobs import (
    ACTIONS,
    REQUEST_TIMEOUT,
    ActionFailed,
    ActionSpec,
    FleetRequest,
    NodeRun,
    _ceiling_refuses,
    resolve_targets,
)

__all__ = ["DATABASE_OPERATIONS", "transfer_request"]

#: The longest database name an engine accepts, as Noust spells one.
MAX_NAME_LENGTH = 64

#: A dump's file name as a server reports it: a name, never a path.
_DUMP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")

#: What a server's dump is checked against.
_SHA256 = re.compile(r"[0-9a-f]{64}")

#: What the server that holds the database must offer.
SOURCE_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/databases/databases"),
    ("GET", "/api/databases/engines"),
    ("POST", "/api/databases/backups"),
    ("GET", "/api/databases/backups/{name}/data"),
    ("DELETE", "/api/databases/backups/{name}"),
)

#: What it must offer besides, to hand its accounts over.
SOURCE_ACCOUNT_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/databases/databases/{engine}/{name}/access"),
    ("POST", "/api/databases/users/{engine}/{username}/password/reveal"),
)

#: What it must offer besides, to drop the database once it has moved.
SOURCE_DROP_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("DELETE", "/api/databases/databases/{engine}/{name}"),
)

#: What the server that receives the database must offer.
DATABASE_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/databases/databases"),
    ("GET", "/api/databases/engines"),
    ("POST", "/api/databases/imports/uploads"),
    ("PUT", "/api/databases/imports/uploads/{upload_id}"),
    ("GET", "/api/databases/imports/uploads/{upload_id}"),
    ("POST", "/api/databases/imports/uploads/{upload_id}/complete"),
    ("DELETE", "/api/databases/imports/uploads/{upload_id}"),
    ("POST", "/api/databases/imports"),
)

#: What it must offer besides, to receive the accounts.
TARGET_ACCOUNT_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/databases/users/{engine}"),
    ("POST", "/api/databases/users"),
)


# --------------------------------------------------------------------------- options


def _database_name(value: object, field: str) -> str:
    """
    Check a database name the way a server will.

    Args:
        value: What was given.
        field: The option it came from.

    Returns:
        The name.

    Raises:
        ValidationError: When it is not a name a server accepts.
    """
    if (
        not isinstance(value, str)
        or len(value) > MAX_NAME_LENGTH
        or not NAME_PATTERN.fullmatch(value)
    ):
        raise ValidationError(
            f"Invalid database name: {value!r}",
            details="A name starts with a letter, a digit or an underscore and holds only letters, "
            f"digits and the characters _ $ -, at most {MAX_NAME_LENGTH}.",
            field=field,
        )
    return value


def _engine_key(value: object, field: str) -> str:
    """
    Check an engine, or an instance key, whose databases travel as a dump.

    Args:
        value: What was given (an alias such as ``pg`` is read as the engine it names).
        field: The option it came from.

    Returns:
        The key, with the engine spelled as the servers know it.

    Raises:
        ValidationError: When it is not an engine or an instance, or its databases do not move as
            a dump (a Redis snapshot is the whole instance).
    """
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            "Say which engine the database is on", details="Such as postgresql.", field=field
        )
    key = value.strip()
    parse_instance_key(key)
    named = DatabaseRegistry.canonical(engine_of(key))
    if named is None:
        raise ValidationError(
            f"Unknown database engine: {engine_of(key)!r}",
            details="List them with 'noust db engines'.",
            field=field,
        )
    if named not in MOVABLE:
        raise ValidationError(
            f"{named} databases do not move to another server as a dump",
            details=f"Only {', '.join(sorted(MOVABLE))} do: a Redis snapshot is the whole instance, "
            "and ClickHouse restores from its own backups.",
            field=field,
        )
    return named + (key[len(engine_of(key)) :] if is_instance_key(key) else "")


def _transfer_options(options: Any) -> dict[str, Any]:
    """
    Check and complete the options of a transfer.

    Args:
        options: What was given.

    Returns:
        ``database``, ``engine``, ``from_node``, ``target_engine``, ``new_name``, ``replace``,
        ``accounts`` and ``drop_source``.

    Raises:
        ValidationError: A name that is not one, an engine whose databases do not travel as a dump,
            an engine on the target of another kind than the source's, or no source server.
    """
    # The helpers of the sibling action modules are fetched when used: the fleet's job module loads
    # them all at its end, so at import time whichever of them was imported first is half made.
    from noust.web.fleet_placement_actions import _check, _flag

    given = _check(
        {
            "database": str,
            "engine": str,
            "from_node": str,
            "target_engine": str,
            "new_name": str,
            "replace": bool,
            "accounts": bool,
            "drop_source": bool,
        },
        options,
    )
    source = given.get("from_node")
    if not isinstance(source, str) or not source:
        raise ValidationError(
            "Say which server the database is moved from",
            details="Give the server that holds it (--from db-1).",
            field="from_node",
        )
    engine = _engine_key(given.get("engine"), "engine")
    target = given.get("target_engine")
    target_engine = _engine_key(target, "target_engine") if target else None
    if target_engine and engine_of(target_engine) != engine_of(engine):
        raise ValidationError(
            f"A {engine_of(engine)} database does not load into {engine_of(target_engine)}",
            details="The target engine must be the same kind as the source's.",
            field="target_engine",
        )
    new = given.get("new_name")
    return {
        "database": _database_name(given.get("database"), "database"),
        "engine": engine,
        "from_node": validate_node_name(source),
        "target_engine": target_engine,
        "new_name": _database_name(new, "new_name") if new else None,
        "replace": _flag(given, "replace", False),
        "accounts": _flag(given, "accounts", False),
        "drop_source": _flag(given, "drop_source", False),
    }


# --------------------------------------------------------------------------- the plan


def _source_operations(options: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """
    Args:
        options: The transfer's checked options.

    Returns:
        What the source must offer for this transfer.
    """
    wanted = SOURCE_OPERATIONS
    if options["accounts"]:
        wanted += SOURCE_ACCOUNT_OPERATIONS
    if options["drop_source"]:
        wanted += SOURCE_DROP_OPERATIONS
    return wanted


def _rows_on(server: NodeSource, engine: str) -> list[dict[str, Any]]:
    """
    List the databases an engine holds on a server, as the server describes them.

    Args:
        server: The server.
        engine: The engine or instance key.

    Returns:
        Their rows (``name``, ``owner``...).

    Raises:
        Failure: The server did not answer.
        ValidationError: The server could not read the engine, so an empty list would lie.
    """
    answer = server.get("/api/databases/databases", params={"engine": engine})
    rows = answer.get("databases") if isinstance(answer, dict) else None
    problems = [
        problem
        for problem in (answer.get("problems") if isinstance(answer, dict) else None) or []
        if isinstance(problem, dict) and problem.get("engine") == engine
    ]
    if problems:
        raise ValidationError(
            f"{server.name} could not read the databases of {engine}",
            details=str(problems[0].get("message") or problems[0].get("output") or "")[:500]
            or "Look at the engine's status there.",
            field="engine",
        )
    return [row for row in rows or [] if isinstance(row, dict) and row.get("name")]


def _names_on(server: NodeSource, engine: str) -> list[str]:
    """
    List the names of the databases an engine holds on a server.

    Args:
        server: The server.
        engine: The engine or instance key.

    Returns:
        The names.

    Raises:
        Failure: The server did not answer.
        ValidationError: The server could not read the engine, so an empty list would lie.
    """
    return [str(row["name"]) for row in _rows_on(server, engine)]


def _engine_row(server: NodeSource, engine: str) -> dict[str, Any] | None:
    """
    Read an engine's state on a server.

    Args:
        server: The server.
        engine: The engine or instance key.

    Returns:
        Its row of ``GET /api/databases/engines``; None when the server does not list it.

    Raises:
        Failure: The server did not answer.
    """
    answer = server.get("/api/databases/engines")
    for row in answer.get("engines", []) if isinstance(answer, dict) else []:
        if isinstance(row, dict) and row.get("name") == engine:
            return row
    return None


def _check_engines(
    options: dict[str, Any], source: NodeSource, target: NodeSource, target_engine: str
) -> list[str]:
    """
    Check that both engines run and that the target is not older than the source.

    Args:
        options: The transfer's checked options.
        source: The server that holds the database.
        target: The server that receives it.
        target_engine: The engine or instance on the target.

    Returns:
        Sentences for the plan (the two are different servers of one engine).

    Raises:
        ValidationError: An engine is not running, or the target is older.
        Failure: A server did not answer.
    """
    engine = options["engine"]
    there = _engine_row(source, engine)
    if there is None or not there.get("running"):
        raise ValidationError(
            f"{engine} is not running on {source.name}",
            details="Start it there (noust db start) and plan again.",
            field="from_node",
        )
    here = _engine_row(target, target_engine)
    if here is None or not here.get("running"):
        raise ValidationError(
            f"{target_engine} is not running on {target.name}",
            details=f"Install or start it there first: noust db install {engine_of(target_engine)}.",
            field="target_engine",
        )
    if there.get("display_name") != here.get("display_name"):
        return [
            f"{source.name} runs {there.get('display_name')} and {target.name} runs "
            f"{here.get('display_name')}: the dump loads, but check what the application relies on."
        ]
    before = release_of(engine_of(engine), there.get("version"))
    after = release_of(engine_of(engine), here.get("version"))
    if before and after and after < before:
        raise ValidationError(
            f"{target.name} runs {target_engine} {here.get('version')}, which is older than the "
            f"source's {there.get('version')}",
            details="A dump of a newer server does not load into an older one. Upgrade the target "
            "first.",
            field="target_engine",
        )
    return []


def _transfer_prepare(
    request: FleetRequest, options: dict[str, Any], manager: Any, asker: Asker
) -> tuple[FleetRequest, list[str]]:
    """
    Refuse a move that cannot work, before a dump is taken, and say what it will do.

    Asked of the servers before anything is dumped, because the dump is the expensive part: the
    source holds the database and offers what moving needs, the target runs the engine, is not
    older than the source and does not hold a database by that name (unless it is to be replaced).

    Args:
        request: The request.
        options: Its checked options.
        manager: The node registry.
        asker: Who asks.

    Returns:
        The request, unchanged, and the sentences the plan shows.

    Raises:
        ValidationError: Anything above.
    """
    targets = resolve_targets(request, manager)
    if len(targets) != 1:
        raise ValidationError(
            "A database goes to one server",
            details="Move it to one, and move it again from there to another if it is wanted.",
            field="targets",
        )
    destination = targets[0]
    origin = manager.get(options["from_node"])
    if origin.name == destination.name:
        raise ValidationError(
            f"{origin.name} is both the source and the target: that is the same server",
            details="To move a database between engines on one server: noust db move.",
            field="targets",
        )
    from noust.web.fleet_ship_actions import _missing_operation

    wanted = _source_operations(options)
    spec = ActionSpec(
        "database_transfer_source",
        "Dump a database for another server",
        wanted,
        serial=1,
        max_failures=0,
        run=_database_transfer,
    )
    refused = _ceiling_refuses(origin, spec)
    if refused is not None:
        raise ValidationError(refused, details="Raise it on the node with 'noust fleet access'.")
    source = NodeSource(manager, origin, asker, REQUEST_TIMEOUT)
    target = NodeSource(manager, destination, asker, REQUEST_TIMEOUT)
    engine = options["engine"]
    target_engine = options["target_engine"] or engine
    name = options["new_name"] or options["database"]
    notes: list[str] = []
    try:
        lacking = _missing_operation(source, wanted)
        if lacking is not None:
            raise ValidationError(
                f"{origin.name} does not offer {lacking}",
                details="It runs an older Noust: update it first.",
                field="from_node",
            )
        if options["database"] not in _names_on(source, engine):
            raise ValidationError(
                f"{options['database']} is not a database of {engine} on {origin.name}",
                details=f"List what {origin.name} holds with 'noust db list' there, or in its page of the console.",
                field="database",
            )
        # A target that cannot be asked is reported by the plan itself, as unsupported or
        # unreachable; asking it more here would only repeat that in the words of a refusal.
        try:
            needed = DATABASE_OPERATIONS + (
                TARGET_ACCOUNT_OPERATIONS if options["accounts"] else ()
            )
            if _missing_operation(target, needed) is None:
                notes += _check_engines(options, source, target, target_engine)
                if name in _names_on(target, target_engine) and not options["replace"]:
                    raise ValidationError(
                        f"{name} is already on {destination.name}",
                        details="Move it under another name (--as), or replace it, which takes a "
                        "safety copy of the one there first (--replace).",
                        field="new_name",
                    )
        except Failure:
            pass
    except Failure as exc:
        raise ValidationError(
            f"{origin.name} cannot be asked to dump {options['database']}: {exc.message}",
            details=exc.hint or "Check it with 'noust node test'.",
            field="from_node",
        ) from exc
    notes.insert(
        0,
        f"{options['database']} is dumped on {origin.name}, read in pieces through the tunnels by "
        f"this central, which keeps none of it, and loaded on {destination.name} by its own import, "
        "as the database's owner: nothing in the dump runs as the superuser.",
    )
    if options["accounts"]:
        notes.append(
            f"The accounts with access to it on {origin.name} are made on {destination.name} with "
            "the password Noust holds for each, which passes through this central and is kept "
            "nowhere, and the profile they had. One whose password Noust does not hold gets a new "
            f"one, which {destination.name} keeps."
        )
    if options["replace"]:
        notes.append(
            f"A {name} that {destination.name} holds is replaced, after a safety copy of it taken "
            "there."
        )
    if options["drop_source"]:
        notes.append(
            f"Once it has arrived it is dropped from {origin.name}, after a last dump kept there; "
            "an application that still uses it keeps that from happening, and says so."
        )
    else:
        notes.append(
            f"It stays on {origin.name}, untouched. The copy is the database at the moment of the "
            "dump: writes after it are not carried, so stop what writes first when that matters. "
            "Applications are not pointed at the new server."
        )
    return request, notes


# --------------------------------------------------------------------------- one server


def _dump_on_source(run: NodeRun, source: NodeSource, engine: str, database: str) -> dict[str, Any]:
    """
    Have the source dump the database, and read what it says it made.

    Args:
        run: The server the database goes to.
        source: The server that holds it.
        engine: The engine or instance key there.
        database: The database.

    Returns:
        ``name``, ``size`` and ``sha256`` of the dump.

    Raises:
        ActionFailed: The dump failed, or the source described something that is not a dump.
    """
    run.step(f"Dumping {database} on {source.name}")
    answer = run.call(
        "POST",
        "/api/databases/backups",
        body={"database": database, "engine": engine, "compress": True},
        on=source,
    )
    job = run.run_job(answer, f"The dump of {database}", on=source)
    result = job.get("result")
    name = result.get("name") if isinstance(result, dict) else None
    size = result.get("size") if isinstance(result, dict) else None
    digest = result.get("sha256") if isinstance(result, dict) else None
    if (
        not isinstance(name, str)
        or not _DUMP_NAME.fullmatch(name)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 1
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
    ):
        raise ActionFailed(
            f"{source.name} dumped {database} and described something that is not a dump",
            output=str(result)[:2000],
        )
    return {"name": name, "size": size, "sha256": digest}


def _let_go_of_dump(run: NodeRun, source: NodeSource, engine: str, name: str) -> None:
    """
    Remove the dump the move made from the source, whatever happened.

    Args:
        run: The server the database goes to.
        source: The server that holds the dump.
        engine: The engine there.
        name: The dump.
    """
    try:
        run.send(
            "DELETE",
            f"/api/databases/backups/{quote(name, safe='')}",
            params={"engine": engine},
            on=source,
        )
    except Failure as exc:
        run.log(f"The dump {name} could not be removed from {source.name}: {exc.message}")


def _read_accounts(
    run: NodeRun, source: NodeSource, target_engine: str
) -> tuple[list[dict[str, Any]], set[tuple[str, str]]]:
    """
    Read who has access to the database on the source, and who exists already on the target.

    Args:
        run: The server the database goes to.
        source: The server that holds it.
        target_engine: The engine or instance key on the target.

    Returns:
        The source's access entries (the engine's own accounts left out), and the
        ``(account, host)`` pairs the target already has.

    Raises:
        Failure: A server did not answer.
    """
    engine = run.options["engine"]
    database = run.options["database"]
    run.step(f"Reading the accounts of {database} on {source.name}")
    listed = run.call(
        "GET",
        f"/api/databases/databases/{quote(engine, safe='')}/{quote(database, safe='')}/access",
        on=source,
    )
    entries = [
        entry
        for entry in (listed.get("access") if isinstance(listed, dict) else None) or []
        if isinstance(entry, dict) and entry.get("username") and not entry.get("internal")
    ]
    known = run.call("GET", f"/api/databases/users/{quote(target_engine, safe='')}")
    present = {
        (str(user.get("username")), str(user.get("host") or "localhost"))
        for user in (known.get("users") if isinstance(known, dict) else None) or []
        if isinstance(user, dict)
    }
    return entries, present


def _make_account(
    run: NodeRun,
    source: NodeSource,
    entry: dict[str, Any],
    present: set[tuple[str, str]],
    accounts: dict[str, list[str]],
    *,
    target_engine: str,
    database: str | None,
) -> Failure | None:
    """
    Make one account on the target, with the password the source holds for it.

    Args:
        run: The server the database goes to.
        source: The server that held it.
        entry: What the source's access list says of the account.
        present: What the target has already; an account in it is left as it is.
        accounts: The record: ``created``, ``existing`` and ``new_password`` are filled in.
        target_engine: The engine or instance key on the target.
        database: The database to give it its profile on; None to make the account alone (the
            owner, before the database exists).

    Returns:
        What went wrong, or None.
    """
    engine = run.options["engine"]
    username = str(entry["username"])
    host = str(entry.get("host") or "localhost")
    if (username, host) in present:
        accounts["existing"].append(username)
        return None
    password = _password_on_source(run, source, engine, username, entry)
    body: dict[str, Any] = {"username": username, "engine": target_engine, "host": host}
    if database is not None:
        body["database"] = database
        body["profile"] = entry.get("profile") if entry.get("profile") != "custom" else "read_write"
    if password is not None:
        body["password"] = password
    run.step(f"Making the account {username} on {run.name}")
    try:
        run.call("POST", "/api/databases/users", body=body)
    except Failure as exc:
        return exc
    accounts["created"].append(username)
    present.add((username, host))
    if password is None:
        accounts["new_password"].append(username)
    return None


def _owner_account(entries: list[dict[str, Any]], owner: str | None) -> dict[str, Any] | None:
    """
    Find the source's owner of the database among the accounts that have access to it.

    Args:
        entries: The source's access entries.
        owner: Who owns the database there, as its listing says.

    Returns:
        The owner's entry; None for an engine with no owners, for the engine's own account
        (never listed) and for an owner the access list does not show.
    """
    if not owner:
        return None
    return next((entry for entry in entries if entry.get("username") == owner), None)


def _discard_upload(run: NodeRun, upload_id: str) -> None:
    """
    Throw away a finished upload on the target, whatever it was for.

    Args:
        run: The server the upload is on.
        upload_id: The upload.
    """
    try:
        run.send("DELETE", f"/api/databases/imports/uploads/{quote(upload_id, safe='')}")
    except Failure as exc:
        run.log(f"The upload {upload_id} could not be removed from {run.name}: {exc.message}")


def _carry_accounts(
    run: NodeRun,
    source: NodeSource,
    item: dict[str, Any],
    name: str,
    target_engine: str,
    entries: list[dict[str, Any]],
    present: set[tuple[str, str]],
    *,
    skip: str | None,
) -> None:
    """
    Make on the target the other accounts that had access to the database on the source.

    Every account is tried, and the ones that could not be made are named together: an account that
    fails does not hide the others, and the data is already there.

    Args:
        run: The server the database went to.
        source: The server that held it.
        item: The record of this move; its ``accounts`` are filled in.
        name: The database's name on the target.
        target_engine: The engine or instance key there.
        entries: The source's access entries.
        present: What the target has already.
        skip: The owner's account, made before the data.

    Raises:
        ActionFailed: Some account could not be made; the node's words are the output.
    """
    accounts = item["accounts"]
    failures: list[tuple[str, Failure]] = []
    for entry in entries:
        username = str(entry["username"])
        if username == skip:
            continue
        failure = _make_account(
            run, source, entry, present, accounts, target_engine=target_engine, database=name
        )
        if failure is not None:
            failures.append((username, failure))
    if failures:
        accounts["failed"] = [username for username, _ in failures]
        raise ActionFailed(
            f"{', '.join(accounts['failed'])} could not be made on {run.name}",
            details=f"The database itself is on {run.name}; make the accounts there "
            "(noust db user-create) and give them access.",
            output="\n\n".join(
                f"{username}: {exc.output or exc.message}" for username, exc in failures
            )[:4000],
        )


def _password_on_source(
    run: NodeRun, source: NodeSource, engine: str, username: str, entry: dict[str, Any]
) -> str | None:
    """
    Ask the source for the password it holds for an account.

    Args:
        run: The server the database goes to.
        source: The server that held it.
        engine: The engine there.
        username: The account.
        entry: What the source's access list says of it.

    Returns:
        The password; None when the source holds none for it (the target will make a new one).

    Raises:
        Failure: The source refused to show it to this operator, or could not be reached.
    """
    if not entry.get("managed"):
        return None
    try:
        answer = run.call(
            "POST",
            f"/api/databases/users/{quote(engine, safe='')}/{quote(username, safe='')}"
            "/password/reveal",
            on=source,
        )
    except Failure as exc:
        if exc.kind in ("error", "unsupported"):
            return None
        raise
    password = answer.get("password") if isinstance(answer, dict) else None
    return password if isinstance(password, str) and password else None


def _drop_source(run: NodeRun, source: NodeSource, item: dict[str, Any]) -> None:
    """
    Drop the database from the source, after the source's own last dump.

    Never fatal: the data is on the target by now, and a source that keeps the database because an
    application uses it is said in the record and not turned into a failed move.

    Args:
        run: The server the database went to.
        source: The server that held it.
        item: The record of this move.
    """
    engine = run.options["engine"]
    database = run.options["database"]
    run.step(f"Dropping {database} from {source.name}")
    try:
        answer = run.call(
            "DELETE",
            f"/api/databases/databases/{quote(engine, safe='')}/{quote(database, safe='')}",
            params={"keep_backup": "true", "force": "false", "unlink": "false"},
            on=source,
        )
        run.run_job(answer, f"Dropping {database}", on=source)
    except (Failure, ActionFailed) as exc:
        item["warnings"].append(
            f"{database} was not dropped from {source.name}: {exc.message}. It is on "
            f"{run.name} now, and still there."
        )
        return
    item["source_dropped"] = True


def _database_transfer(run: NodeRun) -> None:
    """
    Move the database to this server, from the one it is on.

    Args:
        run: The server it goes to.

    Raises:
        ActionFailed: The target already holds it, a dump or a load failed, the transfer kept
            breaking, or an account could not be made.
    """
    options = run.options
    source = run.peer
    if source is None:
        raise ActionFailed("There is no server to move the database from")
    engine = options["engine"]
    database = options["database"]
    target_engine = options["target_engine"] or engine
    name = options["new_name"] or database
    if name in _names_on(run.source, target_engine) and not options["replace"]:
        raise ActionFailed(
            f"{name} is already on {run.name}",
            details="Move it under another name, or replace it (--replace).",
        )
    item: dict[str, Any] = {
        "database": name,
        "engine": target_engine,
        "from": source.name,
        "source_database": database,
        "source_engine": engine,
        "state": "running",
        "data_loaded": False,
        "source_dropped": False,
        "warnings": [],
    }
    run.items.append(item)
    entries: list[dict[str, Any]] = []
    present: set[tuple[str, str]] = set()
    owner: str | None = None
    if options["accounts"]:
        item["accounts"] = {"created": [], "existing": [], "new_password": []}
        entries, present = _read_accounts(run, source, target_engine)
        wanted = _owner_account(
            entries,
            next(
                (
                    row.get("owner")
                    for row in _rows_on(source, engine)
                    if row.get("name") == database
                ),
                None,
            ),
        )
        if wanted is not None:
            # The owner first: the target makes the database with it, so what the dump holds
            # belongs to the account it belonged to, and the other profiles are set against it.
            failure = _make_account(
                run,
                source,
                wanted,
                present,
                item["accounts"],
                target_engine=target_engine,
                database=None,
            )
            if failure is not None:
                raise ActionFailed(
                    f"{wanted['username']}, the owner of {database}, could not be made on "
                    f"{run.name}",
                    details="Nothing was dumped. Make the account there (noust db user-create) "
                    "or move the database without --accounts.",
                    output=failure.output or failure.message,
                ) from failure
            owner = str(wanted["username"])
    dump = _dump_on_source(run, source, engine, database)
    item["dump"] = dict(dump)
    try:
        upload_id = carry(
            run,
            Carriage(
                source=source,
                read_path=f"/api/databases/backups/{quote(dump['name'], safe='')}/data",
                read_params={"engine": engine},
                size=dump["size"],
                start_path="/api/databases/imports/uploads",
                start_body={
                    "file_name": dump["name"],
                    "size": dump["size"],
                    "sha256": dump["sha256"],
                },
                upload_path="/api/databases/imports/uploads/{id}",
                noun="the dump",
                gone_hint="It may have been removed; move the database again to take a new one.",
            ),
        )
        run.step(f"Loading {name} on {run.name}")
        body: dict[str, Any] = {
            "engine": target_engine,
            "database": name,
            "upload_id": upload_id,
            "replace": bool(options["replace"]),
            "safety_backup": True,
        }
        if owner is not None:
            body["owner"] = owner
        try:
            loading = run.call("POST", "/api/databases/imports", body=body)
            run.run_job(loading, f"The load of {name}")
        except (Failure, NoustError):
            # A finished upload stays for a retry; this one is a copy of the database on the
            # target's disk, and the next attempt takes a new dump.
            _discard_upload(run, upload_id)
            raise
    finally:
        _let_go_of_dump(run, source, engine, dump["name"])
    if name not in _names_on(run.source, target_engine):
        raise ActionFailed(
            f"{run.name} reported {name} loaded and does not list it",
            details="Look at its import job before trying again.",
        )
    item["data_loaded"] = True
    if options["accounts"]:
        _carry_accounts(run, source, item, name, target_engine, entries, present, skip=owner)
    if options["drop_source"]:
        _drop_source(run, source, item)
    item["state"] = "succeeded"
    run.step(f"{name} is on {run.name}")


# --------------------------------------------------------------------------- the request


def transfer_request(
    database: str,
    *,
    to: str,
    engine: str,
    from_node: str,
    target_engine: str | None = None,
    new_name: str | None = None,
    replace: bool = False,
    accounts: bool = False,
    drop_source: bool = False,
) -> FleetRequest:
    """
    Build the request that moves a database to another server.

    The one place what the operator asked for becomes a request, for the console's API and the
    terminal alike.

    Args:
        database: The database on the source.
        to: The server that receives it.
        engine: The engine (or instance key) it is on at the source.
        from_node: The server that holds it.
        target_engine: The engine or instance on the target; the same as the source's by default.
        new_name: Its name on the target; the same by default.
        replace: Replace a database of that name the target holds, after a safety copy.
        accounts: Also make the accounts with access to it.
        drop_source: Drop it from the source once it has moved, after a last dump.

    Returns:
        The request.
    """
    return FleetRequest(
        action="database_transfer",
        nodes=[to],
        options={
            "database": database,
            "engine": engine,
            "from_node": from_node,
            "target_engine": target_engine,
            "new_name": new_name,
            "replace": replace,
            "accounts": accounts,
            "drop_source": drop_source,
        },
    )


ACTIONS.update(
    {
        "database_transfer": ActionSpec(
            "database_transfer",
            "Move a database to another server",
            DATABASE_OPERATIONS,
            serial=1,
            max_failures=0,
            run=_database_transfer,
            check_options=_transfer_options,
            prepare=_transfer_prepare,
            peer_option="from_node",
        )
    }
)
