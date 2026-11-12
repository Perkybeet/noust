# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The roles a PostgreSQL dump needs to exist before it is loaded (item 193).

A dump says who owns each object and who may read it, by role name, and loading it names those
roles: on a container that never had one of them (a new container, another server) the load stops
at the first ``GRANT`` or ``ALTER ... OWNER``. Two kinds of role are told apart here, because they
are not the same problem:

* the ones **Noust made** (``noust_ro_<database>``, the read-only account the console's SQL signs
  in as, and the throw-away ``noust_load_`` logins): Noust knows them, so a restore makes the ones
  that are missing, without a login, and the next read-only query provisions each one as it
  always does;
* every other: the operator's, or an application's. Making up a role someone else owns, with
  whatever attributes, is not the restore's to do, so the restore stops before it touches
  anything and names the role and the command that makes it.

This module only reads: the text of the dump's schema (what ``pg_restore --schema-only`` prints),
the names it mentions, and what to make. Running it is
:func:`noust.managers.stack_databases.restore_stack_database`'s.

The text is read the way PostgreSQL reads it, not searched: a table called ``"a TO b"``, a function
body, a comment and a string are not statements, and a quoted name keeps its case and its quotes'
doubling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from noust.managers.database.base import LOAD_ACCOUNT_PREFIX, READ_ONLY_ACCOUNT_PREFIXES

__all__ = ["RolePlan", "create_statement", "is_noust_role", "plan_roles", "roles_named_by"]

#: What may follow a prefix of Noust's own for the name to be one Noust could have made: no
#: character that would need quoting, so a name taken from somebody's dump is never spliced into
#: a statement as anything but a plain identifier.
_NOUST_SUFFIX = re.compile(r"[A-Za-z0-9_$.-]+")

#: Words that stand where a role goes and are not one.
_NOT_ROLES = frozenset({"public", "current_user", "current_role", "session_user", "user"})

#: A dollar-quote opener (``$$`` or ``$tag$``).
_DOLLAR = re.compile(r"\$[A-Za-z_]?[A-Za-z0-9_]*\$")

#: An unquoted word.
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


@dataclass
class RolePlan:
    """
    What a dump asks of the target's roles.

    Attributes:
        create: The roles Noust made that the target lacks, to be made before the load.
        missing: The roles the target lacks that Noust did not make: the load cannot go on.
    """

    create: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def is_noust_role(name: str) -> bool:
    """
    Tell whether a role is one Noust made, and is safe to make again by name.

    Args:
        name: A role as a dump names it.

    Returns:
        True for a name that is one of Noust's prefixes followed by plain characters only.
    """
    prefixes = (*READ_ONLY_ACCOUNT_PREFIXES, LOAD_ACCOUNT_PREFIX)
    for prefix in prefixes:
        if name.startswith(prefix) and _NOUST_SUFFIX.fullmatch(name[len(prefix) :]):
            return True
    return False


def create_statement(role: str) -> str:
    """
    Build the statement that makes one of Noust's roles.

    Args:
        role: A name :func:`is_noust_role` accepted.

    Returns:
        ``CREATE ROLE "<role>" NOLOGIN``: it cannot sign in until Noust provisions it, which is
        what it does the first time the console reads the database.
    """
    return f'CREATE ROLE "{role.replace(chr(34), chr(34) * 2)}" NOLOGIN'


def plan_roles(named: list[str], existing: set[str]) -> RolePlan:
    """
    Decide what to make before a dump is loaded.

    Args:
        named: The roles the dump names (:func:`roles_named_by`).
        existing: The roles the target has.

    Returns:
        The plan: Noust's roles to make, and the others the target is missing.
    """
    plan = RolePlan()
    for name in named:
        if name in existing:
            continue
        (plan.create if is_noust_role(name) else plan.missing).append(name)
    return plan


def _statements(text: str) -> list[list[tuple[str, str]]]:
    """
    Split SQL into statements of tokens, leaving out what is not code.

    Comments, strings and dollar-quoted bodies are skipped; a quoted identifier is one token
    that keeps its text; an unquoted word is lowered, as PostgreSQL folds it.

    Args:
        text: The script.

    Returns:
        One list of ``(kind, value)`` per statement; ``kind`` is ``word``, ``ident`` or ``punct``.
    """
    statements: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    i, size = 0, len(text)
    while i < size:
        char = text[i]
        if char.isspace():
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = size if end < 0 else end + 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = size if end < 0 else end + 2
        elif char == '"':
            value, i = _quoted_identifier(text, i)
            current.append(("ident", value))
        elif char == "'":
            i = _skip_string(text, i, escapes=False)
        elif char == "$" and (opener := _DOLLAR.match(text, i)):
            end = text.find(opener.group(0), opener.end())
            i = size if end < 0 else end + len(opener.group(0))
        elif char in "eE" and text.startswith("'", i + 1) and not _after_word(text, i):
            i = _skip_string(text, i + 1, escapes=True)
        elif (word := _WORD.match(text, i)) is not None:
            current.append(("word", word.group(0).lower()))
            i = word.end()
        elif char == ";":
            if current:
                statements.append(current)
            current = []
            i += 1
        else:
            current.append(("punct", char))
            i += 1
    if current:
        statements.append(current)
    return statements


def _after_word(text: str, index: int) -> bool:
    """
    Args:
        text: The script.
        index: A position in it.

    Returns:
        Whether the character before it belongs to a word, so ``e`` there is not a string's prefix.
    """
    return index > 0 and (text[index - 1].isalnum() or text[index - 1] in "_$")


def _quoted_identifier(text: str, start: int) -> tuple[str, int]:
    """
    Read a double-quoted identifier.

    Args:
        text: The script.
        start: Where its opening quote is.

    Returns:
        Its name, with ``""`` read as one quote, and where it ends.
    """
    value: list[str] = []
    i = start + 1
    while i < len(text):
        if text[i] == '"':
            if text.startswith('""', i):
                value.append('"')
                i += 2
                continue
            return "".join(value), i + 1
        value.append(text[i])
        i += 1
    return "".join(value), i


def _skip_string(text: str, start: int, *, escapes: bool) -> int:
    """
    Step over a single-quoted string.

    Args:
        text: The script.
        start: Where its opening quote is.
        escapes: Whether a backslash escapes the next character (``E'...'``).

    Returns:
        Where the string ends.
    """
    i = start + 1
    while i < len(text):
        if escapes and text[i] == "\\":
            i += 2
            continue
        if text[i] == "'":
            if text.startswith("''", i):
                i += 2
                continue
            return i + 1
        i += 1
    return i


def _roles_after(tokens: list[tuple[str, str]], start: int, stop: frozenset[str]) -> list[str]:
    """
    Read a comma-separated list of roles.

    Args:
        tokens: A statement.
        start: Where the list begins.
        stop: The words that end it.

    Returns:
        The roles in it, without the words that stand in for one.
    """
    roles: list[str] = []
    i = start
    if i < len(tokens) and tokens[i] == ("word", "group"):
        i += 1
    while i < len(tokens):
        kind, value = tokens[i]
        if kind == "word" and value in stop:
            break
        if kind == "ident":
            roles.append(value)
        elif kind == "word" and value not in _NOT_ROLES:
            roles.append(value)
        i += 1
        if i < len(tokens) and tokens[i] == ("punct", ","):
            i += 1
            continue
        break
    return roles


def _last(tokens: list[tuple[str, str]], word: str) -> int | None:
    """
    Args:
        tokens: A statement.
        word: A keyword.

    Returns:
        The position of its last occurrence, or None.
    """
    found = [i for i, token in enumerate(tokens) if token == ("word", word)]
    return found[-1] if found else None


def roles_named_by(script: str) -> list[str]:
    """
    List the roles a dump's schema script needs to exist.

    Only the statements that name a role are read: ``ALTER ... OWNER TO``, ``GRANT ... TO``,
    ``REVOKE ... FROM`` and ``ALTER DEFAULT PRIVILEGES [FOR ROLE ...]``.

    Args:
        script: What ``pg_restore --schema-only`` prints for the dump.

    Returns:
        The names, each once, sorted.
    """
    names: set[str] = set()
    after_list = frozenset({"with", "granted", "cascade", "restrict"})
    for tokens in _statements(script):
        head = tokens[0][1] if tokens and tokens[0][0] == "word" else ""
        if head not in ("alter", "grant", "revoke"):
            continue
        if head == "alter":
            owner = _last(tokens, "owner")
            if (
                owner is not None
                and owner + 1 < len(tokens)
                and tokens[owner + 1] == ("word", "to")
            ):
                names.update(_roles_after(tokens, owner + 2, after_list))
            if tokens[1:3] == [("word", "default"), ("word", "privileges")]:
                if (for_at := _last(tokens, "for")) is not None:
                    names.update(
                        _roles_after(tokens, for_at + 2, frozenset({"in", "grant", "revoke"}))
                    )
        if head == "grant" or ("grant" in {t[1] for t in tokens if t[0] == "word"}):
            to = _last(tokens, "to")
            if to is not None:
                names.update(_roles_after(tokens, to + 1, after_list))
            by = _last(tokens, "by")
            if by is not None and by > 0 and tokens[by - 1] == ("word", "granted"):
                names.update(_roles_after(tokens, by + 1, after_list))
        if head == "revoke" or ("revoke" in {t[1] for t in tokens if t[0] == "word"}):
            source = _last(tokens, "from")
            if source is not None:
                names.update(_roles_after(tokens, source + 1, after_list))
    return sorted(names)
