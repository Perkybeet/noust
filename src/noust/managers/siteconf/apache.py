"""
The Apache dialect: one directive per logical line, sections as tags.

Apache reads a file line by line. A backslash as the very last character of a
line continues the line on the next one; a line whose first non-blank
character is ``#`` is a comment (there are no end-of-line comments); a line
starting with ``<Name`` opens a section that ``</Name>`` closes, matched
without regard to case. Words are split on whitespace; a word starting with a
quote runs to the matching quote, inside which ``\\"`` and ``\\\\`` are the only
escapes (``ap_getword_conf``).
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

# apr_isspace: what Apache splits words on.
_SPACE = " \t\n\r\v\f"
_LINE_SPACE = " \t\r\v\f"
_CONTINUATION = re.compile(r"\\\r?\n")


def unescape(raw: str) -> str:
    """
    Resolve a word's escapes the way ``substring_conf`` does.

    Args:
        raw: The word's text, quotes included when it has them.

    Returns:
        The value Apache reads.
    """
    quote_char = raw[:1] if raw[:1] in ('"', "'") else ""
    body = raw[1:-1] if quote_char and len(raw) >= 2 else raw
    if "\\" not in body:
        return body
    out: list[str] = []
    i = 0
    while i < len(body):
        char = body[i]
        nxt = body[i + 1] if i + 1 < len(body) else ""
        if char == "\\" and (nxt == "\\" or (quote_char and nxt == quote_char)):
            out.append(nxt)
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def quote(value: str) -> str:
    """
    Write a value as an Apache word that reads back as exactly that value.

    Args:
        value: The value an argument should have.

    Returns:
        The value bare when Apache reads it unchanged, else double-quoted.
    """
    if (
        value
        and not value.startswith(('"', "'"))
        and not any(char in _SPACE or char in "\\>" for char in value)
    ):
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

    def logical_line(self) -> tuple[int, int]:
        """Span of the line starting here, continuations included, line break excluded."""
        start = self.pos
        while True:
            newline = self.text.find("\n", self.pos)
            if newline < 0:
                self.pos = self.size
                return start, self.size
            line = self.text[self.pos : newline].rstrip("\r")
            if line.endswith("\\"):
                self.pos = newline + 1
                continue
            self.pos = newline
            return start, newline

    def words(self, start: int, end: int) -> tuple[list[Arg], str]:
        """Split ``text[start:end]`` into words; returns them and what follows the last."""
        text = self.text
        args: list[Arg] = []
        i = start
        while True:
            sep_start = i
            while i < end:
                continuation = _CONTINUATION.match(text, i)
                if continuation and continuation.end() <= end:
                    i = continuation.end()
                elif text[i] in _SPACE:
                    i += 1
                else:
                    break
            if i >= end:
                return args, text[sep_start:end]
            word_start = i
            first = text[i]
            if first in "\"'":
                i += 1
                while i < end and text[i] != first:
                    i += 2 if text[i] == "\\" and text[i + 1 : i + 2] in (first, "\\") else 1
                if i >= end:
                    raise self.error(
                        word_start, "unterminated quoted string: the closing quote is missing"
                    )
                i += 1
            else:
                while i < end and text[i] not in _SPACE and not _CONTINUATION.match(text, i):
                    i += 1
            raw = text[word_start:i]
            line, column = self.where(word_start)
            args.append(Arg(text[sep_start:word_start], Word(raw, unescape(raw), line, column)))

    def body(self, opener: Block | None, opened_at: int) -> tuple[list[Node], str, str]:
        """Read nodes until the matching closing tag; returns children, trailing, closer."""
        children: list[Node] = []
        carry = ""
        while True:
            prefix = carry + self.spaces()
            carry = ""
            if self.pos >= self.size:
                if opener is not None:
                    raise self.error(
                        opened_at,
                        f"<{opener.name.value}> is never closed: </{opener.name.value}> is missing",
                    )
                return children, prefix, ""
            start = self.pos
            char = self.text[start]
            if char == "#":
                line, column = self.where(start)
                begin, end = self.logical_line()
                children.append(Comment(prefix, self.text[begin:end], line, column))
            elif self.text.startswith("</", start):
                begin, end = self.logical_line()
                closer = self.text[begin:end]
                tag = closer.rstrip(_LINE_SPACE)
                if not tag.endswith(">"):
                    raise self.error(start, 'a closing tag must end with ">"')
                name = tag[2:-1].strip()
                if opener is None:
                    raise self.error(start, f"</{name}> without an opening <{name}>")
                if name.lower() != opener.name.value.lower():
                    raise self.error(
                        start, f"expected </{opener.name.value}> here, found </{name}>"
                    )
                return children, prefix, closer
            elif char == "<":
                children.append(self.section(prefix))
            else:
                begin, end = self.logical_line()
                args, tail = self.words(begin, end)
                if not args:
                    # Only continuations: Apache joins them into an empty
                    # line, so the text is whitespace before the next node.
                    carry = prefix + self.text[begin:end]
                    continue
                head, *rest = args
                line = self.where(end)[0] if end > begin else head.word.line
                children.append(Directive(prefix + head.sep, head.word, rest, tail, "", line))

    def section(self, prefix: str) -> Block:
        start = self.pos
        begin, end = self.logical_line()
        line_text = self.text[begin:end]
        content_end = begin + len(line_text.rstrip(_LINE_SPACE))
        if content_end <= begin + 1 or self.text[content_end - 1] != ">":
            raise self.error(start, 'a section tag must end with ">" on its line')
        words, tail = self.words(begin + 1, content_end - 1)
        if not words or words[0].sep:
            raise self.error(start, "a section tag needs a name right after <")
        name, *args = words
        block = Block(
            prefix=prefix,
            name=name.word,
            args=args,
            tail=tail,
            opener=self.text[content_end - 1 : end],
            children=[],
            trailing="",
            closer="",
            end_line=0,
            lead="<",
        )
        block.children, block.trailing, block.closer = self.body(block, start)
        block.end_line = self.where(self.pos - 1)[0] if self.pos else 1
        return block


def parse_apache(text: str) -> Tree:
    """
    Parse Apache configuration text into a lossless tree.

    Args:
        text: The whole file.

    Returns:
        The tree; ``render()`` gives ``text`` back exactly.

    Raises:
        ParseError: At the first place Apache would stop reading.
    """
    reader = _Reader(text)
    children, trailing, _ = reader.body(None, 0)
    return Tree("apache", children, trailing)
