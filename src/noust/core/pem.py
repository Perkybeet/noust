# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading PEM blocks out of what openssl prints.

openssl is asked for a key and a certificate (or a request) on its standard
output, so that the files can be created with the right mode through the
filesystem seam and no stray ``privkey.pem`` lands in a working directory. This
is the one place that takes the blocks apart again.
"""

from __future__ import annotations

import re


def pem_block(text: str, label: str) -> str | None:
    """
    Extract the first PEM block whose label ends with the given words.

    openssl varies the exact label with the key algorithm (``PRIVATE KEY``,
    ``RSA PRIVATE KEY``, ``EC PRIVATE KEY``), so the match is by suffix.

    Args:
        text: Text that may contain PEM blocks.
        label: Final words of the block label, such as ``PRIVATE KEY``.

    Returns:
        The block including its markers, ending in one newline, or None when
        no such block is present.
    """
    escaped = re.escape(label)
    pattern = re.compile(
        rf"-----BEGIN (?:[A-Z0-9]+ )*{escaped}-----\n.*?\n-----END (?:[A-Z0-9]+ )*{escaped}-----",
        re.DOTALL,
    )
    match = pattern.search(text)
    return f"{match.group(0)}\n" if match else None
