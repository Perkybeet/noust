"""
Edit a site file through its tree, touching only the bytes of what changes.

Each operation is applied to the concrete tree and the file is rendered back,
so everything the operation does not name stays exactly as it was: comments,
blank lines, alignment, the quoting of untouched values. New text takes the
indentation and line endings of its surroundings. A statement moves and is
removed together with the comments that belong to it (see
:func:`noust.managers.siteconf.tree.extents`); a duplicate does not copy them.

Operations (``EditOp``), each a dict with ``op`` and its fields:

- ``set_directive``: ``target`` (any directive or block id) and ``args``:
  the new arguments of the directive, or of the head of the block. Or
  ``parent`` and ``name``: the first directive of that name in ``parent``,
  added when it is not there yet.
- ``add_directive``: ``parent`` (empty or absent for the top level),
  ``name``, ``args``; optional ``before``/``after`` (a sibling id). By default
  it goes after the last directive of the same name, else after the last
  simple directive, else before the first block.
- ``remove_directive``: ``target``, or ``parent`` and ``name``.
- ``add_block``: ``parent``, ``name``, ``args`` and ``body`` (a list of
  ``{"name", "args", "body"?}``) or ``template`` (``proxy``, ``static``,
  ``redirect``) with ``to`` and, for a redirect, ``code``; optional
  ``before``/``after``. By default it goes after the last block of the same
  name, else (anything but a server) before the first server, else at the end.
- ``remove_block``: ``target``.
- ``duplicate_block``: ``target`` and optional ``args`` for the copy's head;
  the copy goes right after the original.
- ``move_block``: ``target`` and ``before`` or ``after``.

Ids are resolved against the file as it is when the operation runs, after the
operations before it in the same list.

Nothing here writes anything: the result is text, and saving it is the site
API's one ``PUT /config``.
"""

from __future__ import annotations

import copy
import difflib
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from noust.core.exceptions import ValidationError
from noust.managers.siteconf import apache, nginx
from noust.managers.siteconf.model import IncludeReader, Serializable, SiteStructure, structure
from noust.managers.siteconf.tree import (
    Arg,
    Block,
    Comment,
    Directive,
    Kind,
    Node,
    NodeRef,
    Statement,
    Tree,
    Word,
    extents,
    node_offsets,
    parse,
)


class EditOp(TypedDict, total=False):
    """One edit operation; ``op`` names it, the other keys are its fields."""

    op: str
    target: str
    parent: str
    name: str
    args: list[str]
    body: list[dict[str, Any]]
    template: str
    to: str
    code: int
    before: str
    after: str


@dataclass
class EditResult(Serializable):
    """
    The outcome of a list of operations.

    Attributes:
        config: The edited file.
        structure: Its model, as :func:`structure` builds it.
        changed_lines: How many lines differ from the text the operations
            started from (a replaced line counts once).
    """

    config: str
    structure: SiteStructure
    changed_lines: int


_TEMPLATES = ("proxy", "static", "redirect")
_APACHE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*\Z")


def apply_ops(
    text: str,
    kind: Kind,
    ops: list[EditOp],
    *,
    read_include: IncludeReader | None = None,
) -> EditResult:
    """
    Apply edit operations to a site file's text.

    Args:
        text: The file as it is now (saved or a draft).
        kind: ``"nginx"`` or ``"apache"``.
        ops: The operations, applied in order.
        read_include: Passed to :func:`structure` for the returned model.

    Returns:
        The new text, its model and the number of changed lines.

    Raises:
        ParseError: When ``text`` itself does not parse.
        ValidationError: When an operation is malformed or names something
            that is not there; ``field`` says which (``ops[2].target``).
    """
    current = text
    tree = parse(current, kind)
    for position, op in enumerate(ops):
        where = f"ops[{position}]"
        if not isinstance(op, dict):
            raise ValidationError("Each operation must be an object", field=where)
        _Editor(tree, where).apply(dict(op))
        current = tree.render()
        # Ids and line numbers of the next operation refer to this result.
        tree = parse(current, kind)
    return EditResult(
        config=current,
        structure=structure(tree, read_include=read_include),
        changed_lines=changed_lines(text, current),
    )


def changed_lines(before: str, after: str) -> int:
    """
    Count the lines an edit changed.

    Args:
        before: The original text.
        after: The edited text.

    Returns:
        Lines added, removed or replaced; a replaced line counts once.
    """
    matcher = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False)
    return sum(
        max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"
    )


class _Editor:
    """Applies one operation to a tree, in place."""

    def __init__(self, tree: Tree, where: str) -> None:
        self.tree = tree
        self.where = where
        self.kind = tree.kind
        self.quote = nginx.quote if tree.kind == "nginx" else apache.quote
        rendered = tree.render()
        self.rendered = rendered
        self.nl = "\r\n" if "\r\n" in rendered else "\n"
        self.offsets = node_offsets(tree)
        self.refs = tree.index()
        self.unit = _indent_unit(tree, rendered, self.offsets)

    # -- validation ----------------------------------------------------------

    def fail(self, message: str, key: str, details: str = "") -> ValidationError:
        return ValidationError(message, details=details, field=f"{self.where}.{key}")

    def ref(self, op: dict[str, Any], key: str) -> NodeRef:
        value = op.get(key)
        if not isinstance(value, str) or value not in self.refs:
            raise self.fail(
                f"Nothing in this configuration has the id {value!r}",
                key,
                "Ids come from the structure of the text the operation is applied to.",
            )
        return self.refs[value]

    def container(self, op: dict[str, Any]) -> Tree | Block:
        parent = op.get("parent") or ""
        if parent == "":
            return self.tree
        ref = self.ref(op, "parent")
        if not isinstance(ref.node, Block) or ref.node.raw_body is not None:
            raise self.fail(f"{parent} has no body to add to", "parent")
        return ref.node

    def args(self, op: dict[str, Any], key: str = "args") -> list[str]:
        value = op.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise self.fail("Arguments must be a list of strings", key)
        return value

    def name(self, value: object, key: str = "name") -> str:
        if not isinstance(value, str) or not value:
            raise self.fail("A directive needs a name", key)
        valid = (
            self.quote(value) == value and not value.startswith("#")
            if self.kind == "nginx"
            else bool(_APACHE_NAME.match(value))
        )
        if not valid:
            raise self.fail(f"{value!r} is not a directive name", key)
        return value

    # -- dispatch ------------------------------------------------------------

    def apply(self, op: dict[str, Any]) -> None:
        handlers = {
            "set_directive": self.set_directive,
            "add_directive": self.add_directive,
            "remove_directive": self.remove_directive,
            "add_block": self.add_block,
            "remove_block": self.remove_block,
            "duplicate_block": self.duplicate_block,
            "move_block": self.move_block,
        }
        handler = handlers.get(str(op.get("op")))
        if handler is None:
            raise self.fail(
                f"Unknown operation {op.get('op')!r}",
                "op",
                f"Known operations: {', '.join(handlers)}.",
            )
        handler(op)

    # -- operations ----------------------------------------------------------

    def set_directive(self, op: dict[str, Any]) -> None:
        args = self.args(op)
        if op.get("target"):
            self.set_args(self.ref(op, "target").node, args)
            return
        if "name" not in op:
            raise self.fail("set_directive needs a target, or a parent and a name", "target")
        container = self.container(op)
        name = self.name(op.get("name"))
        existing = self.named_child(container, name)
        if existing is not None:
            self.set_args(existing, args)
        else:
            self.add_directive(
                {"op": "add_directive", "parent": op.get("parent", ""), "name": name, "args": args}
            )

    def add_directive(self, op: dict[str, Any]) -> None:
        name = self.name(op.get("name"))
        args = self.args(op)
        container, position = self.position(op, block=False, name=name)
        snippet = self.directive_text(name, args)
        before_block = position < len(container.children) and isinstance(
            container.children[position], Block
        )
        self.insert(
            container,
            position,
            self.snippet_nodes(snippet),
            blank=isinstance(container, Tree) and before_block,
        )

    def remove_directive(self, op: dict[str, Any]) -> None:
        if op.get("target"):
            ref = self.ref(op, "target")
            if not isinstance(ref.node, Directive):
                raise self.fail(f"{ref.id} is a block: remove it with remove_block", "target")
            self.remove(ref.container, ref.index)
            return
        container = self.container(op)
        name = self.name(op.get("name"))
        node = self.named_child(container, name)
        if node is None:
            raise self.fail(f"There is no {name} directive there", "name")
        self.remove(container, _position(container, node))

    def add_block(self, op: dict[str, Any]) -> None:
        name = self.name(op.get("name"))
        args = self.args(op)
        body = self.body(op)
        container, position = self.position(op, block=True, name=name)
        indent = self.child_indent(container, position)
        snippet = self.block_text(name, args, body, indent)
        self.insert(
            container, position, self.snippet_nodes(snippet), blank=self.blank_style(container)
        )

    def remove_block(self, op: dict[str, Any]) -> None:
        ref = self.block_ref(op)
        self.remove(ref.container, ref.index)

    def duplicate_block(self, op: dict[str, Any]) -> None:
        ref = self.block_ref(op)
        groups, _ = extents(ref.container.children, starts_line=isinstance(ref.container, Tree))
        extent = groups[ref.index]
        duplicate = copy.deepcopy(ref.node)
        if "args" in op:
            self.set_args(duplicate, self.args(op))
        first = ref.container.children[extent.first]
        self.insert(
            ref.container,
            extent.last + 1,
            [duplicate],
            blank=first.prefix.count("\n") >= 2,
            indent=self.indent_of(ref.node),
        )

    def move_block(self, op: dict[str, Any]) -> None:
        ref = self.block_ref(op)
        keys = [key for key in ("before", "after") if op.get(key)]
        if len(keys) != 1:
            raise self.fail("move_block needs exactly one of before and after", "before")
        key = keys[0]
        destination = self.ref(op, key)
        moved_ids = {id(ref.node)} | {id(n) for n in _descendants(ref.node)}
        if id(destination.node) in moved_ids:
            raise self.fail("A block cannot be moved next to itself or into itself", key)
        source_indent = self.indent_of(ref.node)
        groups, _ = extents(ref.container.children, starts_line=isinstance(ref.container, Tree))
        extent = groups[ref.index]
        nodes = ref.container.children[extent.first : extent.last + 1]
        blank = nodes[0].prefix.count("\n") >= 2
        dest_node = destination.node
        dest_container = destination.container
        dest_indent = self.indent_of(dest_node)
        self.remove(ref.container, ref.index)
        position = _position(dest_container, dest_node)
        dest_groups, _ = extents(
            dest_container.children, starts_line=isinstance(dest_container, Tree)
        )
        dest_extent = dest_groups[position]
        position = dest_extent.last + 1 if key == "after" else dest_extent.first
        if source_indent != dest_indent:
            for node in nodes:
                node.prefix = _shift(node.prefix, source_indent, dest_indent)
                _reindent(node, source_indent, dest_indent)
        self.insert(dest_container, position, nodes, blank=blank, indent=dest_indent)

    # -- building blocks -----------------------------------------------------

    def block_ref(self, op: dict[str, Any]) -> NodeRef:
        ref = self.ref(op, "target")
        if not isinstance(ref.node, Block):
            raise self.fail(f"{ref.id} is not a block", "target")
        return ref

    def named_child(self, container: Tree | Block, name: str) -> Directive | None:
        def same(node: Node) -> bool:
            if not isinstance(node, Directive):
                return False
            if self.kind == "apache":
                return node.name.value.lower() == name.lower()
            return node.name.value == name

        return next(
            (node for node in container.children if isinstance(node, Directive) and same(node)),
            None,
        )

    def set_args(self, node: Statement, values: list[str]) -> None:
        old = node.args
        if [arg.word.value for arg in old] == values:
            return
        new: list[Arg] = []
        for index, value in enumerate(values):
            if index < len(old) and old[index].word.value == value:
                previous = old[index]
                sep = previous.sep or " "
                new.append(Arg(sep, previous.word))
                continue
            sep = old[index].sep if index < len(old) and old[index].sep else " "
            new.append(Arg(sep, Word(self.quote(value), value, 0, 0)))
        node.args = new

    def body(self, op: dict[str, Any]) -> list[dict[str, Any]]:
        template = op.get("template")
        if template is None:
            body = op.get("body", [])
            if not isinstance(body, list):
                raise self.fail("A block body must be a list of directives", "body")
            self.check_body(body, "body")
            return body
        if template not in _TEMPLATES:
            raise self.fail(
                f"Unknown template {template!r}", "template", f"Templates: {', '.join(_TEMPLATES)}."
            )
        to = op.get("to")
        if not isinstance(to, str) or not to:
            raise self.fail("The template needs a destination in 'to'", "to")
        code = op.get("code", 301)
        if not isinstance(code, int) or not 300 <= code <= 399:
            raise self.fail("A redirect code is between 300 and 399", "code")
        return _template_body(self.kind, template, to, code, self.fail)

    def check_body(self, body: list[Any], key: str) -> None:
        for index, item in enumerate(body):
            here = f"{key}[{index}]"
            if not isinstance(item, dict):
                raise self.fail("Each body entry must be an object", here)
            self.name(item.get("name"), f"{here}.name")
            values = item.get("args", [])
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise self.fail("Arguments must be a list of strings", f"{here}.args")
            if "body" in item:
                if not isinstance(item["body"], list):
                    raise self.fail("A block body must be a list of directives", f"{here}.body")
                self.check_body(item["body"], f"{here}.body")

    def directive_text(self, name: str, args: list[str]) -> str:
        words = " ".join([name, *(self.quote(value) for value in args)])
        return words + (";" if self.kind == "nginx" else "")

    def block_text(
        self, name: str, args: list[str], body: list[dict[str, Any]], indent: str
    ) -> str:
        head = " ".join([name, *(self.quote(value) for value in args)])
        inner = indent + self.unit
        lines = [f"{head} {{" if self.kind == "nginx" else f"<{head}>"]
        for item in body:
            item_args = list(item.get("args", []))
            if "body" in item:
                lines.append(inner + self.block_text(item["name"], item_args, item["body"], inner))
            else:
                lines.append(inner + self.directive_text(item["name"], item_args))
        lines.append(indent + ("}" if self.kind == "nginx" else f"</{name}>"))
        return self.nl.join(lines)

    def snippet_nodes(self, snippet: str) -> list[Node]:
        nodes = parse(snippet, self.kind).children
        if len(nodes) != 1:
            raise self.fail("The operation does not produce a single statement", "name")
        return nodes

    def position(self, op: dict[str, Any], *, block: bool, name: str) -> tuple[Tree | Block, int]:
        """Where a new statement goes: the container and the index in its children."""
        for key in ("before", "after"):
            if op.get(key):
                ref = self.ref(op, key)
                if (
                    op.get("parent") is not None
                    and op.get("parent") != ""
                    and self.container(op) is not ref.container
                ):
                    raise self.fail(f"{ref.id} is not inside {op.get('parent')}", key)
                groups, _ = extents(
                    ref.container.children, starts_line=isinstance(ref.container, Tree)
                )
                extent = groups[ref.index]
                return ref.container, extent.last + 1 if key == "after" else extent.first
        container = self.container(op)
        children = container.children
        groups, _ = extents(children, starts_line=isinstance(container, Tree))

        def after(index: int) -> int:
            return groups[index].last + 1

        def same_name(node: Node) -> bool:
            if isinstance(node, Comment) or isinstance(node, Block) != block:
                return False
            if self.kind == "apache":
                return node.name.value.lower() == name.lower()
            return node.name.value == name

        same = [index for index, node in enumerate(children) if same_name(node)]
        if same:
            return container, after(same[-1])
        if block:
            if name.lower() not in ("server", "virtualhost"):
                servers = [
                    index
                    for index, node in enumerate(children)
                    if isinstance(node, Block)
                    and node.name.value.lower() in ("server", "virtualhost")
                ]
                if servers:
                    return container, groups[servers[0]].first
            return container, len(children)
        plain = [index for index, node in enumerate(children) if isinstance(node, Directive)]
        if plain:
            return container, after(plain[-1])
        blocks = [index for index, node in enumerate(children) if isinstance(node, Block)]
        if blocks:
            return container, groups[blocks[0]].first
        return container, len(children)

    def blank_style(self, container: Tree | Block) -> bool:
        """Whether the container separates its blocks with blank lines."""
        return any(
            isinstance(node, Block) and node.prefix.count("\n") >= 2 for node in container.children
        ) or any(
            isinstance(node, Comment) and node.prefix.count("\n") >= 2
            for node in container.children
        )

    def indent_of(self, node: Node) -> str:
        """Indentation of the line a node starts on."""
        _, start, _ = self.offsets[id(node)]
        line_start = self.rendered.rfind("\n", 0, start) + 1
        match = re.match(r"[ \t]*", self.rendered[line_start:start])
        return match.group(0) if match else ""

    def container_indent(self, container: Tree | Block) -> str:
        return "" if isinstance(container, Tree) else self.indent_of(container)

    def child_indent(self, container: Tree | Block, position: int) -> str:
        """Indentation for a new child at ``position``: its neighbours', else one level in."""
        children = container.children
        neighbours = [children[position - 1]] if position > 0 else []
        neighbours += children[position : position + 1]
        neighbours += children
        for node in neighbours:
            if not isinstance(node, Comment) and "\n" in node.prefix:
                return self.indent_of(node)
        if isinstance(container, Tree):
            return ""
        return self.container_indent(container) + self.unit

    def insert(
        self,
        container: Tree | Block,
        position: int,
        nodes: list[Node],
        *,
        blank: bool,
        indent: str | None = None,
    ) -> None:
        """Insert nodes so that the text gains one contiguous run and loses nothing."""
        if indent is None:
            indent = self.child_indent(container, position)
        children = container.children
        lead = self.nl * (2 if blank else 1)
        at_file_start = isinstance(container, Tree) and position == 0
        if at_file_start:
            nodes[0].prefix = ""
            if children:
                following = children[0]
                following.prefix = (
                    self.nl if "\n" in following.prefix else lead
                ) + following.prefix
            elif container.trailing == "":
                container.trailing = self.nl
        else:
            nodes[0].prefix = (self.nl if position == 0 else lead) + indent
            if (
                position == len(children)
                and container.trailing == ""
                and isinstance(container, Block)
            ):
                container.trailing = self.nl + self.container_indent(container)
        children[position:position] = nodes

    def remove(self, container: Tree | Block, index: int) -> None:
        """Remove a statement with its comments as one contiguous run of text."""
        children = container.children
        groups, _ = extents(children, starts_line=isinstance(container, Tree))
        extent = groups[index]
        first = children[extent.first]
        at_file_start = isinstance(container, Tree) and extent.first == 0
        del children[extent.first : extent.last + 1]
        if at_file_start and "\n" not in first.prefix:
            # The removed text started the file: the line break that ended it
            # goes too, so the file does not start with an empty line.
            if extent.first < len(children):
                children[extent.first].prefix = children[extent.first].prefix.lstrip("\r\n")
            else:
                container.trailing = container.trailing.lstrip("\r\n")


def _template_body(
    kind: Kind,
    template: str,
    to: str,
    code: int,
    fail: Callable[[str, str, str], ValidationError],
) -> list[dict[str, Any]]:
    if kind == "nginx":
        if template == "proxy":
            return [
                {"name": "proxy_pass", "args": [to]},
                {"name": "proxy_http_version", "args": ["1.1"]},
                {"name": "proxy_set_header", "args": ["Host", "$host"]},
                {"name": "proxy_set_header", "args": ["X-Real-IP", "$remote_addr"]},
                {
                    "name": "proxy_set_header",
                    "args": ["X-Forwarded-For", "$proxy_add_x_forwarded_for"],
                },
                {"name": "proxy_set_header", "args": ["X-Forwarded-Proto", "$scheme"]},
            ]
        if template == "static":
            return [{"name": "alias", "args": [to]}]
        return [{"name": "return", "args": [str(code), to]}]
    if template == "proxy":
        return [{"name": "ProxyPass", "args": [to]}, {"name": "ProxyPassReverse", "args": [to]}]
    if template == "redirect":
        return [{"name": "Redirect", "args": [str(code), to]}]
    raise fail(
        "Apache serves files from DocumentRoot or an Alias, not from a <Location>",
        "template",
        "Add an Alias directive to the virtual host instead.",
    )


def _position(container: Tree | Block, node: Node) -> int:
    return next(index for index, child in enumerate(container.children) if child is node)


def _descendants(node: Node) -> list[Node]:
    if not isinstance(node, Block):
        return []
    found: list[Node] = []
    for child in node.children:
        found.append(child)
        found.extend(_descendants(child))
    return found


def _shift(text: str, old: str, new: str) -> str:
    return re.sub(r"\n" + re.escape(old), lambda _: "\n" + new, text)


def _reindent(node: Node, old: str, new: str) -> None:
    """Change the indentation of every line inside a moved node."""

    def fix(text: str) -> str:
        return _shift(text, old, new)

    if isinstance(node, Comment):
        node.text = fix(node.text)
        return
    for arg in node.args:
        arg.sep = fix(arg.sep)
    node.tail = fix(node.tail)
    if isinstance(node, Block):
        if node.raw_body is not None:
            node.raw_body = fix(node.raw_body)
        for child in node.children:
            child.prefix = fix(child.prefix)
            _reindent(child, old, new)
        node.trailing = fix(node.trailing)


def _indent_unit(tree: Tree, rendered: str, offsets: dict[int, tuple[int, int, int]]) -> str:
    """The indentation step the file uses (four spaces when it shows none)."""

    def indent(start: int) -> str:
        line_start = rendered.rfind("\n", 0, start) + 1
        match = re.match(r"[ \t]*", rendered[line_start:start])
        return match.group(0) if match else ""

    steps: Counter[str] = Counter()

    def walk(container: Tree | Block, base: str) -> None:
        for child in container.children:
            if isinstance(child, Comment) or "\n" not in child.prefix:
                continue
            own = indent(offsets[id(child)][1])
            if own.startswith(base) and len(own) > len(base):
                steps[own[len(base) :]] += 1
            if isinstance(child, Block) and child.raw_body is None:
                walk(child, own)

    walk(tree, "")
    return steps.most_common(1)[0][0] if steps else "    "
