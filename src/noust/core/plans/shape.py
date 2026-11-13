# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one shape of a plan.

Every operation that has a plan answers with a :class:`PlanOut`, so the console draws them all with
one piece (``PlanPreview``) and the built-in assistant, which shows a person the plan of what it
proposes and never describes it itself, reads one rule instead of one per operation.

A plan says what Noust would do **and what it does not know**. A plan without its
:attr:`~PlanOut.unknown` is a promise nobody can keep (whether a build passes, how long a migration
takes, what a script of the repository does), so an operation that cannot be simulated whole still
has one: what it can show, and the sentence of what it cannot.

Sentences are :class:`~noust.core.server_text.ServerText`: a code and its values for the console to
say in its language, with the English that the command line prints. A command, a path and another
program's output are values and are never sentences.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from noust.core.server_text import ServerText

#: What a changed file's lines are: a diff of its text, the names of the variables it holds, or
#: nothing (``reason`` says why).
Shown = Literal["diff", "names", "none"]

#: Why a file's content is not shown, when it is not.
Reason = Literal["secret", "binary", "large", "unreadable"]

#: What a plan says it would do to a path.
Change = Literal["create", "modify", "delete", "move", "copy", "link", "mkdir", "meta"]

#: How many commands, files and findings a plan carries at most; what is beyond is counted in
#: :attr:`PlanOut.omitted`. A plan is read by a person and by a model with a budget: a rehearsal
#: of a stack that touches thousands of files says so instead of sending them all.
MAX_COMMANDS = 400
MAX_FILES = 200


def sentence_dict(sentence: ServerText) -> dict[str, Any]:
    """
    Args:
        sentence: A sentence of the server's.

    Returns:
        Its code and values, with the English text and the fix, as the API sends it.
    """
    return {**sentence.to_dict(), "text": sentence.text, "hint": sentence.hint}


@dataclass(frozen=True)
class PlanCommand:
    """
    One command, as it would run.

    Attributes:
        argv: The argument vector, already redacted: nothing that the runner masks, and no shape of
            a credential, reaches a plan.
        probe: It only looks. Probes run for real while a plan is made (a plan that cannot look
            reports fiction) and they are in the list so that what the plan read is not hidden.
        cwd: The directory it runs in, when it is not the process's.
        runs_as: The account it runs as, or ``sandbox:<account>`` for a build confined in a
            transient unit.
        step: The key of the :class:`PlanStep` it belongs to, when the operation names phases.
        writes_to: The file a command whose output is captured writes (a dump).
        check: Not a process: a check Noust asks itself of the application once it has changed it
            (``GET http://127.0.0.1:3000/``). It only looks, and the plan did not ask it: what it
            would ask about does not exist yet.
        seconds: The longest a check waits for an answer.
    """

    argv: tuple[str, ...]
    probe: bool = False
    cwd: str | None = None
    runs_as: str | None = None
    step: str | None = None
    writes_to: str | None = None
    check: bool = False
    seconds: float | None = None

    @property
    def text(self) -> str:
        """The command as it would be typed, each argument quoted for a shell to read it back."""
        return shlex.join(self.argv)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The command as the API sends it.
        """
        return {
            "argv": list(self.argv),
            "text": self.text,
            "probe": self.probe,
            "cwd": self.cwd,
            "runs_as": self.runs_as,
            "step": self.step,
            "writes_to": self.writes_to,
            "check": self.check,
            "seconds": self.seconds,
        }


@dataclass(frozen=True)
class PlanDiffLine:
    """
    One line of a file's change.

    Attributes:
        kind: ``same`` (context), ``add`` or ``del``.
        text: The line, masked. For a file of variables it is a name and nothing else.
    """

    kind: Literal["same", "add", "del"]
    text: str

    def to_dict(self) -> dict[str, str]:
        """
        Returns:
            The line as the API sends it.
        """
        return {"kind": self.kind, "text": self.text}


@dataclass(frozen=True)
class PlanFile:
    """
    One path a plan would change.

    Attributes:
        path: Where.
        change: What happens to it.
        shown: What :attr:`lines` is: ``diff`` of the text, ``names`` of the variables (a file of
            variables never shows a value) or ``none``.
        lines: The change, when it is shown.
        truncated: The change is longer than what is shown.
        reason: Why nothing is shown, when nothing is: ``secret`` (a file only its owner reads),
            ``binary``, ``large`` or ``unreadable``.
        destination: Where it goes, for a move, a copy or a link.
    """

    path: str
    change: Change
    shown: Shown = "none"
    lines: tuple[PlanDiffLine, ...] = ()
    truncated: bool = False
    reason: Reason | None = None
    destination: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The path and its change as the API sends them.
        """
        return {
            "path": self.path,
            "change": self.change,
            "shown": self.shown,
            "lines": [line.to_dict() for line in self.lines],
            "truncated": self.truncated,
            "reason": self.reason,
            "destination": self.destination,
        }


@dataclass(frozen=True)
class PlanStep:
    """
    A phase an operation has, with the commands that belong to it.

    Attributes:
        key: Its position in the operation (``step-1``), stable within one plan.
        title: What it is, as the operation itself announces it while it runs (the progress line
            its job shows): not a sentence of the catalog, so it is shown as it comes.
        commands: The positions, in :attr:`PlanOut.commands`, of its commands.
    """

    key: str
    title: str
    commands: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The step as the API sends it.
        """
        return {
            "key": self.key,
            "title": self.title,
            "commands": list(self.commands),
        }


@dataclass(frozen=True)
class PlanConfirmation:
    """
    Something the operation will ask for before it goes ahead, and how it is given.

    Attributes:
        kind: ``schema_changed`` (going back past a migration), ``frozen`` (a freeze of deploys) or
            another the operation adds.
        sentence: What is being confirmed.
        field: The field of the request that gives it (``schema_changed_ok``, ``frozen_ok``).
    """

    kind: str
    sentence: ServerText
    field: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The confirmation as the API sends it.
        """
        return {"kind": self.kind, "sentence": sentence_dict(self.sentence), "field": self.field}


@dataclass(frozen=True)
class PlanNeeds:
    """
    What applying the operation will ask of whoever applies it.

    Attributes:
        elevation: Sudo mode: the operation asks the person to confirm who they are. A plan never
            asks for it.
        approval: A second person has to approve the call.
        confirmations: What is confirmed in the request itself.
    """

    elevation: bool = False
    approval: bool = False
    confirmations: tuple[PlanConfirmation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The needs as the API sends them.
        """
        return {
            "elevation": self.elevation,
            "approval": self.approval,
            "confirmations": [item.to_dict() for item in self.confirmations],
        }


@dataclass(frozen=True)
class PlanBlock:
    """
    Why the operation would be refused if it were asked for now.

    A freeze or a schema change that can be confirmed is a :class:`PlanConfirmation`; this is what
    cannot be confirmed away (nothing to go back to, no room on the disk).

    Attributes:
        sentence: What stops it.
    """

    sentence: ServerText

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The block as the API sends it.
        """
        return sentence_dict(self.sentence)


@dataclass(frozen=True)
class PlanOut:
    """
    What an operation would do, before it does it.

    Attributes:
        operation: Which (``app.update``, ``app.rollback``): the key of the registry.
        subject: What it is done to (an application's domain, a unit, a certificate's name).
        effect: What changes for whoever looks, in a sentence.
        commands: The commands in the order they would run, probes included.
        files: The paths that would change.
        steps: The phases, when the operation has any.
        findings: What the plan found out by looking (the commits that would come in, the release
            that would serve again), as sentences.
        unknown: What the plan does not know, as sentences. Never empty for an operation that
            builds or migrates anything.
        needs: What applying it will ask for.
        blocked: Why it would be refused now, when it would.
        asked_remote: The plan asked a repository or a registry, over the network and with a
            deadline, to know what it says.
        omitted: How many commands and files were left out for the plan's size.
    """

    operation: str
    subject: str | None
    effect: ServerText
    commands: tuple[PlanCommand, ...] = ()
    files: tuple[PlanFile, ...] = ()
    steps: tuple[PlanStep, ...] = ()
    findings: tuple[ServerText, ...] = ()
    unknown: tuple[ServerText, ...] = ()
    needs: PlanNeeds = field(default_factory=PlanNeeds)
    blocked: PlanBlock | None = None
    asked_remote: bool = False
    omitted: int = 0

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as the API sends it.
        """
        return {
            "operation": self.operation,
            "subject": self.subject,
            "effect": sentence_dict(self.effect),
            "commands": [command.to_dict() for command in self.commands],
            "files": [item.to_dict() for item in self.files],
            "steps": [step.to_dict() for step in self.steps],
            "findings": [sentence_dict(item) for item in self.findings],
            "unknown": [sentence_dict(item) for item in self.unknown],
            "needs": self.needs.to_dict(),
            "blocked": self.blocked.to_dict() if self.blocked is not None else None,
            "asked_remote": self.asked_remote,
            "omitted": self.omitted,
        }

    def changes_nothing(self) -> bool:
        """
        Returns:
            Whether no command of the plan changes anything and no file would: an operation that
            would find nothing to do.
        """
        return not self.files and all(command.probe for command in self.commands)


def only_changes(commands: Sequence[PlanCommand]) -> list[PlanCommand]:
    """
    Args:
        commands: A plan's commands.

    Returns:
        Those that are not probes, in order: what the plan says would run for real.
    """
    return [command for command in commands if not command.probe]
