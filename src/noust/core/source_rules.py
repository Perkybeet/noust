# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether a push or a pull request is worth a deployment.

Two rules, the same for every forge and for the GitHub App:

- ``[skip ci]`` / ``[skip cd]`` in a commit message or a pull request title
  says the change is not meant to deploy. A push is skipped only when *every*
  commit it carries says so (one real commit among documentation ones still
  ships); a pull request when its title or its head commit does.
- Watch paths are globs over the files a push changed. An application with
  watch paths deploys only when one of the changed files is included by them,
  which is what lets one repository hold several applications (a monorepo)
  without every push rebuilding all of them.

Both are pure functions of text: the callers (the webhook endpoints and the
previews) read the application's rules from the store
(:mod:`noust.core.app_source_store`) and the files from the payload
(:func:`noust.core.forge_events.parse_push_change`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache

from noust.core.exceptions import ValidationError

#: The markers, compared lowercased. ``[skip ci]`` skips a deployment as well as a
#: CI run: this server is the "CD" of the repository.
SKIP_MARKERS: tuple[str, ...] = ("[skip ci]", "[skip cd]")

#: Most patterns an application keeps, and the longest one. A monorepo with
#: thirty workspaces needs a handful each.
MAX_WATCH_PATHS = 100
MAX_PATTERN_LENGTH = 300

#: What the field name is in a refusal, so the console can point at the input.
_FIELD = "watch_paths"


def _marker_in(message: str) -> str | None:
    """
    Find a skip marker in a text.

    Args:
        message: A commit message or a title.

    Returns:
        The marker as written in :data:`SKIP_MARKERS`, or None.
    """
    lowered = message.lower()
    return next((marker for marker in SKIP_MARKERS if marker in lowered), None)


def push_skip_reason(messages: Sequence[str], *, complete: bool = True) -> str | None:
    """
    Say why a push is not deployed because of its commit messages.

    Args:
        messages: The messages of the commits the push carries.
        complete: Whether these are all of them. A payload that was cut short
            (a forge lists at most so many commits) may hide a commit that is
            not marked, so it never skips.

    Returns:
        The sentence for the delivery log, or None when the push deploys.
    """
    if not complete:
        return None
    text = [message for message in messages if message.strip()]
    if not text:
        return None
    markers = [_marker_in(message) for message in text]
    if any(marker is None for marker in markers):
        return None
    if len(text) == 1:
        return f"its only commit says {markers[0]}"
    return f"all {len(text)} commits say [skip ci] or [skip cd]"


def pull_request_skip_reason(title: str, head_message: str) -> str | None:
    """
    Say why a pull request gets no preview because of what it says about itself.

    Args:
        title: The pull request's title.
        head_message: The message of the commit at the tip of its branch, when
            the forge's payload carries it; empty otherwise.

    Returns:
        The sentence for the delivery log, or None when the preview is built.
    """
    for what, text in (("its title", title), ("its latest commit", head_message)):
        marker = _marker_in(text)
        if marker is not None:
            return f"{what} says {marker}"
    return None


def normalize_watch_paths(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """
    Read watch paths from a text (one per line) or a list, and validate them.

    A leading ``/`` means the root of the repository and is dropped; a ``!``
    keeps its meaning in front of it. Blank lines, ``#`` comments and
    duplicates go.

    Args:
        value: The lines, a list, or None.

    Returns:
        The patterns, in order (the order decides which match wins).

    Raises:
        ValidationError: A pattern climbs with ``..``, holds a control
            character or a backslash, is only ``!``, or is too long, or there
            are too many. ``field`` is ``watch_paths``.
    """
    if value is None:
        return ()
    lines = value.splitlines() if isinstance(value, str) else list(value)
    patterns: list[str] = []
    for line in lines:
        text = str(line).strip()
        if not text or text.startswith("#"):
            continue
        negated = text.startswith("!")
        body = text[1:].strip() if negated else text
        body = body.lstrip("/")
        if not body:
            raise ValidationError(
                f"A watch path is only '{text}'",
                details="Name the files to watch after the '!', as in !docs/**.",
                field=_FIELD,
            )
        if len(body) > MAX_PATTERN_LENGTH:
            raise ValidationError(
                f"A watch path is longer than {MAX_PATTERN_LENGTH} characters",
                details="Use a glob such as apps/web/** instead of a long list of files.",
                field=_FIELD,
            )
        if "\\" in body or any(ord(char) < 32 or ord(char) == 127 for char in body):
            raise ValidationError(
                f"The watch path {body!r} holds a character a path cannot",
                details="Write paths with '/' and no control characters.",
                field=_FIELD,
            )
        if ".." in body.split("/"):
            raise ValidationError(
                f"The watch path {body!r} climbs with '..'",
                details="Watch paths are relative to the root of the repository; name the "
                "directory directly (apps/web/**).",
                field=_FIELD,
            )
        pattern = f"!{body}" if negated else body
        if pattern not in patterns:
            patterns.append(pattern)
    if len(patterns) > MAX_WATCH_PATHS:
        raise ValidationError(
            f"{len(patterns)} watch paths is more than the {MAX_WATCH_PATHS} allowed",
            details="Group them with globs (apps/**).",
            field=_FIELD,
        )
    return tuple(patterns)


@lru_cache(maxsize=512)
def _glob(pattern: str) -> re.Pattern[str]:
    """
    Compile a watch path glob.

    ``*`` is any run of characters but ``/``; ``**`` any run of anything,
    directories included (``**/`` also matches no directory at all); ``?`` one
    character but ``/``; ``[abc]`` and ``[!abc]`` a class. A pattern with no
    glob character, or ending in ``/``, also covers everything beneath it, so
    ``apps/web`` and ``apps/web/`` watch the directory.

    Args:
        pattern: A pattern without its leading ``!``.

    Returns:
        The compiled expression, to be matched against a whole path.
    """
    out: list[str] = []
    index = 0
    size = len(pattern)
    plain = not any(char in pattern for char in "*?[")
    while index < size:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**", index):
                index += 2
                if index < size and pattern[index] == "/":
                    index += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            close = pattern.find("]", index + 2)
            if close == -1:
                out.append(re.escape(char))
            else:
                body = pattern[index + 1 : close]
                negate = body[:1] in ("!", "^")
                members = (body[1:] if negate else body).replace("\\", "\\\\").replace("[", "\\[")
                out.append("[" + ("^" if negate else "") + members + "]")
                index = close
        else:
            out.append(re.escape(char))
        index += 1
    expression = "".join(out)
    try:
        if plain or pattern.endswith("/"):
            expression = expression.rstrip("/") if pattern.endswith("/") else expression
            return re.compile(rf"^{expression}(?:/.*)?$")
        return re.compile(rf"^{expression}$")
    except re.error:
        # A class like [z-a] is no range; it is read as the literal text.
        return re.compile(rf"^{re.escape(pattern)}$")


def watch_paths_match(patterns: Sequence[str], path: str) -> bool:
    """
    Decide whether one changed file is watched.

    Patterns are tried in order and the last one that matches decides, so a
    later ``!`` carves a hole in an earlier inclusion and a later inclusion
    puts one file of it back. A file no pattern matches is out when any
    pattern is an inclusion, and in when they are all exclusions.

    Args:
        patterns: Normalised patterns (:func:`normalize_watch_paths`).
        path: The changed file, relative to the root of the repository.

    Returns:
        True when the file is watched.
    """
    decided: bool | None = None
    for pattern in patterns:
        negated = pattern.startswith("!")
        if _glob(pattern[1:] if negated else pattern).match(path):
            decided = not negated
    if decided is not None:
        return decided
    return all(pattern.startswith("!") for pattern in patterns)


def watch_paths_trigger(patterns: Sequence[str], files: Sequence[str] | None) -> bool:
    """
    Decide whether a push is worth a deployment given what it changed.

    Args:
        patterns: Normalised patterns; none means every push deploys.
        files: The files the push changed. None when the payload does not say
            (a branch created, a list cut short, a forge that does not send
            them): the push deploys, because skipping on a guess would lose a
            deployment.

    Returns:
        True when the application should deploy.
    """
    if not patterns or files is None:
        return True
    return any(watch_paths_match(patterns, path) for path in files)


__all__ = [
    "MAX_PATTERN_LENGTH",
    "MAX_WATCH_PATHS",
    "SKIP_MARKERS",
    "normalize_watch_paths",
    "pull_request_skip_reason",
    "push_skip_reason",
    "watch_paths_match",
    "watch_paths_trigger",
]
