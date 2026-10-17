# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Read a Docker Compose document the way Docker Compose reads it.

PyYAML implements YAML 1.1, and Compose does not. Two differences change what a document says:

- ``22222:22`` is a number in YAML 1.1, in base 60 (22222 * 60 + 22 = 1333342), and a string to
  Compose. Any ``host:container`` port mapping whose container port is under 60 and written
  without quotes (``2222:22``, a Gitea's SSH port) became a number no port could be, and a
  document converted and written back from it was refused by Compose itself ("Invalid
  containerPort: 1333342"). The Compose documentation tells people to quote such ports; most do not.
- ``yes``, ``no``, ``on`` and ``off`` are booleans in YAML 1.1 and strings to Compose, so
  ``restart: no`` was read as ``False`` and written back as ``restart: false``.

Everything that reads a Compose file (the deployer, the guard, the pasted-document analysis, the
template converter) reads it through :func:`load_compose`, so they all see the same document Compose
does. Writing needs nothing special: PyYAML quotes a string that its own reading would take for a
number or a boolean, which Compose accepts as the string it is.
"""

from __future__ import annotations

import re
from typing import IO, Any

import yaml

__all__ = ["ComposeLoader", "load_compose"]

#: Only ``true`` and ``false`` (in the spellings YAML 1.2 knows) are booleans.
_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")

#: PyYAML's integers without the base 60 form (``1:30``).
_INT = re.compile(
    r"^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+)$"
)

#: PyYAML's floats without the base 60 form (``1:30.5``).
_FLOAT = re.compile(
    r"^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?"
    r"|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)"
    r"|\.(?:nan|NaN|NAN))$"
)

_REPLACED = frozenset(
    {"tag:yaml.org,2002:bool", "tag:yaml.org,2002:int", "tag:yaml.org,2002:float"}
)


class ComposeLoader(yaml.SafeLoader):
    """A safe loader that reads numbers and booleans as Docker Compose does."""


# A copy of the safe loader's table without the three kinds above, so the base class (read by
# the rest of the program for configuration and manifests) keeps YAML 1.1.
ComposeLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in entries if tag not in _REPLACED]
    for first, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
ComposeLoader.add_implicit_resolver("tag:yaml.org,2002:bool", _BOOL, list("tTfF"))
ComposeLoader.add_implicit_resolver("tag:yaml.org,2002:int", _INT, list("-+0123456789"))
ComposeLoader.add_implicit_resolver("tag:yaml.org,2002:float", _FLOAT, list("-+0123456789."))


def load_compose(source: str | bytes | IO[str] | IO[bytes]) -> Any:
    """
    Parse a Compose document.

    Args:
        source: The text, or an open file.

    Returns:
        What the document holds: ``ports: [22222:22]`` is a list of one string, and
        ``restart: no`` a string.

    Raises:
        yaml.YAMLError: It is not YAML.
    """
    # A SafeLoader subclass: it builds plain data and nothing else.
    return yaml.load(source, Loader=ComposeLoader)  # noqa: S506
