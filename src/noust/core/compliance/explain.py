# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which control asks something of a person, and how to stop it asking.

Approvals and reasons are controls (``approvals``, ``change_reasons``), and they apply to everybody:
the owner of a server, holding every permission, is asked for a second person's approval or for a
reason for a change, and reads it as a problem with their permissions. Whatever refuses or asks
(the command line's ``--reason``, the API's approval and reason answers) says which control it is,
who turned it on, and the command that turns it off, from here, so the words are one set and a
test can run the command they give.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from noust.core.compliance import resolve

#: Where the console shows the controls.
CONSOLE_PLACE = "Settings > Controls"


@dataclass(frozen=True)
class WhyOn:
    """
    Why a control is asking, and what stops it.

    Attributes:
        control: The control's id.
        on: Whether it is on at all.
        forced_by: The enabled frameworks that ask for it; empty when only the operator's own
            setting does.
        command: The command that turns it off: the control's own off switch, or the one that
            turns off the frameworks that hold it on.
        where: The place in the console that does the same.
        key: The configuration key that holds the operator's own setting of it.
    """

    control: str
    on: bool
    forced_by: tuple[str, ...]
    command: str
    where: str = CONSOLE_PLACE
    key: str = ""

    def sentence(self) -> str:
        """
        Returns:
            What to tell the person, ending in the command on a line of its own; empty when the
            control is off and there is nothing to explain.
        """
        if not self.on:
            return ""
        if self.forced_by:
            names = ", ".join(self.forced_by)
            return (
                f"The control {self.control} asks for it, and {names} "
                f"{'asks' if len(self.forced_by) == 1 else 'ask'} for that control, so it stays "
                f"on while {'it is' if len(self.forced_by) == 1 else 'they are'} on and cannot be "
                f"turned off by itself. To turn the framework off ({self.where}):\n"
                f"  {self.command}"
            )
        named = f"{self.control} ({self.key})" if self.key else self.control
        return (
            f"The control {named} asks for it, and it was turned on in {self.where} "
            f"(or with the command line). It applies to everybody, whatever their role. To turn "
            f"it off there, or:\n  {self.command}"
        )

    def to_fields(self) -> dict[str, Any]:
        """
        Returns:
            The same as plain data, for the ``fields`` of an API answer.
        """
        return {
            "control": self.control,
            "forced_by": list(self.forced_by),
            "turn_off": self.command,
        }


def why_on(control_id: str, config: Any | None = None) -> WhyOn:
    """
    Explain a control that is on.

    Args:
        control_id: A control's id, such as ``approvals`` or ``change_reasons``.
        config: The configuration; the process-wide one by default.

    Returns:
        Why it is on and the command that turns it off. When the server asks for a reason for
        every change, the command carries the placeholder for it, so it runs as printed.
    """
    found = resolve.resolve(control_id, config)
    forced = found.forced_by
    if forced:
        command = f"noust compliance disable {' '.join(forced)}"
    else:
        command = f"noust compliance set {control_id} off"
    if resolve.is_on("change_reasons", config):
        command += ' --reason "<why>"'
    return WhyOn(control_id, found.on, forced, command, key=found.control.key)
