# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A refusal made at the door of a route, with its sentence as a code (item 151).

Most refusals are a :class:`~noust.core.exceptions.NoustError` raised by a manager, and the error
handler (:mod:`noust.web.api.deps`) sends their code beside their English. A few are decided where
the request arrives (a sign-in that is wrong, a body that is not what the route takes) and used to
raise ``HTTPException(status, "English")``, which a Spanish console could only show as it came.
:func:`refusal` is the one way to raise one: the sentence is a catalog sentence, its fix is the
sentence's own, and the answer has the shape every other error has (``error``, ``detail``, ``hint``,
``coded``), so a client reads one contract whichever way a refusal was raised.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from noust.core.server_text import ServerText

#: ``error`` for a refusal that names none, by status: the same table every bare ``HTTPException`` is
#: answered with, so that moving a refusal here does not change what a client branches on.
ERROR_BY_STATUS: dict[int, str] = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
}

__all__ = ["ERROR_BY_STATUS", "default_error_for_status", "detail_text", "refusal"]


def default_error_for_status(status_code: int) -> str:
    """
    Args:
        status_code: The HTTP status being answered.

    Returns:
        The ``error`` a refusal with that status has when it names none.
    """
    return ERROR_BY_STATUS.get(
        status_code, "internal" if status_code >= 500 else "validation_error"
    )


def detail_text(exc: HTTPException) -> str:
    """
    Args:
        exc: A refusal, raised by :func:`refusal` or as a bare ``HTTPException``.

    Returns:
        What it says, in English: the sentence of a refusal, or the text a bare one was raised with.
    """
    detail = exc.detail
    if isinstance(detail, Mapping):
        return str(detail.get("detail", ""))
    return str(detail)


def refusal(
    status_code: int,
    sentence: ServerText,
    *,
    error: str | None = None,
    fields: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
) -> HTTPException:
    """
    Build the exception for a refusal whose words are a sentence of the catalog.

    Args:
        status_code: The HTTP status.
        sentence: What is said; its fix, when it has one, is the answer's ``hint``.
        error: The machine code a client branches on, when the status alone does not carry it
            (``invalid_totp`` and ``invalid_token`` are both a 401).
        fields: The request field the refusal is about, to show next to its input.
        headers: Response headers (``Retry-After``, ``WWW-Authenticate``).

    Returns:
        The exception to raise.
    """
    detail: dict[str, Any] = {
        "error": error or default_error_for_status(status_code),
        "detail": sentence.text,
        "hint": sentence.hint,
        "fields": dict(fields) if fields else None,
        "output": None,
        "coded": sentence.to_dict(),
        "notes_coded": [],
    }
    return HTTPException(
        status_code=status_code, detail=detail, headers=dict(headers) if headers else None
    )
