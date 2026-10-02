# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Matching a site's regular expressions without letting one stop the console.

The route explainer matches a configuration's ``location ~`` and
``server_name ~`` against a request, and through the API both the draft and the
request are the caller's. Python's ``re`` backtracks while holding the GIL and
cannot be interrupted from another thread: ``^(a+)+$`` against forty a's and a
``!`` would stop every thread of the console for hours.

So a pattern is matched in this process only when its shape bounds the work
(:func:`is_cheap`: at most one unbounded quantifier, no quantified group that
holds a quantifier or an alternative, no backreference, little branching);
any other runs in a child Python through the
:class:`~noust.core.runner.CommandRunner`, killed at a one second deadline.
Patterns, inputs and child processes are capped too. A match that was not
evaluated is None, which the explainer reports as skipped.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from noust.core.runner import CommandRunner, get_runner

#: Longest pattern evaluated at all.
MAX_PATTERN = 512

#: Longest subject (a host, a normalised path) evaluated at all.
MAX_SUBJECT = 2048

#: Child processes one explanation may start; past them, not evaluated.
MAX_ISOLATED = 4

#: Deadline of one child, in seconds.
ISOLATED_TIMEOUT = 1

#: Most alternatives and bounded repetitions a pattern matched here may
#: multiply to.
_MAX_BRANCHING = 16

#: A bounded repetition larger than this counts as unbounded.
_MAX_BOUNDED = 16

#: The child: reads ``[pattern, flags, subject]`` on stdin, prints whether
#: it matches. Isolated (-I) and without site (-S): it imports nothing of
#: Noust's nor of the environment's.
WORKER = (
    "import json,re,sys\n"
    "p,f,s=json.load(sys.stdin)\n"
    "print(json.dumps(re.compile(p,f).search(s) is not None))\n"
)

_PYTHON = sys.executable or "/usr/bin/python3"

#: What a dry run may execute: this exact child, which only reads its stdin.
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = ((Path(_PYTHON).name, "-I", "-S", "-c", WORKER),)

_BOUNDED_RE = re.compile(r"\{(\d*)(?:(,)(\d*))?\}")


def _scan(pattern: str) -> tuple[int, int, bool]:
    """
    Read what bounds the work of matching a pattern.

    Args:
        pattern: A Python pattern.

    Returns:
        ``(unbounded, branching, unsafe)``: unbounded quantifiers, the
        product of alternatives and bounded repetitions, and whether a shape
        known to backtrack without bound was found.
    """
    unbounded = 0
    branching = 1
    # One frame per open group: whether it holds a quantifier or a '|',
    # and how many alternatives it has.
    stack: list[list[int]] = [[0, 1]]
    previous_group: list[int] | None = None
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        closed: list[int] | None = None
        if char == "\\":
            following = pattern[index + 1 : index + 2]
            if (following.isdigit() and following != "0") or following in ("g", "k"):
                return unbounded, branching, True
            index += 2
        elif char == "[":
            end = index + 1
            if pattern[end : end + 1] == "^":
                end += 1
            if pattern[end : end + 1] == "]":
                end += 1
            while end < length and pattern[end] != "]":
                end += 2 if pattern[end] == "\\" else 1
            index = end + 1
        elif char == "(":
            if pattern.startswith("(?P=", index):
                return unbounded, branching, True
            stack.append([0, 1])
            index += 1
            if pattern[index : index + 1] == "?":
                # (?:, (?=, (?<!, (?P<name>: the '?' is syntax, not a quantifier.
                index += 1
                if pattern.startswith("P<", index) or (
                    pattern.startswith("<", index) and pattern[index + 1 : index + 2] not in "=!"
                ):
                    end = pattern.find(">", index)
                    index = end + 1 if end >= 0 else length
                elif pattern[index : index + 1] == "<":
                    index += 2
                else:
                    index += 1
        elif char == ")":
            if len(stack) == 1:
                return unbounded, branching, True
            closed = stack.pop()
            branching *= closed[1]
            if closed[0]:
                stack[-1][0] = 1
            index += 1
        elif char == "|":
            stack[-1][0] = 1
            stack[-1][1] += 1
            index += 1
        elif char in "*+?{":
            bounded = 2
            size = 1
            if char == "{":
                found = _BOUNDED_RE.match(pattern, index)
                if found is None:
                    index += 1
                    previous_group = None
                    continue
                low, comma, high = found.groups()
                size = found.end() - index
                if comma and not high:
                    bounded = 0
                else:
                    top = int(high or low or 0)
                    bounded = top + 1 if top <= _MAX_BOUNDED else 0
            elif char in "*+":
                bounded = 0
            if bounded == 0:
                unbounded += 1
            else:
                branching *= bounded
            if previous_group is not None and previous_group[0]:
                # (a+)+, (a|ab)*: the classic exponential shapes.
                return unbounded, branching, True
            stack[-1][0] = 1
            index += size
            # A lazy or possessive marker belongs to this quantifier.
            if pattern[index : index + 1] in ("?", "+"):
                index += 1
        else:
            index += 1
        previous_group = closed
    if len(stack) != 1:
        return unbounded, branching, True
    return unbounded, branching * stack[0][1], False


def is_cheap(pattern: str) -> bool:
    """
    Tell whether matching a pattern here is bounded, whatever the subject.

    Args:
        pattern: A Python pattern.

    Returns:
        True when it has at most one unbounded quantifier, no quantified
        group holding a quantifier or an alternative, no backreference and
        little branching: the work is then at most quadratic in the
        subject, which :data:`MAX_SUBJECT` bounds.
    """
    unbounded, branching, unsafe = _scan(pattern)
    return not unsafe and unbounded <= 1 and branching <= _MAX_BRANCHING


class BoundedMatcher:
    """
    Matches patterns for one explanation, each with its work bounded.

    Attributes:
        isolated: Child processes started so far.
    """

    def __init__(self, runner: CommandRunner | None = None) -> None:
        """
        Args:
            runner: Where a child runs; the process-wide runner by default.
        """
        self._runner = runner
        self.isolated = 0

    def search(self, pattern: str, subject: str, *, flags: int = 0) -> bool | None:
        """
        Tell whether a pattern matches somewhere in a subject.

        Args:
            pattern: A Python pattern.
            subject: The text.
            flags: ``re`` flags.

        Returns:
            Whether it matches; None when it was not evaluated: it does not
            compile, a cap was reached, or the child missed its deadline.
        """
        if len(pattern) > MAX_PATTERN or len(subject) > MAX_SUBJECT:
            return None
        try:
            compiled = re.compile(pattern, flags)
        except re.error:
            return None
        if is_cheap(pattern):
            return compiled.search(subject) is not None
        if self.isolated >= MAX_ISOLATED:
            return None
        self.isolated += 1
        runner = self._runner if self._runner is not None else get_runner()
        result = runner.run(
            [_PYTHON, "-I", "-S", "-c", WORKER],
            input=json.dumps([pattern, int(flags), subject]),
            timeout=ISOLATED_TIMEOUT,
        )
        if not result.success:
            return None
        answer = result.stdout.strip()
        return True if answer == "true" else False if answer == "false" else None
