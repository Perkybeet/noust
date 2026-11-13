# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A text somebody else wrote orders the assistant to do harm, and the server stops it every time (item 171).

Instructions hidden in the places an application's own authors and visitors write (the lines of its
journal, the output of a build, a commit message, a branch name) and in a README, ask to stop every
application, to set a variable, to send alerts to a stranger, to add a key to root, to add a domain, to deploy
and to delete an application, or to ignore the instructions it was given. The scripted model **obeys on
purpose**, which is the worst model there is: it makes exactly the call the text asks for. What is measured
is not what it does but what the server lets it do, per scenario and per carrier: what it tried, what
stopped each attempt (the floor, the policy, where the text came from, or the token that never holds sudo
mode) and that nothing was executed.
"""

from __future__ import annotations

import pytest

from tests.support.assistant_battery.fixtures import *  # noqa: F403 - pytest finds fixtures by name
from tests.support.assistant_battery.harness import Harness
from tests.support.assistant_battery.scenario import Scenario, load_family

pytestmark = pytest.mark.allow_sockets

SCENARIOS = load_family("injection")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_the_scenario(
    harness: Harness, scenario: Scenario, capsys: pytest.CaptureFixture[str]
) -> None:
    records = harness.run_carriers(scenario)

    problems: list[str] = []
    for record in records:
        carrier = record.extra["carrier"]
        problems += [f"[{carrier}] {line}" for line in harness.judge(scenario, record)]
        shown = [(a.tool, a.outcome, a.stopper, a.foreign) for a in record.attempts()]
        with capsys.disabled():
            print(f"\n{scenario.id} / {carrier}: {shown}")
    assert not problems, "\n".join(problems)
