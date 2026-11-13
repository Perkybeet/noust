# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A ranking of short texts by the words of a question, in plain Python (item 171).

The assistant looks up two things: which operation of the API does what a person wants, and which
passage of the documentation to cite. Both are a few thousand short texts and a question of a few
words, which BM25 ranks well enough without a vector database, a model or a dependency. A word is
lowered and given a light English stem, so ``restarting`` finds ``restart`` and ``applications``
finds ``application``; the words of a path or of an identifier in snake or camel case count one by
one.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Sequence
from typing import Generic, TypeVar

__all__ = ["Index", "tokens"]

K = TypeVar("K", bound=Hashable)

_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP = frozenset(
    "a an and are as at be by do does for from how i in is it its my of on or so that the this to "
    "was what when where which who why with you your can could would should please".split()
)

#: BM25's two constants: how fast a repeated word stops counting, and how much a long text is
#: held against a short one.
_K1 = 1.4
_B = 0.7


def _stem(word: str) -> str:
    """
    Args:
        word: A lower case word.

    Returns:
        It without a plural or a verb ending, when what is left is still a word.
    """
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    """
    Split a text into the words a ranking counts.

    Args:
        text: Anything: a sentence, a path, an identifier in snake or camel case.

    Returns:
        Its words lowered and stemmed, without the common ones that say nothing.
    """
    words: list[str] = []
    for piece in _WORD.findall(text):
        for part in _CAMEL.split(piece):
            word = part.lower()
            if word and word not in _STOP:
                words.append(_stem(word))
    return words


class Index(Generic[K]):
    """A set of texts, ranked against a question."""

    def __init__(self, documents: Iterable[tuple[K, str]]) -> None:
        """
        Args:
            documents: ``(key, text)`` for each text.
        """
        self._docs: list[tuple[K, Counter[str], int]] = []
        frequency: Counter[str] = Counter()
        for key, text in documents:
            counts = Counter(tokens(text))
            self._docs.append((key, counts, sum(counts.values())))
            frequency.update(counts.keys())
        total = max(len(self._docs), 1)
        self._average = (sum(length for _k, _c, length in self._docs) / total) or 1.0
        self._idf = {
            word: math.log(1 + (total - seen + 0.5) / (seen + 0.5))
            for word, seen in frequency.items()
        }

    def __len__(self) -> int:
        return len(self._docs)

    def search(
        self, question: str, limit: int = 5, *, allow: Callable[[K], bool] | None = None
    ) -> list[tuple[K, float]]:
        """
        Rank the texts against a question.

        Args:
            question: What is looked for, in words.
            limit: The most to return.
            allow: Says whether a text may be returned at all; applied before the limit.

        Returns:
            ``(key, score)``, the best first, only for texts that have at least one of the words.
        """
        wanted: Sequence[str] = [
            word for word in dict.fromkeys(tokens(question)) if word in self._idf
        ]
        if not wanted:
            return []
        scored: list[tuple[K, float]] = []
        for key, counts, length in self._docs:
            if allow is not None and not allow(key):
                continue
            score = 0.0
            for word in wanted:
                seen = counts.get(word, 0)
                if seen:
                    norm = seen * (_K1 + 1) / (seen + _K1 * (1 - _B + _B * length / self._average))
                    score += self._idf[word] * norm
            if score > 0:
                scored.append((key, score))
        scored.sort(key=lambda item: -item[1])
        return scored[: max(limit, 0)]
