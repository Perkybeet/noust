# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What of the store may leave the machine, and what must stay on it (L-22).

Noust keeps most of its credentials in the secret store (:mod:`noust.core.secrets`),
not in the database: a registry's password, a backup destination's key, a
GitHub App's private key are a file each, 0600, and the database holds only their
names. A few columns do hold a secret value, and two things copy the database
somewhere a person other than root can read it: the evidence package of an
incident (a snapshot of the store, handed to whoever investigates) and the dump
of ``noust store export`` (pasted into a ticket, kept in a backup). This module
says which columns those are and takes them out of a copy, so what is handed over
is the inventory and not the keys.

The list is a decision, so it is kept honest by a test (``tests/test_secrets_at_rest.py``)
that reads the schema of a real store: a column whose name suggests a secret, or
that is one of the few that hold a value under an innocent name, must be here with
a rule or in :data:`NOT_SECRET` with the reason, so a table another area adds
cannot put a credential into a snapshot unnoticed.

Encrypting these columns under a key kept beside them is deliberately not done:
whoever can read the database as root reads the key as root, and the protection
would be a claim. What is protected is the copy.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from noust.core.config import REDACTED, redact_secrets
from noust.core.secret_detection import classify, mask_secret_shapes

#: Set the column to nothing.
BLANK = "blank"
#: A JSON object of variables: the secret ones lose their value, the names stay.
ENVIRONMENT = "environment"
#: A JSON document: a value under a key that names a secret, or that looks like one, is masked.
DOCUMENT = "document"
#: Free text: what looks like a credential in it is masked.
TEXT = "text"
#: A SQL statement: the literals it hands a credential in are masked, and what looks like one.
STATEMENT = "statement"
#: A table of one variable per row: the value is masked when the name or the value says it is secret.
VARIABLE = "variable"

#: ``table -> column -> rule``. Every column of the database that holds a value a person
#: must not receive in a copy of it.
RULES: dict[str, dict[str, str]] = {
    "apps": {"env_vars": ENVIRONMENT, "webhook_secret": BLANK},
    "services": {"environment": ENVIRONMENT},
    "accounts": {
        "password_hash": BLANK,
        "totp_secret": BLANK,
        "totp_pending_secret": BLANK,
        "backup_codes": BLANK,
    },
    "site_auth_users": {"password_hash": BLANK},
    "app_container": {"build_args": ENVIRONMENT},
    "approval_requests": {"parameters": DOCUMENT},
    "cloud_init_scripts": {"content": TEXT},
    "database_query_history": {"statement": STATEMENT},
}

#: A column whose name reads as a secret and is not one, or whose value is kept by another rule.
#: ``(table, column) -> why``. A new table's column that looks like a secret goes in RULES or here.
NOT_SECRET: dict[tuple[str, str], str] = {
    ("account_invitations", "code_hash"): "a hash of a long random code, spent once",
    ("account_passkeys", "credential_id"): "a public identifier",
    ("account_passkeys", "public_key"): "a public key",
    ("accounts", "password_changed_at"): "a time",
    ("app_container", "build_secrets"): "the names of build secrets, never their values",
    ("app_container", "registry_secret"): "the name of a secret in the secret store",
    ("app_env_variables", "expression"): "a reference to another variable, not a value",
    ("apps", "env_secret_marks"): "which variable names are secret, no values",
    ("apps", "ssl_certificate"): "a certificate, public",
    ("apps", "ssl_key"): "the path of the private key, not the key",
    (
        "backup_destinations",
        "settings",
    ): "what is not secret of a destination; its keys are in the secret store",
    ("cloud_accounts", "token_tail"): "the last characters of a token, to tell accounts apart",
    ("custom_certificates", "key_type"): "an algorithm name",
    ("database_accounts", "password_changed_at"): "a time",
    ("database_links", "env_var"): "the name of a variable",
    (
        "db_instance_migrations",
        "source_key",
    ): "an instance's key (postgresql@pg16), not a credential",
    ("deploy_key_repositories", "key_id"): "an identifier",
    ("deploy_keys", "pending_public_key"): "a public key",
    ("deploy_keys", "public_key"): "a public key",
    ("deploy_keys", "pending_key_type"): "an algorithm name",
    ("deploy_keys", "key_type"): "an algorithm name",
    (
        "dns_providers",
        "settings",
    ): "what is not secret of a provider; its token is in the secret store",
    ("gitlab_connections", "secret_name"): "the name of a secret in the secret store",
    ("gitlab_oauth_apps", "secret_name"): "the name of a secret in the secret store",
    ("log_drains", "settings"): "what is not secret of a drain; its token is in the secret store",
    ("node_labels", "key"): "a label's key",
    ("node_links", "link_key"): "the central's public key",
    ("nodes", "host_key"): "a host's public key",
    ("oidc_flows", "binding_hash"): "a hash of a one-time value, short-lived",
    ("oidc_flows", "state_hash"): "a hash of a one-time value, short-lived",
    ("oidc_tickets", "binding_hash"): "a hash of a one-time value, short-lived",
    ("oidc_tickets", "ticket_hash"): "a hash of a one-time value, short-lived",
    ("registry_logins", "secret_name"): "the name of a secret in the secret store",
    (
        "secret_providers",
        "settings",
    ): "an address and options; its credential is in the secret store",
    (
        "scim_sources",
        "token_hash",
    ): "the SHA-256 of a 256-bit random bearer token: it finds the source and cannot be presented",
    ("scim_sources", "token_hint"): "the last characters of a token, to tell tokens apart",
    ("scim_sources", "token_created_at"): "a time",
    ("shared_variables", "secret"): "whether the value is secret, not the value",
    ("sites", "ssl_certificate"): "a certificate, public",
    ("sites", "ssl_key"): "the path of the private key, not the key",
}

#: Tables that keep one variable per row, as ``table -> (name column, value column, secret flag column or None)``.
VARIABLE_TABLES: dict[str, tuple[str, str, str | None]] = {
    "shared_variables": ("name", "value", "secret"),
    "app_env_variables": ("name", "build_value", None),
}

#: A column is reviewed when its name says one of these (case-insensitive substrings).
SUSPECT_WORDS = ("secret", "token", "password", "passwd", "credential", "private", "hash", "key")

#: Columns that hold a value under a name that says nothing, and are ruled above or allowed.
KNOWN_VALUE_COLUMNS = frozenset(
    {
        ("apps", "env_vars"),
        ("services", "environment"),
        ("approval_requests", "parameters"),
        ("cloud_init_scripts", "content"),
        ("database_query_history", "statement"),
        ("app_container", "build_args"),
        ("shared_variables", "value"),
        ("app_env_variables", "build_value"),
        ("app_env_variables", "expression"),
    }
)


#: ``PASSWORD 'x'``, ``IDENTIFIED BY 'x'``, ``SECRET = 'x'``: a quoted literal that a statement hands a
#: credential in, which no ``name=value`` pattern sees.
_SQL_CREDENTIAL = re.compile(
    r"(?i)\b(?P<head>password|passwd|identified\s+by|secret|token|api[_-]?key)(?P<join>\s*=\s*|\s+)"
    r"(?P<quote>['\"])(?P<value>(?:(?!(?P=quote)).)*)(?P=quote)"
)


def mask_statement(text: str) -> str:
    """
    Args:
        text: A SQL statement a person ran.

    Returns:
        The statement with the literals that carry a credential masked, and anything
        else that looks like one.
    """
    masked = _SQL_CREDENTIAL.sub(
        lambda m: (
            f"{m.group('head')}{m.group('join')}{m.group('quote')}{REDACTED}{m.group('quote')}"
        ),
        text,
    )
    return mask_secret_shapes(masked)


def as_mapping(value: Any) -> dict[str, Any]:
    """
    Args:
        value: A column of a record: a mapping, or the JSON text a row keeps one as.

    Returns:
        The mapping, or an empty one when it is neither.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def mask_environment(value: Any, marks: Any = None) -> Any:
    """
    Hide the secret values of an environment, as a copy of a record shows it.

    The one classifier decides what is secret (the operator's marks first, then
    the value, then the name), so a copy hides what the console hides.

    Args:
        value: The environment, as a mapping or as the JSON text a row keeps.
        marks: The operator's secret marks for it, in either form.

    Returns:
        The same kind of value, with each secret value replaced by ``***``;
        anything that is not an environment is returned as it was.
    """
    environment = as_mapping(value)
    if not environment:
        return value
    overrides = {str(k): bool(v) for k, v in as_mapping(marks).items()}
    masked = {
        name: REDACTED
        if isinstance(item, str) and item and classify(str(name), item, overrides).secret
        else item
        for name, item in environment.items()
    }
    return json.dumps(masked) if isinstance(value, str) else masked


def _mask_document(value: Any) -> Any:
    """
    Args:
        value: A JSON document, as the JSON text a row keeps or as the structure.

    Returns:
        The same kind of value: a value under a key that names a secret is
        replaced, and what looks like a credential inside any text is masked.
    """
    if isinstance(value, str):
        try:
            return json.dumps(_mask_document(json.loads(value)))
        except ValueError:
            return mask_secret_shapes(value)
    if isinstance(value, dict):
        redacted = redact_secrets(value)
        return {key: _mask_document(item) for key, item in redacted.items()}
    if isinstance(value, list):
        return [_mask_document(item) for item in value]
    if isinstance(value, str):
        return mask_secret_shapes(value)
    return value


def _apply(rule: str, value: Any) -> Any:
    """
    Args:
        rule: One of the rules of this module.
        value: What the column holds.

    Returns:
        What a copy holds instead.
    """
    if value is None:
        return None
    if rule == BLANK:
        return ""
    if rule == ENVIRONMENT:
        return mask_environment(value)
    if rule == DOCUMENT:
        return _mask_document(value)
    if rule == STATEMENT:
        return mask_statement(str(value))
    return mask_secret_shapes(str(value))


def scrub_database(path: Path) -> dict[str, int]:
    """
    Take the secret values out of a copy of the store, in place.

    Only ever called on a copy: the live store keeps what it needs to run.

    Args:
        path: A SQLite file that is a copy of the store.

    Returns:
        How many rows were changed, by table. A table or column this version of
        the store does not have is skipped, so a snapshot of an older store
        scrubs what it has.
    """
    changed: dict[str, int] = {}
    connection = sqlite3.connect(path)
    try:
        # What an UPDATE replaces would otherwise stay in the page it was in: the copy is
        # rewritten at the end, and the freed space is overwritten as it is freed.
        connection.execute("PRAGMA secure_delete = ON")
        existing = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, columns in RULES.items():
            if table not in existing:
                continue
            have = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for column, rule in columns.items():
                if column not in have:
                    continue
                count = 0
                rows = connection.execute(
                    # Table and column names come from RULES, never from input.
                    f"SELECT rowid, {column}, {_marks_column(table, have)} FROM {table}"  # noqa: S608
                ).fetchall()
                for rowid, value, marks in rows:
                    new = _apply(rule, value)
                    if rule == ENVIRONMENT and marks:
                        new = mask_environment(value, marks)
                    if new != value:
                        connection.execute(
                            f"UPDATE {table} SET {column} = ? WHERE rowid = ?",  # noqa: S608
                            (new, rowid),
                        )
                        count += 1
                if count:
                    changed[table] = changed.get(table, 0) + count
        for table, (name_column, value_column, flag_column) in VARIABLE_TABLES.items():
            if table not in existing:
                continue
            have = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not {name_column, value_column} <= have:
                continue
            flag = f", {flag_column}" if flag_column and flag_column in have else ", 0"
            count = 0
            for rowid, name, value, secret in connection.execute(
                f"SELECT rowid, {name_column}, {value_column}{flag} FROM {table}"  # noqa: S608
            ).fetchall():
                if not isinstance(value, str) or not value:
                    continue
                if secret or classify(str(name), value).secret:
                    connection.execute(
                        f"UPDATE {table} SET {value_column} = ? WHERE rowid = ?",  # noqa: S608
                        (REDACTED, rowid),
                    )
                    count += 1
            if count:
                changed[table] = changed.get(table, 0) + count
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()
    return changed


def _marks_column(table: str, have: set[str]) -> str:
    """
    Args:
        table: A table.
        have: Its columns.

    Returns:
        The column that holds the operator's secret marks, or ``NULL``.
    """
    return "env_secret_marks" if table == "apps" and "env_secret_marks" in have else "NULL"


def columns_to_review(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    """
    Find the columns of a database that nobody has said anything about.

    Args:
        connection: A connection to a store.

    Returns:
        ``(table, column)`` for each column whose name reads as a secret, or that is
        one of the few that hold a value under another name, that is neither ruled
        in :data:`RULES` nor allowed with a reason in :data:`NOT_SECRET`.
    """
    unreviewed: list[tuple[str, str]] = []
    tables = [
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not row[0].startswith("sqlite_")
    ]
    for table in sorted(tables):
        for row in connection.execute(f"PRAGMA table_info({table})"):
            column = row[1]
            suspect = any(word in column.lower() for word in SUSPECT_WORDS)
            if not suspect and (table, column) not in KNOWN_VALUE_COLUMNS:
                continue
            ruled = column in RULES.get(table, {})
            allowed = (table, column) in NOT_SECRET
            # The value of a table of variables is masked row by row, by name.
            variable_value = table in VARIABLE_TABLES and column == VARIABLE_TABLES[table][1]
            if not (ruled or allowed or variable_value):
                unreviewed.append((table, column))
    return unreviewed
