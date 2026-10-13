# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The variables Noust gives every application about its own deployment.

``NOUST_URL`` and ``NOUST_DOMAIN`` say where it answers, ``NOUST_BRANCH`` and
``NOUST_COMMIT`` what it was built from, ``NOUST_RELEASE`` which release is
running. The running process gets them from a file beside the release
(:data:`RELEASE_ENV_NAME`), so rolling back to an older release also gives the
process that release's commit. Where the process listens is not here: the unit
sets ``PORT`` itself (each instance its own, when an update runs without
downtime) and, for the Node frameworks, ``HOST``; ``{{app.PORT}}`` and
``{{app.HOST}}`` name the same facts in a value.

The build is given the same, except the commit and the release: both change on
every deploy, and a build that depends on them cannot be reused or compared
with the last one. This is Coolify's decision about ``SOURCE_COMMIT``, kept on
purpose. A build that does need the commit asks for it by name, with a variable
whose value is ``{{app.COMMIT}}`` and whose audience is ``build``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: The file a release holds its predefined variables in. Loaded by the unit
#: with ``EnvironmentFile=-``; not secret, so it is world-readable.
RELEASE_ENV_NAME = ".noust-env"

#: ``{{app.X}}`` field to environment variable.
ENV_NAMES: dict[str, str] = {
    "URL": "NOUST_URL",
    "DOMAIN": "NOUST_DOMAIN",
    "BRANCH": "NOUST_BRANCH",
    "COMMIT": "NOUST_COMMIT",
    "RELEASE": "NOUST_RELEASE",
}

#: The names an operator cannot give a variable of their own: Noust writes them.
RESERVED: frozenset[str] = frozenset(ENV_NAMES.values())

#: What a build is not given, because it changes on every deploy.
BUILD_EXCLUDED: frozenset[str] = frozenset({"NOUST_COMMIT", "NOUST_RELEASE"})

#: Where Noust tells a process to listen: nginx proxies to loopback.
LOOPBACK = "127.0.0.1"


@dataclass(frozen=True)
class Deployment:
    """
    What is known about a deployment when its variables are composed.

    Attributes:
        domain: The application's domain.
        ssl: Whether it answers over HTTPS.
        branch: The branch it follows, if any.
        commit: The commit being deployed or running (the short form).
        release: The release id on the release layout.
        port: The port the process listens on.
    """

    domain: str
    ssl: bool = True
    branch: str | None = None
    commit: str | None = None
    release: str | None = None
    port: int | None = None

    @property
    def url(self) -> str:
        """Where the application answers."""
        return f"{'https' if self.ssl else 'http'}://{self.domain}"

    def fields(self) -> dict[str, str]:
        """
        Returns:
            ``{"URL": ..., ...}`` for every field that has a value; the ones
            nothing is known for (a static site has no port) are left out.
        """
        known = {
            "URL": self.url,
            "DOMAIN": self.domain,
            "BRANCH": self.branch,
            "COMMIT": self.commit,
            "RELEASE": self.release,
            "PORT": None if self.port is None else str(self.port),
            "HOST": LOOPBACK,
        }
        return {name: value for name, value in known.items() if value}

    def environment(self, *, build: bool = False) -> dict[str, str]:
        """
        Args:
            build: The variables for an install or a build, which leave out
                what changes on every deploy.

        Returns:
            ``NOUST_*`` to value.
        """
        values = {
            env: self.fields()[name] for name, env in ENV_NAMES.items() if name in self.fields()
        }
        if build:
            return {name: value for name, value in values.items() if name not in BUILD_EXCLUDED}
        return values


def reserved_in(names: Mapping[str, object] | set[str]) -> list[str]:
    """
    Args:
        names: Variable names.

    Returns:
        The ones that are Noust's, sorted.
    """
    return sorted(set(names) & RESERVED)
