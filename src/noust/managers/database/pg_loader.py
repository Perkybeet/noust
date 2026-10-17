# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The login a PostgreSQL dump is loaded with: a throw-away role that is not a superuser.

A dump is a script. ``psql -f`` and ``pg_restore`` run whatever statements the
file holds with the privileges of the account that connected, and as the
cluster's superuser that includes ``COPY ... FROM PROGRAM``, ``CREATE EXTENSION``
of any extension, ``ALTER SYSTEM`` and reading any file the server can read. A
file from another provider, an upload, or a download is not code the operator
wrote, so (checklist H-19) it is loaded as the database's owner and not as the
superuser, and what needs more is a decision the operator takes with
``--as-superuser``.

Switching roles inside a superuser session is not enough: ``RESET ROLE`` and
``SET SESSION AUTHORIZATION DEFAULT`` give the superuser back from inside the
script. The limit has to be the login itself, so the loader signs in as its
own role, made for the load and dropped after it:

- ``NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS``, a password
  that is a SCRAM verifier (the statement never carries the password), and a
  ``VALID UNTIL`` so a role left behind by a killed process cannot sign in
  again.
- When the database's owner cannot escalate, the loader is made a member of
  that owner and the session starts with ``role = owner`` (``PGOPTIONS``), so
  every object the script creates belongs to the owner, as the real accounts
  expect, and ``RESET ROLE`` only leads back to the loader.
- When the owner is a superuser (a database made without an owner is
  ``postgres``'s), or can escalate (``CREATEROLE`` or a server-file role), the
  owner's identity would give the superuser back, so the loader gets
  ``CREATE`` on the database and owns what it makes; afterwards everything it
  owns is reassigned to the real owner and the role is dropped.

The pure parts (names, statements, the words of a refusal) are here; the
manager runs them (``PostgresManager._restricted_login``).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from noust.managers.database.base import LOAD_ACCOUNT_PREFIX, quote_identifier

__all__ = [
    "LOAD_ACCOUNT_PREFIX",
    "OwnerFacts",
    "asks_for_more_privilege",
    "load_role_name",
    "owner_query",
    "parse_owner",
    "provision_statements",
    "release_statements",
    "superuser_hint",
    "sweep_statement",
]

#: How long after it is made the loader may still sign in. Only the sign in is
#: bounded: a session already open is not cut when the time passes.
SIGN_IN_WINDOW = timedelta(minutes=20)

#: Sessions the loader may hold (pg_restore --jobs is not used; this is slack).
CONNECTION_LIMIT = 4

#: What the server says when a statement asked for more than the owner may do.
#: PostgreSQL words these as ``permission denied``, ``must be superuser`` and
#: ``must be owner of``, in the language of its ``lc_messages``: the English
#: forms are the ones every packaged build prints for ``psql``'s stderr.
_ASKS_FOR_MORE = re.compile(
    r"must be (?:a )?superuser"
    r"|must be owner of"
    r"|must be member of"
    r"|permission denied"
    r"|only roles with"
    r"|only superusers"
    r"|is not a trusted extension"
    r"|must have privileges of role",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OwnerFacts:
    """
    Who owns a database and whether being them would give the superuser back.

    Attributes:
        name: The owning role.
        privileged: The role is a superuser or can become one (``CREATEROLE``,
            ``REPLICATION``, or a member of a role that reads or runs things on
            the server).
    """

    name: str
    privileged: bool


#: ``LIKE`` pattern for the loaders: the underscores are literal.
_LOAD_LIKE = LOAD_ACCOUNT_PREFIX.replace("_", "\\_") + "%"


def load_role_name() -> str:
    """
    Returns:
        A new, unguessable name for a loader login.
    """
    return f"{LOAD_ACCOUNT_PREFIX}{secrets.token_hex(6)}"


def _ident(value: str) -> str:
    return quote_identifier(value, '"')


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def owner_query(database: str) -> str:
    """
    Build the statement that says who owns a database and what that role can do.

    Args:
        database: The database.

    Returns:
        SQL whose one row is ``owner|t`` or ``owner|f``: whether the owner is
        privileged. ``pg_has_role`` is asked for the three roles that read or
        run programs on the server, which exist from PostgreSQL 11.
    """
    return (
        "SELECT r.rolname || '|' || CASE WHEN (r.rolsuper OR r.rolcreaterole OR r.rolreplication "  # noqa: S608 - quoted literal, not interpolated data
        "OR pg_has_role(r.oid, 'pg_execute_server_program', 'USAGE') "
        "OR pg_has_role(r.oid, 'pg_read_server_files', 'USAGE') "
        "OR pg_has_role(r.oid, 'pg_write_server_files', 'USAGE')) THEN 't' ELSE 'f' END "
        "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.oid = d.datdba "
        f"WHERE d.datname = {_literal(database)};"
    )


def parse_owner(output: str) -> OwnerFacts | None:
    """
    Read the answer to :func:`owner_query`.

    Args:
        output: What psql printed.

    Returns:
        The owner, or None when the answer is not one row of that shape.
    """
    line = output.strip()
    name, _, flag = line.rpartition("|")
    if not name or flag not in ("t", "f"):
        return None
    return OwnerFacts(name=name, privileged=flag == "t")


def provision_statements(
    role: str,
    verifier: str,
    database: str,
    owner: OwnerFacts,
    *,
    now: datetime | None = None,
) -> tuple[str, str | None]:
    """
    Build the statements that make a loader login.

    Args:
        role: The loader's name (:func:`load_role_name`).
        verifier: Its password as a SCRAM verifier.
        database: The database it loads into.
        owner: Who owns that database.
        now: The clock, for tests.

    Returns:
        The statements to run from any database (create the role, give it the
        database and, for an owner that cannot be borrowed, ``CREATE`` on it),
        and the one to run inside the target database (its schema), or None
        when the loader borrows the owner and needs none.
    """
    until = (now or datetime.now(timezone.utc)) + SIGN_IN_WINDOW
    cluster = (
        f"CREATE ROLE {_ident(role)} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
        f"NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {CONNECTION_LIMIT} "
        f"VALID UNTIL {_literal(until.strftime('%Y-%m-%d %H:%M:%S+00'))} "
        f"PASSWORD {_literal(verifier)};\n"
        f"GRANT CONNECT ON DATABASE {_ident(database)} TO {_ident(role)};\n"
    )
    if not owner.privileged:
        return cluster + f"GRANT {_ident(owner.name)} TO {_ident(role)};\n", None
    return (
        cluster + f"GRANT CREATE, TEMPORARY ON DATABASE {_ident(database)} TO {_ident(role)};\n",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {_ident(role)};\n",
    )


def release_statements(role: str, owner: OwnerFacts) -> tuple[str, str]:
    """
    Build the statements that take a loader login away.

    Args:
        role: The loader.
        owner: Who owns the database it loaded into.

    Returns:
        The statements to run inside the target database (hand over what the
        loader made, then drop what is granted to it) and the one to run
        anywhere (drop the role).
    """
    inside = ""
    if owner.privileged:
        # What a loader that owned its own objects made belongs to the real
        # owner from here on; nothing it created is dropped with it.
        inside += f"REASSIGN OWNED BY {_ident(role)} TO {_ident(owner.name)};\n"
    inside += f"DROP OWNED BY {_ident(role)};\n"
    return inside, (
        "SELECT pg_catalog.pg_terminate_backend(pid) FROM pg_catalog.pg_stat_activity "  # noqa: S608 - quoted literal, not interpolated data
        f"WHERE usename = {_literal(role)};\n"
        f"DROP ROLE IF EXISTS {_ident(role)};\n"
    )


def sweep_statement() -> str:
    """
    Returns:
        SQL that drops the loader logins whose sign-in window has passed: a
        process killed mid-load leaves its role behind, and nothing else
        would ever remove it. A role that still owns something is left (the
        drop is skipped, not forced).
    """
    return (
        "DO $noust_sweep$\n"  # noqa: S608 - the pattern is a constant of ours, not input
        "DECLARE stale text;\n"
        "BEGIN\n"
        "  FOR stale IN SELECT rolname FROM pg_catalog.pg_roles\n"
        f"    WHERE rolname LIKE '{_LOAD_LIKE}'\n"
        "      AND rolvaliduntil IS NOT NULL AND rolvaliduntil < now()\n"
        "  LOOP\n"
        "    BEGIN\n"
        "      EXECUTE format('DROP ROLE %I', stale);\n"
        "    EXCEPTION WHEN OTHERS THEN\n"
        "      NULL;\n"
        "    END;\n"
        "  END LOOP;\n"
        "END\n"
        "$noust_sweep$;\n"
    )


def asks_for_more_privilege(output: str) -> bool:
    """
    Tell a load that failed for want of privilege from one that failed otherwise.

    Args:
        output: The loader's own words (psql's or pg_restore's stderr).

    Returns:
        True when the server refused a statement for lack of rights.
    """
    return bool(_ASKS_FOR_MORE.search(output))


def superuser_hint(database: str) -> str:
    """
    Say what to do about a dump that asked for more than the owner may do.

    Args:
        database: The database it was being loaded into.

    Returns:
        A paragraph for the error's details.
    """
    return (
        f"The dump was loaded as the owner of '{database}', not as the server's superuser, "
        "so what it asked for beyond the owner's rights was refused. A dump you trust "
        "(extensions that need a superuser, objects owned by other roles) can be loaded "
        "as the superuser instead: repeat the command with --as-superuser (in the API, "
        "as_superuser with confirm). Do that only for a dump you made or have read: a "
        "superuser's session can run programs on this server."
    )
