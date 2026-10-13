# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deciding whether a command runs here or on another server, and with what token.

One place answers it for the whole command line, in a fixed order the operator
can reason about:

1. ``--local`` runs here, whatever else is set.
2. The server is ``--remote URL``, else ``$NOUST_REMOTE``, else the context named
   by ``--context``, else ``$NOUST_CONTEXT``, else the context ``noust context
   use`` chose. None of them: the command runs here, as it always did.
3. The token is ``--token`` (``-``, ``@file`` or ``env:NAME``, never a literal),
   else ``--token-file``, else ``--token-env``, else ``$NOUST_TOKEN_FILE``, else
   ``$NOUST_TOKEN``, else what the context says.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.client.api import ApiClient, normalise_url
from noust.client.contexts import (
    ENV_CONTEXT,
    ENV_REMOTE,
    ENV_TOKEN,
    ENV_TOKEN_FILE,
    ContextStore,
    parse_token_source,
    read_token_file,
)
from noust.client.errors import ContextError


@dataclass(frozen=True)
class RemoteSettings:
    """
    What the root command line was given about another server.

    Attributes:
        remote: ``--remote``: the server's address.
        context: ``--context``: a saved connection.
        token: ``--token``: where to read the token (``-``, ``@file``, ``env:NAME``).
        token_file: ``--token-file``.
        token_env: ``--token-env``.
        ca_file: ``--ca-file``.
        local: ``--local``: run here even if a context is in use.
    """

    remote: str | None = None
    context: str | None = None
    token: str | None = None
    token_file: str | None = None
    token_env: str | None = None
    ca_file: str | None = None
    local: bool = False


@dataclass(frozen=True)
class Target:
    """
    The server a command runs on.

    Attributes:
        url: Its address.
        token: The token; held only in memory.
        ca_file: A bundle to trust for it, or None.
        name: The context it came from, or None for an address given directly.
    """

    url: str
    token: str
    ca_file: str | None = None
    name: str | None = None

    def client(self, **kwargs: Any) -> ApiClient:
        """
        Args:
            **kwargs: Passed to :class:`~noust.client.api.ApiClient`.

        Returns:
            A client for the server.
        """
        return ApiClient(self.url, self.token, ca_file=self.ca_file, **kwargs)

    @property
    def label(self) -> str:
        """The server as people name it: its context, else its address."""
        return self.name or self.url


def resolve_target(
    settings: RemoteSettings,
    *,
    store: ContextStore | None = None,
    environ: Mapping[str, str] | None = None,
    stdin: Any = None,
) -> Target | None:
    """
    Work out whether a command is remote, and where.

    Args:
        settings: The root command line's options.
        store: The saved contexts; the user's by default.
        environ: The environment; the process's by default.
        stdin: Where ``--token -`` reads; standard input by default.

    Returns:
        The server and its token, or None to run the command here.

    Raises:
        ContextError: When a context is named that does not exist, or there is
            a server and no token to reach it with.
        RemoteError: When a token is given literally, or an address is refused.
    """
    if settings.local:
        return None
    env = os.environ if environ is None else environ
    contexts = store or ContextStore()
    context_name = settings.context or env.get(ENV_CONTEXT) or None
    address = settings.remote or env.get(ENV_REMOTE) or None
    context = None
    if context_name:
        context = contexts.require(context_name)
    elif address is None:
        context = contexts.current()
    if address is None and context is None:
        return None
    url = normalise_url(address) if address else (context.url if context else "")
    ca_file = settings.ca_file or (context.ca_file if context else None)
    token = _token(settings, env, context, stdin)
    return Target(url=url, token=token, ca_file=ca_file, name=context.name if context else None)


def _token(settings: RemoteSettings, env: Mapping[str, str], context: Any, stdin: Any) -> str:
    """
    Args:
        settings: The root command line's options.
        env: The environment.
        context: The saved connection in play, or None.
        stdin: Where ``--token -`` reads.

    Returns:
        The token, from the first place that has one.

    Raises:
        ContextError: When none has.
    """
    if settings.token:
        import sys

        return parse_token_source(settings.token, stdin=stdin if stdin is not None else sys.stdin)
    if settings.token_file:
        return read_token_file(Path(settings.token_file).expanduser())
    if settings.token_env:
        found = env.get(settings.token_env, "").strip()
        if not found:
            raise ContextError(f"The variable {settings.token_env} is empty or not set")
        return found
    if env.get(ENV_TOKEN_FILE):
        return read_token_file(Path(env[ENV_TOKEN_FILE]).expanduser())
    if env.get(ENV_TOKEN, "").strip():
        return env[ENV_TOKEN].strip()
    if context is not None:
        return ContextStore.token_of(context)
    raise ContextError(
        "There is a server to call and no token to call it with",
        details=(
            "Give --token-file PATH (or --token -, --token env:NAME), set NOUST_TOKEN_FILE, "
            "or save a context with 'noust context add'."
        ),
    )
