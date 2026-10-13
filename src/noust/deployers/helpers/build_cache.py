# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's build cache: see how much it holds, empty it.

The cache is what the package managers keep between builds so the next one
does not download everything again (``/var/cache/noust/build/<app>``, owned by
the account that builds, one per application and never shared). It is also what
a stale or poisoned install hides behind, so an operator can empty it, and one
build can ignore it (``noust update --no-cache``, ``build.cache: false``).

This is the one place that reads and empties it for the CLI and the API
(rule 3); the deployers ask :func:`~noust.deployers.helpers.sandbox.clear_cache_dir`
for the same thing at the start of a build that skips the cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.core import audit
from noust.core.exceptions import NoustError
from noust.core.store import NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.helpers import sandbox as build_sandbox


@dataclass(frozen=True)
class BuildCache:
    """
    An application's build cache.

    Attributes:
        domain: The application.
        path: Where it is kept.
        exists: Whether the directory exists (an application that never built
            in the sandbox has none).
        size_bytes: What it holds.
    """

    domain: str
    path: Path
    exists: bool
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the cache for the API and ``--json``.

        Returns:
            The fields, with the path as text.
        """
        return {
            "domain": self.domain,
            "path": str(self.path),
            "exists": self.exists,
            "size_bytes": self.size_bytes,
        }


def _known(domain: str, store: NoustStore) -> None:
    """
    Require an application behind a domain.

    Args:
        domain: A validated domain.
        store: The store.

    Raises:
        NoustError: It is unknown.
    """
    if store.get_app(domain) is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )


def describe(domain: str, *, store: NoustStore | None = None) -> BuildCache:
    """
    Say what an application's build cache holds.

    Args:
        domain: A validated domain.
        store: The store; the process-wide one when None.

    Returns:
        The cache.

    Raises:
        NoustError: The application is unknown.
    """
    target = store or get_store()
    _known(domain, target)
    name = domain_to_app_name(domain)
    path = build_sandbox.cache_dir_for(name)
    return BuildCache(
        domain=domain,
        path=path,
        exists=path.is_dir(),
        size_bytes=build_sandbox.cache_size(name),
    )


def clear(domain: str, *, store: NoustStore | None = None) -> BuildCache:
    """
    Empty an application's build cache; the next build fills it again.

    Args:
        domain: A validated domain.
        store: The store; the process-wide one when None.

    Returns:
        The cache as it was before it was emptied (``size_bytes`` is what was
        freed).

    Raises:
        NoustError: The application is unknown.
    """
    target = store or get_store()
    before = describe(domain, store=target)
    freed = build_sandbox.clear_cache_dir(domain_to_app_name(domain))
    audit.record(
        "apps.build_cache",
        target=f"app:{domain}",
        details={"action": "clear", "freed_bytes": freed},
    )
    return before


__all__ = ["BuildCache", "clear", "describe"]
