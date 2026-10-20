# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The provider guides of the backup documentation say what the code accepts (checklist H-15).

A guide that names a backend Noust does not have, or leaves out one it does, or gives an S3 policy
that is not valid JSON, is worse than none: these tests keep the page and the form of destinations
from drifting apart.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from noust.managers.backup_destinations import BACKEND_FIELDS

PAGE = Path(__file__).resolve().parents[1] / "docs" / "disaster-recovery.md"


def guide() -> str:
    """The provider guides section of the page."""
    text = PAGE.read_text(encoding="utf-8")
    start = text.index("## Provider guides")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def test_every_backend_the_form_offers_is_in_the_guide() -> None:
    types = set(re.findall(r"`([a-z0-9]+)`", guide()))

    assert set(BACKEND_FIELDS) <= types, sorted(set(BACKEND_FIELDS) - types)


def test_every_s3_provider_of_the_form_has_its_line() -> None:
    providers = next(field for field in BACKEND_FIELDS["s3"] if field.key == "provider").choices
    named = set(re.findall(r"provider=([A-Za-z]+)", guide()))

    assert set(providers) <= named, sorted(set(providers) - named)


def test_every_type_the_guide_gives_to_a_command_is_one_noust_has() -> None:
    given = set(re.findall(r"--type (\w+)", guide()))

    assert given and given <= set(BACKEND_FIELDS), sorted(given - set(BACKEND_FIELDS))


def test_the_least_access_policy_is_valid_json_and_asks_for_what_a_backup_needs() -> None:
    block = re.search(r"```json\n(.*?)```", guide(), re.S)
    assert block is not None
    policy = json.loads(block.group(1))

    actions = {action for statement in policy["Statement"] for action in statement["Action"]}
    assert actions == {
        "s3:ListBucket",
        "s3:GetBucketLocation",
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
    }
    assert all(statement["Effect"] == "Allow" for statement in policy["Statement"])
    # The bucket, and the objects in it: nothing broader.
    resources = [statement["Resource"] for statement in policy["Statement"]]
    assert resources == ["arn:aws:s3:::my-noust-backups", "arn:aws:s3:::my-noust-backups/*"]


# ------------------------------------------------------------------ what has been tried


def tried() -> str:
    """The subsection that says what was run against a real server."""
    text = guide()
    start = text.index("### What has been tried against the real service")
    end = text.index("\n**Amazon S3 with the least access.**", start)
    return text[start:end]


def rows(table: str) -> list[list[str]]:
    """The cells of the data rows of the first table in some text."""
    lines = [line for line in table.splitlines() if line.startswith("|")]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]


def test_every_row_of_the_tried_table_names_a_date_and_an_rclone_version() -> None:
    found = rows(tried())

    assert found, "the table says nothing was tried"
    for where, date, version, what in found:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), f"{where}: {date!r} is not a date"
        assert re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version), f"{where}: {version!r}"
        assert what, f"{where} says nothing of what ran"


def test_a_tried_row_is_a_type_the_guide_above_lists() -> None:
    listed = guide().split("### What has been tried")[0]

    for where, *_ in rows(tried()):
        kind = re.search(r"`([a-z0-9]+)`", where)
        assert kind is not None, f"{where} names no --type"
        assert f"`{kind.group(1)}`" in listed, f"{kind.group(1)} is not in the table above"


def test_what_is_listed_as_not_tried_is_not_also_in_the_tried_table() -> None:
    section = tried()
    table = "\n".join(line for line in section.splitlines() if line.startswith("|"))
    after = section.split("**Not tried against the real service**")[1].split("\n\n")[0]

    names = [name.strip(" .") for name in re.split(r",| and ", after.split(":", 1)[1])]
    names = [name for name in names if name]
    assert names
    for name in names:
        assert name not in table, f"{name} is both tried and not tried"


def test_the_runs_the_page_cites_are_scenarios_of_the_harness() -> None:
    import importlib.util

    harness = Path(__file__).resolve().parent / "integration" / "backups_run.py"
    spec = importlib.util.spec_from_file_location("backups_run_for_docs", harness)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]

    cited = set(re.findall(r"--scenario ([a-z0-9_]+)", tried()))

    assert cited and cited <= set(module.SCENARIOS), sorted(cited - set(module.SCENARIOS))
