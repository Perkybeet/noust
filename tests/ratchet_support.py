# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The comparison every structural guard makes against its list of known offenders."""

from __future__ import annotations


def check_ratchet(
    violations: set[str],
    known: set[str],
    rule: str,
    where: str = "tests/test_architecture.py",
) -> None:
    """
    Compare violations against the list of known offenders.

    Args:
        violations: Files that currently break the rule.
        known: Files recorded as breaking it.
        rule: What the rule is, for the failure message.
        where: The file that holds the list, for the failure message.

    Raises:
        AssertionError: When a new violation appears, or when a recorded one
            has been fixed without updating the list.
    """
    added = violations - known
    assert not added, (
        f"New violations of '{rule}':\n"
        + "\n".join(f"  {name}" for name in sorted(added))
        + f"\n\nFix them, or add them to the list in {where} with a reason."
    )

    fixed = known - violations
    assert not fixed, (
        f"These files no longer violate '{rule}':\n"
        + "\n".join(f"  {name}" for name in sorted(fixed))
        + "\n\nRemove them from the list so it keeps shrinking."
    )
