# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A port another application owns is not handed to a new one."""

from __future__ import annotations

import pytest

from noust.core.exceptions import PortError
from noust.validators.port import validate_unclaimed_port


def test_a_port_nobody_owns_is_returned_as_given() -> None:
    assert validate_unclaimed_port(3000, {3001, 3002}) == 3000


@pytest.mark.parametrize("claimed", [{3000}, {2999, 3000, 3001}, frozenset({3000})])
def test_a_port_an_application_owns_is_refused_without_naming_the_application(
    claimed: set[int],
) -> None:
    with pytest.raises(PortError) as raised:
        validate_unclaimed_port(3000, claimed)

    assert "belongs to another application" in raised.value.message
    assert "listen" in raised.value.details
