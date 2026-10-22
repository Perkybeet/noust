# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The roles of a PostgreSQL server, read back out of ``pg_dumpall --globals-only``.

A database dump does not carry the roles it names: they belong to the whole server. Loaded on
a new server, a dump loses its owners (or fails on its first ``ALTER ... OWNER TO``). So every
copy of PostgreSQL that Noust takes can be accompanied by the server's globals, and putting
them back is done by this module's rule and no other: **only what the server lacks is created,
and nothing that exists is touched**. The superuser's password, the roles an operator made on
the new server, and the memberships of the roles that were already there are the new server's.

The file is executed by ``psql`` as the superuser, so it is not trusted as text: it is read
statement by statement (quotes, quoted names and ``''`` escapes understood), only the handful of
statements ``pg_dumpall`` writes for roles are carried over, and a statement that holds a
backslash or a dollar sign outside a quotation (a ``psql`` meta-command, a dollar-quoted body) is
dropped, as is anything else. What cannot be put back yet is listed, not failed on: a
tablespace needs its directory, a setting ``IN DATABASE`` needs the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["GlobalsPlan", "GlobalsRestore", "filter_missing", "role_names"]

#: The session settings ``pg_dumpall`` starts a file with; any other ``SET`` is dropped.
_ALLOWED_SET = (
    re.compile(r"SET default_transaction_read_only = (on|off);"),
    re.compile(r"SET client_encoding = 'UTF8';"),
    re.compile(r"SET standard_conforming_strings = on;"),
)

#: Prepended to what is applied, so a file that tried to turn backslash escapes on cannot.
_PREAMBLE = "SET standard_conforming_strings = on;\n"

#: The words of an ``ALTER ROLE ... WITH`` that ``pg_dumpall`` writes.
_ATTRIBUTES = frozenset(
    {
        "SUPERUSER",
        "NOSUPERUSER",
        "INHERIT",
        "NOINHERIT",
        "CREATEROLE",
        "NOCREATEROLE",
        "CREATEDB",
        "NOCREATEDB",
        "LOGIN",
        "NOLOGIN",
        "REPLICATION",
        "NOREPLICATION",
        "BYPASSRLS",
        "NOBYPASSRLS",
        "PASSWORD",
        "ENCRYPTED",
        "VALID",
        "UNTIL",
        "CONNECTION",
        "LIMIT",
        "NULL",
    }
)


@dataclass(frozen=True)
class _Token:
    """One piece of a statement: a ``word``, a quoted ``ident``, a ``string`` or ``other``."""

    kind: str
    text: str

    @property
    def name(self) -> str:
        """The role or database name this token spells (quotes removed, ``""`` unescaped)."""
        if self.kind == "ident":
            return self.text[1:-1].replace('""', '"')
        return self.text


@dataclass(frozen=True)
class GlobalsPlan:
    """
    What applying a globals file to a given server would do.

    Attributes:
        sql: The statements to run, as one script.
        created: The roles it creates, unquoted, in file order.
        present: The roles the file names that the server already has (left exactly as they are).
        left_out: What was not carried over and why, each a sentence.
    """

    sql: str
    created: tuple[str, ...]
    present: tuple[str, ...]
    left_out: tuple[str, ...]


@dataclass(frozen=True)
class GlobalsRestore:
    """
    What putting the roles of a dump back did.

    Attributes:
        created: The roles that were missing and were created, unquoted.
        present: The roles the file names that the server already had (left as they were).
        left_out: What could not be put back yet and why.
        errors: What the server refused, in its own words.
    """

    created: tuple[str, ...]
    present: tuple[str, ...]
    left_out: tuple[str, ...]
    errors: tuple[str, ...]


def _statements(text: str) -> list[str]:
    """
    Split a script into its statements, honouring quotation.

    Comment lines are skipped. A statement ends at a ``;`` that is not inside single quotes or a
    quoted name. A statement still open at the end of the text (a quote never closed) is dropped:
    what it would swallow is not read.

    Args:
        text: The script.

    Returns:
        Each statement with its closing ``;``.
    """
    found: list[str] = []
    current: list[str] = []
    quote = ""
    for line in text.splitlines():
        if not quote and not current and (line.startswith("--") or not line.strip()):
            continue
        index = 0
        while index < len(line):
            char = line[index]
            if quote:
                current.append(char)
                if char == quote:
                    if line[index + 1 : index + 2] == quote:
                        current.append(quote)
                        index += 1
                    else:
                        quote = ""
            elif char in ("'", '"'):
                quote = char
                current.append(char)
            elif char == ";":
                current.append(char)
                found.append("".join(current).strip())
                current = []
            else:
                current.append(char)
            index += 1
        if quote or "".join(current).strip():
            current.append("\n" if quote else " ")
        else:
            current = []
    return found


def _tokens(statement: str) -> list[_Token] | None:
    """
    Cut a statement into words, quoted names, strings and punctuation.

    Args:
        statement: One statement, with its ``;``.

    Returns:
        The tokens without the final ``;``, or None when it holds a backslash or a dollar sign
        outside a quotation, or does not end in one ``;``.
    """
    if not statement.endswith(";"):
        return None
    body = statement[:-1]
    tokens: list[_Token] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char.isspace():
            index += 1
        elif char in ("'", '"'):
            end = index + 1
            while end < len(body):
                if body[end] == char:
                    if body[end + 1 : end + 2] == char:
                        end += 2
                        continue
                    break
                end += 1
            else:
                return None
            tokens.append(_Token("string" if char == "'" else "ident", body[index : end + 1]))
            index = end + 1
        elif char.isalnum() or char in "_.-":
            end = index
            while end < len(body) and (body[end].isalnum() or body[end] in "_.-"):
                end += 1
            tokens.append(_Token("word", body[index:end]))
            index = end
        elif char in "\\$;":
            return None
        else:
            tokens.append(_Token("other", char))
            index += 1
    return tokens


def role_names(text: str) -> tuple[str, ...]:
    """
    List the roles a globals file creates.

    Args:
        text: The file's text.

    Returns:
        Their names, unquoted, in file order.
    """
    names: list[str] = []
    for statement in _statements(text):
        tokens = _tokens(statement)
        if (
            tokens
            and len(tokens) == 3
            and [t.text.upper() for t in tokens[:2]] == ["CREATE", "ROLE"]
        ):
            names.append(tokens[2].name)
    return tuple(names)


def filter_missing(text: str, *, existing: set[str]) -> GlobalsPlan:
    """
    Keep of a globals file only what a server that has ``existing`` roles needs.

    Args:
        text: The file's text, as ``pg_dumpall --globals-only`` wrote it.
        existing: The roles the server has now.

    Returns:
        The script to run and what it does and leaves out. Nothing is raised for a file that is
        odd: whatever is not a role statement it knows is simply not in the script.
    """
    kept: list[str] = []
    created: list[str] = []
    present: list[str] = []
    left_out: list[str] = []
    new: set[str] = set()
    for statement in _statements(text):
        if any(pattern.fullmatch(statement) for pattern in _ALLOWED_SET):
            kept.append(statement)
            continue
        tokens = _tokens(statement)
        if not tokens:
            continue
        words = [token.text.upper() if token.kind == "word" else "" for token in tokens]
        if words[:2] == ["CREATE", "ROLE"] and len(tokens) == 3:
            name = tokens[2].name
            if name in existing:
                if name not in present:
                    present.append(name)
            else:
                new.add(name)
                created.append(name)
                kept.append(statement)
        elif words[:2] == ["ALTER", "ROLE"] and len(tokens) >= 4:
            name = tokens[2].name
            if name not in new:
                continue
            if words[3] == "WITH" and _only_attributes(tokens[4:]):
                kept.append(statement)
            elif words[3] == "SET":
                kept.append(statement)
            elif words[3:5] == ["IN", "DATABASE"] and len(tokens) > 5:
                left_out.append(
                    f"a setting of {name} for database {tokens[5].name}: set it again once the "
                    "database is restored"
                )
        elif words[:2] == ["COMMENT", "ON"] and words[2] == "ROLE" and len(tokens) == 6:
            if tokens[3].name in new and words[4] == "IS" and tokens[5].kind == "string":
                kept.append(statement)
        elif words[0] == "GRANT" and _is_membership(tokens, words):
            member = tokens[words.index("TO") + 1].name
            if member in new:
                kept.append(statement)
        elif words[0] in ("CREATE", "ALTER") and words[1] == "TABLESPACE":
            left_out.append(
                f"tablespace {tokens[2].name}: it needs its directory, which a new server does not have"
            )
    unique_left_out = tuple(dict.fromkeys(left_out))
    script = _PREAMBLE + "\n".join(kept) + ("\n" if kept else "")
    return GlobalsPlan(script, tuple(created), tuple(present), unique_left_out)


def _only_attributes(tokens: list[_Token]) -> bool:
    """
    Tell whether the rest of an ``ALTER ROLE ... WITH`` holds only what ``pg_dumpall`` writes there.

    Args:
        tokens: What follows ``WITH``.

    Returns:
        True when every word is a role attribute, a number or a keyword of those, and the rest
        are strings.
    """
    for token in tokens:
        if token.kind == "string":
            continue
        if token.kind == "word" and (token.text.upper() in _ATTRIBUTES or token.text.isdigit()):
            continue
        return False
    return True


def _is_membership(tokens: list[_Token], words: list[str]) -> bool:
    """
    Tell whether a ``GRANT`` is a role membership of the shape ``pg_dumpall`` writes.

    Args:
        tokens: The statement's tokens.
        words: Their upper-cased words ("" for what is not a word).

    Returns:
        True for ``GRANT role TO member [WITH ...] [GRANTED BY grantor]``.
    """
    if "TO" not in words or words.index("TO") != 2 or len(tokens) <= 3:
        return False
    for token, word in zip(tokens[3:], words[3:], strict=True):
        if token.kind in ("word", "ident"):
            continue
        if token.text == "," and word == "":
            continue
        return False
    return True
