# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The value of each control on this server: the strictest of everything that asks.

Three things can ask for a control:

1. **The operator's own setting**, the control's configuration key
   (``auth.session.idle_minutes``). Absent, it is the default.
2. **Each enabled framework** (:mod:`noust.core.compliance.frameworks`), listed in
   ``security.frameworks``. ``security.profile: ens-medium`` - and any spelling of
   it, failing strict as it always did - means ENS MEDIA is enabled, so a server
   configured before frameworks existed behaves exactly as it did.
3. **The default**, permissive, the ``standard`` profile's.

The value in force is the strictest of all of them (:meth:`Control.strictest`). An
operator may therefore tighten any control, and may never loosen one below an
enabled framework: the *floor* is what the frameworks ask, and the setting is
only ever read on top of it.

This is the one place that reads the configuration for compliance; everything that
enforces a control asks here (rule 3). It reads on every call, so a change takes
effect on the next request and the next command without a restart.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from noust.core.compliance import controls, frameworks
from noust.core.ens import profile

logger = logging.getLogger(__name__)

#: Where the enabled frameworks are listed.
SETTING_FRAMEWORKS = "security.frameworks"

_WARNED: set[str] = set()


def _parse_list(raw: Any) -> list[str]:
    """
    Args:
        raw: What ``security.frameworks`` holds: a list, or text a person typed.

    Returns:
        The names, lower-cased, without repeats.
    """
    if isinstance(raw, str):
        items: Iterable[Any] = [part for part in re.split(r"[,\s]+", raw) if part]
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        items = []
    names: list[str] = []
    for item in items:
        name = str(item).strip().lower()
        if name and name not in names:
            names.append(name)
    return names


def ordered(ids: Iterable[str]) -> list[str]:
    """
    Args:
        ids: Framework ids, known or not.

    Returns:
        The known ones, in catalog order and once each.
    """
    wanted = set(ids)
    return [name for name in frameworks.FRAMEWORK_IDS if name in wanted]


def enabled_from(framework_names: Any = None, legacy_profile: Any = None) -> list[str]:
    """
    Which frameworks are on, given the two settings that can say so.

    Args:
        framework_names: ``security.frameworks``.
        legacy_profile: ``security.profile``.

    Returns:
        The enabled frameworks in catalog order. An unknown name is ignored (and
        logged once): it cannot be judged, and ``noust compliance status`` names it.
    """
    names = _parse_list(framework_names)
    for name in names:
        if not frameworks.exists(name) and name not in _WARNED:
            _WARNED.add(name)
            logger.warning(
                "Ignoring unknown compliance framework %r in %s", name, SETTING_FRAMEWORKS
            )
    if profile.normalise_profile(legacy_profile) == profile.PROFILE_ENS_MEDIUM:
        names.append(frameworks.LEGACY_PROFILE_FRAMEWORK)
    return ordered(names)


def enabled_ids(config: Any | None = None) -> list[str]:
    """
    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        The frameworks on right now, in catalog order.
    """
    return enabled_from(
        profile.read_config(config, SETTING_FRAMEWORKS, []),
        profile.read_config(config, "security.profile", profile.PROFILE_STANDARD),
    )


def listed(config: Any | None = None) -> list[str]:
    """
    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        The names ``security.frameworks`` holds, known or not, without
        ``security.profile``: what the list itself says.
    """
    return _parse_list(profile.read_config(config, SETTING_FRAMEWORKS, []))


def unknown_names(config: Any | None = None) -> list[str]:
    """
    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        Names in ``security.frameworks`` that are not frameworks of this version.
    """
    return [
        name
        for name in _parse_list(profile.read_config(config, SETTING_FRAMEWORKS, []))
        if not frameworks.exists(name)
    ]


# ------------------------------------------------------------------------- floors


def requirements_of(
    control_id: str, framework_ids: Sequence[str]
) -> list[tuple[str, frameworks.Requirement]]:
    """
    Args:
        control_id: A control's id.
        framework_ids: Frameworks to ask.

    Returns:
        ``(framework id, requirement)`` for each that asks something of it.
    """
    found = []
    for name in framework_ids:
        required = frameworks.get(name).requirement(control_id)
        if required is not None:
            found.append((name, required))
    return found


def floor_for(control_id: str, framework_ids: Sequence[str]) -> Any:
    """
    The least a set of frameworks lets a control be.

    Args:
        control_id: A control's id.
        framework_ids: The frameworks that are on.

    Returns:
        The strictest value any of them asks; the control's weakest value
        (nothing asked) when none does. The default is not in it: an operator
        may set a control looser than the default when no framework objects.
    """
    control = controls.get(control_id)
    values = [
        control.weakest,
        *(required.value for _, required in requirements_of(control_id, framework_ids)),
    ]
    return control.strictest(*values)


def apply_floor(control_id: str, framework_ids: Sequence[str], own: Any) -> Any:
    """
    Hold a value at what the frameworks ask.

    Args:
        control_id: A control's id.
        framework_ids: The frameworks that are on.
        own: The operator's value (or the default).

    Returns:
        ``own`` when no framework asks more of it, otherwise the stricter.
    """
    return controls.get(control_id).stricter(own, floor_for(control_id, framework_ids))


def floor(control_id: str, config: Any | None = None) -> Any:
    """
    Args:
        control_id: A control's id.
        config: The configuration; the process-wide one by default.

    Returns:
        What the enabled frameworks leave of it: the least the operator may set.
    """
    return floor_for(control_id, enabled_ids(config))


def operator_value(control: controls.Control, config: Any | None, *, forced: bool) -> Any:
    """
    The operator's own setting for a control.

    Args:
        control: The control.
        config: The configuration; the process-wide one by default.
        forced: Whether a framework asks something stricter than the default of
            it, which a ``when_forced`` setting needs to count at all.

    Returns:
        The value the setting holds; the default when it holds nothing, nothing
        that can be read, or (for a ``when_forced`` setting) no framework is
        asking.
    """
    if not control.key:
        return control.standard
    if control.setting == controls.SETTING_WHEN_FORCED and not forced:
        return control.standard
    raw = profile.read_config(config, control.key, None)
    if raw is None:
        return control.standard
    try:
        return control.coerce(raw)
    except ValueError:
        return control.standard


@dataclass(frozen=True)
class Resolution:
    """
    A control as it stands on this server.

    Attributes:
        control: Its definition.
        value: What is in force: the strictest of the setting and the floor.
        operator: The operator's own setting.
        floor: What the enabled frameworks leave of it.
        forced_by: The enabled frameworks that ask anything of it, the one that
            sets the floor first.
    """

    control: controls.Control
    value: Any
    operator: Any
    floor: Any
    forced_by: tuple[str, ...]

    @property
    def locked(self) -> bool:
        """Whether an enabled framework asks anything of it, so it cannot be loosened."""
        return bool(self.forced_by)

    @property
    def on(self) -> bool:
        """Whether the control is strict: a flag on, a choice at its strict end, a limit set."""
        control = self.control
        if control.kind == controls.KIND_FLAG:
            return bool(self.value)
        return control.is_stricter_than_standard(self.value)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The control and where it stands, JSON-ready.
        """
        return {
            **self.control.to_dict(),
            "value": self.value,
            "operator": self.operator,
            "floor": self.floor,
            "forced_by": list(self.forced_by),
            "locked": self.locked,
            "enabled": self.on,
        }


def resolve_for(
    control_id: str,
    framework_ids: Sequence[str],
    operator: Any = None,
    *,
    has_operator: bool = False,
) -> Resolution:
    """
    Resolve a control for a given set of frameworks and, optionally, a value of the setting.

    Args:
        control_id: A control's id.
        framework_ids: The frameworks that are on.
        operator: The operator's value, when ``has_operator``.
        has_operator: Whether ``operator`` is a value to count.

    Returns:
        The resolution.
    """
    control = controls.get(control_id)
    asked = requirements_of(control_id, framework_ids)
    floor_value = floor_for(control_id, framework_ids)
    own = operator if has_operator else control.standard
    if control.setting == controls.SETTING_WHEN_FORCED and not asked:
        own = control.standard
    top = max((control.rank(required.value) for _, required in asked), default=None)
    first = [name for name, required in asked if control.rank(required.value) == top]
    rest = [name for name, _ in asked if name not in first]
    return Resolution(
        control=control,
        value=control.stricter(own, floor_value),
        operator=own,
        floor=floor_value,
        forced_by=(*first, *rest),
    )


def resolve(control_id: str, config: Any | None = None) -> Resolution:
    """
    Args:
        control_id: A control's id.
        config: The configuration; the process-wide one by default.

    Returns:
        The control as it stands here.
    """
    control = controls.get(control_id)
    ids = enabled_ids(config)
    return resolve_for(
        control_id,
        ids,
        operator_value(control, config, forced=bool(requirements_of(control_id, ids))),
        has_operator=True,
    )


def resolve_all(config: Any | None = None) -> list[Resolution]:
    """
    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        Every control as it stands, in catalog order.
    """
    return [resolve(control.id, config) for control in controls.CONTROLS]


# ----------------------------------------------------------------------- questions


def value(control_id: str, config: Any | None = None) -> Any:
    """
    Args:
        control_id: A control's id.
        config: The configuration; the process-wide one by default.

    Returns:
        The value in force.
    """
    return resolve(control_id, config).value


def is_on(control_id: str, config: Any | None = None) -> bool:
    """
    The one answer to "is this control enforced here".

    Args:
        control_id: A control's id.
        config: The configuration; the process-wide one by default.

    Returns:
        For a flag, whether it is on; for anything else, whether the value in
        force is stricter than the default.
    """
    return resolve(control_id, config).on


def restricts(control_id: str, config: Any | None = None) -> bool:
    """
    Args:
        control_id: A control of kind choice.
        config: The configuration; the process-wide one by default.

    Returns:
        Whether the value in force is the strict end of the choice (``refused``,
        ``required``, ``recovery``).
    """
    control = controls.get(control_id)
    return bool(resolve(control_id, config).value == control.choices[-1])


def floor_value(control_id: str, config: Any | None = None) -> Any:
    """
    Args:
        control_id: A control's id.
        config: The configuration; the process-wide one by default.

    Returns:
        What the enabled frameworks leave of it (:func:`floor`).
    """
    return floor(control_id, config)


# ---------------------------------------------------------------- reasons for text

#: What is recorded when a decision is taken without a reason on a server that
#: does not ask for one.
NO_REASON = "no reason given"


def reason_text(reason: str | None, *, control_id: str = "exception_reasons") -> str | None:
    """
    Judge the reason given with a decision that lowers protection.

    Building as root, accepting a risk, allowing a privileged container and
    freezing the console are recorded with a reason when the operator gives one
    and refused without one only where the control asks for it.

    Args:
        reason: What the person typed, or None.
        control_id: The control that makes it mandatory.

    Returns:
        The trimmed reason; :data:`NO_REASON` when none was given and none is
        required; None when one is required and missing, which the caller turns
        into its own error.
    """
    text = (reason or "").strip()
    if text:
        return text
    return None if is_on(control_id) else NO_REASON
