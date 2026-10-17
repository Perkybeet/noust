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
