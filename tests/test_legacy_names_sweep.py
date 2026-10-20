# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The names from before 4.0 are spelled in one module, so there is one list to read.
"""

from __future__ import annotations

import re
from pathlib import Path

from noust.core import legacy_names

SRC = Path(legacy_names.__file__).resolve().parents[1]

#: Modules allowed to spell the old name, besides ``legacy_names`` itself: the reader of
#: backups and exports from before the rename.
ALLOWED_SPELLINGS = {
    "core/legacy_names.py",
    "core/legacy_archive.py",
    # Removed by the other lane of wave 5 (a 2.x server's migration), which owns it.
    "core/migrate_from_wasm.py",
}


def test_no_other_module_spells_a_name_from_before_the_rename() -> None:
    """
    The old spelling lives here and in the archive reader, so there is one list to read.

    The sweep is narrow on purpose: it looks for the names this module owns, not for the
    word, so a comment or a message that talks about the history does not fail it.
    """
    owned = [
        legacy_names.SESSION_COOKIE,
        legacy_names.CSRF_COOKIE,
        legacy_names.CSRF_HEADER,
        legacy_names.WS_SUBPROTOCOL,
        legacy_names.WS_TOKEN_PREFIX,
        legacy_names.API_TOKEN_PREFIX,
        legacy_names.ONLY_PARAMETER,
        legacy_names.READ_ONLY_PREFIX,
        legacy_names.UPSTREAM_PREFIX,
        legacy_names.TREE_RUNTIME_STAMP,
        legacy_names.TREE_PHP_SETTINGS,
        legacy_names.TREE_PHP_TMP,
        legacy_names.FOLLOW_BRANCH_KEY,
        legacy_names.PREVIOUS_IMAGE_TAG,
        legacy_names.BACKUP_FOLDER,
        legacy_names.GENERATED_MARKER,
        *legacy_names.NGINX_OVERRIDE_FILES,
    ]
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative in ALLOWED_SPELLINGS:
            continue
        text = path.read_text(encoding="utf-8")
        for name in owned:
            if re.search(rf"[\"'`]{re.escape(name)}[\"'`]", text):
                offenders.append(f"{relative}: {name}")
    assert not offenders, "spell it through noust.core.legacy_names:\n" + "\n".join(offenders)
