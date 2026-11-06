# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a refusal of a hand-written unit is made of (:mod:`noust.managers.unit_check`).

The manager that writes the file applies the rule, so a script that calls the API gets the
same refusal as the console's form; these tests hold the shape that clients rely on to show
systemd's own words.
"""

from __future__ import annotations

import pytest

from noust.core.exceptions import ValidationError
from noust.managers import unit_check


def test_a_unit_systemd_accepted_is_let_through() -> None:
    assert unit_check.require_accepted(True, "") is None


def test_a_unit_it_accepted_with_remarks_is_let_through_too() -> None:
    # `systemd-analyze verify` prints warnings about units it accepts; they are not a refusal.
    assert unit_check.require_accepted(True, "worker.service: Unit uses KillMode=none.") is None


def test_a_refusal_carries_systemds_words_and_names_the_field_of_the_file() -> None:
    words = "worker.service: Service has no ExecStart=, ExecStop=, or SuccessAction=. Refusing."
    with pytest.raises(ValidationError) as refused:
        unit_check.require_accepted(False, words)
    assert refused.value.output == words
    assert refused.value.field == "raw_content"
    assert refused.value.details is not None
    assert "nothing was written" in refused.value.details
