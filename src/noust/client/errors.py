# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What goes wrong when a program talks to a Noust server's API.

One error type for the remote command line, the integrations and anything else
that is a client of the API (J-12): it carries the server's own error contract
(``error``, ``detail``, ``hint``, ``output``) untouched, because a system error
is never paraphrased - nginx's or systemd's own words reach the operator
exactly as the server sent them.
"""

from __future__ import annotations

from typing import Any

from noust.core.exceptions import NoustError


class RemoteError(NoustError):
    """
    The server refused a call, or could not be reached.

    Attributes:
        status: The HTTP status, or 0 when no answer arrived.
        code: The server's machine-readable ``error`` code, or ``unreachable``,
            ``bad_response`` or ``refused_locally``.
        body: The decoded error body, when there was one.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        code: str = "error",
        details: str | None = None,
        output: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> None:
        """
        Args:
            message: What happened, in the server's words.
            status: The HTTP status, or 0 for none.
            code: The server's ``error`` code.
            details: How to fix it: the server's ``hint``.
            output: The underlying system's own output, verbatim.
            body: The decoded error body.
        """
        super().__init__(message, details or "", output=output)
        self.status = status
        self.code = code
        self.body = body or {}


class ContextError(NoustError):
    """A saved connection (a context) is missing, malformed or unsafe."""
