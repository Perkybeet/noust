# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who is calling: the one answer, in three classes (item 184).

The approval policy (:mod:`noust.core.caller_policy`) is about a caller that is not a person, so the
server has to know which of three it is, and it knows from the credential that authenticated the
request, never from anything the caller says of itself (a header, a client name, a field of a body):

- ``person``: a session of the console or of the command line, the master token (break-glass, held by
  whoever has the machine) and a central acting for one of its operators (the central asked that
  person and vouches for the call, which the node's own guard checks).
- ``token``: an API token, whether CI, a script or a client of MCP.
- ``assistant``: the built-in assistant's own token (item 171). It is told apart by one fact of the
  token's record, ``caller``, which is written when the token is issued and by nothing else; the API
  that issues ordinary tokens cannot ask for it. The record's value reaches the authenticated
  payload as ``payload["caller"]``, which is all this module reads.

Nothing here imports the web layer: the payload is a mapping, and the same function serves the guard of
the API, the MCP, the audit trail and the command line.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: A session, the master token or a central acting for its operator.
PERSON = "person"
#: An API token: CI, a script, a client of MCP.
TOKEN = "token"  # noqa: S105 - a class of caller, not a credential
#: The built-in assistant's own token.
ASSISTANT = "assistant"

#: Every class, in the order the policy table lists them.
CALLERS: tuple[str, ...] = (PERSON, TOKEN, ASSISTANT)

#: The classes the approval policy has a cell for: a person is held to the rules of the roles and of the
#: four-eyes approval, not to this.
POLICED: tuple[str, ...] = (TOKEN, ASSISTANT)

#: The payload key the token's record fills in for the assistant, and the only value that means it.
MARK_KEY = "caller"
ASSISTANT_MARK = "assistant"

_FLEET_SCOPE = "fleet"


def caller_of(payload: Mapping[str, Any] | None) -> str:
    """
    Say which class of caller a request is, from the payload its credential authenticated.

    Args:
        payload: The authenticated payload :func:`noust.web.auth.require_auth` hands to a route; None
            or empty for a request that holds no credential.

    Returns:
        :data:`PERSON`, :data:`TOKEN` or :data:`ASSISTANT`. A request that holds no credential is a
        token: the class that is judged, never the one that is not.
    """
    if not payload:
        return TOKEN
    if payload.get("fleet") or payload.get("scope") == _FLEET_SCOPE:
        return PERSON
    if payload.get("account_id") is not None:
        return PERSON
    if payload.get("type") != "api_token":
        # The master token, in a session or as a bearer: break-glass, which the roles do not govern.
        return PERSON
    return ASSISTANT if payload.get(MARK_KEY) == ASSISTANT_MARK else TOKEN


def is_person(caller: str) -> bool:
    """
    Args:
        caller: A class from :data:`CALLERS`.

    Returns:
        Whether it is a person.
    """
    return caller == PERSON


__all__ = [
    "ASSISTANT",
    "CALLERS",
    "PERSON",
    "POLICED",
    "TOKEN",
    "caller_of",
    "is_person",
]
