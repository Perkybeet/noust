# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the CodeQL configuration leaves out is still there to leave out (item 87).

``.github/codeql/codeql-config.yml`` keeps files that are not Noust's code out of the analysis and
sets one query aside; an entry that matches nothing is a rename that silently widened or narrowed
what is analysed, and an exclusion is only valid while the guarantee that justifies it is tested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".github" / "codeql" / "codeql-config.yml"

#: Folders that exist only after a build or an install, so a checkout may not have them.
GENERATED = {"panel/node_modules"}


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_every_path_it_leaves_out_exists_or_matches_something(config: dict[str, Any]) -> None:
    for entry in config["paths-ignore"]:
        if entry in GENERATED:
            continue
        if "*" in entry:
            matches = list(ROOT.glob(entry))
        else:
            matches = [ROOT / entry] if (ROOT / entry).exists() else []
        assert matches, f"{entry} matches nothing: renamed, or no longer needed?"


def test_the_code_that_ships_is_not_among_what_it_leaves_out(config: dict[str, Any]) -> None:
    ignored = set(config["paths-ignore"])

    assert "src/noust" not in ignored
    assert "panel/src" not in ignored
    assert not any(entry.startswith("src/noust/") and entry.endswith(".py") for entry in ignored)


def test_log_injection_is_set_aside_only_while_the_records_are_neutral(
    config: dict[str, Any],
) -> None:
    excluded = [f["exclude"]["id"] for f in config["query-filters"] if "exclude" in f]

    assert excluded == ["py/log-injection"]
    assert (ROOT / "tests" / "test_log_neutral.py").is_file()
    assert (ROOT / "src" / "noust" / "core" / "logsafe.py").is_file()
