# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The battery of the built-in assistant, explaining (item 171).

Beside a failed deployment, a diagnosis or an alert, "explain this" gives the model what the page shows,
which Noust reads, and the model answers. It is offered no tools: whatever it is told to do, it can only
talk. Each scenario of ``tests/fixtures/assistant_battery/explain`` is one failure and what is asked of it.
"""

from __future__ import annotations

import pytest

from tests.support.assistant_battery.fixtures import *  # noqa: F403 - pytest finds fixtures by name
from tests.support.assistant_battery.harness import Harness
from tests.support.assistant_battery.scenario import Scenario, load_family

pytestmark = pytest.mark.allow_sockets

SCENARIOS = load_family("explain")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_the_scenario(harness: Harness, scenario: Scenario) -> None:
    record = harness.run(scenario)

    problems = harness.judge(scenario, record)
    assert not problems, "\n".join(problems)
