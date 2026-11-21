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

What a stack "already had" is a :class:`Heritage`, and it has sources that neither the repository nor the
stack's ``.env`` can write. What the stack was **accepted with** (the store's record of every activation that
passed the guard, :mod:`noust.core.compose_acceptance`) is the proof for everything, of any kind, and for a
stack that has one it replaces the compose file that served: a container that mounts the tree writes that
file, and the ``.git`` folder it is read from. For a stack with no record yet the compose file that served,
**as written** (a finding of it that the update did not change; one that goes through a link which the
update changed is not one that was there before, :func:`unchanged`), is the proof for everything. And what
the stack's **containers**
were given (:mod:`noust.deployers.helpers.compose_running`, read from Docker the first time it matters
and once) is the proof for what only the model Compose resolves shows: a mount made of a variable, a
service an ``include`` brought in, a definition an ``extends`` took from another file. The stack
ran with those, and no file as written says so.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from noust.deployers.helpers import compose_running

if TYPE_CHECKING:
    from noust.deployers.helpers.compose_guard import ComposeFindings, FindingKey
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


@dataclass
class Heritage:
    """
    What a stack that already runs is known to have had.

    Attributes:
        sentences: The refused sentences of the compose file that served, as written, which the update
            did not change.
        changed_links: The links of the application's tree that the commits since changed
            (:func:`noust.managers.source_links.links_changed`); None when git could not say, which
            is every link. A finding of a container that goes through one of them is not inherited.
        running: Reads what the stack's containers have, or None for a stack that has none to read
            (a first deployment). It is asked at most once, and only when a finding that nothing else
            covers could be one of theirs; Docker that cannot be asked raises
            :class:`~noust.core.docker.errors.DockerUnavailableError`, which nothing here hides.
        keys: What the stack was accepted with, from the store
            (:mod:`noust.core.compose_acceptance`): the identity of each finding, of any kind. It is
            what an activation that passed the guard wrote down, which nothing in the stack's tree can
            write, so it replaces the file that served for a stack that has an entry.
    """

    sentences: frozenset[str] = frozenset()
    changed_links: frozenset[Path] | None = None
    running: Callable[[], compose_running.Running] | None = None
    keys: frozenset[FindingKey] = frozenset()
    _read: compose_running.Running | None = field(default=None, init=False, repr=False)

    def _keys(self) -> frozenset[FindingKey]:
        """What the containers have, read the first time it is needed."""
        if self._read is None:
            if self.running is None:
                return frozenset()
            self._read = self.running()
        return self._read.keys

    def _could_be_theirs(self, findings: ComposeFindings, sentence: str) -> bool:
        """
        Tell whether a container is a possible source of a finding's heritage.

        Args:
            findings: What the guard found.
            sentence: One of its refused sentences.

        Returns:
            Whether the file that served does not already cover it, and it is of a kind a container
            can vouch for, and no link the update changed is on its way.
        """
        if self.running is None or sentence in self.sentences or sentence in findings.hard:
            return False
        key = findings.keys.get(sentence)
        if key is None or key[1] not in compose_running.INHERITED_FROM_CONTAINERS:
            return False
        links = findings.links.get(sentence, ())
        return not (
            links
            and (
                self.changed_links is None
                or any(Path(link) in self.changed_links for link in links)
            )
        )

    def covers(self, findings: ComposeFindings, sentence: str) -> bool:
        """
        Tell whether the stack already had what a finding says.

        Args:
            findings: What the guard found.
            sentence: One of its refused sentences.

        Returns:
            True when the record of what the stack was accepted with or the file that served says it, or a
            container of the same service has the same thing.

        Raises:
            DockerUnavailableError: A container had to be asked about and Docker could not be.
        """
        if sentence in self.sentences:
            return True
        accepted = findings.keys.get(sentence)
        if accepted is not None and accepted in self.keys:
            return True
        if not self._could_be_theirs(findings, sentence):
            return False
        key = findings.keys[sentence]
        return compose_running.has(self._keys(), key, linked=bool(findings.links.get(sentence)))

    def undecided(self, findings: ComposeFindings) -> list[str]:
        """
        List the refused sentences that only a container could have vouched for.

        Args:
            findings: What the guard found.

        Returns:
            The sentences whose heritage depends on what Docker says.
        """
        return [s for s in findings.refused if self._could_be_theirs(findings, s)]


def decide(
    findings: ComposeFindings,
    *,
    already: Collection[str] | Heritage = (),
    exception: ComposeException | None = None,
) -> Verdict:
    """
    Sort what the guard refused into what stops, what an exception allows and what was there.

    Args:
        findings: What the guard found.
        already: What the stack already ran with: the sentences of the file that served, or a
            :class:`Heritage`, which can also ask its containers.
        exception: The exception recorded for the application, if any.

    Returns:
        The verdict.

    Raises:
        DockerUnavailableError: The heritage had to ask a container and Docker could not be asked.
    """
    heritage = already if isinstance(already, Heritage) else Heritage(frozenset(already))
    stop: list[str] = []
    excused: list[str] = []
    had: list[str] = []
    for sentence in findings.refused:
        if exception is not None and sentence not in findings.hard:
            excused.append(sentence)
        elif heritage.covers(findings, sentence):
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
