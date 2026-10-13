# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server written down: plan and apply (J-15 of the 3.4 checklist).

- :mod:`~noust.manifest.schema`: the closed schema and its parser.
- :mod:`~noust.manifest.plan`: what applying a manifest would change, read
  through the server's API and printing no value.
- :mod:`~noust.manifest.apply`: making a server match it, one API call at a time.

It is a client of the API, never of the managers (rule 3): the role of the
token, the team it is bound to, sudo mode and the audit trail are the
server's, and the same engine serves ``noust manifest``, a pipeline and the
console's preview.
"""

import os
from pathlib import Path

from noust.manifest.apply import Applier, ApplyResult, Outcome
from noust.manifest.plan import Action, Plan, RefusingResolver, Resolver, Transport, plan
from noust.manifest.schema import Manifest, ManifestError, Secret, parse


class EnvironmentResolver:
    """
    Read variables where the manifest is applied from: its environment and its files.

    Args:
        environ: The environment; the process's by default.
        base: Directory a relative ``from_file`` is read from; the current one by default.
    """

    available = True

    def __init__(self, environ: dict[str, str] | None = None, base: Path | None = None) -> None:
        self._environ = dict(os.environ) if environ is None else environ
        self._base = base or Path.cwd()

    def resolve(self, secret: Secret) -> str:
        """
        Args:
            secret: A value read where the manifest is applied from.

        Returns:
            The value.

        Raises:
            ManifestError: When the variable is not set or the file cannot be read.
        """
        if secret.kind == "from_env":
            value = self._environ.get(secret.source)
            if value is None:
                raise ManifestError(
                    f"The variable {secret.source} is not set here",
                    details="Export it (or give it to the pipeline's step) before applying.",
                )
            return value
        path = Path(secret.source).expanduser()
        path = path if path.is_absolute() else self._base / path
        try:
            return path.read_text(encoding="utf-8").rstrip("\n")
        except OSError as exc:
            raise ManifestError(f"Cannot read {path}", details=f"{exc.strerror or exc}.") from exc


def load(path: Path) -> Manifest:
    """
    Read a manifest from a file.

    Args:
        path: The file.

    Returns:
        The manifest.

    Raises:
        ManifestError: When the file cannot be read or is not a valid manifest.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"Cannot read {path}", details=f"{exc.strerror or exc}.") from exc
    return parse(text)


__all__ = [
    "Action",
    "Applier",
    "ApplyResult",
    "EnvironmentResolver",
    "Manifest",
    "ManifestError",
    "Outcome",
    "Plan",
    "RefusingResolver",
    "Resolver",
    "Secret",
    "Transport",
    "load",
    "parse",
    "plan",
]
