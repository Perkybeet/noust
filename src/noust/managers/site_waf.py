# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a site's policy may say about the web application firewall.

Two things only: the mode (``detect`` judges every request and writes down what the rules saw,
``block`` also refuses the ones that score too high; empty is no WAF) and the numbers of the rules
to leave out for this site. A rule number ends up inside the web server's configuration, so
nothing but a whole number in range is ever accepted. The server's side of it (the module, the
rules, the log) is :mod:`noust.managers.waf`; this is the part a policy can import without the
web server.
"""

from __future__ import annotations

from typing import Any

from noust.core.exceptions import ValidationError

#: The modes a site may ask for; empty is "no WAF".
MODES = ("detect", "block")

#: The most rules one site may leave out, and the largest number a rule may have.
MAX_EXCLUSIONS = 100
MAX_RULE_ID = 99_999_999


def checked_mode(value: Any) -> str:
    """
    Validate a site's WAF mode.

    Args:
        value: ``detect``, ``block``, or empty / ``off`` / None for none.

    Returns:
        The mode, empty for none.

    Raises:
        ValidationError: It is something else.
    """
    if value in (None, "", "off"):
        return ""
    if value not in MODES:
        raise ValidationError(
            f"waf is one of {', '.join(MODES)} (or off)",
            details="detect writes down what the rules see and refuses nothing; block refuses.",
            field="waf",
        )
    return str(value)


def checked_exclusions(value: Any) -> tuple[int, ...]:
    """
    Validate the rules a site leaves out.

    Args:
        value: A list of rule numbers.

    Returns:
        The numbers, without repeats, in ascending order.

    Raises:
        ValidationError: It is not a list of whole numbers, there are too many, or one is out of
            range.
    """
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ValidationError("waf_exclusions is a list of rule numbers", field="waf_exclusions")
    numbers: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= MAX_RULE_ID:
            raise ValidationError(
                f"{item!r} is not a rule number",
                details=f"A rule has a number from 1 to {MAX_RULE_ID}, such as 942100.",
                field="waf_exclusions",
            )
        numbers.add(item)
    if len(numbers) > MAX_EXCLUSIONS:
        raise ValidationError(
            f"At most {MAX_EXCLUSIONS} rules can be left out of a site", field="waf_exclusions"
        )
    return tuple(sorted(numbers))
