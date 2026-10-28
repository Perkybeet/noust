# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Console 2.2, the marks on an environment variable (why one is hidden)."""

from __future__ import annotations

from typing import Any

from .sandbox import Sandbox

# --- 2.2: environment variable marks -----------------------------------------


def seed_env_marks(sandbox: Sandbox, store: Any) -> None:
    """
    Give one application's variables an operator's own secret / not secret marks.

    The Environment tab says why each value is hidden or shown; with only the
    classifier's verdicts it never shows "marked ... by you". The application
    is one whose ``.env`` the suite never replaces, and the variable marked not
    secret is added to that file first, so both marks name a line it holds.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
    """
    from noust.core.utils import domain_to_app_name
    from tests.support.panel_factory import ENV_MARKS_APP, ENV_MARKS_PUBLIC
    from tests.support.panel_factory import seed_env_marks as mark

    env_file = sandbox.apps_dir / domain_to_app_name(ENV_MARKS_APP) / ".env"
    name, value = ENV_MARKS_PUBLIC
    with env_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")
    mark(store, ENV_MARKS_APP)
