# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JSON with comments and trailing commas, as ``deno.jsonc`` and ``tsconfig.json`` write it.

A repository is untrusted input, so the file is only ever parsed: comments are removed with a
scanner that knows where a string begins and ends (a ``//`` inside ``"https://..."`` is part of
the string), and the result goes through :func:`json.loads`. Nothing in it is evaluated.
"""

from __future__ import annotations

import json
from typing import Any

#: Largest document read: a configuration file, not a data set.
MAX_BYTES = 1024 * 1024


def strip_comments(text: str) -> str:
    """
    Remove the comments and the trailing commas of a JSONC document.

    Args:
        text: The document.

    Returns:
        Plain JSON text. A comment becomes whitespace, so the line numbers of a parse error
        still point at the right place.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        pair = text[index : index + 2]
        if char == '"':
            in_string = True
            out.append(char)
        elif pair == "//":
            while index < length and text[index] != "\n":
                index += 1
            continue
        elif pair == "/*":
            end = text.find("*/", index + 2)
            stop = length if end == -1 else end + 2
            # Keep the line breaks, drop the rest.
            out.append("".join("\n" if c == "\n" else " " for c in text[index:stop]))
            index = stop
            continue
        else:
            out.append(char)
        index += 1
    return _drop_trailing_commas("".join(out))


def _drop_trailing_commas(text: str) -> str:
    """
    Remove a comma that is followed only by whitespace and a closing bracket.

    Args:
        text: JSON text without comments.

    Returns:
        The text without them; strings are left as they are.
    """
    out: list[str] = []
    in_string = False
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            out.append(char)
        elif char == ",":
            ahead = index + 1
            while ahead < length and text[ahead] in " \t\r\n":
                ahead += 1
            if ahead < length and text[ahead] in "}]":
                index += 1
                continue
            out.append(char)
        else:
            out.append(char)
        index += 1
    return "".join(out)


def loads(text: str) -> Any:
    """
    Parse a JSONC document.

    Args:
        text: The document.

    Returns:
        The value.

    Raises:
        ValueError: The document is too large or is not JSON once its comments are gone.
    """
    if len(text) > MAX_BYTES:
        raise ValueError("The document is larger than a configuration file")
    return json.loads(strip_comments(text))
