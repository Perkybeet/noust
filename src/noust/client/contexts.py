# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Saved connections to other Noust servers: contexts (J-12).

A context is a name, a server's address and *where to find* its token. The
token itself is never in the list of contexts: it is a file the operator owns
(mode 0600, in a directory only they can enter), or the name of an environment
variable, or - from CI - a secret the pipeline already injects. That is the
difference from keeping every server's credential in clear in one JSON file:
read the list and you learn which servers exist, not how to get into them.

Never in an argument either: everything on a command line is visible in ``ps``
to every local user, so ``--token`` takes ``-`` (read standard input),
``@file`` or ``env:NAME``, and refuses a literal value with the way to do it.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from noust.client.api import normalise_url
from noust.client.errors import ContextError, RemoteError
from noust.core.fs import SECRET_MODE, get_fs

#: The variables a pipeline sets instead of a file or a flag.
ENV_REMOTE = "NOUST_REMOTE"
ENV_CONTEXT = "NOUST_CONTEXT"
ENV_TOKEN = "NOUST_TOKEN"  # noqa: S105 - the name of a variable, not a credential
ENV_TOKEN_FILE = "NOUST_TOKEN_FILE"  # noqa: S105

#: A context's name: what is typed after ``--context``.
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
#: A variable's name, for ``token_env``.
VARIABLE_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

#: A token is read from at most this many bytes of a file.
MAX_TOKEN_BYTES = 4096


def config_dir() -> Path:
    """
    Returns:
        Where the contexts live: ``$XDG_CONFIG_HOME/noust``, else ``~/.config/noust``.
    """
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "noust"


@dataclass(frozen=True)
class Context:
    """
    One saved connection.

    Attributes:
        name: What it is called.
        url: The server's address.
        token_file: A file holding the token (mode 0600), or None.
        token_env: The environment variable holding it, or None.
        ca_file: A certificate authority bundle to trust for this server, or None.
    """

    name: str
    url: str
    token_file: str | None = None
    token_env: str | None = None
    ca_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The context as it is shown and stored; it holds no token.
        """
        return asdict(self)


def parse_token_source(value: str, *, stdin: Any) -> str:
    """
    Read a token from where ``--token`` points, never from the command line.

    Args:
        value: ``-`` (standard input), ``@path`` (a file) or ``env:NAME``.
        stdin: The stream standard input is read from.

    Returns:
        The token.

    Raises:
        RemoteError: When ``value`` is itself a token, which would be in ``ps``.
        ContextError: When the source is empty, unreadable or unsafe.
    """
    token: str
    if value == "-":
        token = str(stdin.read(MAX_TOKEN_BYTES)).strip()
    elif value.startswith("@"):
        token = read_token_file(Path(value[1:]).expanduser())
    elif value.startswith("env:"):
        token = os.environ.get(value[4:], "").strip()
        if not token:
            raise ContextError(
                f"The variable {value[4:]} is empty or not set",
                details="Export it before the command: the token stays out of the argument list.",
            )
    else:
        raise RemoteError(
            "A token is not given on the command line",
            code="refused_locally",
            details=(
                "Everything in an argument is visible to every user of this machine. Pass "
                "--token - to read it from standard input, --token @FILE for a file, "
                "--token env:NAME for a variable, or use --token-file / --token-env."
            ),
        )
    if not token:
        raise ContextError("The token is empty", details="Check where --token points.")
    return token


def read_token_file(path: Path) -> str:
    """
    Read a token from a file, refusing one other users can read.

    Args:
        path: The file.

    Returns:
        The token.

    Raises:
        ContextError: When the file is missing, readable by group or others,
            not a regular file, or empty.
    """
    try:
        info = path.stat()
    except OSError as exc:
        raise ContextError(
            f"Cannot read the token file {path}",
            details=f"{exc.strerror or exc}. Make one with 'noust context add NAME --url URL --token-file FILE'.",
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise ContextError(f"{path} is not a regular file")
    if info.st_mode & 0o077:
        raise ContextError(
            f"The token file {path} can be read by other users",
            details=f"Make it private first: chmod 600 {path}",
        )
    with path.open("rb") as handle:
        token = handle.read(MAX_TOKEN_BYTES).decode("utf-8", errors="replace").strip()
    if not token:
        raise ContextError(f"The token file {path} is empty")
    return token


class ContextStore:
    """
    The saved contexts and the one in use.

    Args:
        directory: Where they are kept; the user's configuration by default.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or config_dir()
        self.path = self.directory / "contexts.json"
        self.token_dir = self.directory / "tokens"

    # ------------------------------------------------------------------ reads

    def _load(self) -> dict[str, Any]:
        """
        Returns:
            The file's content, or an empty document when there is none.

        Raises:
            ContextError: When the file is not what this writes.
        """
        if not self.path.exists():
            return {"current": None, "contexts": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContextError(
                f"Cannot read {self.path}", details=f"{exc}. Delete it to start again."
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("contexts", {}), dict):
            raise ContextError(f"{self.path} is not a list of contexts")
        data.setdefault("contexts", {})
        data.setdefault("current", None)
        return data

    def list(self) -> list[Context]:
        """
        Returns:
            Every saved context, by name.
        """
        found = []
        for name, item in sorted(self._load()["contexts"].items()):
            if isinstance(item, Mapping) and item.get("url"):
                found.append(
                    Context(
                        name=name,
                        url=str(item["url"]),
                        token_file=item.get("token_file"),
                        token_env=item.get("token_env"),
                        ca_file=item.get("ca_file"),
                    )
                )
        return found

    def get(self, name: str) -> Context | None:
        """
        Args:
            name: A context's name.

        Returns:
            The context, or None.
        """
        return next((c for c in self.list() if c.name == name), None)

    def require(self, name: str) -> Context:
        """
        Args:
            name: A context's name.

        Returns:
            The context.

        Raises:
            ContextError: When there is none.
        """
        found = self.get(name)
        if found is None:
            known = ", ".join(c.name for c in self.list()) or "none yet"
            raise ContextError(
                f"No context named {name!r}",
                details=f"Saved contexts: {known}. Add one with 'noust context add'.",
            )
        return found

    def current(self) -> Context | None:
        """
        Returns:
            The context in use, or None when none was chosen.
        """
        name = self._load().get("current")
        return self.get(str(name)) if name else None

    # ----------------------------------------------------------------- writes

    def _write(self, data: dict[str, Any]) -> None:
        """
        Write the list atomically, private to the user.

        Args:
            data: The whole document.
        """
        fs = get_fs()
        fs.make_dir(self.directory, mode=0o700)
        fs.chmod(self.directory, 0o700)
        fs.write_text(
            self.path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=SECRET_MODE
        )

    def add(
        self,
        name: str,
        url: str,
        *,
        token: str | None = None,
        token_file: str | None = None,
        token_env: str | None = None,
        ca_file: str | None = None,
        use: bool = False,
    ) -> Context:
        """
        Save a context.

        Args:
            name: What to call it.
            url: The server's address.
            token: A token to keep in a private file of its own (read from
                standard input by the command; never from an argument).
            token_file: A file that already holds the token.
            token_env: A variable that holds it.
            ca_file: A certificate authority bundle to trust for this server.
            use: Also make it the one in use.

        Returns:
            The context.

        Raises:
            ContextError: When the name or the way to the token is not valid.
            RemoteError: When the address is refused.
        """
        if not NAME_PATTERN.fullmatch(name or ""):
            raise ContextError(
                f"Invalid context name: {name!r}",
                details="Use letters, digits, '.', '-' or '_', up to 40, such as 'prod'.",
            )
        sources = [value for value in (token, token_file, token_env) if value]
        if len(sources) != 1:
            raise ContextError(
                "Give exactly one way to find the token",
                details="--token - (standard input), --token-file PATH or --token-env VARIABLE.",
            )
        if token_env and not VARIABLE_PATTERN.fullmatch(token_env):
            raise ContextError(f"Invalid variable name: {token_env!r}")
        address = normalise_url(url)
        if token:
            token_file = str(self._keep_token(name, token))
        elif token_file:
            token_file = str(Path(token_file).expanduser().resolve())
            read_token_file(Path(token_file))
        data = self._load()
        data["contexts"][name] = {
            "url": address,
            "token_file": token_file,
            "token_env": token_env,
            "ca_file": str(Path(ca_file).expanduser().resolve()) if ca_file else None,
        }
        if use or not data.get("current"):
            data["current"] = name
        self._write(data)
        return self.require(name)

    def _keep_token(self, name: str, token: str) -> Path:
        """
        Args:
            name: The context it is for.
            token: The token.

        Returns:
            The private file that now holds it.
        """
        fs = get_fs()
        fs.make_dir(self.token_dir, mode=0o700)
        fs.chmod(self.token_dir, 0o700)
        path = self.token_dir / name
        fs.write_text(path, token.strip() + "\n", mode=SECRET_MODE)
        return path

    def use(self, name: str) -> Context:
        """
        Args:
            name: The context to use from now on.

        Returns:
            It.

        Raises:
            ContextError: When there is none.
        """
        found = self.require(name)
        data = self._load()
        data["current"] = found.name
        self._write(data)
        return found

    def remove(self, name: str) -> Context:
        """
        Forget a context, and the token file this store kept for it.

        Args:
            name: The context.

        Returns:
            It, as it was.

        Raises:
            ContextError: When there is none.
        """
        found = self.require(name)
        data = self._load()
        data["contexts"].pop(found.name, None)
        if data.get("current") == found.name:
            data["current"] = None
        self._write(data)
        kept = self.token_dir / found.name
        if found.token_file and Path(found.token_file) == kept and kept.exists():
            get_fs().remove(kept)
        return found

    # ------------------------------------------------------------------ token

    @staticmethod
    def token_of(context: Context) -> str:
        """
        Args:
            context: A saved connection.

        Returns:
            Its token, read now from the file or the variable it names.

        Raises:
            ContextError: When it cannot be found or the file is unsafe.
        """
        if context.token_env:
            token = os.environ.get(context.token_env, "").strip()
            if not token:
                raise ContextError(
                    f"The variable {context.token_env} is empty or not set",
                    details=f"The context {context.name!r} reads its token from it.",
                )
            return token
        if context.token_file:
            return read_token_file(Path(context.token_file))
        raise ContextError(f"The context {context.name!r} says where its token is no more")
