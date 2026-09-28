# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A migration leaves a PHP application's settings at the application root.

``.wasm-php.json`` belongs to the application directory, whatever the layout:
moved into the first release, it is no longer where
:func:`~wasm.deployers.php_fpm.load_php_settings` reads it, which then answers
the defaults without a word, losing the web root and the refused paths.
"""

# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_migrate import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    machine,
    root,
    store,
)
from wasm.core.exceptions import ValidationError
from wasm.core.store import WASMStore
from wasm.deployers.migrate import migrate, plan_migration
from wasm.deployers.php_fpm import PHP_SETTINGS_FILE


def test_the_php_settings_stay_at_the_root(root: Path, store: WASMStore, machine: Any) -> None:
    """Not moved into the release nor to shared/, and not a persistent path."""
    (root / PHP_SETTINGS_FILE).write_text('{"webroot": "public"}\n')
    prefix = ["git", "-c", f"safe.directory={root}", "--no-optional-locks"]
    machine.runner.script(
        [*prefix, "status"],
        stdout="\0".join(
            ["?? uploads/", "!! node_modules/", "!! .env", f"?? {PHP_SETTINGS_FILE}", ""]
        ),
    )

    plan = plan_migration(DOMAIN)
    migrate(DOMAIN, plan)

    assert PHP_SETTINGS_FILE not in plan.persistent
    assert PHP_SETTINGS_FILE not in plan.untracked_files
    assert (root / PHP_SETTINGS_FILE).read_text() == '{"webroot": "public"}\n'
    release = next((root / "releases").iterdir())
    assert not (release / PHP_SETTINGS_FILE).exists()


def test_naming_it_with_persist_is_refused(root: Path, machine: Any) -> None:
    """It stays at the root; shared/ is for what the application writes."""
    with pytest.raises(ValidationError, match="does not need --persist"):
        plan_migration(DOMAIN, persist=[PHP_SETTINGS_FILE])
