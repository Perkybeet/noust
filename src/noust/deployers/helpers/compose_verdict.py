# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the guard of a compose stack does with what it found: stop, say, or let through.

One rule, said once, for every way a compose file reaches a server: a deployment, an update, a
redeploy, an adoption, a document edited in the Docker zone and a document pasted.

- What the guard refuses and the stack **already had, unchanged**, is warned about and never
  stops anything: an application in production keeps updating.
- What a deployment, an update, a redeploy, an adoption or an edit **adds** is refused as it is
  for a new stack, unless an operator recorded an exception for the application
  (``noust app sandbox compose-exception``, equivalent to root).
- What no exception can allow (:attr:`~noust.deployers.helpers.compose_guard.ComposeFindings.hard`:
  another application's files or Noust's own through a door with no reason to read them, what
  belongs to another application) is refused whatever the exception says, and only a stack that
  already had it is let through, with a warning.

What a stack "already had" is a set of sentences: the findings of the compose file that served,
judged now. A finding that goes through a link which the update changed is not one that was
there before (:func:`unchanged`).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noust.deployers.helpers.compose_guard import ComposeFindings
    from noust.deployers.helpers.sandbox import ComposeException


@dataclass(frozen=True)
class Verdict:
    """
    What to do with the findings of one compose file.

    Attributes:
        stop: The refused sentences that stop the operation: new, and not allowed by an exception
            (or allowed by none that exists).
        excused: The refused sentences an exception recorded for the application allows.
        had: The refused sentences the stack already had: warned about.
        hard: The sentences of ``stop`` that no exception could have allowed.
    """

    stop: tuple[str, ...] = ()
    excused: tuple[str, ...] = ()
    had: tuple[str, ...] = ()
    hard: tuple[str, ...] = ()


def decide(
    findings: ComposeFindings,
    *,
    already: Collection[str] = (),
    exception: ComposeException | None = None,
) -> Verdict:
    """
    Sort what the guard refused into what stops, what an exception allows and what was there.

    Args:
        findings: What the guard found.
        already: The sentences the stack already ran with.
        exception: The exception recorded for the application, if any.

    Returns:
        The verdict.
    """
    stop: list[str] = []
    excused: list[str] = []
    had: list[str] = []
    for sentence in findings.refused:
        if exception is not None and sentence not in findings.hard:
            excused.append(sentence)
        elif sentence in already:
            had.append(sentence)
        else:
            stop.append(sentence)
    hard = tuple(sentence for sentence in stop if sentence in findings.hard)
    return Verdict(tuple(stop), tuple(excused), tuple(had), hard)


def unchanged(findings: ComposeFindings, changed_links: Collection[Path] | None) -> list[str]:
    """
    List the refused sentences of a file that were there before an update, as far as a link says.

    A finding that goes through a link is the link's: when the update added or changed that link,
    the same line of the compose file reaches somewhere else now, and the stack did not run with
    it. The links an operator made by hand are not in any commit and are not in ``changed_links``.

    Args:
        findings: What the guard found in the compose file that served, judged on the tree now.
        changed_links: The links the update changed (:func:`noust.managers.source_links.links_changed`);
            None when git could not say, which is every link.

    Returns:
        The refused sentences that can count as already had.
    """
    kept = []
    for sentence in findings.refused:
        links = findings.links.get(sentence, ())
        if links and (changed_links is None or any(Path(link) in changed_links for link in links)):
            continue
        kept.append(sentence)
    return kept
