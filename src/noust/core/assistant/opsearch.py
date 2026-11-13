# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Finding the operation of the API that does what a person wants (item 171).

The API has a thousand operations and more: they do not fit in a request, and most of them are not
for the assistant. It is given three tools instead of a thousand: this search over the API's own
description (``find_operation``), a tool that prepares the exact call of an operation that was found
(``call_operation``), and the short list of tools it shares with the MCP endpoint. The search is a
ranking over each operation's summary, description, tags and the words of its path; what the
assistant is not offered (its floor, what is a person's own) is not found.

The class of an operation (a read, a change, one that asks for sudo mode or a second person) is the
one :mod:`noust.core.operation_classes` computes from what the API publishes, so a result says
what will happen to a call before it is made.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.assistant.bm25 import Index
from noust.core.operation_classes import (
    HTTP_METHODS,
    OperationClass,
    Unclassified,
    classify,
)
from noust.core.plans.registry import PLAN_EXTENSION

__all__ = ["OperationHit", "OperationIndex", "plan_of"]


_MAX_DESCRIPTION = 400


@dataclass(frozen=True)
class OperationHit:
    """
    An operation of the API.

    Attributes:
        method: The HTTP method.
        path: The route's template.
        summary: What the API says it does, in a line.
        permission: What it needs.
        kind: Its class (``read``, ``action`` or ``strong``).
        elevation: It asks for sudo mode.
        approval: When a second person is asked (``always`` or the condition), or None.
        plan: The operation that shows what it would do before it does it, when it has one.
    """

    method: str
    path: str
    summary: str
    permission: str
    kind: str
    elevation: bool
    approval: str | None
    plan: str | None

    @property
    def strong(self) -> bool:
        """Whether a person has to carry it out in their own session."""
        return self.kind == "strong"

    @property
    def changes(self) -> bool:
        """Whether it changes something."""
        return self.kind in ("action", "strong")


def plan_of(operation: Mapping[str, Any]) -> str | None:
    """
    Args:
        operation: An operation of the OpenAPI document.

    Returns:
        What it publishes as its plan (``x-noust-plan``), as ``METHOD /path``; None when it has
        none. A bare path means a ``GET``.
    """
    declared = operation.get(PLAN_EXTENSION)
    if isinstance(declared, Mapping):
        method, path = declared.get("method", "GET"), declared.get("path")
        return f"{str(method).upper()} {path}" if isinstance(path, str) else None
    if isinstance(declared, str) and declared:
        return declared if " " in declared else f"GET {declared}"
    return None


class OperationIndex:
    """The operations of an OpenAPI document, ranked against what a person wants to do."""

    def __init__(
        self,
        openapi: Mapping[str, Any],
        classes: Mapping[tuple[str, str], OperationClass] | None = None,
    ) -> None:
        """
        Args:
            openapi: The application's OpenAPI document.
            classes: The class of each operation (``(METHOD, path)`` to it); computed from the
                document when None.
        """
        self._hits: dict[tuple[str, str], OperationHit] = {}
        self._classes: dict[tuple[str, str], OperationClass] = {}
        documents: list[tuple[tuple[str, str], str]] = []
        for path, item in (openapi.get("paths") or {}).items():
            for method, operation in item.items():
                verb = method.upper()
                if verb not in HTTP_METHODS or not isinstance(operation, Mapping):
                    continue
                found = (classes or {}).get((verb, path))
                if found is None:
                    try:
                        found = classify(verb, path, operation)
                    except Unclassified:
                        continue
                key = (verb, path)
                self._classes[key] = found
                summary = str(operation.get("summary") or "")
                self._hits[key] = OperationHit(
                    verb,
                    path,
                    summary,
                    found.permission,
                    found.kind,
                    found.elevation,
                    found.approval,
                    plan_of(operation),
                )
                description = str(operation.get("description") or "")[:_MAX_DESCRIPTION]
                tags = " ".join(str(tag) for tag in operation.get("tags") or ())
                words = f"{summary} {summary} {path} {path} {operation.get('operationId', '')} {tags} {description}"
                documents.append((key, f"{verb} {words}"))
        self._index: Index[tuple[str, str]] = Index(documents)

    def class_of(self, method: str, path: str) -> OperationClass | None:
        """
        Args:
            method: The HTTP method.
            path: The route's template.

        Returns:
            Its class, or None when the document has no such operation.
        """
        return self._classes.get((method.upper(), path))

    def get(self, method: str, path: str) -> OperationHit | None:
        """
        Args:
            method: The HTTP method.
            path: The route's template.

        Returns:
            The operation, or None when the document has none (or the class cannot be told).
        """
        return self._hits.get((method.upper(), path))

    def search(
        self,
        question: str,
        limit: int = 8,
        *,
        allow: Callable[[OperationClass], bool] | None = None,
    ) -> list[OperationHit]:
        """
        Args:
            question: What the person wants to do, in words.
            limit: The most to return.
            allow: Says whether an operation may be found at all (what the asker is offered).

        Returns:
            The operations that fit best and may be offered, the best first.
        """

        def offered(key: tuple[str, str]) -> bool:
            return allow is None or allow(self._classes[key])

        return [
            self._hits[key] for key, _score in self._index.search(question, limit, allow=offered)
        ]
