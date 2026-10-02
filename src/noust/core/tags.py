# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tags as versions: which ones an application follows and which is newer.

An application that follows tags (``apps.follow_tags``, a glob such as ``v*``)
deploys the tag a release or a tag push names instead of the head of a branch.
The webhooks, the update and the CLI all need the same two answers - does this
tag belong to the pattern, and is it newer than what is deployed - so they are
here once and pure, testable with strings.

Ordering is by semantic version, never by text: ``v1.10.0`` is newer than
``v1.9.0``, and a release is newer than its own release candidates. A tag that
carries no version number (``latest``, ``nightly``) has no place in the order,
and is never compared: nothing can be said of how it relates to ``v1.0.0``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase

# A letters-and-separators prefix (``v``, ``release-``), dotted numbers, an
# optional pre-release (``-rc.1``) and build metadata (``+build5``). Anchored
# at both ends on purpose, and with no hyphen inside a pre-release identifier
# (which semver allows): that is what keeps ``release-2024-10-01`` a date in a
# name, not 2024 with a pre-release called "10-01".
_VERSION = re.compile(
    r"^[A-Za-z_/-]*?"
    r"(?P<numbers>\d+(?:\.\d+)*)"
    r"(?:-(?P<pre>[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)

_TAGS_PREFIX = "refs/tags/"


@dataclass(frozen=True, order=True)
class TagVersion:
    """
    A tag's version, comparable with another's.

    The field order is the comparison order: the numbers first, then a release
    over its pre-releases, then the pre-release's identifiers, numeric ones
    below alphanumeric ones, as semantic versioning says.

    Attributes:
        numbers: The dotted numbers without trailing zeros, so ``1.2`` and
            ``1.2.0`` are one version.
        final: False for a pre-release, which is older than the release it
            precedes.
        pre: The pre-release's identifiers, each tagged ``0`` (a number) or
            ``1`` (text) so a number never has to be compared with text.
    """

    numbers: tuple[int, ...]
    final: bool
    pre: tuple[tuple[int, int | str], ...] = ()


def tag_version(tag: str) -> TagVersion | None:
    """
    Read the version a tag name carries.

    Args:
        tag: A tag name such as ``v1.10.0`` or ``release-2.0.0-rc.1``.

    Returns:
        Its version, or None when the name is not one.
    """
    found = _VERSION.match(tag or "")
    if found is None:
        return None
    numbers = [int(part) for part in found.group("numbers").split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    pre = found.group("pre")
    if pre is None:
        return TagVersion(tuple(numbers), final=True)
    identifiers = tuple((0, int(part)) if part.isdigit() else (1, part) for part in pre.split("."))
    return TagVersion(tuple(numbers), final=False, pre=identifiers)


def compare_tags(left: str, right: str) -> int | None:
    """
    Order two tags by version.

    Args:
        left: A tag name.
        right: Another.

    Returns:
        1 when ``left`` is newer, -1 when it is older, 0 when they are the same
        version, and None when either is not a version.
    """
    first, second = tag_version(left), tag_version(right)
    if first is None or second is None:
        return None
    return (first > second) - (first < second)


def tag_matches(pattern: str, tag: str) -> bool:
    """
    Tell whether a tag belongs to what an application follows.

    Args:
        pattern: A glob over tag names, as ``--follow-tags`` takes it.
        tag: A tag name.

    Returns:
        True when the whole name matches, case-sensitively as git's are.
    """
    return fnmatchcase(tag, pattern)


def newest_tag(tags: Iterable[str], pattern: str | None = None) -> str | None:
    """
    Pick the newest version among some tags.

    Args:
        tags: Tag names.
        pattern: Only the ones matching this glob; all of them when None.

    Returns:
        The newest tag, or None when none matches and is a version.
    """
    best: str | None = None
    for tag in tags:
        if pattern is not None and not tag_matches(pattern, tag):
            continue
        if tag_version(tag) is None:
            continue
        if best is None or compare_tags(tag, best) == 1:
            best = tag
    return best


def tag_from_ref(ref: object) -> str | None:
    """
    Read the tag a pushed ref names.

    Args:
        ref: The ``ref`` of a push payload: ``refs/tags/v1.3.0``, or a branch.

    Returns:
        The tag name, or None for a branch, a bare name or anything not a ref.
    """
    if not isinstance(ref, str) or not ref.startswith(_TAGS_PREFIX):
        return None
    return ref[len(_TAGS_PREFIX) :] or None


def why_not_deploy(tag: str, pattern: str, deployed: str | None) -> str | None:
    """
    Say why a tag a webhook announced is not deployed, if it is not.

    The one decision both webhooks make, so a delivery is ignored for the same
    reason whichever endpoint it reached. The sentence is what the delivery
    log shows the operator, so it names the tags involved.

    Args:
        tag: The tag announced.
        pattern: The glob the application follows.
        deployed: The newest tag already deployed, or None when the
            application has none to compare with.

    Returns:
        None when the tag should deploy; otherwise one sentence saying why not.
    """
    if not tag_matches(pattern, tag):
        return f"{tag} does not match {pattern}, the pattern this application follows"
    if tag_version(tag) is None:
        return f"{tag} is not a version number, so it cannot be ordered against what is deployed"
    if deployed is None:
        return None
    order = compare_tags(tag, deployed)
    if order is None:
        return None
    if order == 0:
        return f"{tag} is already deployed"
    if order < 0:
        return f"{tag} is older than {deployed}, which is deployed: an update never goes back"
    return None
