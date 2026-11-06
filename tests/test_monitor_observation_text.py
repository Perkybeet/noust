# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the process monitor writes about a process is one of the server's sentences: the English
detail it always stored, and beside it in the API the code and values the console translates.
"""

from __future__ import annotations

from noust.monitor.models import ProcessInfo
from noust.monitor.signals import observe_process
from noust.web.api import monitor as monitor_api


def test_sustained_usage_is_said_with_its_code_and_numbers() -> None:
    process = ProcessInfo(pid=7, name="node", cpu_percent=92.44, memory_percent=18.2)

    observation = observe_process(process, cpu_threshold=80.0, memory_threshold=80.0)

    assert observation is not None
    assert observation.detail == (
        "Sustained resource usage: CPU 92.4% (threshold 80.0%), memory 18.2% (threshold 80.0%)."
    )
    entry = monitor_api._observation_entry(observation, {"scope": "admin"})
    assert entry.detail_coded is not None
    assert entry.detail_coded.code == "monitor.observation.resource_usage"
    assert entry.detail_coded.params == {
        "cpu": "92.4",
        "cpu_threshold": "80.0",
        "memory": "18.2",
        "memory_threshold": "80.0",
    }


def test_a_notable_name_is_said_with_its_prefix() -> None:
    process = ProcessInfo(pid=8, name="xmrig", cpu_percent=1.0, memory_percent=1.0)

    observation = observe_process(process, cpu_threshold=80.0, memory_threshold=80.0)

    assert observation is not None
    assert observation.detail is not None
    assert observation.detail.startswith("Executable name starts with 'xmr")
    entry = monitor_api._observation_entry(observation, {"scope": "admin"})
    assert entry.detail_coded is not None
    assert entry.detail_coded.code == "monitor.observation.name_pattern"


def test_a_stored_detail_in_other_words_keeps_its_text_and_no_code() -> None:
    entry = monitor_api._row_to_entry(
        {
            "id": 1,
            "observed_at": "2026-11-06T00:00:00",
            "pid": 3,
            "process_name": "x",
            "signal": "resource_usage",
            "severity": "notice",
            "detail": "high CPU",
        },
        {"scope": "admin"},
    )

    assert entry.detail == "high CPU"
    assert entry.detail_coded is None
