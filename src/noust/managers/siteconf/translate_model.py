# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a site file says, apart from how either web server spells it.

Translating a site from nginx to Apache and back is two steps that share this module: a reader
turns one dialect's tree into these records, a writer turns the records into the other dialect's
text. The records name only what the two servers have in common (names, listeners, TLS, a proxy,
a directory of files, a redirect, a refusal, a header); anything a reader meets that it cannot
place in them becomes a :class:`Finding`, never a silently dropped line. That is the contract of
the whole translation: **what the new site does not do is said before anything is written.**

A finding has a severity because the operator's decision depends on it:

- ``blocks``: the site cannot be moved until somebody acts (the file does not parse).
- ``loses``: the new site will not do something the old one did. Going on needs the operator's
  word, and a ``protection`` finding is a thing the old site kept out or limited.
- ``notes``: carried over in another form, or a tuning knob of the old server that changes no
  answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from noust.core.server_text import ServerText, say

Severity = Literal["blocks", "loses", "notes"]

#: How each web server is named in a sentence.
DISPLAY = {"nginx": "nginx", "apache": "Apache"}


@dataclass(frozen=True)
class Finding:
    """
    One thing about the old site that the new one does not carry over as it was.

    Attributes:
        reason: Why: ``unsupported`` (nothing equivalent), ``protection`` (a protection nothing
            equivalent replaces), ``included`` (a file the site includes), ``variable`` (a value
            the new server cannot read), ``approximate`` (carried over in another form) or
            ``tuning`` (a knob of the old server that changes no answer).
        severity: What the operator must decide, see the module documentation.
        directive: The directive as the old server names it.
        line: Its line in the old file, None when it is about the file as a whole.
        sentence: The same finding as a code and its values, for the console to translate.
    """

    reason: str
    severity: Severity
    directive: str
    line: int | None
    sentence: ServerText

    @property
    def protection(self) -> bool:
        """Whether the old site was protected by what is lost."""
        return self.reason == "protection"

    def to_dict(self) -> dict[str, object]:
        """
        Returns:
            The finding as plain data: what the API sends and the plan prints.
        """
        return {
            "reason": self.reason,
            "severity": self.severity,
            "directive": self.directive,
            "line": self.line,
            "protection": self.protection,
            "code": self.sentence.code,
            "params": dict(self.sentence.params),
            "text": self.sentence.text,
            "hint": self.sentence.hint,
        }


class Findings:
    """
    The findings of one translation, collected as the reader and the writer meet them.

    Args:
        source: The dialect read, ``nginx`` or ``apache``.
        target: The dialect written.
    """

    def __init__(self, source: str, target: str) -> None:
        self.source = source
        self.target = target
        self.items: list[Finding] = []

    def _add(
        self,
        reason: str,
        severity: Severity,
        directive: str,
        line: int | None,
        code: str,
        **values: str | int,
    ) -> None:
        shown = line if line is not None else 0
        # The sentence of a file as a whole, and of an include, does not name a directive.
        unnamed = ("webserver_move.unreadable", "webserver_move.included")
        named = {} if code in unnamed else {"directive": directive}
        self.items.append(
            Finding(reason, severity, directive, line, say(code, line=shown, **named, **values))
        )

    def unreadable(self, line: int, problem: str) -> None:
        """
        Record that the old file is not one its server would read, so nothing can be translated.

        Args:
            line: Where the parser stopped.
            problem: What it said, in the web server's terms.
        """
        self._add(
            "unreadable",
            "blocks",
            "site",
            line,
            "webserver_move.unreadable",
            problem=problem,
            source=DISPLAY[self.source],
        )

    def unsupported(self, directive: str, line: int | None) -> None:
        """
        Record a directive the new server has nothing equivalent to.

        Args:
            directive: Its name in the old file.
            line: Its line.
        """
        self._add(
            "unsupported",
            "loses",
            directive,
            line,
            "webserver_move.unsupported",
            target=DISPLAY[self.target],
        )

    def cannot(self, directive: str, line: int | None) -> None:
        """
        Record a directive that makes the file something else than a website, so it is not
        translated at all (a TCP or UDP forward written as a ``server``).

        Args:
            directive: Its name in the old file.
            line: Its line.
        """
        self._add(
            "unsupported",
            "blocks",
            directive,
            line,
            "webserver_move.unsupported",
            target=DISPLAY[self.target],
        )

    def protection(self, directive: str, line: int | None) -> None:
        """
        Record a protection (a login, a limit, a firewall) the new server cannot give.

        Args:
            directive: Its name in the old file.
            line: Its line.
        """
        self._add(
            "protection",
            "loses",
            directive,
            line,
            "webserver_move.protection",
            target=DISPLAY[self.target],
        )

    def included(self, directive: str, line: int | None, path: str) -> None:
        """
        Record a file the old site includes, which the translation does not read.

        Args:
            directive: ``include`` or ``Include``.
            line: Its line.
            path: What it includes, as written.
        """
        self._add("included", "loses", directive, line, "webserver_move.included", path=path)

    def variable(self, directive: str, line: int | None, name: str) -> None:
        """
        Record a value the new server cannot read (an nginx variable with no Apache twin).

        Args:
            directive: The directive that reads it.
            line: Its line.
            name: The variable as written.
        """
        self._add(
            "variable",
            "loses",
            directive,
            line,
            "webserver_move.variable",
            variable=name,
            target=DISPLAY[self.target],
        )

    def approximate(self, directive: str, line: int | None, result: str) -> None:
        """
        Record something carried over in another form.

        Args:
            directive: Its name in the old file.
            line: Its line.
            result: What it became, in a few words.
        """
        self._add(
            "approximate", "notes", directive, line, "webserver_move.approximate", result=result
        )

    def tuning(self, directive: str, line: int | None) -> None:
        """
        Record a knob of the old server that has no counterpart and changes no answer.

        Args:
            directive: Its name in the old file.
            line: Its line.
        """
        self._add(
            "tuning",
            "notes",
            directive,
            line,
            "webserver_move.tuning",
            source=DISPLAY[self.source],
        )


@dataclass(frozen=True)
class Translation:
    """
    A site in the other server's words, and what did not come across.

    Attributes:
        text: The new site. Empty when a finding blocks.
        source: The dialect read.
        target: The dialect written.
        findings: Everything that is not carried over as it was.
        modules: The Apache modules the new site needs (empty for nginx), which the move enables.
        names: Every name the new site answers on, in file order.
    """

    text: str
    source: str
    target: str
    findings: tuple[Finding, ...]
    modules: tuple[str, ...] = ()
    names: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        """Whether something stops the move."""
        return any(item.severity == "blocks" for item in self.findings)

    @property
    def losses(self) -> tuple[Finding, ...]:
        """What the new site will not do: what the operator has to accept."""
        return tuple(item for item in self.findings if item.severity == "loses")

    @property
    def notes(self) -> tuple[Finding, ...]:
        """What was carried over in another form."""
        return tuple(item for item in self.findings if item.severity == "notes")


# -- The records ------------------------------------------------------------------------------


@dataclass
class Listener:
    """
    One address and port a server accepts connections on.

    Attributes:
        port: The port.
        address: A specific address, None for every address.
        ipv6: Whether it is the IPv6 wildcard (``[::]``), which Apache's ``*`` already covers.
        ssl: Whether TLS is spoken on it.
        http2: Whether HTTP/2 is on it.
        default: Whether the server is the default one of this address and port.
    """

    port: int
    address: str | None = None
    ipv6: bool = False
    ssl: bool = False
    http2: bool = False
    default: bool = False


@dataclass
class Tls:
    """
    A server's TLS settings.

    Attributes:
        certificate: The certificate chain file.
        key: The private key file.
        protocols: The protocol versions allowed (``TLSv1.2``), empty for the server's own.
        ciphers: The cipher list.
        prefer_server_ciphers: Whether the server's cipher order wins, None when not said.
        tickets: Whether session tickets are on, None when not said.
        client_ca: The authority a client's certificate must come from.
        verify_client: ``on``, ``optional`` or ``off``, None when not said.
        verify_depth: How deep the client's chain may go.
    """

    certificate: str | None = None
    key: str | None = None
    protocols: list[str] = field(default_factory=list)
    ciphers: str | None = None
    prefer_server_ciphers: bool | None = None
    tickets: bool | None = None
    client_ca: str | None = None
    verify_client: str | None = None
    verify_depth: int | None = None


@dataclass
class Header:
    """
    A header added to a response.

    Attributes:
        name: Its name.
        value: Its value, with the old server's variables already turned into the new one's.
        always: Whether it is sent on error responses too.
    """

    name: str
    value: str
    always: bool = True


@dataclass
class Backend:
    """
    Where a proxy sends a request.

    Attributes:
        scheme: ``http`` or ``https``.
        address: ``host:port``, or the path of a Unix socket.
        unix: Whether ``address`` is a socket.
        uri: The path the old directive gave after the address (``/``), None when it gave none and
            the request's own path goes through untouched.
        upstream: The group's name when the address is a group the site defines.
    """

    scheme: str
    address: str
    unix: bool = False
    uri: str | None = None
    upstream: str | None = None


@dataclass
class Upstream:
    """
    A named group of backends.

    Attributes:
        name: Its name.
        servers: ``(address, weight, backup)`` of each member.
    """

    name: str
    servers: list[tuple[str, int, bool]] = field(default_factory=list)


@dataclass
class Route:
    """
    What the server does for the requests of one path.

    Attributes:
        match: ``prefix``, ``exact``, ``regex`` or ``iregex`` (a regular expression that ignores case).
        path: The path, or the expression.
        shield: Whether it is a ``^~`` prefix, which keeps the regular expressions from claiming it.
        kind: ``proxy``, ``alias``, ``redirect``, ``deny``, ``php``, ``attrs`` (only headers and
            caching), ``acme`` (the certificate authority's challenge directory) or ``empty``.
        backend: The proxy's target.
        files: The directory ``alias`` or ``acme`` serve from.
        redirect: ``(status, target)`` of a redirect, the target in the new server's variables.
        php_socket: Where PHP-FPM listens: a socket path or ``host:port``.
        websocket: Whether the connection may be upgraded.
        preserve_host: Whether the backend sees the visitor's ``Host``.
        request_headers: Headers sent to the backend, ``(name, value)``.
        headers: Headers added to the response.
        cache_seconds: How long a response may be cached, None when not said.
        read_timeout: Seconds to wait for the backend's answer.
        send_timeout: Seconds to wait to send the request.
        connect_timeout: Seconds to wait to connect.
        unbuffered: Whether the response goes to the visitor as it is produced.
        body_limit: Largest request body in bytes, None for the server's.
        open_access: Whether the password and the address list are lifted here (the certificate
            authority's directory, which anybody must be able to reach).
        line: Where it starts in the old file.
    """

    match: str
    path: str
    kind: str
    shield: bool = False
    backend: Backend | None = None
    files: str | None = None
    redirect: tuple[int, str] | None = None
    php_socket: str | None = None
    websocket: bool = False
    preserve_host: bool = False
    request_headers: list[tuple[str, str]] = field(default_factory=list)
    headers: list[Header] = field(default_factory=list)
    cache_seconds: int | None = None
    read_timeout: int | None = None
    send_timeout: int | None = None
    connect_timeout: int | None = None
    unbuffered: bool = False
    body_limit: int | None = None
    open_access: bool = False
    line: int = 0


@dataclass
class Access:
    """
    Who may reach a site.

    Attributes:
        rules: ``(allow, address)`` in the order written (``address`` is ``all`` for everyone).
        satisfy: ``any`` or ``all``: whether an address or a password is enough, or both are needed.
        realm: The name a password is asked under, None when none is.
        users_file: The file holding the accounts.
        line: Where it starts in the old file.
    """

    rules: list[tuple[bool, str]] = field(default_factory=list)
    satisfy: str = "all"
    realm: str | None = None
    users_file: str | None = None
    line: int = 0

    @property
    def empty(self) -> bool:
        """Whether nobody is asked anything."""
        return not self.rules and self.realm is None


@dataclass
class Vhost:
    """
    One virtual server.

    Attributes:
        names: The names it answers on, first the main one.
        listeners: Its addresses and ports.
        tls: Its TLS settings, None when it speaks none.
        tokens_off: Whether it hides the server's version.
        root: Its document root.
        index: Its index files.
        fallback: The file a path that is no file is given to (the single page's ``index.html``,
            PHP's ``index.php``).
        body_limit: Largest request body in bytes.
        access_log: ``(path, format name)`` of the access log, None for none.
        error_log: The error log's path.
        headers: Headers added to every response.
        gzip_types: The content types compressed, None when compression is off.
        gzip_level: The compression level.
        https_redirect: Whether every request but the certificate authority's is sent to HTTPS.
        routes: What it does per path.
        access: Who may reach it.
        real_ip: ``(header, trusted networks)`` for the visitor's address behind a proxy.
        rewrites: ``(pattern, replacement, flag)`` rewrite rules.
        error_pages: ``(status, page)`` pairs.
        redirect_all: ``(status, target)`` when a server-level ``return`` answers everything.
        preserve_host: Whether any proxy hands the visitor's ``Host`` on.
        line: Where it starts in the old file.
    """

    names: list[str] = field(default_factory=list)
    listeners: list[Listener] = field(default_factory=list)
    tls: Tls | None = None
    tokens_off: bool = False
    root: str | None = None
    index: list[str] = field(default_factory=list)
    fallback: str | None = None
    body_limit: int | None = None
    access_log: tuple[str, str | None] | None = None
    error_log: str | None = None
    headers: list[Header] = field(default_factory=list)
    gzip_types: list[str] | None = None
    gzip_level: int | None = None
    https_redirect: bool = False
    routes: list[Route] = field(default_factory=list)
    access: Access = field(default_factory=Access)
    real_ip: tuple[str, list[str]] | None = None
    rewrites: list[tuple[str, str, str]] = field(default_factory=list)
    error_pages: list[tuple[int, str]] = field(default_factory=list)
    redirect_all: tuple[int, str] | None = None
    preserve_host: bool = False
    line: int = 0


@dataclass
class SiteModel:
    """
    A whole site file.

    Attributes:
        vhosts: Its virtual servers, in file order.
        upstreams: Its groups of backends, by name.
        log_formats: Its named log formats, as the old server writes them, by name.
        preamble: The leading comments of the old file.
    """

    vhosts: list[Vhost] = field(default_factory=list)
    upstreams: dict[str, Upstream] = field(default_factory=dict)
    log_formats: dict[str, str] = field(default_factory=dict)
    preamble: list[str] = field(default_factory=list)


_REGEX_META = re.compile(r"([.+*?()\[\]{}|^$\\])")


def regex_escape(text: str) -> str:
    """
    Escape a path so a regular expression matches it literally.

    Unlike :func:`re.escape` it leaves ``-``, ``/`` and ``~`` alone, so what is written in a site
    file reads like the paths of the templates do (``/\\.well-known/``, not ``/\\.well\\-known/``).

    Args:
        text: A path.

    Returns:
        The expression.
    """
    return _REGEX_META.sub(r"\\\1", text)


# -- Sizes and times --------------------------------------------------------------------------

_SIZE = re.compile(r"(\d+)\s*([kKmMgG]?)[bB]?")
_SIZE_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}

_DURATION = re.compile(r"((?:\d+(?:ms|s|m|h|d|w|M|y))+|\d+)")
_DURATION_PART = re.compile(r"(\d+)(ms|s|m|h|d|w|M|y)?")
_DURATION_UNITS = {
    "ms": 0.001,
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
    "M": 2592000,
    "y": 31536000,
    None: 1,
}


def parse_size(value: str) -> int | None:
    """
    Read a size the way nginx writes one.

    Args:
        value: ``1m``, ``100M``, ``512k`` or a number of bytes.

    Returns:
        The size in bytes, None when it is not one.
    """
    match = _SIZE.fullmatch(value.strip())
    if match is None:
        return None
    return int(match.group(1)) * _SIZE_UNITS[match.group(2).lower()]


def format_size(size: int) -> str:
    """
    Write a size the way nginx reads one.

    Args:
        size: Bytes.

    Returns:
        ``100m`` when it is a whole number of megabytes, else ``512k`` or the bytes.
    """
    for unit, factor in (("g", 1024**3), ("m", 1024**2), ("k", 1024)):
        if size >= factor and size % factor == 0:
            return f"{size // factor}{unit}"
    return str(size)


def parse_seconds(value: str) -> int | None:
    """
    Read a time the way nginx writes one.

    Args:
        value: ``60s``, ``1m``, ``30d``, ``1y`` or a number of seconds.

    Returns:
        Whole seconds, None when it is not a time.
    """
    text = value.strip()
    if _DURATION.fullmatch(text) is None:
        return None
    total = 0.0
    for number, unit in _DURATION_PART.findall(text):
        total += int(number) * _DURATION_UNITS[unit or None]
    return int(total)
