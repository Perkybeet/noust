# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What can go wrong when calling Noust, and what the server said about it."""

from __future__ import annotations

from typing import Any


class NoustError(Exception):
    """Anything this library raises on purpose."""


class ConnectionFailed(NoustError):
    """The server could not be reached, or answered something that is not the API."""


class APIError(NoustError):
    """
    The server refused a call, in the API's one error contract.

    Attributes:
        status: The HTTP status.
        code: The server's machine-readable ``error``.
        detail: What happened, in the server's words.
        hint: How to fix it, when the server said.
        output: The output of the system tool behind the failure, verbatim (nginx's, systemd's).
        body: The whole decoded answer.
        retry_after: Seconds the server asked to wait, for a ``429``.
    """

    def __init__(
        self,
        status: int,
        body: Any,
        *,
        retry_after: float | None = None,
    ) -> None:
        contract = body if isinstance(body, dict) else {}
        detail = contract.get("detail")
        if isinstance(detail, dict):
            # An error raised as an HTTP exception carries the contract inside ``detail``.
            contract, detail = detail, detail.get("detail")
        self.status = status
        self.body = body
        self.code: str = str(contract.get("error") or "error")
        self.detail: str = str(detail if detail is not None else body or f"HTTP {status}")
        self.hint: str | None = contract.get("hint") or None
        self.output: str | None = contract.get("output") or None
        self.retry_after = retry_after
        super().__init__(self.detail if not self.hint else f"{self.detail} ({self.hint})")


class ElevationRequired(APIError):
    """The call needs sudo mode, which this token may not enter (or has not confirmed)."""


class RateLimited(APIError):
    """The server's budget for this credential is spent: ``retry_after`` says for how long."""


class JobFailed(NoustError):
    """
    A job the server ran ended badly.

    Attributes:
        job: The job as the server last described it, with its log and its error.
    """

    def __init__(self, job: dict[str, Any]) -> None:
        self.job = job
        state = job.get("state") or job.get("status")
        super().__init__(f"The job {job.get('id')} {state}: {job.get('error') or 'see its log'}")
