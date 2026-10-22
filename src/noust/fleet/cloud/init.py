# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The cloud-init library, and the document a new server boots with.

A server a central creates starts from one ``#cloud-config`` document that has two
kinds of content:

- **Noust's own part**, which the operator does not edit: the SSH host key the
  central generated (so the central knows the key before the machine exists and
  pins it, instead of trusting whatever answers first), password logins off, and
  root reachable by the provider's keys only. It carries *no fleet token*: a
  provider's metadata service hands user-data to every local user, so a token in
  it would be a token for every process on the server.
- **The operator's additions**, picked by name from a library: a ``#cloud-config``
  fragment (packages, files, commands) or a shell script. A script is written under
  ``/var/lib/noust/cloud-init/`` and run once at first boot, after the fragments.

The library is validated when it is saved and again when a server is created,
because it is untrusted input to a machine that will run it as root: a fragment
may not set the keys Noust's part owns (an ``ssh_keys`` of its own would replace
the pinned host key), a script must say what interpreter runs it, and neither
may carry what the secret detector recognises as a credential: whatever is in
here can be read back from the server by any local user.

Instead of one document with MIME parts the additions are merged into the one
``#cloud-config`` (lists are joined, mappings merged), so the result is a single,
inspectable document that :func:`preview` shows with the host key withheld.
"""

from __future__ import annotations

import builtins
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import yaml

from noust.core import audit
from noust.core.exceptions import NodeError, ValidationError
from noust.core.secret_detection import classify
from noust.core.store import NoustStore, get_store
from noust.fleet.cloud.models import validate_script_name

#: The two kinds of entry.
KIND_CLOUD_CONFIG = "cloud-config"
KIND_SCRIPT = "script"
KINDS = (KIND_CLOUD_CONFIG, KIND_SCRIPT)

#: Largest single entry, in bytes. Providers take 32 KiB of user-data in all.
MAX_ENTRY_BYTES = 12 * 1024

#: The first line every cloud-config document has.
CLOUD_CONFIG_HEADER = "#cloud-config"

#: Where scripts are written on the server.
SCRIPT_DIRECTORY = "/var/lib/noust/cloud-init"

#: Top-level keys that belong to Noust's part of the document.
OWNED_KEYS = frozenset(
    {
        "ssh_keys",
        "ssh_deletekeys",
        "ssh_genkeytypes",
        "ssh_authorized_keys",
        "ssh_pwauth",
        "ssh_publish_hostkeys",
        "ssh_quiet_keygen",
        "disable_root",
        "disable_root_opts",
        "chpasswd",
        "password",
    }
)

#: Keys of a ``users`` entry that put a password in user-data.
_PASSWORD_FIELDS = frozenset({"passwd", "plain_text_passwd", "hashed_passwd"})

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.+)$")
_EXPANSION = re.compile(r"^[\"']?\$")


@dataclass(frozen=True)
class LibraryEntry:
    """
    One script or fragment of the library.

    Attributes:
        name: Its name.
        kind: :data:`KIND_CLOUD_CONFIG` or :data:`KIND_SCRIPT`.
        description: What it does, in a line.
        content: The document or script.
        builtin: Shipped with Noust: it cannot be changed or deleted.
        created_at: When it was saved, ISO 8601 UTC.
        created_by: Who saved it.
        updated_at: When it last changed.
    """

    name: str
    kind: str
    description: str
    content: str
    builtin: bool = False
    created_at: str | None = None
    created_by: str | None = None
    updated_at: str | None = None

    def to_dict(self, *, content: bool = True) -> dict[str, Any]:
        """
        Args:
            content: Include the text. A listing leaves it out.

        Returns:
            The entry, JSON-ready.
        """
        data: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "builtin": self.builtin,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "bytes": len(self.content.encode("utf-8")),
        }
        if content:
            data["content"] = self.content
        return data


#: What Noust ships, as starting points the operator can read and copy.
BUILTIN_ENTRIES: tuple[LibraryEntry, ...] = (
    LibraryEntry(
        name="swap-2g",
        kind=KIND_SCRIPT,
        description="Create a 2 GB swap file when the server has no swap (small plans run out of memory in builds).",
        content=(
            "#!/bin/sh\n"
            "set -eu\n"
            'if [ "$(swapon --show --noheadings | wc -l)" -gt 0 ]; then exit 0; fi\n'
            "fallocate -l 2G /swapfile\n"
            "chmod 600 /swapfile\n"
            "mkswap /swapfile\n"
            "swapon /swapfile\n"
            "echo '/swapfile none swap sw 0 0' >> /etc/fstab\n"
        ),
        builtin=True,
    ),
    LibraryEntry(
        name="base-packages",
        kind=KIND_CLOUD_CONFIG,
        description="Install the tools an operator reaches for first: curl, git, htop, unzip.",
        content=(
            "#cloud-config\n"
            "package_update: true\n"
            "packages:\n"
            "  - curl\n"
            "  - git\n"
            "  - htop\n"
            "  - unzip\n"
        ),
        builtin=True,
    ),
    LibraryEntry(
        name="unattended-security-updates",
        kind=KIND_CLOUD_CONFIG,
        description="Debian and Ubuntu: install security updates by themselves. Never reboots.",
        content=(
            "#cloud-config\n"
            "package_update: true\n"
            "packages:\n"
            "  - unattended-upgrades\n"
            "write_files:\n"
            "  - path: /etc/apt/apt.conf.d/20auto-upgrades\n"
            "    permissions: '0644'\n"
            "    content: |\n"
            '      APT::Periodic::Update-Package-Lists "1";\n'
            '      APT::Periodic::Unattended-Upgrade "1";\n'
        ),
        builtin=True,
    ),
)


def _now() -> str:
    """Returns: The current time, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- validation


def _credential_in(text: str) -> str | None:
    """
    Find something in a script or fragment that looks like a credential.

    Args:
        text: The content.

    Returns:
        What it looks like (``value: stripe``, ``name``), or None.
    """
    # The shapes of vendors' tokens, private key blocks and URLs with a password
    # are looked for in the whole text; a random-looking string alone is not
    # enough here, since a checksum or an identifier in a script is not a secret.
    whole = classify("CONTENT", text)
    if whole.secret and whole.reason not in ("value: high entropy", "name"):
        return whole.reason
    for line in text.splitlines():
        match = _ASSIGNMENT.fullmatch(line)
        if match and not _EXPANSION.match(match.group(2).strip()):
            verdict = classify(match.group(1), match.group(2).strip().strip("'\""))
            if verdict.secret and verdict.reason != "value: high entropy":
                return verdict.reason
    return None


def validate_entry(kind: str, content: str) -> str:
    """
    Check a library entry and return it in the form it is kept.

    Args:
        kind: :data:`KIND_CLOUD_CONFIG` or :data:`KIND_SCRIPT`.
        content: The document or script.

    Returns:
        The content with line endings normalised.

    Raises:
        ValidationError: When it is not valid for its kind, is too large, sets what
            Noust's part owns or carries a credential.
    """
    if kind not in KINDS:
        raise ValidationError(
            f"Unknown kind: {kind!r}", details=f"Use one of: {', '.join(KINDS)}.", field="kind"
        )
    if not isinstance(content, str) or not content.strip():
        raise ValidationError("A script cannot be empty", field="content")
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise ValidationError("A script cannot hold NUL characters", field="content")
    if len(text.encode("utf-8")) > MAX_ENTRY_BYTES:
        raise ValidationError(
            f"A script can be at most {MAX_ENTRY_BYTES} bytes",
            details="The provider accepts 32 KiB of user-data in all, and Noust's own part "
            "needs some of it. Fetch what is large from a place the server can reach.",
            field="content",
        )
    if kind == KIND_SCRIPT:
        if not text.startswith("#!"):
            raise ValidationError(
                "A script must start with the interpreter that runs it",
                details="Begin it with a line such as '#!/bin/sh' or '#!/usr/bin/env bash'.",
                field="content",
            )
    else:
        _fragment(text)
    found = _credential_in(text)
    if found is not None:
        raise ValidationError(
            f"This looks like it holds a credential ({found})",
            details="Whatever is in a cloud-init document can be read from the provider's "
            "metadata service by every user on the server, not only root. Fetch the secret "
            "after the server is up instead.",
            field="content",
        )
    return text if text.endswith("\n") else text + "\n"


def _fragment(text: str) -> dict[str, Any]:
    """
    Parse and check a cloud-config fragment.

    Args:
        text: The fragment.

    Returns:
        The mapping.

    Raises:
        ValidationError: When it is not a ``#cloud-config`` mapping, or sets a key
            Noust's part owns, or a user with a password or the root user.
    """
    if not text.startswith(CLOUD_CONFIG_HEADER):
        raise ValidationError(
            "A cloud-config fragment must start with #cloud-config",
            details="Its first line is exactly '#cloud-config'.",
            field="content",
        )
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError(
            "That is not valid YAML",
            details=str(exc).splitlines()[0] if str(exc) else "",
            field="content",
        ) from exc
    if document is None:
        return {}
    if not isinstance(document, dict) or not all(isinstance(key, str) for key in document):
        raise ValidationError("A cloud-config fragment is a mapping of settings", field="content")
    owned = sorted(OWNED_KEYS & set(document))
    if owned:
        raise ValidationError(
            f"A fragment cannot set {', '.join(owned)}",
            details="Noust sets the server's SSH host key and who can log in, so that the central "
            "can pin the host key before the machine exists. A key of your own here would "
            "replace it.",
            field="content",
        )
    users = document.get("users")
    for user in users if isinstance(users, list) else []:
        if isinstance(user, dict):
            if user.get("name") == "root":
                raise ValidationError(
                    "A fragment cannot configure the root user",
                    details="Noust decides who may log in as root.",
                    field="content",
                )
            if _PASSWORD_FIELDS & set(user):
                raise ValidationError(
                    "A user's password cannot be in user-data",
                    details="Anyone on the server can read it. Use ssh_authorized_keys.",
                    field="content",
                )
    return document


# ---------------------------------------------------------------- rendering


class _Dumper(yaml.SafeDumper):
    """A YAML dumper that writes multi-line text as a block, so a document reads."""


def _text(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    """Represent multi-line strings as literal blocks."""
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _text)

#: What the preview shows in place of the host key.
WITHHELD = "<generated for this server when it is created; never shown>"


def bootstrap_document(host_private: str | None, host_public: str | None) -> dict[str, Any]:
    """
    Build Noust's own part of the document.

    Args:
        host_private: The server's ed25519 host key, as OpenSSH text; None for a preview.
        host_public: Its public half; None for a preview.

    Returns:
        The settings every server Noust creates boots with.
    """
    return {
        "ssh_keys": {
            "ed25519_private": host_private or WITHHELD,
            "ed25519_public": host_public or WITHHELD,
        },
        "ssh_deletekeys": True,
        "ssh_pwauth": False,
        "disable_root": False,
    }


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """
    Merge a fragment into the document: lists are joined, mappings merged.

    Args:
        base: The document so far.
        extra: The fragment.

    Returns:
        ``base``, changed.
    """
    for key, value in extra.items():
        current = base.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _merge(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            current.extend(value)
        else:
            base[key] = value
    return base


def render_user_data(
    host_private: str | None, host_public: str | None, entries: list[LibraryEntry]
) -> str:
    """
    Build the document a new server boots with.

    Args:
        host_private: The server's host key, OpenSSH text; None for a preview.
        host_public: Its public half; None for a preview.
        entries: The library entries to add, in order: fragments are merged in that
            order, then every script runs in that order.

    Returns:
        The ``#cloud-config`` document.

    Raises:
        ValidationError: When an entry is not valid (the library is checked again here).
    """
    document = bootstrap_document(host_private, host_public)
    scripts: list[tuple[int, LibraryEntry]] = []
    for position, entry in enumerate(entries, start=1):
        text = validate_entry(entry.kind, entry.content)
        if entry.kind == KIND_CLOUD_CONFIG:
            _merge(document, _fragment(text))
        else:
            scripts.append((position, entry))
    for position, entry in scripts:
        path = f"{SCRIPT_DIRECTORY}/{position:02d}-{entry.name}.sh"
        _merge(
            document,
            {
                "write_files": [
                    {
                        "path": path,
                        "permissions": "0700",
                        "owner": "root:root",
                        "content": validate_entry(KIND_SCRIPT, entry.content),
                    }
                ],
                "runcmd": [[path]],
            },
        )
    body = yaml.dump(
        document, Dumper=_Dumper, sort_keys=False, default_flow_style=False, width=10_000
    )
    return f"{CLOUD_CONFIG_HEADER}\n{body}"


def preview(entries: list[LibraryEntry]) -> str:
    """
    Show what a server would boot with, the host key withheld.

    Args:
        entries: The library entries that would be added.

    Returns:
        The document.
    """
    return render_user_data(None, None, entries)


# ---------------------------------------------------------------- the library


class CloudInitLibrary:
    """
    The scripts and fragments an operator keeps for new servers.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def list(self) -> list[LibraryEntry]:
        """
        Returns:
            The built-in entries, then the operator's by name.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM cloud_init_scripts ORDER BY name")
            .fetchall()
        )
        return [*BUILTIN_ENTRIES, *(_entry(row) for row in rows)]

    def get(self, name: str) -> LibraryEntry:
        """
        Read one entry.

        Args:
            name: Its name.

        Returns:
            The entry.

        Raises:
            NodeError: When there is none by that name.
        """
        for entry in BUILTIN_ENTRIES:
            if entry.name == name:
                return entry
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM cloud_init_scripts WHERE name = ?", (name,))
            .fetchone()
        )
        if row is None:
            raise NodeError(
                f"No cloud-init script named {name}",
                details="List them with 'noust fleet cloud script list'.",
                field="scripts",
            )
        return _entry(row)

    def resolve(self, names: builtins.list[str]) -> builtins.list[LibraryEntry]:
        """
        Find the entries a server is to boot with.

        Args:
            names: Their names, in the order to apply them.

        Returns:
            The entries.

        Raises:
            NodeError: When a name is unknown.
            ValidationError: When a name is repeated.
        """
        if len(set(names)) != len(names):
            raise ValidationError("A script is listed twice", field="scripts")
        return [self.get(name) for name in names]

    def save(
        self,
        name: str,
        kind: str,
        content: str,
        *,
        description: str = "",
        actor: str | None = None,
    ) -> LibraryEntry:
        """
        Add an entry, or replace the operator's own of the same name.

        Args:
            name: Its name.
            kind: :data:`KIND_CLOUD_CONFIG` or :data:`KIND_SCRIPT`.
            content: The document or script.
            description: What it does, in a line.
            actor: Who saves it.

        Returns:
            The entry.

        Raises:
            ValidationError: When the name or the content is not valid, or the name
                is a built-in's.
        """
        validate_script_name(name)
        if any(entry.name == name for entry in BUILTIN_ENTRIES):
            raise ValidationError(
                f"{name} is a script that ships with Noust and cannot be changed",
                details="Save a copy under another name.",
                field="name",
            )
        text = validate_entry(kind, content)
        note = (description or "").strip()
        if len(note) > 200 or "\n" in note:
            raise ValidationError(
                "A description is one line of at most 200 characters", field="description"
            )
        now = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO cloud_init_scripts (name, kind, description, content, created_at, "
                "created_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET kind = excluded.kind, "
                "description = excluded.description, content = excluded.content, "
                "updated_at = excluded.updated_at",
                (name, kind, note, text, now, actor, now),
            )
        audit.record(
            "fleet.cloud.script.save",
            target=f"cloud-script:{name}",
            details={"kind": kind, "bytes": len(text.encode("utf-8")), "by": actor},
        )
        return self.get(name)

    def delete(self, name: str, *, actor: str | None = None) -> None:
        """
        Delete the operator's entry.

        Args:
            name: Its name.
            actor: Who deletes it.

        Raises:
            ValidationError: When it is one that ships with Noust.
            NodeError: When there is none by that name.
        """
        if any(entry.name == name for entry in BUILTIN_ENTRIES):
            raise ValidationError(f"{name} ships with Noust and cannot be deleted", field="name")
        self.get(name)
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM cloud_init_scripts WHERE name = ?", (name,))
        audit.record(
            "fleet.cloud.script.delete", target=f"cloud-script:{name}", details={"by": actor}
        )


def _entry(row: Any) -> LibraryEntry:
    """
    Build an entry out of its row.

    Args:
        row: A ``cloud_init_scripts`` row.

    Returns:
        The entry.
    """
    return LibraryEntry(
        name=str(row["name"]),
        kind=str(row["kind"]),
        description=str(row["description"] or ""),
        content=str(row["content"]),
        created_at=row["created_at"],
        created_by=row["created_by"],
        updated_at=row["updated_at"],
    )
