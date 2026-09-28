# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What another platform's configuration says, in WASM's terms.

Each importer reads the files a platform keeps in the repository and answers
with one :class:`Proposal`: the application type, the commands the platform
ran, the port, the health check, the environment, the database it needs, the
domains, and a warning for everything that has no equivalent here. A
proposal changes nothing; the new-app wizard prefills its review with it and
``wasm import --deploy`` hands it to the normal create path through
:func:`wasm.deployers.app_export.proposal_document`.

The platform's commands are carried for the operator to compare, not run:
every WASM deployer builds and starts a project with the commands of its type
(``package.json`` scripts, the Python entry point), which is what makes a
redeploy and a rollback behave the same way as the first deploy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wasm.core.exceptions import ValidationError
from wasm.core.secret_detection import name_looks_secret
from wasm.validators.health import HEALTH_TIMEOUT_MAX, HEALTH_TIMEOUT_MIN

#: Larger than any configuration file a person writes.
MAX_CONFIG_SIZE = 512 * 1024

#: Engines a proposal can say the application needs, as ``wasm db`` names them.
DATABASE_ENGINES = ("postgresql", "mysql", "redis", "mongodb")


@dataclass
class ProposedEnv:
    """
    One environment variable the platform's configuration declares.

    Attributes:
        name: Variable name.
        value: The default the configuration gives it, or None when it gives
            none (a value kept in the platform's dashboard, a generated one,
            one read from a database).
        secret: Whether it holds a credential: generated, or its name says so.
        generated: The platform generates the value (Render's
            ``generateValue``, Heroku's ``generator: secret``); WASM generates
            a random one in its place.
        required: The application needs a value the configuration does not
            give, and nothing generates.
        note: Where the value came from on the platform, when that matters
            (``from database shop-db``).
    """

    name: str
    value: str | None = None
    secret: bool = False
    generated: bool = False
    required: bool = False
    note: str | None = None


@dataclass
class Proposal:
    """
    WASM's reading of another platform's configuration.

    Attributes:
        platform: ``vercel``, ``railway``, ``render`` or ``heroku``.
        files: The configuration files read, relative to the repository.
        app_type: The WASM application type, or None to let detection decide
            (the platform's own builder detected it too).
        install_command: The platform's install command, as written there.
        build_command: The platform's build command.
        start_command: The platform's start command.
        output_directory: Where the platform took the built site from.
        port: The port the application listens on, when the configuration
            fixes one.
        health_path: The path the platform's health check probes.
        health_timeout: Seconds the platform's health check waits.
        env: The environment variables declared, in file order.
        databases: Engines of the databases the application needs.
        domains: Custom domains the configuration names.
        persistent_paths: Paths, relative to the application, that the
            platform kept on a persistent disk.
        warnings: Everything read that WASM has no equivalent for, one
            sentence each, with what to do instead.
    """

    platform: str
    files: list[str] = field(default_factory=list)
    app_type: str | None = None
    install_command: str | None = None
    build_command: str | None = None
    start_command: str | None = None
    output_directory: str | None = None
    port: int | None = None
    health_path: str | None = None
    health_timeout: int | None = None
    env: list[ProposedEnv] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    persistent_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_env(self, variable: ProposedEnv) -> None:
        """
        Add a variable, replacing an earlier one of the same name.

        Args:
            variable: The variable.
        """
        self.env = [existing for existing in self.env if existing.name != variable.name]
        self.env.append(variable)

    def need_database(self, engine: str) -> None:
        """
        Record that the application needs a database of an engine, once.

        Args:
            engine: One of :data:`DATABASE_ENGINES`.
        """
        if engine not in self.databases:
            self.databases.append(engine)

    def warn(self, message: str) -> None:
        """
        Record something without an equivalent, once.

        Args:
            message: What it is and what to do instead.
        """
        if message not in self.warnings:
            self.warnings.append(message)

    def note_commands(self) -> None:
        """
        Say once that the platform's commands are shown, not run.

        Only when the configuration names a command at all: a proposal with
        none has nothing to explain.
        """
        if self.install_command or self.build_command or self.start_command:
            self.warn(
                "WASM installs, builds and starts the project with the commands of its "
                "type (the package.json scripts, the Python entry point); the platform's "
                "commands are shown to compare. Put a custom one in package.json's "
                "scripts so every deploy runs it."
            )


def declared_env(
    name: str, value: str | None, *, generated: bool = False, required: bool | None = None
) -> ProposedEnv:
    """
    Build a variable from what a configuration file says about it.

    Args:
        name: Variable name.
        value: The default given, or None.
        generated: The platform generates it.
        required: Whether a value is needed; None decides it: needed when
            there is no default and nothing generates one.

    Returns:
        The variable. A secret-looking name never keeps a default: a
        configuration file committed to a repository is not where a real
        credential belongs, and a proposal must not spread one further.
    """
    secret = generated or name_looks_secret(name)
    shown = None if secret else value
    needed = (value is None and not generated) if required is None else required
    if secret and value is not None and not generated:
        # The file had a value for a secret: it is withheld, so it must be
        # given again at deploy time.
        needed = True
    return ProposedEnv(name=name, value=shown, secret=secret, generated=generated, required=needed)


def read_text(root: Path, name: str) -> str | None:
    """
    Read one configuration file of a repository, if it is there.

    A link is not followed: a repository is untrusted input, and a link to
    ``/etc/shadow`` named ``vercel.json`` must not be read as root.

    Args:
        root: The repository.
        name: Path relative to it.

    Returns:
        The file's text, or None when it does not exist.

    Raises:
        ValidationError: The file is a link, too large, unreadable, or not
            UTF-8.
    """
    path = root / name
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValidationError(
            f"{name} is not a regular file",
            details="WASM reads configuration files that are committed as files, not links.",
        )
    try:
        size = path.stat().st_size
        if size > MAX_CONFIG_SIZE:
            raise ValidationError(
                f"{name} is too large to be a configuration file ({size} bytes)",
                details=f"The limit is {MAX_CONFIG_SIZE} bytes.",
            )
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{name} is not UTF-8 text", details=str(exc)) from exc
    except OSError as exc:
        raise ValidationError(f"Cannot read {name}", details=str(exc)) from exc


def too_deep(name: str) -> ValidationError:
    """
    Build the refusal for a file nested deeper than a parser follows.

    Python's JSON, YAML and TOML parsers recurse once per level and raise
    ``RecursionError`` past the interpreter's limit; turned into this, it is
    an unreadable file like any other instead of a crash of the inspection.

    Args:
        name: The file, relative to the repository.

    Returns:
        The error.
    """
    return ValidationError(
        f"{name} nests too deeply to be read",
        details="A configuration file nests a few levels; check it is the one the platform reads.",
    )


def read_json_object(root: Path, name: str) -> dict[str, Any] | None:
    """
    Read a JSON configuration file that must hold an object.

    Args:
        root: The repository.
        name: Path relative to it.

    Returns:
        The object, or None when the file does not exist.

    Raises:
        ValidationError: The file cannot be read, is not JSON (or nests too
            deeply to parse), or is not an object.
    """
    text = read_text(root, name)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{name} is not valid JSON", details=str(exc)) from exc
    except RecursionError as exc:
        raise too_deep(name) from exc
    if not isinstance(data, dict):
        raise ValidationError(f"{name} does not hold a JSON object")
    return data


def text_value(data: dict[str, Any], key: str) -> str | None:
    """
    Read a string setting, ignoring one of another type.

    Args:
        data: The mapping.
        key: The setting.

    Returns:
        The stripped string, or None when it is absent, empty or not a string.
    """
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def int_value(data: dict[str, Any], key: str) -> int | None:
    """
    Read a whole-number setting, accepting one written as a string.

    Args:
        data: The mapping.
        key: The setting.

    Returns:
        The number, or None when it is absent or not a whole number.
    """
    value = data.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def health_timeout(seconds: int | None, proposal: Proposal, *, source: str) -> int | None:
    """
    Fit a platform's health-check wait into the range the health gate accepts.

    Args:
        seconds: The platform's wait.
        proposal: Where a warning goes when it had to be changed.
        source: The setting's name on the platform, for the warning.

    Returns:
        The wait, clamped to what :func:`wasm.validators.health.check_health_timeout`
        accepts, or None when there was none.
    """
    if seconds is None:
        return None
    clamped = min(max(seconds, HEALTH_TIMEOUT_MIN), HEALTH_TIMEOUT_MAX)
    if clamped != seconds:
        proposal.warn(
            f"{source} is {seconds} seconds; WASM's health gate waits from "
            f"{HEALTH_TIMEOUT_MIN} to {HEALTH_TIMEOUT_MAX}, so {clamped} is proposed."
        )
    return clamped
