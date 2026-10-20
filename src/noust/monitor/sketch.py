# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How many different visitors a site had, without keeping who they were.

A count of distinct visitors cannot be added hour by hour: the person who came at nine and again at
ten is one visitor of the day, not two. Keeping every address would answer it and would keep what
the traffic digest has refused to keep (``traffic_digest``: a client is its network, never its
address). A sketch answers it and keeps neither: each visitor is reduced to a hash, and what is
stored is either a short list of hashes (up to :data:`EXACT_LIMIT` visitors, so a small site's
count is exact) or, past that, a HyperLogLog (the highest run of leading zeros each of
:data:`REGISTERS` slots has seen), which says how many there were to within a few percent and from
which no address can be recovered. Two sketches are merged into the sketch of their union, so a day
is the merge of its hours.

The hash is 48 bits of BLAKE2b, enough for a site of millions of visitors a day to count within the
sketch's own error, and short enough that the exact list is 6 bytes a visitor.
"""

from __future__ import annotations

import hashlib
import math

#: Registers are ``2 ** PRECISION``: 512 of them, about 4.6 percent of error past the exact range.
PRECISION = 9
REGISTERS = 1 << PRECISION

#: The most visitors kept as hashes before the sketch becomes a HyperLogLog.
EXACT_LIMIT = 100

_HASH_BITS = 48
_RANK_BITS = _HASH_BITS - PRECISION
_ALPHA = 0.7213 / (1 + 1.079 / REGISTERS)

_EXACT = b"E"
_DENSE = b"H"


def _hash(data: bytes) -> int:
    """
    Args:
        data: A visitor, as the digest names one.

    Returns:
        :data:`_HASH_BITS` bits of its BLAKE2b.
    """
    digest = hashlib.blake2b(data, digest_size=_HASH_BITS // 8).digest()
    return int.from_bytes(digest, "big")


class Sketch:
    """A set of visitors, counted and merged but never listed."""

    def __init__(self) -> None:
        self._exact: set[int] = set()
        #: The registers once there are too many visitors to list; None while they are listed.
        self._registers: bytearray | None = None

    def add(self, data: bytes) -> None:
        """
        Count a visitor.

        Args:
            data: What identifies the visitor (the address and the agent, joined).
        """
        self._add_hash(_hash(data))

    def _add_hash(self, value: int) -> None:
        """
        Args:
            value: A visitor's hash.
        """
        registers = self._registers
        if registers is None:
            self._exact.add(value)
            if len(self._exact) > EXACT_LIMIT:
                self._densify()
            return
        index = value >> _RANK_BITS
        rest = value & ((1 << _RANK_BITS) - 1)
        rank = _RANK_BITS - rest.bit_length() + 1
        if rank > registers[index]:
            registers[index] = rank

    def _densify(self) -> bytearray:
        """
        Turn the exact list into registers, once there are too many to list.

        Returns:
            The registers.
        """
        if self._registers is not None:
            return self._registers
        hashes, self._exact = self._exact, set()
        self._registers = bytearray(REGISTERS)
        for value in hashes:
            self._add_hash(value)
        return self._registers

    def merge(self, other: Sketch) -> None:
        """
        Make this the union of itself and another sketch.

        Args:
            other: The visitors to add.
        """
        if other._registers is None:
            for value in other._exact:
                self._add_hash(value)
            return
        registers = self._densify()
        for index, rank in enumerate(other._registers):
            if rank > registers[index]:
                registers[index] = rank

    def estimate(self) -> int:
        """
        Returns:
            How many different visitors were added: exact up to :data:`EXACT_LIMIT`.
        """
        registers = self._registers
        if registers is None:
            return len(self._exact)
        zeros = registers.count(0)
        raw = _ALPHA * REGISTERS * REGISTERS / sum(2.0**-rank for rank in registers)
        if raw <= 2.5 * REGISTERS and zeros:
            return round(REGISTERS * math.log(REGISTERS / zeros))
        return round(raw)

    def to_bytes(self) -> bytes:
        """Returns: The sketch as the store keeps it."""
        if self._registers is None:
            return _EXACT + b"".join(
                value.to_bytes(_HASH_BITS // 8, "big") for value in sorted(self._exact)
            )
        return _DENSE + bytes(self._registers)

    @classmethod
    def from_bytes(cls, blob: bytes | None) -> Sketch:
        """
        Args:
            blob: What :meth:`to_bytes` wrote, or None.

        Returns:
            The sketch; an empty one for anything that is not a sketch.
        """
        sketch = cls()
        if not blob:
            return sketch
        kind, body = blob[:1], blob[1:]
        width = _HASH_BITS // 8
        if kind == _EXACT and len(body) % width == 0 and len(body) // width <= EXACT_LIMIT:
            sketch._exact = {
                int.from_bytes(body[offset : offset + width], "big")
                for offset in range(0, len(body), width)
            }
        elif kind == _DENSE and len(body) == REGISTERS:
            sketch._registers = bytearray(body)
        return sketch
