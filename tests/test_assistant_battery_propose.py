# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The battery of the built-in assistant, proposing (item 171).

What changes something is never a promise of the model's. Each scenario of
``tests/fixtures/assistant_battery/propose`` is one thing the person asks for: the model prepares the exact
call, the server decides what the assistant may do with it (nothing, its own policy's "does it", or a
proposal for the person), and the false executor says what the machine was given **before** anyone approved
(nothing, unless the policy says the assistant does it alone) and **after** the person carried the call out
in their own session.
"""

from __future__ import annotations

import pytest

from tests.support.assistant_battery.fixtures import *  # noqa: F403 - pytest finds fixtures by name
from tests.support.assistant_battery.harness import Harness
from tests.support.assistant_battery.scenario import Scenario, load_family

pytestmark = pytest.mark.allow_sockets

SCENARIOS = load_family("propose")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_the_scenario(harness: Harness, scenario: Scenario) -> None:
    record = harness.run(scenario)

    problems = harness.judge(scenario, record)
    assert not problems, "\n".join(problems)
