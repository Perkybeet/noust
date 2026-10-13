# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
User-defined alert rules on the metrics Noust records (checklist G-18).

``model`` says what a rule is, ``rules`` keeps them in the store and the engine
(:mod:`noust.monitor.alert_engine`, which belongs with the monitor that runs it)
judges them against the recorded history (the monitor runs it every scan).
Messages leave through the notification channels like every other event:
``alert_firing`` when a limit has been breached long enough and
``alert_resolved`` when it is back inside it.
"""

from noust.core.alerts.model import (
    COMPARATORS,
    METRICS,
    SEVERITIES,
    AlertRule,
    MetricSpec,
    format_duration_short,
    format_value,
    parse_duration,
    parse_threshold,
    validate_rule,
)
from noust.core.alerts.rules import AlertRules, AlertState

__all__ = [
    "COMPARATORS",
    "METRICS",
    "SEVERITIES",
    "AlertRule",
    "AlertRules",
    "AlertState",
    "MetricSpec",
    "format_duration_short",
    "format_value",
    "parse_duration",
    "parse_threshold",
    "validate_rule",
]
