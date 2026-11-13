# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The battery of the built-in assistant, with a fleet (item 171).

The assistant belongs to the central, and what it does on a server it does through the central: the same tool,
called on that server through the proxy, with the assistant's token, so the node's own answer is what the
model reads and the node's own audit trail names the assistant and the person behind it. A node that runs the
previous version lacks what was added since, and says so in words.
"""

from __future__ import annotations

import pytest

from tests.support.assistant_battery.fixtures import *  # noqa: F403 - pytest finds fixtures by name
from tests.support.assistant_battery.harness import Harness
from tests.support.assistant_battery.scenario import Scenario, load_family

pytestmark = pytest.mark.allow_sockets

SCENARIOS = load_family("fleet")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_the_scenario(fleet_harness: Harness, scenario: Scenario) -> None:
    record = fleet_harness.run(scenario)

    problems = fleet_harness.judge(scenario, record)
    assert not problems, "\n".join(problems)
