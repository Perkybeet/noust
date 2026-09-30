# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where code may be deployed from (ENS G18: op.exp.5, mp.sw.1).

A deploy is the execution of code from somewhere else as this server. The
operator can name where that code may come from in ``security.allowed_sources``,
a list of:

- a host: ``git.example.com`` (any repository there), or ``*.example.com``
  (any subdomain);
- an organisation on a host: ``github.com/acme`` (every repository of
  ``acme``, not of ``acme-evil``);
- a repository: ``github.com/acme/shop``;
- ``local``: directories on this server, which are otherwise refused once a
  list is set, because a path says nothing about where its code came from.

An empty list allows everything, as every earlier version did. The list is a
security setting (``security.*``, changed by the ``security`` role), so an
administrator cannot allow a repository of their own to themselves.

The check runs where every source is classified before anything is fetched
(:func:`noust.managers.source_manager._validate_source_keeping_credentials`),
so a deploy, an update, a preview and a webhook all meet it (rule 4). A
refusal is an audit event, ``apps.source.denied``, naming the source.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from noust.core.exceptions import SourceError

#: The configuration key holding the list.
CONFIG_KEY = "security.allowed_sources"

#: The entry that allows directories on this server.
LOCAL = "local"


class OriginNotAllowed(SourceError):
    """A source is outside the origins this server allows code from."""


@dataclass(frozen=True)
class Origin:
    """
    Where a source is, without its credential.

    Attributes:
        kind: ``git``, ``archive`` or ``local``.
        host: The host, lower-case; None for a local directory.
        path: The path's segments on that host (organisation, repository),
            without a ``.git`` suffix; empty for a local directory.
        display: ``host/org/repo``, or the directory, for messages.
    """

    kind: str
    host: str | None
    path: tuple[str, ...]
    display: str


def _strip_credentials(url: str) -> str:
    """
    Drop the userinfo of a URL, whatever scheme.

    Args:
        url: A URL, possibly ``https://user:token@host/...``.

    Returns:
        The URL without ``user:token@``.
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    authority, slash, tail = rest.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    return f"{scheme}://{authority}{slash}{tail}"


def describe_origin(source: str, kind: str | None = None) -> Origin:
    """
    Say where a source is.

    Args:
        source: The source as given or stored: a git URL (HTTPS, SSH or the
            ``user@host:path`` form), an archive URL, a local path, or GitHub's
            ``owner/repo`` shorthand.
        kind: ``git``, ``archive`` or ``local`` when the caller already
            classified it; guessed from the text otherwise.

    Returns:
        The origin.
    """
    from noust.validators.source import expand_github_shorthand, is_local_path, parse_git_url

    text = (source or "").strip().split("#", 1)[0]
    if kind is None:
        kind = "local" if is_local_path(text) else "git"
    if kind == "local":
        return Origin("local", None, (), text)
    text = _strip_credentials(expand_github_shorthand(text))
    host = ""
    segments: tuple[str, ...] = ()
    if kind == "git":
        parsed = parse_git_url(text)
        host = str(parsed.get("host") or "")
        segments = tuple(part for part in (parsed.get("owner"), parsed.get("repo")) if part)
    if not host:
        split = urlsplit(text)
        host = split.hostname or ""
        segments = tuple(part for part in split.path.split("/") if part)
    host = host.lower().rstrip(".")
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    host = host.split(":", 1)[0]
    segments = tuple(segment.removesuffix(".git") for segment in segments)
    display = "/".join((host, *segments)) if host else text
    return Origin(kind, host or None, segments, display)


def _pattern_parts(pattern: str) -> tuple[str, tuple[str, ...]]:
    """
    Split an allowed-origin entry into its host and path.

    Args:
        pattern: ``host[/org[/repo]]``, optionally with a scheme.

    Returns:
        The host pattern, lower-case, and the path segments.
    """
    text = pattern.strip()
    if "://" in text:
        text = _strip_credentials(text).split("://", 1)[1]
    parts = [part for part in text.split("/") if part]
    if not parts:
        return "", ()
    host = parts[0].lower().split(":", 1)[0].rstrip(".")
    return host, tuple(part.removesuffix(".git").lower() for part in parts[1:])


def matches(pattern: str, origin: Origin) -> bool:
    """
    Report whether one allowed-origin entry covers a source.

    Args:
        pattern: An entry of ``security.allowed_sources``.
        origin: Where the source is.

    Returns:
        True when the entry allows it. Hosts match exactly, or ``*.domain``
        any subdomain of ``domain``; path segments match whole, so
        ``github.com/acme`` never allows ``github.com/acme-evil``.
    """
    entry = pattern.strip()
    if entry.lower() == LOCAL:
        return origin.kind == "local"
    if origin.host is None:
        return False
    host, path = _pattern_parts(entry)
    if not host:
        return False
    if host.startswith("*."):
        if not origin.host.endswith(host[1:]):
            return False
    elif origin.host != host:
        return False
    if len(path) > len(origin.path):
        return False
    return all(want == have.lower() for want, have in zip(path, origin.path, strict=False))


def allowed_sources(config: Any | None = None) -> list[str]:
    """
    Read the list this server allows code from.

    Args:
        config: The configuration; the process-wide one by default.

    Returns:
        The entries, stripped; empty when none is set (anything allowed).
    """
    if config is None:
        from noust.core.config import Config

        config = Config()
    get = getattr(config, "get", None)
    raw = get(CONFIG_KEY, []) if callable(get) else []
    if isinstance(raw, str):
        raw = [part for part in raw.replace(",", " ").split() if part]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(entry).strip() for entry in raw if str(entry).strip()]


def _record_refusal(display: str, allowed: list[str]) -> None:
    """
    Put a refused source on record.

    Args:
        display: Where it was, without credentials.
        allowed: The list that refused it.
    """
    from noust.core.audit import record

    record(
        "apps.source.denied",
        target=f"source:{display}",
        outcome="denied",
        details={"reason": "origin not allowed", "allowed_sources": allowed},
    )


def check_origin(
    source: str, allowed: Sequence[str] | None = None, kind: str | None = None
) -> None:
    """
    Refuse a source outside the allowed origins.

    Args:
        source: The source as given or stored.
        allowed: The allowed entries; read from the configuration when None.
        kind: ``git``, ``archive`` or ``local`` when already classified.

    Raises:
        OriginNotAllowed: A list is set and no entry covers the source.
    """
    entries = list(allowed) if allowed is not None else allowed_sources()
    if not entries:
        return
    origin = describe_origin(source, kind)
    if any(matches(entry, origin) for entry in entries):
        return
    _record_refusal(origin.display, entries)
    raise OriginNotAllowed(
        f"Code from {origin.display} is not allowed on this server",
        details=(
            f"{CONFIG_KEY} allows: {', '.join(entries)}. Deploy from one of those, or ask the "
            f"security officer to add it: noust config set {CONFIG_KEY} "
            f"'[{', '.join([*entries, origin.display])}]'."
        ),
    )
