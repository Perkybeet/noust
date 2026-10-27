# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What one entry of ``security.allowed_sources`` may be.

The list says where code may be deployed from (:mod:`noust.core.ens.origins` reads it). An entry
is compared as text against where a source is: a host, a host and an organisation, or a host, an
organisation and a repository, or one of the words that stand for a place that has no host. An
entry that is anything else cannot match and looks like protection that is not there, and one that
matches more than it seems to is worse, so a mistake is refused where it is saved, with the shape
that works.

The case that made this a rule: ``'[github.com/Arenna-Labs, local]'`` was saved as the two
entries ``[github.com/Arenna-Labs`` and ``local]``, and from then on no repository of the
organisation matched and every deployment was refused.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from noust.core.exceptions import ValidationError
from noust.validators.domain import is_valid_domain

#: The word for directories on this server.
LOCAL = "local"

#: The word for code that arrives with ``git push`` to this server's own repositories.
PUSH = "push"

#: The word for an archive somebody uploaded.
UPLOAD = "upload"

#: The word for the code Noust prepares from a recipe of the catalogue.
RECIPES = "recipes"

#: The words an entry may be, each a place that has no host of its own.
KEYWORDS = (LOCAL, PUSH, UPLOAD, RECIPES)

#: The longest entry. Longer than any host, organisation and repository joined.
MAX_ENTRY_LENGTH = 300

#: The most segments after the host: an organisation, subgroups (GitLab) and a repository.
MAX_SEGMENTS = 8

#: What may be typed before the host, and is dropped: a URL pasted whole still names its place.
_SCHEMES = ("https", "http", "ssh", "git")

#: A segment: what GitHub, GitLab and Gitea allow in an organisation or a repository name.
_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")

#: The shapes an entry takes, said once for every error.
SHAPES = (
    "Write a host (git.example.com, or *.example.com for every subdomain), an organisation "
    "(github.com/acme), a repository (github.com/acme/shop), or one of the words "
    f"{', '.join(KEYWORDS)}."
)


@dataclass(frozen=True)
class AllowedSource:
    """
    One entry, read.

    Attributes:
        keyword: ``local``, ``push``, ``upload`` or ``recipes``; None for a host.
        host: The host, lower-case, possibly ``*.domain``; empty for a keyword.
        path: The organisation, subgroups and repository, without ``.git``; empty for a keyword
            or a whole host.
    """

    keyword: str | None
    host: str
    path: tuple[str, ...]

    @property
    def text(self) -> str:
        """The entry in the one form it is stored in."""
        if self.keyword is not None:
            return self.keyword
        return "/".join((self.host, *self.path))


def _refuse(entry: object, why: str) -> ValidationError:
    """
    Build the refusal of an entry.

    Args:
        entry: What was given.
        why: What is wrong with it.

    Returns:
        The error, naming the entry and the shapes that work.
    """
    shown = entry if isinstance(entry, str) and len(entry) <= 80 else "that"
    return ValidationError(
        f"{shown!r} cannot be an allowed source: {why}",
        details=SHAPES,
        field="security.allowed_sources",
    )


def parse_allowed_source(entry: object) -> AllowedSource:
    """
    Read one entry of ``security.allowed_sources``.

    Args:
        entry: The candidate.

    Returns:
        The entry, with the host lower-cased, a scheme and a ``.git`` suffix dropped.

    Raises:
        ValidationError: It is not a keyword, a host, an organisation or a repository.
    """
    if not isinstance(entry, str):
        raise _refuse(entry, "an entry is text")
    text = entry.strip()
    if not text:
        raise _refuse(entry, "it is empty")
    if len(text) > MAX_ENTRY_LENGTH:
        raise _refuse(entry, f"it is longer than {MAX_ENTRY_LENGTH} characters")
    if not text.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in text):
        raise _refuse(entry, "it has a space, a control or a character that is not plain ASCII")
    if text.lower() in KEYWORDS:
        return AllowedSource(text.lower(), "", ())
    scheme, separator, rest = text.partition("://")
    if separator:
        if scheme.lower() not in _SCHEMES:
            raise _refuse(entry, f"{scheme!r} is not a scheme a repository is reached by")
        text = rest
    if "@" in text:
        raise _refuse(entry, "it names a user or a token, and the list compares places, not people")
    if ":" in text:
        raise _refuse(
            entry,
            "a port, or the user@host:path form, is not compared; only the host and its path are",
        )
    if "[" in text or "]" in text:
        raise _refuse(
            entry, "brackets are not part of an entry (a list is written a,b or [a, b] as a whole)"
        )
    if any(char in text for char in "?#%\\'\"`$;|&<>(){}!~=,^+"):
        raise _refuse(entry, "it has a character no host, organisation or repository has")
    segments = text.split("/")
    if len(segments) > 1 and segments[-1] == "":
        segments.pop()
    host, *path = segments
    return AllowedSource(None, _host(entry, host), _path(entry, path))


def _host(entry: object, host: str) -> str:
    """
    Check the host of an entry.

    Args:
        entry: The whole entry, for the error.
        host: Its first segment.

    Returns:
        The host, lower-case and without a trailing dot.

    Raises:
        ValidationError: It is not a name, an address, or ``*.`` and a domain.
    """
    name = host.lower().rstrip(".")
    wildcard = name.startswith("*.")
    base = name[2:] if wildcard else name
    try:
        ipaddress.IPv4Address(name)
    except ValueError:
        pass
    else:
        return name
    valid, reason = is_valid_domain(base)
    if not valid or "*" in base:
        raise _refuse(entry, f"{host!r} is not a host name ({reason or 'a * only starts a name'})")
    if wildcard and "." not in base:
        raise _refuse(entry, f"{host!r} would allow every {base!r}; a wildcard needs a domain")
    return name


def _path(entry: object, segments: list[str]) -> tuple[str, ...]:
    """
    Check the organisation, subgroups and repository of an entry.

    Args:
        entry: The whole entry, for the error.
        segments: What follows the host.

    Returns:
        The segments, the last one without ``.git``.

    Raises:
        ValidationError: A segment is empty, ``.``, ``..`` or has a character no name has, or
            there are too many.
    """
    if len(segments) > MAX_SEGMENTS:
        raise _refuse(entry, f"it has more than {MAX_SEGMENTS} parts after the host")
    cleaned: list[str] = []
    for index, segment in enumerate(segments):
        if index == len(segments) - 1:
            segment = segment.removesuffix(".git")
        if segment in ("", ".", "..") or _SEGMENT.fullmatch(segment) is None:
            raise _refuse(entry, f"{segment!r} is not the name of an organisation or repository")
        cleaned.append(segment)
    return tuple(cleaned)


def check_allowed_source(entry: object) -> str:
    """
    Accept one entry of ``security.allowed_sources`` and give it in the form it is stored in.

    Args:
        entry: The candidate.

    Returns:
        The entry: a keyword, or ``host``, ``host/org`` or ``host/org/repo``.

    Raises:
        ValidationError: As :func:`parse_allowed_source`.
    """
    return parse_allowed_source(entry).text
