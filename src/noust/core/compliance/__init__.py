# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Compliance as controls and the frameworks that switch them on (item 83).

- :mod:`~noust.core.compliance.controls`: the unit. Each technical thing Noust can
  require is a control with its own setting, permissive by default.
- :mod:`~noust.core.compliance.frameworks`: presets over the controls (ENS BÁSICA,
  MEDIA and ALTA, ISO/IEC 27001, NIS2, CIS IG1 and IG2, SOC 2 Security, GDPR art.
  32) and, for every control a preset turns on, the clause it serves. The one place
  that mapping lives; ``docs/COMPLIANCE.md`` is generated from it.
- :mod:`~noust.core.compliance.resolve`: the value in force, the strictest of the
  operator's setting and every enabled framework. The one place that reads the
  configuration for this.
- :mod:`~noust.core.compliance.manager`: planning and applying a change of
  frameworks and controls, for ``noust compliance`` and the console.
- :mod:`~noust.core.compliance.check`: ``noust compliance check``, per framework.
- :mod:`~noust.core.compliance.docs`: the generator of ``docs/COMPLIANCE.md``.

A preset turns on technical controls that help with a framework. It never makes a
server "compliant": what stays organisational is listed with each framework.

Code that enforces a control asks the functions re-exported here::

    from noust.core.compliance import is_on, value

    if is_on("approvals"):
        ...
    longest = value("session_idle_minutes")

Importing this package reads no configuration.
"""

from noust.core.compliance.resolve import (
    NO_REASON,
    SETTING_FRAMEWORKS,
    Resolution,
    enabled_ids,
    floor,
    is_on,
    reason_text,
    resolve_all,
    restricts,
    value,
)

__all__ = [
    "NO_REASON",
    "SETTING_FRAMEWORKS",
    "Resolution",
    "enabled_ids",
    "floor",
    "is_on",
    "reason_text",
    "resolve_all",
    "restricts",
    "value",
]
