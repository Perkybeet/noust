# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether the system's own database engines are something this machine has, in a sentence.

A central that deploys containers and nothing else (the one on a NAS) has the engines it runs in containers
(the ``database_instances`` of the table of functions) and none of the system's: it has no PostgreSQL or MySQL
of its own, and no package manager to install one with. The listings of the area used to answer that with a
refusal, which left the console nothing to draw. They answer with what there is (the instances, their databases
and their users), and this says, with the table's own sentence and code, why there is no engine of the system,
so that nothing offers to install one.
"""

from __future__ import annotations

from typing import Any

from noust import central

__all__ = ["system_engines"]


def system_engines() -> dict[str, Any]:
    """
    Returns:
        What the table of functions says of ``databases`` (the system's own engines) on this Noust and this
        machine: ``{"available", "reason"}``, the reason as ``{code, params, text, hint}`` when it is not
        available.
    """
    return central.feature_states().get("databases") or {"available": True, "reason": None}
