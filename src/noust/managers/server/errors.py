# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ways an action on the server itself is refused, as types.

These are not failures of the tool underneath: apt exiting non-zero is a
:class:`~noust.core.exceptions.NoustError` carrying apt's own words. These are
the answers Noust gives before it touches anything, and each one is a state the
console shows differently: a busy host is a wait, a confirmation is a question,
an unsupported host is an explanation. The API maps each to its own status and
error code (see ``noust.web.api.server.errors``), so the console branches on the
code, never on the sentence.
"""

from __future__ import annotations

from typing import Any

from noust.core.exceptions import NoustError


class ServerError(NoustError):
    """Base class for what the server-management managers refuse or cannot do."""


class HostBusyError(ServerError):
    """
    Another operation holds the host: a package manager lock or a running job.

    Attributes:
        holders: Names of the processes or jobs in the way, for the message.
    """

    def __init__(self, message: str, details: str = "", *, holders: list[str] | None = None):
        """
        Args:
            message: What is busy.
            details: What to do about it.
            holders: The processes or jobs in the way.
        """
        super().__init__(message, details)
        self.holders = holders or []


class ConfirmationRequiredError(ServerError):
    """
    The action would do more than the caller asked for and needs an explicit yes.

    A full upgrade that removes packages, a Docker prune, an ``autoremove``: the
    list of what would go is returned first, and the same request is repeated
    with the confirmation flag once someone has read it.

    Attributes:
        required: What the caller has to acknowledge, machine readable
            (``{"removals": [...]}``), so a console can render the list rather
            than parse the sentence.
    """

    def __init__(self, message: str, details: str = "", *, required: dict[str, Any] | None = None):
        """
        Args:
            message: What needs confirming.
            details: How to confirm it.
            required: The list the caller has to acknowledge.
        """
        super().__init__(message, details)
        self.required = required or {}


class UnsupportedHostError(ServerError):
    """This host cannot do that: no supported package manager, a container, a read-only root."""


class PreflightError(ServerError):
    """
    Checks that run before a change found something that must be settled first.

    Attributes:
        blockers: One sentence per blocker, in the order they were found.
    """

    def __init__(self, message: str, details: str = "", *, blockers: list[str] | None = None):
        """
        Args:
            message: The headline.
            details: What to do.
            blockers: One sentence per blocker.
        """
        super().__init__(message, details)
        self.blockers = blockers or []
