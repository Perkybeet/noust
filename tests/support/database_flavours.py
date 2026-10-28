# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_flavours.py`` that other tests build on."""

from __future__ import annotations


def colons(*fingerprints: str) -> str:
    """
    Render what ``gpg --show-keys --with-colons`` prints for some keys.

    Args:
        *fingerprints: One primary fingerprint per key.

    Returns:
        The output, with a subkey after each primary key.
    """
    lines: list[str] = []
    for fingerprint in fingerprints:
        lines += [
            f"pub:-:4096:1:{fingerprint[-16:]}:1318537154:::-:::scSC::::::23::0:",
            f"fpr:::::::::{fingerprint}:",
            "uid:-::::1483540543::690A::Signing Key::::::::::0:",
            "sub:-:4096:1:AAAABBBBCCCCDDDD:1318537154::::::e::::::23:",
            "fpr:::::::::0000000000000000000000000000AAAABBBBCCCCDDDD:",
        ]
    return "\n".join(lines) + "\n"
