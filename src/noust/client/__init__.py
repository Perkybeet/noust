# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust as a client of another Noust server (J-12).

The command line normally runs on the server it manages, as root, and is the
emergency channel: it reaches the managers directly. This package is the other
way to operate a server - from a laptop or a pipeline, with a token and no SSH -
and it goes through the API, so the role the token holds, the team it is bound
to and the audit trail are the server's, not this machine's.

- :mod:`~noust.client.api`: :class:`ApiClient`, an HTTPS client that verifies
  certificates, never follows a redirect and retries only what is safe to repeat.
- :mod:`~noust.client.contexts`: saved connections; the token is a file or a
  variable, never a line of the list.
- :mod:`~noust.client.connect`: the order in which the command line decides
  whether it runs here or there.
"""

from noust.client.api import ApiClient, ApiResponse, normalise_url
from noust.client.connect import RemoteSettings, Target, resolve_target
from noust.client.contexts import Context, ContextStore
from noust.client.errors import ContextError, RemoteError

__all__ = [
    "ApiClient",
    "ApiResponse",
    "Context",
    "ContextError",
    "ContextStore",
    "RemoteError",
    "RemoteSettings",
    "Target",
    "normalise_url",
    "resolve_target",
]
