"""
The concrete syntax tree both dialects parse into.

Every byte of a site file belongs to exactly one place in this tree: the
whitespace before a statement is its ``prefix``, the separators between words
live with the word they precede, and what is left before a closing brace or at
the end of the file is the container's ``trailing`` text. Rendering is plain
concatenation, which is what makes ``parse(text).render() == text`` hold by
construction rather than by care, and what lets an edit replace the bytes of
one element while every other byte stays where it was.

The same node types serve nginx and Apache; only the punctuation stored in
them differs (``;`` and ``{`` ``}`` against end of line and ``<Tag>``
``</Tag>``).

Elements are addressed by stable ids derived from their position:

- ``d3``: the fourth statement (comments do not count) of the file;
  ``s1/d0`` the first statement inside server ``s1``, and so on down.
- ``s1``: the second server (nginx ``server`` at the top level or in ``http``;
  Apache ``<VirtualHost>`` wherever it is), in file order.
- ``s1/l3``: the fourth ``location`` (Apache ``<Location>``/``<LocationMatch>``)
  directly inside ``s1``; nested ones continue the path, ``s1/l3/l0``.
- ``u:name``: the upstream (Apache ``<Proxy balancer://name>``) of that name.

An element answers to its ``d`` path and to its alias, so an id the structure
hands out is always one the edit operations accept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from noust.core.exceptions import SiteError

Kind = Literal["nginx", "apache"]


class ParseError(SiteError):
    """
    The text is not a configuration the web server would read.

    Raised instead of returning a partial tree: a caller either gets the whole
    file or this error, never a structure that silently lost the part after
    the mistake.
    """

    def __init__(self, line: int, column: int, message: str) -> None:
        """
        Args:
            line: 1-based line of the offending character.
            column: 1-based column of the offending character.
            message: What is wrong there, in the web server's terms.
        """
        self.line = line
        self.column = column
        super().__init__(
            message,
            details=(
                f"Line {line}, column {column}. Fix the text there; the web server "
                "would refuse it for the same reason."
            ),
        )


@dataclass
class Word:
    """
    One token: a directive name or one of its arguments.

    Attributes:
        raw: The exact source text, quotes and escapes included.
        value: What the web server reads: quotes removed, escapes resolved.
        line: 1-based line where the token starts.
        column: 1-based column where the token starts.
    """

    raw: str
    value: str
    line: int
    column: int

    @property
    def quoted(self) -> bool:
        """Whether the token is written between quotes."""
        return self.raw[:1] in ('"', "'")


@dataclass
class Arg:
    """
    An argument and the separator written before it.

    Attributes:
        sep: Whitespace (and, for nginx, comments) between the previous token
            and this one. Empty when nginx reads two tokens with nothing in
            between, as in ``"x")``.
        word: The argument itself.
    """

    sep: str
    word: Word


@dataclass
class Comment:
    """
    A comment, from its ``#`` to the end of its line.

    Attributes:
        prefix: Whitespace before the ``#``.
        text: The comment, ``#`` included, line break excluded.
        line: 1-based line of the ``#``.
        column: 1-based column of the ``#``.
    """

    prefix: str
    text: str
    line: int
    column: int

    @property
    def end_line(self) -> int:
        """Last line the comment covers (an Apache comment can continue)."""
        return self.line + self.text.count("\n")

    def render_head(self) -> str:
        """The comment without its prefix."""
        return self.text

    def render_text(self) -> str:
        """The comment without its prefix."""
        return self.text

    def render(self) -> str:
        """The comment exactly as it was written."""
        return self.prefix + self.text


@dataclass
class Directive:
    """
    A simple directive: ``name args;`` in nginx, one logical line in Apache.

    Attributes:
        prefix: Whitespace before the name.
        name: The directive name.
        args: The arguments, each with its separator.
        tail: What sits between the last token and the terminator.
        terminator: ``;`` for nginx; empty for Apache, whose line break
            belongs to the next node's prefix.
        end_line: 1-based line of the terminator (or of the last token).
    """

    prefix: str
    name: Word
    args: list[Arg]
    tail: str
    terminator: str
    end_line: int

    @property
    def line(self) -> int:
        """1-based line where the directive starts."""
        return self.name.line

    def render_head(self) -> str:
        """The directive without its prefix: all of it is its head."""
        return self.render_text()

    def render_text(self) -> str:
        """The directive without its prefix."""
        return (
            self.name.raw
            + "".join(arg.sep + arg.word.raw for arg in self.args)
            + self.tail
            + self.terminator
        )

    def render(self) -> str:
        """The directive exactly as it was written."""
        return self.prefix + self.render_text()


@dataclass
class Block:
    """
    A directive with a body: ``server { ... }`` or ``<VirtualHost ...> ... </VirtualHost>``.

    Attributes:
        prefix: Whitespace before the block.
        name: The block's name (``server``, ``location``, ``VirtualHost``).
        args: The arguments of its head.
        tail: What sits between the last head token and ``opener``.
        opener: ``{`` for nginx; ``>`` and the rest of its line for Apache.
        children: The statements and comments of the body.
        trailing: Whitespace between the last child and ``closer``.
        closer: ``}`` for nginx; the whole ``</Tag>`` line for Apache.
        end_line: 1-based line of the closer.
        lead: ``<`` for Apache, empty for nginx.
        raw_body: For blocks whose body is another language (nginx's
            ``*_by_lua_block``), the body verbatim; ``children`` is then empty.
    """

    prefix: str
    name: Word
    args: list[Arg]
    tail: str
    opener: str
    children: list[Node]
    trailing: str
    closer: str
    end_line: int
    lead: str = ""
    raw_body: str | None = None

    @property
    def line(self) -> int:
        """1-based line where the block starts."""
        return self.name.line

    def render_head(self) -> str:
        """The head of the block, up to and including its opener."""
        return (
            self.lead
            + self.name.raw
            + "".join(arg.sep + arg.word.raw for arg in self.args)
            + self.tail
            + self.opener
        )

    def render_body(self) -> str:
        """Everything between the opener and the closer."""
        if self.raw_body is not None:
            return self.raw_body
        return "".join(child.render() for child in self.children) + self.trailing

    def render_text(self) -> str:
        """The block without its prefix."""
        return self.render_head() + self.render_body() + self.closer

    def render(self) -> str:
        """The block exactly as it was written."""
        return self.prefix + self.render_text()


Node = Comment | Directive | Block
Statement = Directive | Block


@dataclass
class NodeRef:
    """
    Where an addressed element lives.

    Attributes:
        node: The element.
        container: The tree or block whose ``children`` hold it.
        index: Its position in ``container.children``.
        id: The canonical id the structure gives it (its alias when it has
            one, its ``d`` path otherwise).
    """

    node: Statement
    container: Tree | Block
    index: int
    id: str


@dataclass
class Tree:
    """
    A parsed site file.

    Attributes:
        kind: The dialect it was parsed as.
        children: Top-level statements and comments.
        trailing: Whitespace after the last top-level node.
    """

    kind: Kind
    children: list[Node]
    trailing: str = ""

    def render(self) -> str:
        """
        The file, byte for byte.

        Returns:
            The text this tree was parsed from, with any edits applied.
        """
        return "".join(child.render() for child in self.children) + self.trailing

    def index(self) -> dict[str, NodeRef]:
        """
        Every id this tree answers to.

        Returns:
            A map from each id (``d`` paths and aliases) to its element.
        """
        return _build_index(self)

    def find(self, node_id: str) -> Statement:
        """
        The element an id names.

        Args:
            node_id: An id as described in the module documentation.

        Returns:
            The directive or block.

        Raises:
            KeyError: When nothing in the tree has that id.
        """
        return self.index()[node_id].node

    def span_of(self, node_id: str) -> tuple[int, int]:
        """
        Where an element's text is in :meth:`render`'s output.

        Args:
            node_id: The element's id.

        Returns:
            Start and end offsets of the element, prefix excluded.

        Raises:
            KeyError: When nothing in the tree has that id.
        """
        node = self.find(node_id)
        _, start, end = node_offsets(self)[id(node)]
        return start, end


def node_offsets(tree: Tree) -> dict[int, tuple[int, int, int]]:
    """
    Offsets of every node in the rendered text.

    Args:
        tree: The tree to measure.

    Returns:
        A map from ``id(node)`` to ``(prefix_start, start, end)``.
    """
    offsets: dict[int, tuple[int, int, int]] = {}

    def visit(nodes: list[Node], pos: int) -> int:
        for node in nodes:
            prefix_start = pos
            start = pos + len(node.prefix)
            if isinstance(node, Block) and node.raw_body is None:
                pos = visit(node.children, start + len(node.render_head()))
                pos += len(node.trailing) + len(node.closer)
            else:
                pos = start + len(node.render_text())
            offsets[id(node)] = (prefix_start, start, pos)
        return pos

    visit(tree.children, 0)
    return offsets


def is_server(node: Node, container: Tree | Block, kind: Kind) -> bool:
    """
    Whether a node is a virtual server.

    Args:
        node: The node.
        container: What holds it.
        kind: The dialect.

    Returns:
        True for an nginx ``server`` in the main or ``http`` context (not
        ``stream`` or ``mail``, which serve no HTTP) and for an Apache
        ``<VirtualHost>``.
    """
    if not isinstance(node, Block):
        return False
    if kind == "apache":
        return node.name.value.lower() == "virtualhost"
    return node.name.value == "server" and _is_http_context(container)


def upstream_name(node: Node, container: Tree | Block, kind: Kind) -> str | None:
    """
    The name of an upstream, when the node is one.

    Args:
        node: The node.
        container: What holds it.
        kind: The dialect.

    Returns:
        The upstream's name (an nginx ``upstream`` block in the main or
        ``http`` context, an Apache ``<Proxy balancer://name>``), or None.
    """
    if not isinstance(node, Block) or not node.args:
        return None
    first = node.args[0].word.value
    if kind == "apache":
        if node.name.value.lower() == "proxy" and first.lower().startswith("balancer://"):
            return first[len("balancer://") :].rstrip("/")
        return None
    if node.name.value == "upstream" and _is_http_context(container):
        return first
    return None


def is_location(node: Node, kind: Kind) -> bool:
    """
    Whether a node is a location.

    Args:
        node: The node.
        kind: The dialect.

    Returns:
        True for an nginx ``location`` and an Apache ``<Location>`` or
        ``<LocationMatch>``.
    """
    if not isinstance(node, Block):
        return False
    if kind == "apache":
        return node.name.value.lower() in ("location", "locationmatch")
    return node.name.value == "location"


def _is_http_context(container: Tree | Block) -> bool:
    return isinstance(container, Tree) or container.name.value == "http"


def _build_index(tree: Tree) -> dict[str, NodeRef]:
    index: dict[str, NodeRef] = {}
    servers = 0
    upstream_seen: dict[str, int] = {}

    def walk(container: Tree | Block, base: str) -> None:
        nonlocal servers
        statements = 0
        locations = 0
        for position, child in enumerate(container.children):
            if isinstance(child, Comment):
                continue
            path = f"{base}/d{statements}" if base else f"d{statements}"
            statements += 1
            alias: str | None = None
            if is_server(child, container, tree.kind):
                alias = f"s{servers}"
                servers += 1
            elif base and is_location(child, tree.kind):
                alias = f"{base}/l{locations}"
                locations += 1
            else:
                name = upstream_name(child, container, tree.kind)
                if name is not None:
                    # nginx refuses a second upstream of the same name; the
                    # suffix keeps the id unique until the operator fixes it.
                    seen = upstream_seen.get(name, 0)
                    upstream_seen[name] = seen + 1
                    alias = f"u:{name}" if seen == 0 else f"u:{name}#{seen + 1}"
            ref = NodeRef(node=child, container=container, index=position, id=alias or path)
            index[path] = ref
            if alias:
                index[alias] = ref
            if isinstance(child, Block) and child.raw_body is None:
                walk(child, alias or path)

    walk(tree, "")
    return index


@dataclass
class Extent:
    """
    A statement together with the comments that belong to it.

    Attributes:
        first: Index in the container of the extent's first node: its first
            leading comment, or the statement itself.
        node: Index of the statement.
        last: Index of the extent's last node: its end-of-line comment, or
            the statement itself.
        leading: The comment lines directly above it, with no blank line in
            between.
        inline: The comment on the same line, after it.
    """

    first: int
    node: int
    last: int
    leading: list[Comment] = field(default_factory=list)
    inline: Comment | None = None


@dataclass
class FreeComment:
    """
    A comment that belongs to no single statement.

    Attributes:
        comment: The comment.
        after: Index of the statement before it, None at the container start.
    """

    comment: Comment
    after: int | None


def extents(
    children: list[Node], *, starts_line: bool = False
) -> tuple[dict[int, Extent], list[FreeComment]]:
    """
    Group a container's comments with the statements they describe.

    A comment on the same line after a statement is that statement's; a run
    of comment lines directly above a statement, with no blank line between
    them, is that statement's too. Anything else (a section header followed
    by a blank line, a comment at the end of a block, one on the line of the
    opening brace) is free: the structure shows it as a note of the
    container. Edits move and remove a statement with its extent, so a note
    never ends up describing the wrong thing.

    Args:
        children: A container's children.
        starts_line: Whether the container's first child starts a line even
            without a line break in its prefix (true for the file itself).

    Returns:
        The extent of each statement, by its index, and the free comments.
    """
    result: dict[int, Extent] = {}
    free: list[FreeComment] = []
    pending: list[tuple[int, Comment]] = []
    previous: int | None = None

    def flush() -> None:
        free.extend(FreeComment(comment, previous) for _, comment in pending)
        pending.clear()

    for position, node in enumerate(children):
        blank_line = node.prefix.count("\n") >= 2
        if isinstance(node, Comment):
            same_line = "\n" not in node.prefix and (position > 0 or not starts_line)
            if same_line and previous is not None and previous == position - 1:
                result[previous].inline = node
                result[previous].last = position
                continue
            if same_line or blank_line:
                flush()
            if same_line:
                free.append(FreeComment(node, previous))
            else:
                pending.append((position, node))
            continue
        if blank_line:
            flush()
        result[position] = Extent(
            first=pending[0][0] if pending else position,
            node=position,
            last=position,
            leading=[comment for _, comment in pending],
        )
        pending.clear()
        previous = position
    flush()
    return result, free


def comment_lines(comment: Comment) -> list[str]:
    """
    A comment's text as the console shows it: without ``#`` and the space after.

    Args:
        comment: The comment.

    Returns:
        One entry per line (an Apache comment can continue over several).
    """
    lines = []
    for line in comment.text.replace("\\\r\n", "\n").replace("\\\n", "\n").split("\n"):
        text = line.strip().removeprefix("#")
        lines.append(text[1:] if text.startswith(" ") else text)
    return [line.rstrip() for line in lines]


def parse(text: str, kind: Kind) -> Tree:
    """
    Parse a site file.

    Args:
        text: The whole file.
        kind: ``"nginx"`` or ``"apache"``.

    Returns:
        The concrete tree; ``render()`` gives ``text`` back exactly.

    Raises:
        ParseError: When the web server would not read the text.
        ValueError: When ``kind`` is not a dialect this package knows.
    """
    # The dialect modules build on this one, so they are imported here rather
    # than at the top, where they would import each other in a circle.
    if kind == "nginx":
        from noust.managers.siteconf.nginx import parse_nginx

        return parse_nginx(text)
    if kind == "apache":
        from noust.managers.siteconf.apache import parse_apache

        return parse_apache(text)
    raise ValueError(f"Unknown web server kind: {kind!r}")
