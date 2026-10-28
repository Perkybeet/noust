# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_previews_variables_data.py`` that other tests build on."""

from __future__ import annotations

from typing import Any

from noust.integrations.github.pulls import OpenPullRequest


def open_pull(**changes: Any) -> OpenPullRequest:
    fields: dict[str, Any] = {
        "number": 42,
        "title": "Add search",
        "branch": "feature/search",
        "base_branch": "main",
        "head_sha": "c" * 40,
        "author": "alice",
        "author_association": "MEMBER",
        "draft": False,
        "from_fork": False,
        "url": "https://github.com/acme/shop/pull/42",
    }
    fields.update(changes)
    return OpenPullRequest(**fields)
