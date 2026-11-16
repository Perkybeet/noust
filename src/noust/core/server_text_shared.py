# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sentences that many places say, for the console to translate (item 151).

A table of :mod:`noust.core.server_text`, which merges it into its catalog: a sentence is its
entry here and a code to ask for it, and the console's own catalogs say it in Spanish. The catalog
grew a table per area so that none passes the size a module may have; this one is for what is said
from more than one area ("Application not found" was typed 114 times), because a sentence that
exists in one place is said the same everywhere (rule 3).
"""

from __future__ import annotations

SENTENCES: dict[str, str] = {
    "apps.not_found": "Application not found: {domain}",
    "apps.not_found.hint": "Run 'noust list' to see what is deployed.",
    "apps.not_found_here": "Application not found: {domain}",
    "apps.not_found_here.hint": (
        "Nothing is deployed at {app_path}. Deploy it with: noust create -d {domain}"
    ),
    "apps.not_found_bare": "Application not found: {domain}",
    "apps.not_found_to_delete": "Application not found: {domain}",
    "apps.not_found_to_delete.hint": "Nothing to delete; check 'noust list' for the exact domain.",
}
