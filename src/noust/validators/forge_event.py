# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading an event of a code host that a central forwarded to this server.

A central that holds the GitHub App receives the deliveries and hands each server that deploys the
repository the event, already read into :mod:`noust.core.forge_events`' records. The server acts on
it as it acts on its own webhook (it queues an update), so what the event says goes into the names
of branches and tags, the commit of a deployment and the decision to deploy at all. The central is
trusted with a ``deploy`` ceiling and not with the server's shell, and the event is the text of a
repository, so it is judged here, at the one place, by an allowlist:

- **The schema is closed.** A field nobody defined is a refusal that names it; so is a missing one
  and one of the wrong type. Nothing is half read.
- **A name is a name.** A repository is ``owner/repo`` as GitHub spells it; a branch or a tag is
  made of the characters git allows in a reference that are also safe in an argument and a path;
  a commit is lower-case hexadecimal of a SHA-1 or a SHA-256; a clone address is an https address
  with no user in it and nothing a header could be split by.
- **A list is bounded**, so one event cannot be a way to make the server hold a gigabyte.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.forge_events import (
    EVENT_KINDS,
    Forge,
    PullRequestAction,
    PullRequestEvent,
    PushChange,
    PushEvent,
    TagEvent,
)
from noust.validators.source import github_repository_parts

__all__ = ["parse_forwarded_event"]

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: What a branch or a tag may be called here: git's own rules, narrowed to what is safe as an
#: argument and as part of a path. Checked as a whole (``fullmatch``).
_REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/+@-]{0,254}")
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_CLONE_URL = re.compile(r"https://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?/[A-Za-z0-9._~/+-]{1,500}")
_LOGIN = re.compile(r"[A-Za-z0-9._\[\]@+-]{0,100}")
_ASSOCIATION = re.compile(r"[A-Z_]{1,24}")

#: Most commit messages, and most files, one push may list; a delivery lists no more than
#: GitHub's own limits, which are well under these.
MAX_MESSAGES = 1000
MAX_FILES = 20000


def _fail(field: str, why: str) -> ValidationError:
    """
    Args:
        field: The field of the event.
        why: What is wrong with it.

    Returns:
        The refusal, naming the field.
    """
    return ValidationError(
        f"A forwarded event has a wrong {field}: {why}",
        field=field,
        details="The central sends events it has read itself; check that it runs the same Noust.",
    )


def _text(data: Mapping[str, Any], field: str, *, limit: int, blank: bool = False) -> str:
    """
    Args:
        data: The event.
        field: The field.
        limit: Most characters.
        blank: Whether it may be empty.

    Returns:
        The text.

    Raises:
        ValidationError: When it is not text, is too long, is empty and may not be, or has a
            control character other than a line break or a tab.
    """
    value = data[field]
    if not isinstance(value, str) or len(value) > limit or (not blank and not value):
        raise _fail(field, f"it is not text of 1 to {limit} characters")
    if _CONTROL.search(value):
        raise _fail(field, "it has a control character")
    return value


def _one_line(data: Mapping[str, Any], field: str, *, limit: int, blank: bool = False) -> str:
    """
    Args:
        data: The event.
        field: The field.
        limit: Most characters.
        blank: Whether it may be empty.

    Returns:
        The text, which has no line break.

    Raises:
        ValidationError: As :func:`_text`, and for a line break.
    """
    value = _text(data, field, limit=limit, blank=blank)
    if "\n" in value or "\r" in value or "\t" in value:
        raise _fail(field, "it is more than one line")
    return value


def _match(
    data: Mapping[str, Any], field: str, pattern: re.Pattern[str], *, blank: bool = False
) -> str:
    """
    Args:
        data: The event.
        field: The field.
        pattern: What the whole value must be.
        blank: Whether it may be empty.

    Returns:
        The value.

    Raises:
        ValidationError: When it is not a string that is all of the pattern.
    """
    value = data[field]
    if blank and value == "":
        return ""
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise _fail(field, "it is not what this field holds")
    return value


def _ref(data: Mapping[str, Any], field: str, *, blank: bool = False) -> str:
    """
    Args:
        data: The event.
        field: A branch or a tag.
        blank: Whether it may be empty.

    Returns:
        The name.

    Raises:
        ValidationError: When it is not a safe reference name.
    """
    value = _match(data, field, _REF, blank=blank)
    if value and (".." in value or "//" in value or value.endswith(("/", ".", ".lock"))):
        raise _fail(field, "it is not a reference git would accept")
    return value


def _repository(data: Mapping[str, Any], field: str = "repository", *, blank: bool = False) -> str:
    """
    Args:
        data: The event.
        field: The field.
        blank: Whether it may be empty.

    Returns:
        ``owner/repo``.

    Raises:
        ValidationError: When it is not a repository.
    """
    value = data[field]
    if blank and value == "":
        return ""
    if not isinstance(value, str):
        raise _fail(field, "it is not text")
    try:
        github_repository_parts(value)
    except ValidationError:
        raise _fail(field, "it is not owner/repo") from None
    return value


def _flag(data: Mapping[str, Any], field: str) -> bool:
    """
    Args:
        data: The event.
        field: The field.

    Returns:
        The flag.

    Raises:
        ValidationError: When it is not true or false.
    """
    value = data[field]
    if not isinstance(value, bool):
        raise _fail(field, "it is not true or false")
    return value


def _number(data: Mapping[str, Any], field: str, *, optional: bool = False) -> int | None:
    """
    Args:
        data: The event.
        field: The field.
        optional: Whether it may be null.

    Returns:
        The number, from 1 to a billion.

    Raises:
        ValidationError: When it is not a whole number in that range.
    """
    value = data[field]
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10**9:
        raise _fail(field, "it is not a whole number from 1 to a billion")
    return int(value)


def _forge(data: Mapping[str, Any]) -> Forge:
    """
    Args:
        data: The event.

    Returns:
        The host that sent it.

    Raises:
        ValidationError: When it is not one Noust knows.
    """
    value = data["forge"]
    try:
        return Forge(value)
    except ValueError:
        raise _fail("forge", "it is not a code host Noust knows") from None


def _strings(value: Any, field: str, *, most: int, limit: int, multiline: bool) -> tuple[str, ...]:
    """
    Args:
        value: A list the event carries.
        field: Its field, for the refusal.
        most: Most items.
        limit: Most characters in an item.
        multiline: Whether an item may have line breaks (a commit message may).

    Returns:
        The items.

    Raises:
        ValidationError: When it is not a bounded list of clean text.
    """
    if not isinstance(value, list) or len(value) > most:
        raise _fail(field, f"it is not a list of at most {most}")
    for item in value:
        if not isinstance(item, str) or len(item) > limit:
            raise _fail(field, "an item is not text of a reasonable length")
        if (_CONTROL.search(item) is not None) or (not multiline and "\n" in item):
            raise _fail(field, "an item has a control character")
    return tuple(value)


def _closed(
    data: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> Mapping[str, Any]:
    """
    Args:
        data: What arrived.
        required: The fields every event of the kind has.
        optional: Those it may leave out.

    Returns:
        The event as a mapping, with the optional fields it left out as None.

    Raises:
        ValidationError: When it is not an object, lacks a required field or has one nobody defined.
    """
    if not isinstance(data, dict):
        raise ValidationError(
            "A forwarded event is a JSON object", details="The central sends it as one."
        )
    unknown = sorted(set(data) - set(required) - set(optional))
    if unknown:
        raise _fail(unknown[0], "nobody defined this field")
    missing = [name for name in required if name not in data]
    if missing:
        raise _fail(missing[0], "it is missing")
    return {**dict.fromkeys(optional), **data}


def _change(data: Any) -> PushChange:
    """
    Args:
        data: The ``change`` of a push.

    Returns:
        What its commits say and touch.

    Raises:
        ValidationError: When it is not what a push carries.
    """
    if not isinstance(data, dict) or set(data) - {"messages", "files", "messages_complete"}:
        raise _fail("change", "it is not what a push carries")
    files = data.get("files")
    return PushChange(
        messages=_strings(
            data.get("messages", []), "messages", most=MAX_MESSAGES, limit=100_000, multiline=True
        ),
        files=None
        if files is None
        else _strings(files, "files", most=MAX_FILES, limit=4096, multiline=False),
        messages_complete=_flag(
            {"messages_complete": data.get("messages_complete", True)}, "messages_complete"
        ),
    )


def _push(data: Any) -> PushEvent:
    """Read a push; see :func:`parse_forwarded_event`."""
    d = _closed(
        data,
        ("forge", "repository", "clone_url", "branch", "head_sha"),
        ("installation_id", "change"),
    )
    return PushEvent(
        forge=_forge(d),
        repository=_repository(d),
        clone_url=_match(d, "clone_url", _CLONE_URL),
        branch=_ref(d, "branch"),
        head_sha=_match(d, "head_sha", _COMMIT),
        installation_id=_number(d, "installation_id", optional=True),
        change=_change(d["change"]) if d["change"] is not None else PushChange(),
    )


def _tag(data: Any) -> TagEvent:
    """Read a tag; see :func:`parse_forwarded_event`."""
    d = _closed(
        data,
        ("forge", "tag", "source"),
        ("repository", "clone_url", "sha", "installation_id", "skip"),
    )
    if d["source"] not in ("push", "release"):
        raise _fail("source", "it is neither a push nor a release")
    d = {**d, "repository": d["repository"] or "", "clone_url": d["clone_url"] or ""}
    sha = d["sha"]
    skip = d["skip"]
    if skip is not None:
        skip = _one_line(d, "skip", limit=200)
    return TagEvent(
        forge=_forge(d),
        tag=_ref(d, "tag"),
        source=d["source"],
        repository=_repository(d, blank=True),
        clone_url=_match(d, "clone_url", _CLONE_URL, blank=True),
        sha=None if sha is None else _match(d, "sha", _COMMIT),
        installation_id=_number(d, "installation_id", optional=True),
        skip=skip,
    )


def _pull_request(data: Any) -> PullRequestEvent:
    """Read a pull request; see :func:`parse_forwarded_event`."""
    d = _closed(
        data,
        (
            "forge",
            "action",
            "repository",
            "clone_url",
            "number",
            "title",
            "branch",
            "base_branch",
            "head_sha",
            "from_fork",
        ),
        ("installation_id", "author", "sender", "bot", "author_association", "head_message"),
    )
    try:
        action = PullRequestAction(d["action"])
    except ValueError:
        raise _fail("action", "it is not opened, updated or closed") from None
    d = {
        **d,
        "author": d["author"] or "",
        "sender": d["sender"] or "",
        "bot": bool(d["bot"]) if isinstance(d["bot"], bool) or d["bot"] is None else d["bot"],
        "head_message": d["head_message"] or "",
    }
    association = d["author_association"]
    return PullRequestEvent(
        forge=_forge(d),
        action=action,
        repository=_repository(d),
        clone_url=_match(d, "clone_url", _CLONE_URL, blank=True),
        number=_number(d, "number") or 0,
        title=_one_line(d, "title", limit=1000, blank=True),
        branch=_ref(d, "branch"),
        base_branch=_ref(d, "base_branch", blank=True),
        head_sha=_match(d, "head_sha", _COMMIT, blank=True),
        from_fork=_flag(d, "from_fork"),
        installation_id=_number(d, "installation_id", optional=True),
        author=_match(d, "author", _LOGIN),
        sender=_match(d, "sender", _LOGIN),
        bot=_flag(d, "bot"),
        author_association=None
        if association is None
        else _match(d, "author_association", _ASSOCIATION),
        head_message=_text(d, "head_message", limit=20000, blank=True),
    )


_READERS: dict[str, Callable[[Any], PushEvent | TagEvent | PullRequestEvent]] = {
    "push": _push,
    "tag": _tag,
    "pull_request": _pull_request,
}


def parse_forwarded_event(kind: Any, data: Any) -> PushEvent | TagEvent | PullRequestEvent:
    """
    Read an event a central forwarded, refusing anything that is not exactly one.

    Args:
        kind: ``push``, ``tag`` or ``pull_request``.
        data: The event as :func:`noust.core.forge_events.event_to_dict` wrote it.

    Returns:
        The event as a record, the one the server's own webhook makes of a delivery.

    Raises:
        ValidationError: When the kind is not one, the event is not an object, a field is missing,
            unknown or not what it holds, or a list is longer than a delivery can be. The
            refusal names the field.
    """
    if not isinstance(kind, str) or kind not in EVENT_KINDS:
        raise ValidationError(
            f"Not a kind of event: {kind!r}",
            field="kind",
            details=f"Use one of: {', '.join(EVENT_KINDS)}.",
        )
    return _READERS[kind](data)
