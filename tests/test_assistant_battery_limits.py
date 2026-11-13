# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The battery of the built-in assistant, under its limits (item 171).

A limit is asked before every step and cuts between two steps: the step that crossed it is whole and written
down, and the next one is not made. Each scenario of ``tests/fixtures/assistant_battery/limits`` is a model
that would go on (a script that never stops, one that reads too much, a provider that reports no usage, one
that spends, one that changes things) and what the server does at the limit, with the sentence that says
which one it was and who changes it.
"""

from __future__ import annotations

import pytest

from tests.support.assistant_battery.fixtures import *  # noqa: F403 - pytest finds fixtures by name
from tests.support.assistant_battery.harness import Harness
from tests.support.assistant_battery.scenario import Scenario, load_family

pytestmark = pytest.mark.allow_sockets

SCENARIOS = load_family("limits")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_the_scenario(harness: Harness, scenario: Scenario) -> None:
    record = harness.run(scenario)

    problems = harness.judge(scenario, record)
    assert not problems, "\n".join(problems)

    stop = record.turn.get("stop")
    if stop is not None:
        # A limit says which it was and who changes it.
        assert stop["hint"], stop
