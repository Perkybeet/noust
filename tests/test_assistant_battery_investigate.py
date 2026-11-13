# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The battery of the built-in assistant, investigating (item 171).

With a model that calls tools, the assistant reads what it needs, step by step, with read-only tools, and
every step is shown. Each scenario of ``tests/fixtures/assistant_battery/investigate`` is a failure whose
cause the model reaches through the tools of the short list, or (the last three) a question the evidence
does not answer, where what is measured is that nothing is invented: a citation to a line that does not
exist leaves no trace and nothing is proposed.
"""

from __future__ import annotations

import pytest

from tests.support.assistant_battery.fixtures import *  # noqa: F403 - pytest finds fixtures by name
from tests.support.assistant_battery.harness import Harness
from tests.support.assistant_battery.scenario import Scenario, load_family

pytestmark = pytest.mark.allow_sockets

SCENARIOS = load_family("investigate")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_the_scenario(harness: Harness, scenario: Scenario) -> None:
    record = harness.run(scenario)

    problems = harness.judge(scenario, record)
    assert not problems, "\n".join(problems)
