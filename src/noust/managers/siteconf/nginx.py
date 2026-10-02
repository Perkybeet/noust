"""
The nginx dialect: a tokenizer that reads text the way nginx does.

The rules come from ``ngx_conf_read_token`` rather than from intuition,
because the places they surprise are exactly where a hand-written reader
would disagree with nginx and show the operator a different file from the
one being served:

- whitespace is space, tab, CR and LF; a word ends at whitespace, ``;`` or
  ``{``, but not at ``}`` and not at ``#``;
- ``#`` starts a comment only where a token could start, including between
  the arguments of a directive;
- a quoted word must be followed by whitespace, ``;``, ``{`` or ``)``;
- ``$`` followed by ``{`` keeps the brace in the word (``${var}``);
- a backslash takes the next character into the word, whatever it is;
- ``\\"``, ``\\'``, ``\\\\``, ``\\t``, ``\\r`` and ``\\n`` are unescaped in every
  word, quoted or not, and any other backslash is kept.

The bodies of ``*_by_lua_block`` directives are Lua, not nginx: the module
that owns them reads them with its own lexer, so they are scanned for their
closing brace with Lua's rules (strings, long brackets, comments) and kept
verbatim.
"""

from __future__ import annotations

import bisect
import re

from noust.managers.siteconf.tree import (
    Arg,
    Block,
    Comment,
    Directive,
    Node,
    ParseError,
    Tree,
    Word,
)

_SPACE = " \t\r\n"
_LONG_BRACKET = re.compile(r"\[(=*)\[")
_ESCAPES = {'"': '"', "'": "'", "\\": "\\", "t": "\t", "r": "\r", "n": "\n"}
# Characters after which nginx would read a bare word differently from the
# value it was meant to be.
_NEEDS_QUOTES = set(_SPACE) | set(";{}\"'")


def unescape(raw: str) -> str:
    """
    Resolve escapes the way nginx copies a token's characters.

    Args:
        raw: A token's text, without its surrounding quotes.

    Returns:
        The value nginx reads.
    """
    if "\\" not in raw:
        return raw
    out: list[str] = []
    i = 0
    while i < len(raw):
        char = raw[i]
        if char == "\\" and i + 1 < len(raw) and raw[i + 1] in _ESCAPES:
            out.append(_ESCAPES[raw[i + 1]])
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def quote(value: str) -> str:
    """
    Write a value as an nginx token that reads back as exactly that value.

    Plain values are written bare, so a timeout stays ``120s`` and a regex
    stays readable; anything nginx would split, end early or unescape is put
    in double quotes.

    Args:
        value: The value an argument should have.

    Returns:
        Its token text.
    """
    bare = bool(value) and not value.startswith("#") and not (_NEEDS_QUOTES & set(value))
    if bare and "\\" in value:
        # A backslash survives bare only when nginx would not take the next
        # character as an escape, and it must not be the last character.
        bare = all(
            i + 1 < len(value) and value[i + 1] not in _ESCAPES
            for i, char in enumerate(value)
            if char == "\\"
        )
    if bare:
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class _Reader:
    """One pass over the text, producing the tree or the first error."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.size = len(text)
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def where(self, pos: int) -> tuple[int, int]:
        line = bisect.bisect_right(self.line_starts, pos)
        return line, pos - self.line_starts[line - 1] + 1

    def error(self, pos: int, message: str) -> ParseError:
        line, column = self.where(pos)
        return ParseError(line, column, message)

    def spaces(self) -> str:
        start = self.pos
        while self.pos < self.size and self.text[self.pos] in _SPACE:
            self.pos += 1
        return self.text[start : self.pos]

    def comment(self) -> str:
        end = self.text.find("\n", self.pos)
        if end < 0:
            end = self.size
        text = self.text[self.pos : end]
        # A CR before the line break is whitespace to nginx; leaving it to the
        # next prefix keeps the comment's text free of it.
        if text.endswith("\r"):
            end -= 1
            text = text[:-1]
        self.pos = end
        return text

    def separator(self) -> str:
        """Whitespace and comments between two tokens of a directive."""
        start = self.pos
        while self.pos < self.size:
            char = self.text[self.pos]
            if char in _SPACE:
                self.pos += 1
            elif char == "#":
                self.comment()
            else:
                break
        return self.text[start : self.pos]

    def word(self) -> Word:
        start = self.pos
        text = self.text
        line, column = self.where(start)
        first = text[start]
        if first in "\"'":
            i = start + 1
            while i < self.size and text[i] != first:
                i += 2 if text[i] == "\\" else 1
            if i >= self.size:
                raise self.error(start, "unterminated quoted string: the closing quote is missing")
            end = i + 1
            if end < self.size and text[end] not in " \t\r\n;{)":
                raise self.error(end, f'unexpected "{text[end]}" after a quoted string')
            self.pos = end
            return Word(text[start:end], unescape(text[start + 1 : i]), line, column)
        i = start
        variable = False
        while i < self.size:
            char = text[i]
            if char == "\\":
                variable = False
                i += 2
                continue
            if char == "{" and variable:
                i += 1
                continue
            variable = char == "$"
            if char in " \t\r\n;{":
                break
            i += 1
        end = min(i, self.size)
        self.pos = end
        raw = text[start:end]
        return Word(raw, unescape(raw), line, column)

    def body(self, opened_at: int | None) -> tuple[list[Node], str]:
        """Read statements until the closing brace (left unread) or the end."""
        children: list[Node] = []
        while True:
            prefix = self.spaces()
            if self.pos >= self.size:
                if opened_at is not None:
                    raise self.error(self.pos, 'unexpected end of file, expecting "}"')
                return children, prefix
            char = self.text[self.pos]
            if char == "#":
                line, column = self.where(self.pos)
                children.append(Comment(prefix, self.comment(), line, column))
            elif char == "}":
                if opened_at is None:
                    raise self.error(self.pos, 'unexpected "}"')
                return children, prefix
            elif char in ";{":
                raise self.error(self.pos, f'unexpected "{char}"')
            else:
                children.append(self.statement(prefix))

    def statement(self, prefix: str) -> Directive | Block:
        name = self.word()
        args: list[Arg] = []
        while True:
            sep = self.separator()
            if self.pos >= self.size:
                raise self.error(self.pos, 'unexpected end of file, expecting ";" or "}"')
            char = self.text[self.pos]
            if char == ";":
                self.pos += 1
                return Directive(prefix, name, args, sep, ";", self.where(self.pos - 1)[0])
            if char == "{":
                opened_at = self.pos
                self.pos += 1
                raw_body: str | None = None
                children: list[Node] = []
                trailing = ""
                if name.value.endswith("_by_lua_block"):
                    raw_body = self.lua_body(opened_at)
                else:
                    children, trailing = self.body(opened_at)
                    self.pos += 1
                return Block(
                    prefix=prefix,
                    name=name,
                    args=args,
                    tail=sep,
                    opener="{",
                    children=children,
                    trailing=trailing,
                    closer="}",
                    end_line=self.where(self.pos - 1)[0],
                    raw_body=raw_body,
                )
            if char == "}":
                raise self.error(self.pos, 'unexpected "}"')
            args.append(Arg(sep, self.word()))

    def lua_body(self, opened_at: int) -> str:
        """Scan a Lua block for its closing brace, leaving the reader after it."""
        text = self.text
        start = i = self.pos
        depth = 0
        unclosed = "the Lua block opened here is never closed"
        while i < self.size:
            char = text[i]
            if char == "-" and text.startswith("--", i):
                long_comment = _LONG_BRACKET.match(text, i + 2)
                if long_comment:
                    i = self._after_long_bracket(long_comment, opened_at, unclosed)
                else:
                    newline = text.find("\n", i)
                    i = self.size if newline < 0 else newline
                continue
            if char in "\"'":
                j = i + 1
                while j < self.size and text[j] != char:
                    j += 2 if text[j] == "\\" else 1
                if j >= self.size:
                    raise self.error(opened_at, unclosed)
                i = j + 1
                continue
            if char == "[":
                long_string = _LONG_BRACKET.match(text, i)
                if long_string:
                    i = self._after_long_bracket(long_string, opened_at, unclosed)
                    continue
            if char == "{":
                depth += 1
            elif char == "}":
                if depth == 0:
                    self.pos = i + 1
                    return text[start:i]
                depth -= 1
            i += 1
        raise self.error(opened_at, unclosed)

    def _after_long_bracket(self, match: re.Match[str], opened_at: int, unclosed: str) -> int:
        closing = "]" + match.group(1) + "]"
        end = self.text.find(closing, match.end())
        if end < 0:
            raise self.error(opened_at, unclosed)
        return end + len(closing)


def parse_nginx(text: str) -> Tree:
    """
    Parse nginx configuration text into a lossless tree.

    Args:
        text: The whole file.

    Returns:
        The tree; ``render()`` gives ``text`` back exactly.

    Raises:
        ParseError: At the first place nginx would stop reading.
    """
    reader = _Reader(text)
    children, trailing = reader.body(None)
    return Tree("nginx", children, trailing)
