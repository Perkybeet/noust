# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Noust reads of the page a question is asked from, to give to the assistant (item 171).

"Explain this", pressed beside a failed deployment, a diagnosis or an alert, is a question about a
page. Noust, not the model, decides what of that page the model is given: the readings below, made
with the assistant's token like any other (so what a person's team may not see is not read), masked,
bounded and marked as data. The model is not asked to go and find the log: a model that cannot call
tools can still explain what it is shown.

The page is what the console says, judged first (:func:`noust.validators.assistant_page.validate_page`):
a kind from a closed list and the one value each takes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["describe", "reads_for"]


def reads_for(page: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """
    Args:
        page: A page, judged.

    Returns:
        The tools to read it with, in order, with their arguments. A page that is only a place in
        the console (``page``) has nothing to read.
    """
    kind = page.get("kind")
    if kind == "deployment":
        return [
            ("get_deployment", {"deployment_id": page["id"]}),
            ("deployment_log", {"deployment_id": page["id"]}),
        ]
    if kind == "diagnosis":
        return [("diagnose_application", {"domain": page["domain"]})]
    if kind == "alert":
        return [("call_operation", {"method": "GET", "path": "/api/alerts/rules"})]
    return []


def describe(page: Mapping[str, Any]) -> str:
    """
    Args:
        page: A page, judged.

    Returns:
        What the page is, in a few words, for the line that tells the model where the person is.
    """
    kind = page.get("kind")
    server = f" on the server {page['server']}" if page.get("server") else ""
    if kind == "deployment":
        return f"the page of deployment {page['id']}{server}"
    if kind == "diagnosis":
        return f"the diagnosis of {page['domain']}{server}"
    if kind == "alert":
        rule = f" {page['rule']}" if page.get("rule") else "s"
        return f"the alert{rule}{server}"
    return f"the page {page.get('route', '/')} of the console{server}"
