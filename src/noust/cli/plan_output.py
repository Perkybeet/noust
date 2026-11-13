# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``--plan``: what a command would do, printed (item 183).

``noust update SHOP --plan`` prints what the console's "See what it will do" shows for the same
operation, from the same function (:mod:`noust.deployers.plans`), and applies nothing. It is not
``--dry-run``: that runs the command with every change refused and prints the lines of what it skipped;
a plan is the commands in their order, the files and their diff, what the plan found out, what it does
not know and what applying it will ask for, in the one shape every plan has.
"""

from __future__ import annotations

import click

from noust.core.plans.shape import PlanOut


def render(plan: PlanOut) -> str:
    """
    Say a plan in English, for a terminal.

    Args:
        plan: The plan.

    Returns:
        The text: the effect, the commands with their order, the files with their diff, what was found,
        what is not known, what it will ask and why it would be refused, each under a heading.
    """
    lines = [plan.effect.text, ""]
    if plan.blocked is not None:
        lines += [f"Refused now: {plan.blocked.sentence.text}"]
        if plan.blocked.sentence.hint:
            lines += [f"  {plan.blocked.sentence.hint}"]
        lines += [""]
    if plan.commands:
        lines += ["Commands, in this order:"]
        for number, command in enumerate(plan.commands, start=1):
            if command.check:
                note = (
                    f"  (a check, up to {command.seconds:g} s)"
                    if command.seconds
                    else "  (a check)"
                )
            else:
                note = "  (only looks)" if command.probe else ""
            lines += [f"  {number:>3}. {command.text}{note}"]
        lines += [""]
    for item in plan.files:
        target = f" -> {item.destination}" if item.destination else ""
        lines += [f"{item.change} {item.path}{target}"]
        if item.reason:
            lines += [f"    (content not shown: {item.reason})"]
        marks = {"add": "+", "del": "-", "same": " "}
        lines += [f"    {marks[line.kind]} {line.text}" for line in item.lines]
        if item.truncated:
            lines += ["    ..."]
    if plan.files:
        lines += [""]
    for heading, sentences in (
        ("What the plan found:", plan.findings),
        ("What it does not know:", plan.unknown),
    ):
        if sentences:
            lines += [heading, *[f"  - {sentence.text}" for sentence in sentences], ""]
    asks = [item.sentence.text for item in plan.needs.confirmations]
    if plan.needs.elevation:
        asks.append("Sudo mode: the console asks you to confirm who you are.")
    if plan.needs.approval:
        asks.append("A second person has to approve it.")
    if asks:
        lines += ["Applying it will ask for:", *[f"  - {text}" for text in asks], ""]
    if plan.omitted:
        lines += [f"({plan.omitted} more commands or files are not listed.)"]
    return "\n".join(lines).rstrip() + "\n"


def show(plan: PlanOut) -> None:
    """
    Print a plan.

    Args:
        plan: The plan.
    """
    click.echo(render(plan), nl=False)
