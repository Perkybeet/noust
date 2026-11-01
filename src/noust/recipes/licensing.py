# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which licences are free software, so that a recipe whose software is not says so wherever it is shown.

A recipe describes how to run software somebody else publishes; its ``license`` is the project's.
Some projects are source-available (n8n's Sustainable Use License, a Business Source License, the
SSPL) or open core (the repository holds some files under a proprietary licence): an operator may
run them for their own use, and the catalogue says what they are instead of letting the list read as
if everything in it were free software. This module is the one place that decides.

The decision is made on the SPDX expression the recipe declares: a licence of the families the Open
Source Initiative and the Free Software Foundation approve is free, anything else (a
``LicenseRef-...`` the project wrote, BUSL, FSL, SSPL, Elastic, PolyForm) is not, and an unknown
identifier is never read in the recipe's favour.
"""

from __future__ import annotations

import re

#: SPDX identifiers (without ``-only``, ``-or-later`` or ``+``) of licences approved as open source
#: by the OSI or as free software by the FSF. A licence that is not here is not called free.
FREE_LICENSES = frozenset(
    {
        "0BSD",
        "AFL-3.0",
        "AGPL-1.0",
        "AGPL-3.0",
        "APSL-2.0",
        "Apache-1.1",
        "Apache-2.0",
        "Artistic-1.0",
        "Artistic-2.0",
        "BSD-1-Clause",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "BSD-4-Clause",
        "BSL-1.0",
        "CAL-1.0",
        "CC0-1.0",
        "CDDL-1.0",
        "CECILL-2.1",
        "ECL-2.0",
        "EPL-1.0",
        "EPL-2.0",
        "EUPL-1.1",
        "EUPL-1.2",
        "GPL-2.0",
        "GPL-3.0",
        "HPND",
        "ISC",
        "LGPL-2.0",
        "LGPL-2.1",
        "LGPL-3.0",
        "MIT",
        "MIT-0",
        "MPL-1.1",
        "MPL-2.0",
        "MS-PL",
        "MS-RL",
        "NCSA",
        "OFL-1.1",
        "OSL-3.0",
        "PostgreSQL",
        "Python-2.0",
        "Ruby",
        "UPL-1.0",
        "Unlicense",
        "WTFPL",
        "X11",
        "Zlib",
    }
)

_SUFFIX = re.compile(r"(?:-only|-or-later|\+)$")


def _is_free_identifier(identifier: str) -> bool:
    """
    Tell whether one SPDX identifier names a free licence.

    Args:
        identifier: ``MIT``, ``GPL-3.0-or-later``, ``LicenseRef-x``.

    Returns:
        True for a licence of :data:`FREE_LICENSES`, with or without a version qualifier.
    """
    return _SUFFIX.sub("", identifier) in FREE_LICENSES


def _is_free_conjunction(text: str) -> bool:
    """
    Tell whether every licence a conjunction asks for is free.

    Args:
        text: ``A AND B WITH exception``, with no ``OR`` in it.

    Returns:
        True when each of the licences is, an exception being no licence of its own.
    """
    parts = [part.strip() for part in re.split(r"\s+AND\s+", text.strip())]
    return bool(parts) and all(
        _is_free_identifier(re.split(r"\s+WITH\s+", part)[0].strip()) for part in parts
    )


def is_free_software(expression: str | None) -> bool:
    """
    Tell whether a recipe's licence is a free software licence.

    An ``OR`` is the operator's choice, so an expression is free when any alternative is (Stalwart's
    ``AGPL-3.0-only OR LicenseRef-Stalwart-Enterprise-v2`` is); an ``AND`` needs all of its licences
    to be (an open core ``MIT AND LicenseRef-Enterprise`` is not).

    Args:
        expression: The recipe's ``license``, an SPDX expression, or None.

    Returns:
        True only when the expression is satisfied by free licences; False for a source-available,
        open core or unknown one, and for no licence at all.
    """
    if not expression or not expression.strip():
        return False
    alternatives = re.split(r"\s+OR\s+", expression.strip())
    return any(_is_free_conjunction(alternative) for alternative in alternatives)
