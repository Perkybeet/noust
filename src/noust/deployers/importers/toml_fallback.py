# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A TOML reader for Python 3.10, where ``tomllib`` does not exist.

Ubuntu 22.04 ships Python 3.10 and no TOML parser WASM may depend on, so
``railway.toml`` is read here there. It reads TOML 1.0 as ``tomllib`` does -
tables, dotted and quoted keys, the four kinds of string with their escapes,
integers in every base, floats, booleans, arrays over several lines and
inline tables - so a file means the same on every release WASM runs on. Two
constructs a platform configuration has no use for are not read: arrays of
tables (``[[name]]``) and dates and times. They are reported as warnings
and left out, because refusing a whole file for one setting WASM would
ignore anyway costs the operator everything else in it. Text that is not
TOML at all is refused, as ``tomllib`` refuses it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from wasm.core.exceptions import ValidationError

#: Deepest nesting of arrays and inline tables read; a configuration file
#: needs two or three, and a recursive reader must stop somewhere.
MAX_DEPTH = 64

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:\d{2})?)?"
    r"|\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
)
_SPECIAL_FLOAT = re.compile(r"[+-]?(?:inf|nan)")
_FLOAT = re.compile(
    r"[+-]?(?:0|[1-9](?:_?\d)*)(?:\.\d(?:_?\d)*(?:[eE][+-]?\d(?:_?\d)*)?|[eE][+-]?\d(?:_?\d)*)"
)
_INTEGER = re.compile(r"[+-]?(?:0|[1-9](?:_?\d)*)")
_BASED = {
    "0x": (re.compile(r"0x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*"), 16),
    "0o": (re.compile(r"0o[0-7](?:_?[0-7])*"), 8),
    "0b": (re.compile(r"0b[01](?:_?[01])*"), 2),
}
_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
#: What may follow a value on its line, or inside an array or inline table.
_VALUE_END = " \t\n,]}#"


class _Skipped:
    """A value that was read past but is not kept (a date or a time)."""


_SKIPPED = _Skipped()


def load_toml_fallback(
    text: str, *, name: str, warn: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """
    Parse TOML without ``tomllib``.

    Args:
        text: The file's text.
        name: The file's name, for errors and warnings.
        warn: Where each construct that is left out is reported; None drops
            the reports.

    Returns:
        The document, as ``tomllib.loads`` would return it, less what was
        reported.

    Raises:
        ValidationError: The text is not TOML.
    """
    return _Reader(text, name, warn or (lambda _message: None)).document()


class _Reader:
    """One pass over one file."""

    def __init__(self, text: str, name: str, warn: Callable[[str], None]) -> None:
        self.text = text.replace("\r\n", "\n")
        self.pos = 0
        self.name = name
        self.warn = warn

    # Positions -------------------------------------------------------------

    def line(self) -> int:
        """The line the reader is on, counted from 1."""
        return self.text.count("\n", 0, self.pos) + 1

    def fail(self, what: str) -> ValidationError:
        """Build the refusal for what is at the current position."""
        return ValidationError(
            f"{self.name} is not valid TOML", details=f"Line {self.line()}: {what}."
        )

    def peek(self, count: int = 1) -> str:
        """The next ``count`` characters, fewer at the end."""
        return self.text[self.pos : self.pos + count]

    def expect(self, token: str) -> None:
        """Step over ``token``, or refuse the file."""
        if not self.text.startswith(token, self.pos):
            raise self.fail(f"expected {token!r}")
        self.pos += len(token)

    def skip_spaces(self) -> None:
        """Step over spaces and tabs."""
        while self.pos < len(self.text) and self.text[self.pos] in " \t":
            self.pos += 1

    def skip_comment(self) -> None:
        """Step over a comment, up to the end of its line."""
        if self.peek() == "#":
            end = self.text.find("\n", self.pos)
            self.pos = len(self.text) if end == -1 else end

    def skip_blank(self) -> None:
        """Step over spaces, comments and line ends."""
        while True:
            self.skip_spaces()
            self.skip_comment()
            if self.peek() == "\n":
                self.pos += 1
                continue
            return

    def end_of_line(self) -> None:
        """Step to the next line, refusing anything but a comment before it."""
        self.skip_spaces()
        self.skip_comment()
        if self.pos < len(self.text):
            if self.peek() != "\n":
                raise self.fail("expected the end of the line")
            self.pos += 1

    # Document --------------------------------------------------------------

    def document(self) -> dict[str, Any]:
        """Read every statement."""
        root: dict[str, Any] = {}
        table = root
        # The current table's key, to name a setting whole in a warning.
        where: list[str] = []
        while True:
            self.skip_blank()
            if self.pos >= len(self.text):
                return root
            line = self.line()
            if self.peek(2) == "[[":
                self.pos += 2
                self.skip_spaces()
                keys = self.keys()
                self.skip_spaces()
                self.expect("]]")
                self.end_of_line()
                self.warn(
                    f"{self.name} line {line}: [[{'.'.join(keys)}]] is an array of tables, "
                    "which WASM does not read on Python 3.10; that section is ignored."
                )
                # Its keys go nowhere: a table no one reads.
                table = {}
                where = keys
                continue
            if self.peek() == "[":
                self.pos += 1
                self.skip_spaces()
                keys = self.keys()
                self.skip_spaces()
                self.expect("]")
                self.end_of_line()
                table = self.descend(root, keys)
                where = keys
                continue
            keys = self.keys()
            self.skip_spaces()
            self.expect("=")
            self.skip_spaces()
            value = self.value(0)
            self.end_of_line()
            if value is _SKIPPED:
                self.warn(
                    f"{self.name} line {line}: {'.'.join([*where, *keys])} holds a date or a time, "
                    "which WASM does not read on Python 3.10; it is ignored."
                )
                continue
            self.assign(table, keys, value, line)

    def descend(self, table: dict[str, Any], keys: list[str]) -> dict[str, Any]:
        """Find or create the table a dotted key names."""
        for key in keys:
            nested = table.setdefault(key, {})
            if not isinstance(nested, dict):
                raise self.fail(f"{key} is already a value, not a table")
            table = nested
        return table

    def assign(self, table: dict[str, Any], keys: list[str], value: Any, line: int) -> None:
        """Set a dotted key once."""
        target = self.descend(table, keys[:-1])
        if keys[-1] in target:
            raise ValidationError(
                f"{self.name} is not valid TOML",
                details=f"Line {line}: {'.'.join(keys)} is set twice.",
            )
        target[keys[-1]] = value

    # Keys ------------------------------------------------------------------

    def keys(self) -> list[str]:
        """Read a key, dotted or not."""
        parts = [self.simple_key()]
        while True:
            self.skip_spaces()
            if self.peek() != ".":
                return parts
            self.pos += 1
            self.skip_spaces()
            parts.append(self.simple_key())

    def simple_key(self) -> str:
        """Read one part of a key: bare, or quoted."""
        if self.peek() == '"':
            self.pos += 1
            return self.basic_string()
        if self.peek() == "'":
            self.pos += 1
            return self.literal_string()
        match = _BARE_KEY.match(self.text, self.pos)
        if match is None:
            raise self.fail("expected a key")
        self.pos = match.end()
        return match.group(0)

    # Values ----------------------------------------------------------------

    def value(self, depth: int) -> Any:
        """Read one value."""
        if depth > MAX_DEPTH:
            raise self.fail(f"arrays and inline tables nest more than {MAX_DEPTH} levels deep")
        char = self.peek()
        if self.peek(3) == '"""':
            self.pos += 3
            return self.multiline_basic_string()
        if char == '"':
            self.pos += 1
            return self.basic_string()
        if self.peek(3) == "'''":
            self.pos += 3
            return self.multiline_literal_string()
        if char == "'":
            self.pos += 1
            return self.literal_string()
        if char == "[":
            self.pos += 1
            return self.array(depth + 1)
        if char == "{":
            self.pos += 1
            return self.inline_table(depth + 1)
        for word, meaning in (("true", True), ("false", False)):
            if self.text.startswith(word, self.pos) and self.ends_at(self.pos + len(word)):
                self.pos += len(word)
                return meaning
        return self.scalar()

    def ends_at(self, index: int) -> bool:
        """Whether a value may end at ``index``."""
        return index >= len(self.text) or self.text[index] in _VALUE_END

    def scalar(self) -> Any:
        """Read a number, or step over a date or a time."""
        date = _DATETIME.match(self.text, self.pos)
        if date is not None and self.ends_at(date.end()):
            self.pos = date.end()
            return _SKIPPED
        special = _SPECIAL_FLOAT.match(self.text, self.pos)
        if special is not None and self.ends_at(special.end()):
            self.pos = special.end()
            return float(special.group(0))
        based = _BASED.get(self.peek(2))
        if based is not None:
            pattern, base = based
            match = pattern.match(self.text, self.pos)
            if match is not None and self.ends_at(match.end()):
                self.pos = match.end()
                return int(match.group(0)[2:].replace("_", ""), base)
        for pattern, kind in ((_FLOAT, float), (_INTEGER, int)):
            match = pattern.match(self.text, self.pos)
            if match is not None and self.ends_at(match.end()):
                self.pos = match.end()
                return kind(match.group(0).replace("_", ""))
        raise self.fail("cannot read the value")

    def array(self, depth: int) -> Any:
        """Read an array, after its ``[``."""
        items: list[Any] = []
        skipped = False
        while True:
            self.skip_blank()
            if self.peek() == "]":
                self.pos += 1
                return _SKIPPED if skipped else items
            item = self.value(depth)
            if item is _SKIPPED:
                skipped = True
            else:
                items.append(item)
            self.skip_blank()
            if self.peek() == ",":
                self.pos += 1
            elif self.peek() != "]":
                raise self.fail("expected ',' or ']' in the array")

    def inline_table(self, depth: int) -> Any:
        """Read an inline table, after its ``{``."""
        table: dict[str, Any] = {}
        skipped = False
        self.skip_spaces()
        if self.peek() == "}":
            self.pos += 1
            return table
        while True:
            self.skip_spaces()
            line = self.line()
            keys = self.keys()
            self.skip_spaces()
            self.expect("=")
            self.skip_spaces()
            value = self.value(depth)
            if value is _SKIPPED:
                skipped = True
            else:
                self.assign(table, keys, value, line)
            self.skip_spaces()
            if self.peek() == ",":
                self.pos += 1
            elif self.peek() == "}":
                self.pos += 1
                return _SKIPPED if skipped else table
            else:
                raise self.fail("expected ',' or '}' in the inline table")

    # Strings ---------------------------------------------------------------

    def escape(self) -> str:
        """Read an escape, after its backslash."""
        char = self.peek()
        if char in _ESCAPES:
            self.pos += 1
            return _ESCAPES[char]
        if char in ("u", "U"):
            width = 4 if char == "u" else 8
            digits = self.text[self.pos + 1 : self.pos + 1 + width]
            if len(digits) != width or not all(c in "0123456789abcdefABCDEF" for c in digits):
                raise self.fail(f"\\{char} needs {width} hexadecimal digits")
            code = int(digits, 16)
            if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                raise self.fail(f"\\{char}{digits} is not a Unicode scalar value")
            self.pos += 1 + width
            return chr(code)
        raise self.fail(f"\\{char} is not an escape TOML has")

    def basic_string(self) -> str:
        """Read a ``"..."`` string, after its opening quote."""
        out: list[str] = []
        while True:
            char = self.peek()
            if not char or char == "\n":
                raise self.fail("the string is not closed on its line")
            self.pos += 1
            if char == '"':
                return "".join(out)
            if char == "\\":
                out.append(self.escape())
            else:
                out.append(char)

    def literal_string(self) -> str:
        """Read a ``'...'`` string, after its opening quote."""
        end = self.text.find("'", self.pos)
        newline = self.text.find("\n", self.pos)
        if end == -1 or (newline != -1 and newline < end):
            raise self.fail("the string is not closed on its line")
        value = self.text[self.pos : end]
        self.pos = end + 1
        return value

    def closing(self, quote: str) -> int | None:
        """
        At three quotes, how many of the quotes here belong to the content.

        A multi-line string may end with one or two quotes of its own right
        before its closing three.
        """
        run = 0
        while self.text.startswith(quote, self.pos + run):
            run += 1
        if run < 3:
            return None
        if run > 5:
            raise self.fail("too many quotes close the string")
        return run - 3

    def multiline_basic_string(self) -> str:
        """Read a ``\"\"\"...\"\"\"`` string, after its opening quotes."""
        if self.peek() == "\n":
            self.pos += 1
        out: list[str] = []
        while True:
            if self.pos >= len(self.text):
                raise self.fail("the string is not closed")
            extra = self.closing('"') if self.peek() == '"' else None
            if extra is not None:
                self.pos += extra + 3
                return "".join(out) + '"' * extra
            char = self.peek()
            self.pos += 1
            if char != "\\":
                out.append(char)
                continue
            rest = self.pos
            while rest < len(self.text) and self.text[rest] in " \t":
                rest += 1
            if rest < len(self.text) and self.text[rest] == "\n":
                # A backslash ending a line joins it to the next, dropping the
                # whitespace in between.
                self.pos = rest
                while self.peek() and self.peek() in " \t\n":
                    self.pos += 1
                continue
            out.append(self.escape())

    def multiline_literal_string(self) -> str:
        """Read a ``'''...'''`` string, after its opening quotes."""
        if self.peek() == "\n":
            self.pos += 1
        start = self.pos
        while self.pos < len(self.text):
            extra = self.closing("'") if self.peek() == "'" else None
            if extra is not None:
                value = self.text[start : self.pos + extra]
                self.pos += extra + 3
                return value
            self.pos += 1
        raise self.fail("the string is not closed")
