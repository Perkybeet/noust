"""
The structured model of a site file, common to nginx and Apache.

This is what the console's Structure view and the diagram are drawn from:
servers with their listens, names and TLS; locations with what they hand the
request to (a proxy, files on disk, a redirect, PHP); upstreams with their
servers; the includes; and every comment, attached to the element it sits on
or kept as a note of its container.

Nothing in the file is left out. What the model has no field for (Lua,
``map``, ``if``, third-party modules) is listed as a raw directive of the
element it belongs to, with its exact text and an id the edit operations
accept, so the console can show it and change it. ``modeled`` on a raw
directive says whether one of the element's fields already reads it.

Every element carries the stable id of :mod:`noust.managers.siteconf.tree`.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from noust.managers.siteconf.tree import (
    Block,
    Comment,
    Directive,
    Node,
    ParseError,
    Statement,
    Tree,
    comment_lines,
    extents,
    is_location,
    is_server,
    parse,
    upstream_name,
)

#: Reads an include: given the pattern exactly as the file writes it, the
#: files it matches as ``(path, text)``. It decides what may be read (Noust's
#: own files and ``/etc/nginx``) and raises ``OSError`` or ``ValueError`` for
#: anything else, which is recorded on the include rather than raised.
IncludeReader = Callable[[str], Sequence[tuple[str, str]]]

TargetKind = Literal["proxy", "static", "return", "fastcgi", "other"]


class Serializable:
    """Turns a model dataclass into the plain dict the API returns."""

    def to_dict(self) -> dict[str, Any]:
        """
        The element as JSON-ready data.

        Returns:
            Nested dicts and lists of plain values.
        """
        # Every subclass is a dataclass; the mixin itself cannot say so to mypy.
        data: dict[str, Any] = dataclasses.asdict(cast(Any, self))
        return data


@dataclass
class RawDirective(Serializable):
    """
    A directive as written, for the "more directives" list.

    Attributes:
        id: Its id, accepted by every edit operation.
        name: The directive's name.
        args: Its arguments, as the web server reads them.
        text: Its exact source text (the whole block for a block).
        block: Whether it has a body.
        line: First line.
        end_line: Last line.
        comments: Comment lines attached to it.
        modeled: Whether a field of its element already shows it.
    """

    id: str
    name: str
    args: list[str]
    text: str
    block: bool
    line: int
    end_line: int
    comments: list[str] = field(default_factory=list)
    modeled: bool = False


@dataclass
class Note(Serializable):
    """
    A comment that belongs to no single element.

    Attributes:
        text: The comment, without ``#``.
        line: Its line.
        after: Id of the element it follows, None at the container start.
    """

    text: str
    line: int
    after: str | None


@dataclass
class Listen(Serializable):
    """
    One address and port a server accepts connections on.

    Attributes:
        id: The directive's id (None for an Apache ``<VirtualHost>`` address).
        address: Address part (``*``, ``127.0.0.1``, ``[::]``, ``unix:...``),
            None when only a port is written.
        port: The port, None for a Unix socket.
        ssl: Whether TLS is spoken on it.
        http2: Whether HTTP/2 is enabled on it.
        quic: Whether it is an HTTP/3 (QUIC) listener.
        ipv6: Whether the address is IPv6.
        default_server: Whether the server is the default for this address.
        raw: The arguments as written.
    """

    id: str | None
    address: str | None
    port: int | None
    ssl: bool = False
    http2: bool = False
    quic: bool = False
    ipv6: bool = False
    default_server: bool = False
    raw: str = ""


@dataclass
class Tls(Serializable):
    """
    A server's TLS settings.

    Attributes:
        certificate: Certificate path.
        key: Private key path.
        protocols: Protocol versions allowed.
    """

    certificate: str | None = None
    key: str | None = None
    protocols: list[str] = field(default_factory=list)


@dataclass
class Header(Serializable):
    """
    A response header the element adds, or a header passed to a backend.

    Attributes:
        id: The directive's id.
        name: Header name.
        value: Header value.
        always: Whether it is sent on error responses too.
    """

    id: str
    name: str
    value: str
    always: bool = False


@dataclass
class Return(Serializable):
    """
    A server-level ``return`` or ``Redirect``.

    Attributes:
        id: The directive's id.
        code: Status code.
        destination: URL or text.
    """

    id: str
    code: int | None
    destination: str | None


@dataclass
class Target(Serializable):
    """
    What a location hands the request to.

    Attributes:
        kind: ``proxy``, ``static``, ``return``, ``fastcgi`` or ``other``.
        directive: Id of the directive that decides it.
        url: The proxy URL as written.
        protocol: ``http``, ``https``, ``grpc``, ``uwsgi``...
        upstream: The upstream it reaches, when the model defines it.
        host: Host part of the proxy URL (an upstream name defined in
            another file looks like this).
        port: Port of the proxy URL.
        address: Backend address of a FastCGI/uWSGI/memcached handler.
        root: Directory files are served from (``root``).
        alias: Directory the location maps to (``alias``).
        inherited: Whether ``root`` comes from an enclosing block.
        code: Status code of a ``return``.
        destination: URL or text of a ``return``.
        detail: What an ``other`` location does, in a few words.
    """

    kind: TargetKind
    directive: str | None = None
    url: str | None = None
    protocol: str | None = None
    upstream: str | None = None
    host: str | None = None
    port: int | None = None
    address: str | None = None
    root: str | None = None
    alias: str | None = None
    inherited: bool = False
    code: int | None = None
    destination: str | None = None
    detail: str | None = None


@dataclass
class Settings(Serializable):
    """
    The settings the Structure view edits in line.

    Attributes:
        read_timeout: Backend read timeout as written (``90s``).
        send_timeout: Backend send timeout.
        connect_timeout: Backend connect timeout.
        client_max_body_size: Largest request body accepted.
        limit_req: ``{"zone", "burst", "nodelay"}`` of a ``limit_req``.
        websocket: Whether the Upgrade header is passed on.
        buffering: Whether response buffering is on, None when not set.
        expires: Cache lifetime given to responses.
        cache: Cache zone used.
        deny: Whether every client is refused (``deny all``).
        headers: Response headers added.
        proxy_headers: Headers passed to the backend.
    """

    read_timeout: str | None = None
    send_timeout: str | None = None
    connect_timeout: str | None = None
    client_max_body_size: str | None = None
    limit_req: dict[str, Any] | None = None
    websocket: bool = False
    buffering: bool | None = None
    expires: str | None = None
    cache: str | None = None
    deny: bool = False
    headers: list[Header] = field(default_factory=list)
    proxy_headers: list[Header] = field(default_factory=list)


@dataclass
class Location(Serializable):
    """
    A location, or an Apache ``ProxyPass`` rule, which plays the same part.

    Attributes:
        id: Its id.
        modifier: ``=``, ``^~``, ``~``, ``~*``, ``@`` or empty for a prefix.
        path: The path, regex or name (without ``@``).
        source: What declares it (``location``, ``Location``,
            ``LocationMatch``, ``ProxyPass``, ``ProxyPassMatch``).
        line: First line.
        end_line: Last line.
        target: What it hands the request to.
        settings: Its in-line settings.
        directives: All its directives, raw.
        locations: Nested locations, in file order.
        evaluation_order: Ids of the nested locations in nginx's order.
        comments: Comment lines attached to it.
        notes: Comments inside it that belong to nothing in particular.
    """

    id: str
    modifier: str
    path: str
    source: str
    line: int
    end_line: int
    target: Target
    settings: Settings
    directives: list[RawDirective] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    evaluation_order: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


@dataclass
class Server(Serializable):
    """
    A virtual server: an nginx ``server`` or an Apache ``<VirtualHost>``.

    Attributes:
        id: Its id (``s0``, ``s1``...).
        line: First line.
        end_line: Last line.
        listens: Addresses and ports.
        names: Host names, in order.
        tls: TLS settings, None when the server speaks no TLS.
        http2: Whether HTTP/2 is on.
        root: Document root.
        headers: Response headers added.
        gzip: Whether compression is on, None when not set.
        client_max_body_size: Largest request body accepted.
        returns: A ``return``/``Redirect`` that answers every request.
        rewrites: Ids of server-level rewrite rules.
        locations: Locations, in file order.
        evaluation_order: Location ids in the order the web server tries them.
        directives: Every other directive, raw.
        comments: Comment lines attached to it.
        notes: Comments inside it that belong to nothing in particular.
    """

    id: str
    line: int
    end_line: int
    listens: list[Listen] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    tls: Tls | None = None
    http2: bool = False
    root: str | None = None
    headers: list[Header] = field(default_factory=list)
    gzip: bool | None = None
    client_max_body_size: str | None = None
    returns: Return | None = None
    rewrites: list[str] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    evaluation_order: list[str] = field(default_factory=list)
    directives: list[RawDirective] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


@dataclass
class UpstreamServer(Serializable):
    """
    One backend of an upstream.

    Attributes:
        address: ``host:port``, ``unix:/path`` or a URL (Apache).
        params: Its parameters (``weight=5``, ``backup``...).
        id: The directive's id, None when it comes from an included file.
        source: Path of the included file it comes from, None when written
            in the site file itself.
    """

    address: str
    params: list[str] = field(default_factory=list)
    id: str | None = None
    source: str | None = None


@dataclass
class Upstream(Serializable):
    """
    A named group of backends.

    Attributes:
        id: ``u:<name>``.
        name: Its name.
        line: First line (in ``source`` when it has one).
        end_line: Last line.
        servers: Its backends, included ones too.
        keepalive: Idle connections kept open, None when not set.
        directives: All its directives, raw.
        comments: Comment lines attached to it.
        notes: Comments inside it that belong to nothing in particular.
        used_by: Ids of the locations that reach it.
        source: Path of the included file defining it, None when it is in
            the site file (and so editable).
    """

    id: str
    name: str
    line: int
    end_line: int
    servers: list[UpstreamServer] = field(default_factory=list)
    keepalive: int | None = None
    directives: list[RawDirective] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    used_by: list[str] = field(default_factory=list)
    source: str | None = None


@dataclass
class IncludedFile(Serializable):
    """
    A file an include brought in, read-only.

    Attributes:
        path: Where it is.
        text: Its content.
    """

    path: str
    text: str


@dataclass
class Include(Serializable):
    """
    An ``include`` directive and what it resolved to.

    Attributes:
        id: The directive's id.
        parent: Id of the element it is in, empty at the top level.
        pattern: The path or glob as written.
        line: Its line.
        files: The files it matched, when the caller could read them.
        error: Why it could not be resolved, None when it was or when no
            reader was given.
    """

    id: str
    parent: str
    pattern: str
    line: int
    files: list[IncludedFile] = field(default_factory=list)
    error: str | None = None


@dataclass
class SiteStructure(Serializable):
    """
    Everything in a site file, structured.

    Attributes:
        kind: ``nginx`` or ``apache``.
        servers: Virtual servers, in file order.
        upstreams: Upstreams, in file order, then those from includes.
        includes: Every include, wherever it is.
        directives: Top-level directives that are not servers or upstreams.
        notes: Top-level comments that belong to nothing in particular.
    """

    kind: str
    servers: list[Server] = field(default_factory=list)
    upstreams: list[Upstream] = field(default_factory=list)
    includes: list[Include] = field(default_factory=list)
    directives: list[RawDirective] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


def structure(tree: Tree, *, read_include: IncludeReader | None = None) -> SiteStructure:
    """
    Build the structured model of a parsed site file.

    Args:
        tree: The parsed file.
        read_include: Reads included files (see :data:`IncludeReader`); when
            None, includes are listed but not resolved. This is the only way
            the model touches anything outside the text.

    Returns:
        The model. Building it never fails on a tree that parsed.
    """
    builder: _Builder = (
        _NginxBuilder(tree, read_include)
        if tree.kind == "nginx"
        else _ApacheBuilder(tree, read_include)
    )
    return builder.build()


# Directives a field of their element reads, so the console can tell them
# apart from the rest in the raw list.
_NGINX_SERVER_MODELED = frozenset(
    {
        "listen",
        "server_name",
        "ssl_certificate",
        "ssl_certificate_key",
        "ssl_protocols",
        "http2",
        "add_header",
        "gzip",
        "client_max_body_size",
        "return",
        "root",
        "rewrite",
        "include",
    }
)
_PASS_DIRECTIVES = {
    "proxy_pass": "proxy",
    "grpc_pass": "proxy",
    "uwsgi_pass": "proxy",
    "scgi_pass": "proxy",
    "memcached_pass": "proxy",
    "fastcgi_pass": "fastcgi",
}
_TIMEOUT_PREFIXES = ("proxy", "fastcgi", "grpc", "uwsgi", "scgi")
_NGINX_LOCATION_MODELED = frozenset(
    {
        *_PASS_DIRECTIVES,
        *(f"{p}_{t}_timeout" for p in _TIMEOUT_PREFIXES for t in ("read", "send", "connect")),
        "root",
        "alias",
        "return",
        "client_max_body_size",
        "limit_req",
        "proxy_buffering",
        "expires",
        "add_header",
        "proxy_cache",
        "fastcgi_cache",
        "deny",
        "proxy_set_header",
        "include",
    }
)
# A location with one of these answers on its own, whatever root it inherits.
_CONTENT_HANDLERS = frozenset(
    {
        "content_by_lua_block",
        "content_by_lua",
        "content_by_lua_file",
        "js_content",
        "stub_status",
        "perl",
        "empty_gif",
        "flv",
        "mp4",
        "dav_methods",
    }
)
_APACHE_SERVER_MODELED = frozenset(
    {
        "servername",
        "serveralias",
        "sslengine",
        "sslcertificatefile",
        "sslcertificatekeyfile",
        "sslprotocol",
        "protocols",
        "documentroot",
        "header",
        "limitrequestbody",
        "redirect",
        "include",
        "includeoptional",
    }
)


class _Builder:
    """What both dialects share: ids, comments, raw directives, includes."""

    def __init__(self, tree: Tree, read_include: IncludeReader | None) -> None:
        self.tree = tree
        self.read_include = read_include
        self.ids = {id(ref.node): ref.id for ref in tree.index().values()}
        self.model = SiteStructure(kind=tree.kind)

    def build(self) -> SiteStructure:
        raise NotImplementedError

    def id_of(self, node: Statement) -> str:
        return self.ids[id(node)]

    def container_parts(
        self, children: list[Node], *, starts_line: bool = False
    ) -> tuple[list[tuple[Statement, list[str]]], list[Note]]:
        """A container's statements with their comments, and its notes."""
        groups, free = extents(children, starts_line=starts_line)
        statements: list[tuple[Statement, list[str]]] = []
        for position, extent in sorted(groups.items()):
            node = children[position]
            if isinstance(node, Comment):
                continue
            lines = [line for c in extent.leading for line in comment_lines(c)]
            if extent.inline is not None:
                lines.extend(comment_lines(extent.inline))
            statements.append((node, lines))
        notes: list[Note] = []
        for item in free:
            after_node = children[item.after] if item.after is not None else None
            after = (
                self.id_of(after_node)
                if after_node is not None and not isinstance(after_node, Comment)
                else None
            )
            notes.extend(
                Note(text=line, line=item.comment.line, after=after)
                for line in comment_lines(item.comment)
            )
        return statements, notes

    def raw(self, node: Statement, comments: list[str], modeled: frozenset[str]) -> RawDirective:
        name = node.name.value
        return RawDirective(
            id=self.id_of(node),
            name=name,
            args=values(node),
            text=node.render_text(),
            block=isinstance(node, Block),
            line=node.line,
            end_line=node.end_line,
            comments=comments,
            modeled=(name.lower() if self.tree.kind == "apache" else name) in modeled,
        )

    def include(self, node: Statement, parent: str) -> Include:
        args = values(node)
        entry = Include(
            id=self.id_of(node), parent=parent, pattern=args[0] if args else "", line=node.line
        )
        self.model.includes.append(entry)
        if self.read_include is None or not args:
            return entry
        try:
            matched = self.read_include(entry.pattern)
        except (OSError, ValueError) as exc:
            entry.error = str(exc) or type(exc).__name__
            return entry
        entry.files = [IncludedFile(path=path, text=text) for path, text in matched]
        return entry

    def included_trees(self, entry: Include) -> list[tuple[str, Tree]]:
        """Parse what an include brought in; a file that does not parse is its error."""
        trees = []
        for included in entry.files:
            try:
                trees.append((included.path, parse(included.text, self.tree.kind)))
            except ParseError as exc:
                entry.error = (
                    f"{included.path}: {exc.message} (line {exc.line}, column {exc.column})"
                )
        return trees

    def link_upstreams(self) -> None:
        """Point proxy targets at the upstreams they name, and back."""
        by_name: dict[str, Upstream] = {}
        for upstream in self.model.upstreams:
            by_name.setdefault(upstream.name, upstream)

        def walk(locations: list[Location]) -> None:
            for location in locations:
                target = location.target
                # nginx reads ``http://name`` without a port as an upstream when
                # one has that name; Apache only through ``balancer://``.
                names_upstream = target.port is None and (
                    target.protocol == "balancer"
                    if self.tree.kind == "apache"
                    else target.protocol != "balancer"
                )
                if target.kind == "proxy" and names_upstream and target.host in by_name:
                    target.upstream = target.host
                    by_name[target.host].used_by.append(location.id)
                walk(location.locations)

        for server in self.model.servers:
            walk(server.locations)


def values(node: Statement) -> list[str]:
    """
    A statement's arguments as the web server reads them.

    Args:
        node: A directive or block.

    Returns:
        The decoded argument values.
    """
    return [arg.word.value for arg in node.args]


def _first(nodes: list[Statement], name: str) -> Statement | None:
    return next((node for node in nodes if node.name.value == name), None)


def _int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None


def split_url(url: str) -> tuple[str | None, str | None, int | None]:
    """
    Split a backend URL into scheme, host and port.

    Args:
        url: ``http://host:port/uri``, ``grpc://...``, ``host:port`` or ``unix:...``.

    Returns:
        Scheme (None when absent), host (None for a Unix socket) and port.
    """
    scheme: str | None = None
    rest = url
    if "://" in url:
        scheme, rest = url.split("://", 1)
        scheme = scheme.lower()
    if rest.startswith("unix:"):
        return scheme, None, None
    hostport = re.split(r"[/?]", rest, maxsplit=1)[0]
    if hostport.startswith("["):
        host, _, tail = hostport.partition("]")
        port = tail[1:] if tail.startswith(":") else None
        return scheme, host + "]", _int(port)
    host, sep, port_text = hostport.rpartition(":")
    if sep and port_text.isdigit():
        return scheme, host, int(port_text)
    return scheme, hostport, None


def evaluation_order(locations: list[Location]) -> list[str]:
    """
    Location ids in the order nginx considers them.

    Exact matches end the search at once; then the longest matching prefix
    is found (so prefixes are listed longest first); then regular
    expressions are tried in file order; named locations are only reached by
    an internal redirect.

    Args:
        locations: Sibling locations, in file order.

    Returns:
        Their ids, reordered.
    """
    exact = [loc.id for loc in locations if loc.modifier == "="]
    prefixes = sorted(
        (loc for loc in locations if loc.modifier in ("", "^~")), key=lambda loc: -len(loc.path)
    )
    regexes = [loc.id for loc in locations if loc.modifier in ("~", "~*")]
    named = [loc.id for loc in locations if loc.modifier == "@"]
    return exact + [loc.id for loc in prefixes] + regexes + named


def _location_modifier(args: list[str]) -> tuple[str, str]:
    if len(args) >= 2 and args[0] in ("=", "^~", "~", "~*"):
        return args[0], args[1]
    if len(args) == 1:
        value = args[0]
        for modifier in ("^~", "~*", "~", "=", "@"):
            if value.startswith(modifier):
                return modifier, value[len(modifier) :]
        return "", value
    return "", " ".join(args)


class _NginxBuilder(_Builder):
    def build(self) -> SiteStructure:
        self.walk_context(self.tree, "", top=True)
        self.link_upstreams()
        return self.model

    def walk_context(self, container: Tree | Block, parent_id: str, *, top: bool) -> None:
        """The main or ``http`` context: servers, upstreams and the rest."""
        statements, notes = self.container_parts(container.children, starts_line=top)
        if top:
            self.model.notes.extend(notes)
        for node, comments in statements:
            if is_server(node, container, "nginx") and isinstance(node, Block):
                self.model.servers.append(self.server(node, comments))
                continue
            name = upstream_name(node, container, "nginx")
            if name is not None and isinstance(node, Block):
                self.model.upstreams.append(self.upstream(node, name, comments))
                continue
            if node.name.value == "include":
                entry = self.include(node, parent_id)
                self.upstreams_from(entry)
            if top:
                self.model.directives.append(self.raw(node, comments, frozenset({"include"})))
            if isinstance(node, Block) and node.name.value == "http" and node.raw_body is None:
                self.walk_context(node, self.id_of(node), top=False)

    def upstreams_from(self, entry: Include) -> None:
        for path, included in self.included_trees(entry):
            for child in included.children:
                name = upstream_name(child, included, "nginx")
                if name is None or not isinstance(child, Block):
                    continue
                upstream = Upstream(
                    id=f"u:{name}", name=name, line=child.line, end_line=child.end_line, source=path
                )
                for item in child.children:
                    if isinstance(item, Directive) and item.name.value == "server" and item.args:
                        args = values(item)
                        upstream.servers.append(UpstreamServer(args[0], args[1:], None, path))
                self.model.upstreams.append(upstream)

    def upstream(self, node: Block, name: str, comments: list[str]) -> Upstream:
        upstream = Upstream(
            id=self.id_of(node),
            name=name,
            line=node.line,
            end_line=node.end_line,
            comments=comments,
        )
        statements, upstream.notes = self.container_parts(node.children)
        for child, child_comments in statements:
            args = values(child)
            child_name = child.name.value
            upstream.directives.append(
                self.raw(child, child_comments, frozenset({"server", "keepalive", "include"}))
            )
            if child_name == "server" and args:
                upstream.servers.append(UpstreamServer(args[0], args[1:], self.id_of(child)))
            elif child_name == "keepalive" and args:
                upstream.keepalive = _int(args[0])
            elif child_name == "include":
                entry = self.include(child, upstream.id)
                for path, included in self.included_trees(entry):
                    for item in included.children:
                        if (
                            isinstance(item, Directive)
                            and item.name.value == "server"
                            and item.args
                        ):
                            item_args = values(item)
                            upstream.servers.append(
                                UpstreamServer(item_args[0], item_args[1:], None, path)
                            )
        return upstream

    def server(self, node: Block, comments: list[str]) -> Server:
        server = Server(
            id=self.id_of(node), line=node.line, end_line=node.end_line, comments=comments
        )
        statements, server.notes = self.container_parts(node.children)
        plain = [child for child, _ in statements if not is_location(child, "nginx")]
        server.root = _arg(_first(plain, "root"))
        for child, child_comments in statements:
            if is_location(child, "nginx") and isinstance(child, Block):
                server.locations.append(self.location(child, child_comments, server.root))
                continue
            server.directives.append(self.raw(child, child_comments, _NGINX_SERVER_MODELED))
            self.server_directive(server, child)
        if any(listen.http2 for listen in server.listens):
            server.http2 = True
        has_tls = any(listen.ssl for listen in server.listens) or _first(plain, "ssl_certificate")
        if has_tls:
            server.tls = Tls(
                certificate=_arg(_first(plain, "ssl_certificate")),
                key=_arg(_first(plain, "ssl_certificate_key")),
                protocols=values(protocols)
                if (protocols := _first(plain, "ssl_protocols"))
                else [],
            )
        server.evaluation_order = evaluation_order(server.locations)
        return server

    def server_directive(self, server: Server, child: Statement) -> None:
        name = child.name.value
        args = values(child)
        if name == "listen" and args:
            server.listens.append(_nginx_listen(self.id_of(child), args))
        elif name == "server_name":
            server.names.extend(args)
        elif name == "http2":
            server.http2 = args[:1] == ["on"]
        elif name == "add_header" and len(args) >= 2:
            server.headers.append(Header(self.id_of(child), args[0], args[1], "always" in args[2:]))
        elif name == "gzip" and args:
            server.gzip = args[0] == "on"
        elif name == "client_max_body_size" and args:
            server.client_max_body_size = args[0]
        elif name == "return" and args:
            server.returns = Return(self.id_of(child), *_return_parts(args))
        elif name == "rewrite":
            server.rewrites.append(self.id_of(child))
        elif name == "include":
            self.include(child, server.id)

    def location(self, node: Block, comments: list[str], inherited_root: str | None) -> Location:
        modifier, path = _location_modifier(values(node))
        statements, notes = self.container_parts(node.children)
        plain = [child for child, _ in statements if not is_location(child, "nginx")]
        own_root = _arg(_first(plain, "root"))
        root = own_root or inherited_root
        location = Location(
            id=self.id_of(node),
            modifier=modifier,
            path=path,
            source="location",
            line=node.line,
            end_line=node.end_line,
            target=self.target(plain, own_root, inherited_root),
            settings=self.settings(plain),
            comments=comments,
            notes=notes,
        )
        for child, child_comments in statements:
            if is_location(child, "nginx") and isinstance(child, Block):
                location.locations.append(self.location(child, child_comments, root))
                continue
            location.directives.append(self.raw(child, child_comments, _NGINX_LOCATION_MODELED))
            if child.name.value == "include":
                self.include(child, location.id)
        location.evaluation_order = evaluation_order(location.locations)
        return location

    def target(self, plain: list[Statement], own_root: str | None, inherited: str | None) -> Target:
        names = {node.name.value for node in plain}
        returned = _first(plain, "return")
        if returned is not None:
            code, destination = _return_parts(values(returned))
            return Target("return", self.id_of(returned), code=code, destination=destination)
        for directive, kind in _PASS_DIRECTIVES.items():
            node = _first(plain, directive)
            if node is None or not node.args:
                continue
            url = values(node)[0]
            if kind == "fastcgi":
                return Target("fastcgi", self.id_of(node), address=url, protocol="fastcgi")
            scheme, host, port = split_url(url)
            protocol = scheme or directive.removesuffix("_pass")
            if directive != "proxy_pass" and scheme is None:
                return Target("proxy", self.id_of(node), url=url, protocol=protocol, address=url)
            # ``upstream`` is filled in by link_upstreams, once every upstream
            # (later in the file, or in an include) is known.
            return Target(
                "proxy", self.id_of(node), url=url, protocol=protocol, host=host, port=port
            )
        alias = _first(plain, "alias")
        if alias is not None and alias.args:
            return Target("static", self.id_of(alias), alias=values(alias)[0])
        root_node = _first(plain, "root")
        if root_node is not None and own_root is not None:
            return Target("static", self.id_of(root_node), root=own_root)
        denies_all = any(n.name.value == "deny" and values(n)[:1] == ["all"] for n in plain)
        if inherited is not None and not denies_all and not names & _CONTENT_HANDLERS:
            return Target("static", root=inherited, inherited=True)
        handler = sorted(names & _CONTENT_HANDLERS)
        detail = "deny all" if denies_all else (handler[0] if handler else None)
        return Target("other", detail=detail)

    def settings(self, plain: list[Statement]) -> Settings:
        settings = Settings()

        def timeout(kind: str) -> str | None:
            for prefix in _TIMEOUT_PREFIXES:
                node = _first(plain, f"{prefix}_{kind}_timeout")
                if node is not None and node.args:
                    return values(node)[0]
            return None

        settings.read_timeout = timeout("read")
        settings.send_timeout = timeout("send")
        settings.connect_timeout = timeout("connect")
        settings.client_max_body_size = _arg(_first(plain, "client_max_body_size"))
        limit = _first(plain, "limit_req")
        if limit is not None:
            parts = dict(arg.split("=", 1) for arg in values(limit) if "=" in arg)
            settings.limit_req = {
                "zone": parts.get("zone"),
                "burst": parts.get("burst"),
                "nodelay": "nodelay" in values(limit),
            }
        buffering = _arg(_first(plain, "proxy_buffering"))
        settings.buffering = None if buffering is None else buffering == "on"
        expires = _first(plain, "expires")
        settings.expires = " ".join(values(expires)) if expires is not None else None
        settings.cache = _arg(_first(plain, "proxy_cache")) or _arg(_first(plain, "fastcgi_cache"))
        for node in plain:
            args = values(node)
            if node.name.value == "deny" and args[:1] == ["all"]:
                settings.deny = True
            elif node.name.value == "add_header" and len(args) >= 2:
                settings.headers.append(
                    Header(self.id_of(node), args[0], args[1], "always" in args[2:])
                )
            elif node.name.value == "proxy_set_header" and len(args) >= 2:
                settings.proxy_headers.append(Header(self.id_of(node), args[0], args[1]))
                if args[0].lower() == "upgrade":
                    settings.websocket = True
        return settings


def _arg(node: Statement | None) -> str | None:
    return node.args[0].word.value if node is not None and node.args else None


def _return_parts(args: list[str]) -> tuple[int | None, str | None]:
    if args and args[0].isdigit():
        return int(args[0]), args[1] if len(args) > 1 else None
    # ``return URL`` is a 302 to it.
    return (302 if args else None), (args[0] if args else None)


def _nginx_listen(directive_id: str, args: list[str]) -> Listen:
    first, params = args[0], args[1:]
    address: str | None
    port: int | None
    if first.startswith("unix:"):
        address, port = first, None
    elif first.startswith("["):
        host, _, tail = first.partition("]")
        address = host + "]"
        port = _int(tail[1:]) if tail.startswith(":") else 80
    elif first.isdigit():
        address, port = None, int(first)
    elif ":" in first:
        host, _, port_text = first.rpartition(":")
        address, port = host, _int(port_text)
    else:
        address, port = first, 80
    return Listen(
        id=directive_id,
        address=address,
        port=port,
        ssl="ssl" in params,
        http2="http2" in params,
        quic="quic" in params,
        ipv6=bool(address and address.startswith("[")),
        default_server="default_server" in params or "default" in params,
        raw=" ".join(args),
    )


class _ApacheBuilder(_Builder):
    def build(self) -> SiteStructure:
        self.walk(self.tree, "", top=True)
        self.link_upstreams()
        return self.model

    def walk(self, container: Tree | Block, parent_id: str, *, top: bool) -> None:
        statements, notes = self.container_parts(container.children, starts_line=top)
        if top:
            self.model.notes.extend(notes)
        for node, comments in statements:
            lowered = node.name.value.lower()
            if is_server(node, container, "apache") and isinstance(node, Block):
                self.model.servers.append(self.server(node, comments))
                continue
            name = upstream_name(node, container, "apache")
            if name is not None and isinstance(node, Block):
                self.model.upstreams.append(self.balancer(node, name, comments))
                continue
            if lowered in ("include", "includeoptional"):
                self.include(node, parent_id)
            if top:
                self.model.directives.append(self.raw(node, comments, frozenset()))
            if isinstance(node, Block):
                # Conditional sections (<IfModule>, <IfDefine>...) can hold
                # virtual hosts; nothing else that can is worth telling apart.
                self.walk(node, self.id_of(node), top=False)

    def balancer(self, node: Block, name: str, comments: list[str]) -> Upstream:
        upstream = Upstream(
            id=self.id_of(node),
            name=name,
            line=node.line,
            end_line=node.end_line,
            comments=comments,
        )
        statements, upstream.notes = self.container_parts(node.children)
        for child, child_comments in statements:
            upstream.directives.append(
                self.raw(child, child_comments, frozenset({"balancermember"}))
            )
            args = values(child)
            if child.name.value.lower() == "balancermember" and args:
                upstream.servers.append(UpstreamServer(args[0], args[1:], self.id_of(child)))
        return upstream

    def server(self, node: Block, comments: list[str]) -> Server:
        server = Server(
            id=self.id_of(node), line=node.line, end_line=node.end_line, comments=comments
        )
        statements, server.notes = self.container_parts(node.children)
        everything = list(_descendants(node))
        ssl = any(
            n.name.value.lower() == "sslengine" and [a.lower() for a in values(n)[:1]] == ["on"]
            for n in everything
        )
        server.listens.extend(_apache_listen(address, ssl) for address in values(node))
        for child, child_comments in statements:
            lowered = child.name.value.lower()
            if is_location(child, "apache") and isinstance(child, Block):
                server.locations.append(self.location_section(child, child_comments))
                continue
            if lowered in ("proxypass", "proxypassmatch"):
                rule = self.proxy_rule(child, child_comments)
                if rule is not None:
                    server.locations.append(rule)
                    continue
            server.directives.append(self.raw(child, child_comments, _APACHE_SERVER_MODELED))
            self.server_directive(server, child)
        if any(
            n.name.value.lower() in ("addoutputfilterbytype", "setoutputfilter")
            and "DEFLATE" in values(n)
            for n in everything
        ):
            server.gzip = True
        if ssl:
            server.tls = Tls(
                certificate=_arg(_first_ci(everything, "sslcertificatefile")),
                key=_arg(_first_ci(everything, "sslcertificatekeyfile")),
                protocols=values(p) if (p := _first_ci(everything, "sslprotocol")) else [],
            )
        proxied = [
            loc
            for loc in server.locations
            if loc.target.kind == "proxy" or loc.source.startswith("ProxyPass")
        ]
        in_sections = [loc.id for loc in proxied if loc.source.startswith("Location")]
        rules = [loc.id for loc in proxied if loc.source.startswith("ProxyPass")]
        rest = [
            loc.id for loc in server.locations if loc.id not in in_sections and loc.id not in rules
        ]
        server.evaluation_order = in_sections + rules + rest
        return server

    def server_directive(self, server: Server, child: Statement) -> None:
        lowered = child.name.value.lower()
        args = values(child)
        if lowered == "servername" and args:
            name = re.sub(r"^[a-z]+://", "", args[0])
            server.names.insert(0, name.rsplit(":", 1)[0] if name.count(":") == 1 else name)
        elif lowered == "serveralias":
            server.names.extend(args)
        elif lowered == "documentroot" and args:
            server.root = args[0]
        elif lowered == "protocols":
            server.http2 = "h2" in args
        elif lowered == "header":
            header = _apache_header(self.id_of(child), args)
            if header is not None:
                server.headers.append(header)
        elif lowered == "limitrequestbody" and args:
            server.client_max_body_size = args[0]
        elif lowered == "redirect" and args:
            code, path, destination = _apache_redirect(args)
            if path == "/":
                server.returns = Return(self.id_of(child), code, destination)
        elif lowered in ("include", "includeoptional"):
            self.include(child, server.id)

    def proxy_target(self, url: str, directive_id: str) -> Target:
        if url == "!":
            return Target("other", directive_id, detail="not proxied")
        scheme, host, port = split_url(url)
        return Target("proxy", directive_id, url=url, protocol=scheme, host=host, port=port)

    def proxy_rule(self, node: Statement, comments: list[str]) -> Location | None:
        args = values(node)
        if len(args) < 2:
            return None
        directive_id = self.id_of(node)
        match = node.name.value.lower() == "proxypassmatch"
        return Location(
            id=directive_id,
            modifier="~" if match else "",
            path=args[0],
            source="ProxyPassMatch" if match else "ProxyPass",
            line=node.line,
            end_line=node.end_line,
            target=self.proxy_target(args[1], directive_id),
            settings=Settings(),
            directives=[self.raw(node, comments, frozenset({"proxypass", "proxypassmatch"}))],
            comments=comments,
        )

    def location_section(self, node: Block, comments: list[str]) -> Location:
        args = values(node)
        regex = node.name.value.lower() == "locationmatch" or (len(args) >= 2 and args[0] == "~")
        path = args[-1] if args else ""
        statements, notes = self.container_parts(node.children)
        plain = [child for child, _ in statements]
        location = Location(
            id=self.id_of(node),
            modifier="~" if regex else "",
            path=path,
            source="LocationMatch" if node.name.value.lower() == "locationmatch" else "Location",
            line=node.line,
            end_line=node.end_line,
            target=Target("other"),
            settings=Settings(),
            comments=comments,
            notes=notes,
        )
        for child, child_comments in statements:
            location.directives.append(
                self.raw(
                    child, child_comments, frozenset({"proxypass", "header", "limitrequestbody"})
                )
            )
        proxy = _first_ci(plain, "proxypass")
        if proxy is not None and proxy.args:
            location.target = self.proxy_target(values(proxy)[0], self.id_of(proxy))
        else:
            redirect = _first_ci(plain, "redirect")
            if redirect is not None and redirect.args:
                code, _, destination = _apache_redirect(values(redirect))
                location.target = Target(
                    "return", self.id_of(redirect), code=code, destination=destination
                )
        for child in plain:
            args_child = values(child)
            lowered = child.name.value.lower()
            if lowered == "require" and args_child[:2] == ["all", "denied"]:
                location.settings.deny = True
            elif lowered == "header":
                header = _apache_header(self.id_of(child), args_child)
                if header is not None:
                    location.settings.headers.append(header)
            elif lowered == "limitrequestbody" and args_child:
                location.settings.client_max_body_size = args_child[0]
        if location.target.kind == "other":
            location.target.detail = "deny all" if location.settings.deny else None
        return location


def _apache_listen(address: str, ssl: bool) -> Listen:
    port: int | None
    if address.startswith("["):
        host, _, tail = address.partition("]")
        host += "]"
        port = _int(tail[1:]) if tail.startswith(":") else None
    elif ":" in address:
        host, _, port_text = address.rpartition(":")
        port = _int(port_text)
    else:
        # No port: the virtual host answers on every port Apache listens on.
        host, port = address, None
    return Listen(
        id=None,
        address=host,
        port=port,
        ssl=ssl,
        ipv6=host.startswith("["),
        default_server=host == "_default_",
        raw=address,
    )


def _descendants(node: Block) -> list[Statement]:
    found: list[Statement] = []
    for child in node.children:
        if isinstance(child, Comment):
            continue
        found.append(child)
        if isinstance(child, Block):
            found.extend(_descendants(child))
    return found


def _first_ci(nodes: list[Statement], lowered: str) -> Statement | None:
    return next((node for node in nodes if node.name.value.lower() == lowered), None)


def _apache_header(directive_id: str, args: list[str]) -> Header | None:
    always = bool(args) and args[0].lower() in ("always", "onsuccess")
    rest = args[1:] if always else args
    if len(rest) >= 3 and rest[0].lower() in ("set", "append", "add", "merge", "setifempty"):
        return Header(directive_id, rest[1], rest[2], always and args[0].lower() == "always")
    return None


def _apache_redirect(args: list[str]) -> tuple[int | None, str | None, str | None]:
    statuses = {"permanent": 301, "temp": 302, "seeother": 303, "gone": 410}
    code: int | None = 302
    rest = args
    if args and (args[0].lower() in statuses or args[0].isdigit()):
        code = statuses.get(args[0].lower()) or _int(args[0])
        rest = args[1:]
    path = rest[0] if rest else None
    destination = rest[1] if len(rest) > 1 else None
    return code, path, destination
