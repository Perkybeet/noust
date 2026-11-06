# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What is done with systemd's verdict on a unit file somebody wrote by hand.

The unit file page asks ``systemd-analyze verify`` before it saves, so the editor never
installs a unit systemd would refuse. The advanced form of a new service wrote the file
without asking, and a client that does not go through the console (a script, the API) could
install one as well: a rule held by the caller has as many holes as there are callers, so the
manager that writes the file applies it (:meth:`ServiceManager.create_from_unit`) and this is
the one place that says what a refusal is.

The words are systemd's own, kept verbatim in the error's ``output`` so a client shows them
as they are; nothing was written when this raises.
"""

from __future__ import annotations

from noust.core.exceptions import ValidationError


def require_accepted(accepted: bool, output: str) -> None:
    """
    Refuse a unit file that systemd's check did not accept.

    Args:
        accepted: Whether ``systemd-analyze verify`` accepted the file, the first half of
            :meth:`ServiceManager.verify_unit`'s answer.
        output: What the check printed, verbatim: the reason a refusal is made of.

    Raises:
        ValidationError: When it did not accept it, on the field that holds the file, with
            the check's own words as ``output``.
    """
    if accepted:
        return
    raise ValidationError(
        "systemd rejected the unit file",
        details="Fix what systemd says and create it again; nothing was written.",
        output=output,
        field="raw_content",
    )
