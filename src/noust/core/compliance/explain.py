# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which control asks something of a person, and how to stop it asking.

Approvals and reasons are controls (``approvals``, ``change_reasons``), and they apply to everybody:
the owner of a server, holding every permission, is asked for a second person's approval or for a
reason for a change, and reads it as a problem with their permissions. Whatever refuses or asks
(the command line's ``--reason``, the API's approval and reason answers, the server's terminal) says
which control it is, who turned it on, and the command that turns it off, from here.

This module decides **which** control asks and **who** holds it on, and the command that turns it
off. It keeps no words: the sentences are in :mod:`noust.core.server_text`
(``controls.asked_by_setting`` and ``controls.asked_by_framework``), with the control, the
frameworks and the command as values, so the console translates them and a test can run the command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from noust.core.compliance import controls, resolve
from noust.core.server_text import ServerText, say


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
    """

    control: str
    on: bool
    forced_by: tuple[str, ...]
    command: str

    def said(self) -> ServerText | None:
        """
        Returns:
            The sentence that tells the person which control asks and how to stop it, with the
            command as a value; None when the control is off and there is nothing to explain.
        """
        if not self.on:
            return None
        if self.forced_by:
            return say(
                "controls.asked_by_framework",
                control=self.control,
                frameworks=", ".join(self.forced_by),
                command=self.command,
            )
        return say("controls.asked_by_setting", control=self.control, command=self.command)

    def sentence(self) -> str:
        """
        Returns:
            What to tell the person in English, ending in the command on a line of its own; empty
            when the control is off and there is nothing to explain.
        """
        said = self.said()
        return "" if said is None else f"{said.text} {said.hint}"


def reason_suffix(config: Any | None = None) -> str:
    """
    Say what a command that changes something must end with on this server.

    A server that asks for a reason for every change (``change_reasons``, which ENS and the other
    frameworks turn on) refuses a command without one, so a command printed for a person to run has
    to carry the placeholder or it fails as printed.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        `` --reason "<why>"``, or nothing when this server does not ask.
    """
    return ' --reason "<why>"' if resolve.is_on("change_reasons", config) else ""


def _loosest(control: controls.Control) -> str:
    """
    Args:
        control: A control.

    Returns:
        The word that sets it to what asks for nothing: ``off`` for a flag, its first choice
        (``allowed``) for a choice, ``none`` for a limit.
    """
    if control.kind == controls.KIND_FLAG:
        return "off"
    if control.kind == controls.KIND_CHOICE:
        return control.choices[0]
    return "none"


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
        command = f"noust compliance set {control_id} {_loosest(found.control)}"
    command += reason_suffix(config)
    return WhyOn(control_id, found.on, forced, command)
