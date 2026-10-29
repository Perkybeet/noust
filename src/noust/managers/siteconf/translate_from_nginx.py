# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Read an nginx site into the records a translation is made of (:mod:`translate_model`).

The reader knows the vocabulary Noust's own templates write, in the shapes they write it (the
certificate authority's directory, the redirect to HTTPS, the single-page fallback, the PHP
handler, the refusals), and the directives an operator usually adds to a site (a proxy or a
directory per path, headers, rewrites, redirects, a password, an address list). A line of that
vocabulary is carried into the records; every other line is a :class:`Finding`. A shape is
recognised whole (``location ~ \\.php$`` with ``fastcgi_pass`` is a PHP handler, and the
``fastcgi_param`` lines under it are what Apache does by itself), so the lines it is made of do
not each ask the operator a question.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence

from noust.managers.siteconf.translate_model import (
    Access,
    Backend,
    Findings,
    Header,
    Listener,
    Route,
    SiteModel,
    Tls,
    Upstream,
    Vhost,
    parse_seconds,
    parse_size,
)
from noust.managers.siteconf.tree import Block, Comment, Node, Statement, Tree

#: Settings of nginx that change no answer a visitor gets, or that Apache does by itself.
_TUNING = frozenset(
    {
        "proxy_http_version",
        "proxy_buffer_size",
        "proxy_buffers",
        "proxy_busy_buffers_size",
        "gzip_vary",
        "gzip_proxied",
        "gzip_min_length",
        "gzip_buffers",
        "gzip_http_version",
        "ssl_session_cache",
        "ssl_session_timeout",
        "ssl_buffer_size",
        "ssl_stapling",
        "ssl_stapling_verify",
        "log_not_found",
        "sendfile",
        "tcp_nopush",
        "tcp_nodelay",
        "keepalive_timeout",
        "keepalive_requests",
        "server_names_hash_bucket_size",
        "proxy_redirect",
        "proxy_intercept_errors",
        "charset",
        "etag",
        "open_file_cache",
        "resolver",
    }
)

#: Directives that keep somebody out or slow them down, which nothing in Apache replaces.
_PROTECTIONS = frozenset(
    {
        "limit_req",
        "limit_conn",
        "limit_req_status",
        "limit_conn_status",
        "limit_rate",
        "auth_request",
        "auth_request_set",
        "modsecurity",
        "modsecurity_rules",
        "modsecurity_rules_file",
        "ssl_reject_handshake",
    }
)

#: What a ``proxy_set_header`` for a header Apache's proxy adds by itself means.
_FORWARDED_BY_APACHE = frozenset({"x-forwarded-for", "x-forwarded-host"})

_PHP_FILES = ("fastcgi_params", "snippets/fastcgi-php.conf", "fastcgi.conf")


def _words(node: Statement) -> list[str]:
    return [arg.word.value for arg in node.args]


def _name(node: Statement) -> str:
    return node.name.value


def _statements(children: Sequence[Node]) -> list[Statement]:
    return [child for child in children if not isinstance(child, Comment)]


def _text(node: Statement) -> str:
    return " ".join([node.name.value, *_words(node)])


class NginxReader:
    """
    Read one nginx site file.

    Args:
        tree: The parsed file.
        findings: Where everything that is not carried over is recorded.
        domain: The site's domain, which names its log format.
    """

    def __init__(self, tree: Tree, findings: Findings, domain: str) -> None:
        self.tree = tree
        self.findings = findings
        self.domain = domain
        self.model = SiteModel()

    # -- The file ------------------------------------------------------------------------

    def read(self) -> SiteModel:
        """
        Read the whole file.

        Returns:
            The records.
        """
        self._top(self.tree.children)
        return self.model

    def _top(self, children: Sequence[Node]) -> None:
        for node in _statements(children):
            name = _name(node)
            if isinstance(node, Block):
                if name == "server":
                    self.model.vhosts.append(self._server(node))
                elif name == "upstream":
                    self._upstream(node)
                elif name == "http":
                    self._top(node.children)
                elif name in ("map", "geo", "types", "limit_except"):
                    self.findings.unsupported(name, node.line)
                else:
                    self.findings.unsupported(name, node.line)
                continue
            words = _words(node)
            if name == "log_format" and len(words) >= 2:
                self.model.log_formats[words[0]] = "".join(words[1:])
            elif name in ("limit_req_zone", "limit_conn_zone"):
                # Declares a zone; the line that uses it is the one that is reported.
                continue
            elif name == "include":
                self.findings.included(name, node.line, words[0] if words else "")
            elif name in _TUNING:
                self.findings.tuning(name, node.line)
            else:
                self.findings.unsupported(name, node.line)

    def _upstream(self, node: Block) -> None:
        group = Upstream(_words(node)[0] if node.args else "")
        for child in _statements(node.children):
            if isinstance(child, Block):
                self.findings.unsupported(_name(child), child.line)
                continue
            words = _words(child)
            if _name(child) == "server" and words:
                weight = 1
                backup = False
                for option in words[1:]:
                    if option.startswith("weight="):
                        weight = int(option.split("=", 1)[1] or 1)
                    elif option == "backup":
                        backup = True
                    elif option.startswith(("max_fails=", "fail_timeout=")):
                        self.findings.tuning(_name(child), child.line)
                group.servers.append((words[0], weight, backup))
            elif _name(child) in ("keepalive", "keepalive_timeout", "keepalive_requests"):
                self.findings.tuning(_name(child), child.line)
            else:
                self.findings.unsupported(_name(child), child.line)
        self.model.upstreams[group.name] = group

    # -- A server ------------------------------------------------------------------------

    def _server(self, node: Block) -> Vhost:
        vhost = Vhost(line=node.line)
        proxy_defaults = _Proxy()
        tls = Tls()
        has_tls = False
        for child in _statements(node.children):
            name = _name(child)
            words = _words(child)
            if isinstance(child, Block):
                if name == "location":
                    self._location(vhost, child, proxy_defaults)
                elif name == "if":
                    self._conditional(child)
                else:
                    self.findings.unsupported(name, child.line)
                continue
            if name == "listen":
                self._listen(vhost, words, child)
            elif name == "server_name":
                vhost.names.extend(words)
            elif name == "server_tokens":
                vhost.tokens_off = bool(words) and words[0] == "off"
            elif name in ("ssl_certificate", "ssl_certificate_key"):
                has_tls = True
                if name == "ssl_certificate":
                    tls.certificate = words[0] if words else None
                else:
                    tls.key = words[0] if words else None
            elif name == "ssl_protocols":
                has_tls = True
                tls.protocols = words
            elif name == "ssl_ciphers":
                tls.ciphers = words[0] if words else None
            elif name == "ssl_prefer_server_ciphers":
                tls.prefer_server_ciphers = bool(words) and words[0] == "on"
            elif name == "ssl_session_tickets":
                tls.tickets = bool(words) and words[0] == "on"
            elif name == "ssl_client_certificate":
                tls.client_ca = words[0] if words else None
            elif name == "ssl_verify_client":
                tls.verify_client = words[0] if words else None
            elif name == "ssl_verify_depth":
                tls.verify_depth = int(words[0]) if words and words[0].isdigit() else None
            elif name == "root":
                vhost.root = words[0] if words else None
            elif name == "index":
                vhost.index = words
            elif name == "client_max_body_size":
                vhost.body_limit = parse_size(words[0]) if words else None
            elif name == "access_log":
                self._access_log(vhost, words, child)
            elif name == "error_log":
                vhost.error_log = words[0] if words else None
            elif name == "add_header":
                self._add_header(vhost.headers, words, child)
            elif name == "gzip":
                vhost.gzip_types = vhost.gzip_types or [] if words and words[0] == "on" else None
            elif name == "gzip_types":
                vhost.gzip_types = [*(vhost.gzip_types or []), *words]
            elif name == "gzip_comp_level":
                vhost.gzip_level = int(words[0]) if words and words[0].isdigit() else None
            elif name == "try_files":
                self._try_files(vhost, words, child)
            elif name == "rewrite":
                self._rewrite(vhost, words, child)
            elif name == "return":
                self._server_return(vhost, words, child)
            elif name == "error_page":
                self._error_page(vhost, words, child)
            elif name in ("allow", "deny"):
                vhost.access.rules.append((name == "allow", words[0] if words else "all"))
            elif name == "satisfy":
                vhost.access.satisfy = words[0] if words else "all"
            elif name == "auth_basic":
                self._auth_realm(vhost.access, words, child)
            elif name == "auth_basic_user_file":
                vhost.access.users_file = words[0] if words else None
            elif name == "real_ip_header":
                vhost.real_ip = (words[0], vhost.real_ip[1] if vhost.real_ip else [])
            elif name == "set_real_ip_from":
                header = vhost.real_ip[0] if vhost.real_ip else "X-Forwarded-For"
                vhost.real_ip = (header, [*(vhost.real_ip[1] if vhost.real_ip else []), *words])
            elif name == "real_ip_recursive":
                self.findings.approximate(name, child.line, "RemoteIPInternalProxy is not set")
            elif name in ("proxy_set_header", "proxy_read_timeout", "proxy_send_timeout"):
                self._proxy_setting(proxy_defaults, name, words, child)
            elif name == "proxy_connect_timeout":
                self._proxy_setting(proxy_defaults, name, words, child)
            elif name == "proxy_buffering":
                self._proxy_setting(proxy_defaults, name, words, child)
            elif name in ("http2", "http3", "quic_retry", "quic_gso", "quic_bpf"):
                self._http3_or_http2(vhost, name, words, child)
            elif name == "include":
                self.findings.included(name, child.line, words[0] if words else "")
            elif name in _PROTECTIONS:
                self.findings.protection(name, child.line)
            elif name in _TUNING:
                self.findings.tuning(name, child.line)
            elif name == "set":
                self.findings.unsupported(name, child.line)
            else:
                self.findings.unsupported(name, child.line)

        if has_tls or any(listener.ssl for listener in vhost.listeners):
            vhost.tls = tls
        vhost.preserve_host = any(route.preserve_host for route in vhost.routes)
        self._settle(vhost, proxy_defaults)
        return vhost

    def _listen(self, vhost: Vhost, words: list[str], node: Statement) -> None:
        if not words:
            return
        spec, options = words[0], words[1:]
        if "quic" in options:
            self.findings.unsupported("listen " + spec + " quic", node.line)
            return
        if spec.startswith("unix:"):
            self.findings.unsupported("listen " + spec, node.line)
            return
        ipv6 = spec.startswith("[")
        address: str | None
        if ipv6:
            closing = spec.index("]")
            address = spec[1:closing]
            rest = spec[closing + 1 :].lstrip(":")
        elif ":" in spec:
            address, _, rest = spec.partition(":")
        elif spec.isdigit():
            address, rest = None, spec
        else:
            address, rest = spec, "80"
        port = int(rest) if rest.isdigit() else 80
        if address is not None and _unspecified(address):
            address = None
        for option in options:
            if option.startswith(("backlog=", "rcvbuf=", "sndbuf=", "reuseport", "fastopen=")):
                self.findings.tuning("listen " + option.split("=", 1)[0], node.line)
        vhost.listeners.append(
            Listener(
                port=port,
                address=address,
                ipv6=ipv6 and address is None,
                ssl="ssl" in options,
                http2="http2" in options,
                default="default_server" in options or "default" in options,
            )
        )

    def _http3_or_http2(self, vhost: Vhost, name: str, words: list[str], node: Statement) -> None:
        if name == "http2":
            for listener in vhost.listeners:
                listener.http2 = listener.http2 or (bool(words) and words[0] == "on")
            return
        self.findings.unsupported(name, node.line)

    def _access_log(self, vhost: Vhost, words: list[str], node: Statement) -> None:
        if not words or words[0] == "off":
            vhost.access_log = None
            return
        vhost.access_log = (words[0], words[1] if len(words) > 1 else None)

    def _add_header(self, into: list[Header], words: list[str], node: Statement) -> None:
        if len(words) < 2:
            return
        name, value = words[0], words[1]
        if name.lower() == "alt-svc":
            # It advertises HTTP/3, which the listener that served it was refused above.
            self.findings.unsupported("add_header Alt-Svc", node.line)
            return
        converted = self._variables(value, "add_header", node)
        into.append(Header(name, converted, always="always" in words[2:]))

    def _variables(self, value: str, directive: str, node: Statement) -> str:
        converted, unknown = to_apache_variables(value)
        for variable in unknown:
            self.findings.variable(directive, node.line, variable)
        return converted

    def _try_files(self, vhost: Vhost, words: list[str], node: Statement) -> None:
        fallback = _fallback_of(words)
        if fallback is None:
            self.findings.unsupported("try_files", node.line)
            return
        if fallback != "=404":
            vhost.fallback = fallback

    def _rewrite(self, vhost: Vhost, words: list[str], node: Statement) -> None:
        if len(words) < 2:
            return
        flag = words[2] if len(words) > 2 else "last"
        if flag not in ("last", "break", "redirect", "permanent"):
            self.findings.unsupported("rewrite", node.line)
            return
        replacement, unknown = to_apache_variables(words[1])
        for variable in unknown:
            self.findings.variable("rewrite", node.line, variable)
        vhost.rewrites.append((words[0], replacement, flag))

    def _server_return(self, vhost: Vhost, words: list[str], node: Statement) -> None:
        status = int(words[0]) if words and words[0].isdigit() else 0
        if status in (301, 302, 303, 307, 308) and len(words) > 1:
            target, unknown = to_apache_variables(words[1])
            for variable in unknown:
                self.findings.variable("return", node.line, variable)
            vhost.redirect_all = (status, target)
        elif status in (403, 444, 404) and len(words) == 1:
            vhost.routes.append(Route("prefix", "/", "deny", line=node.line))
        else:
            self.findings.unsupported("return", node.line)

    def _error_page(self, vhost: Vhost, words: list[str], node: Statement) -> None:
        page = words[-1] if words else ""
        statuses = [int(word) for word in words[:-1] if word.isdigit()]
        if not statuses or page.startswith("@") or page.startswith("=") or "=" in words[:-1]:
            self.findings.unsupported("error_page", node.line)
            return
        vhost.error_pages.extend((status, page) for status in statuses)

    def _auth_realm(self, access: Access, words: list[str], node: Statement) -> None:
        access.line = node.line
        if words and words[0] != "off":
            access.realm = words[0]
        else:
            access.realm = None

    def _conditional(self, node: Block) -> None:
        refuses = any(
            _name(child) in ("deny",)
            or (_name(child) == "return" and _words(child)[:1] and _words(child)[0][:1] in "45")
            for child in _statements(node.children)
            if not isinstance(child, Block)
        )
        if refuses:
            self.findings.protection("if", node.line)
        else:
            self.findings.unsupported("if", node.line)

    # -- A location ----------------------------------------------------------------------

    def _location(self, vhost: Vhost, node: Block, defaults: _Proxy) -> None:
        words = _words(node)
        if not words:
            return
        modifier = words[0] if len(words) > 1 else ""
        path = words[-1]
        if path.startswith("@"):
            self.findings.unsupported("location " + path, node.line)
            return
        match = {"": "prefix", "=": "exact", "^~": "prefix", "~": "regex", "~*": "iregex"}.get(
            modifier
        )
        if match is None:
            self.findings.unsupported("location " + modifier, node.line)
            return
        route = Route(match, path, "empty", shield=modifier == "^~", line=node.line)
        local = _Proxy(inherited=defaults)
        rooted: str | None = None
        refused = False
        open_to_all = False
        for child in _statements(node.children):
            name = _name(child)
            args = _words(child)
            if isinstance(child, Block):
                if name == "if":
                    self._conditional(child)
                elif name == "limit_except":
                    self.findings.protection(name, child.line)
                else:
                    self.findings.unsupported(name, child.line)
                continue
            if name == "proxy_pass" and args:
                route.kind = "proxy"
                route.backend = self._backend(args[0], child)
            elif name in ("proxy_set_header", "proxy_read_timeout", "proxy_send_timeout"):
                self._proxy_setting(local, name, args, child)
            elif name in ("proxy_connect_timeout", "proxy_buffering"):
                self._proxy_setting(local, name, args, child)
            elif name == "fastcgi_pass" and args:
                route.kind = "php"
                route.php_socket = args[0].removeprefix("unix:")
            elif name == "fastcgi_read_timeout" and args:
                route.read_timeout = parse_seconds(args[0])
            elif name in ("fastcgi_param", "fastcgi_index", "fastcgi_split_path_info"):
                continue
            elif name == "include" and args and args[0] in _PHP_FILES:
                continue
            elif name == "alias" and args:
                route.kind = "alias"
                route.files = args[0]
            elif name == "root" and args:
                rooted = args[0]
            elif name == "return":
                refused = self._location_return(route, args, child) or refused
            elif name == "deny" and args and args[0] == "all":
                refused = True
            elif name == "allow" and args and args[0] == "all":
                open_to_all = True
            elif name in ("auth_basic", "auth_request") and args and args[0] == "off":
                open_to_all = True
            elif name == "expires" and args:
                route.cache_seconds = _expires(args[0])
                if route.cache_seconds is None and args[0] not in ("off",):
                    self.findings.unsupported("expires", child.line)
            elif name == "add_header":
                self._add_header(route.headers, args, child)
            elif name == "client_max_body_size" and args:
                route.body_limit = parse_size(args[0])
            elif name == "try_files":
                self._location_try_files(vhost, route, args, child)
            elif name == "access_log":
                if args and args[0] == "off":
                    self.findings.tuning("access_log off", child.line)
                else:
                    self.findings.unsupported(name, child.line)
            elif name == "log_not_found":
                self.findings.tuning(name, child.line)
            elif name in _PROTECTIONS:
                self.findings.protection(name, child.line)
            elif name in _TUNING:
                self.findings.tuning(name, child.line)
            elif name == "include":
                self.findings.included(name, child.line, args[0] if args else "")
            else:
                self.findings.unsupported(name, child.line)
        self._settle_route(vhost, route, local, rooted, refused, open_to_all, node)

    def _settle_route(
        self,
        vhost: Vhost,
        route: Route,
        local: _Proxy,
        rooted: str | None,
        refused: bool,
        open_to_all: bool,
        node: Block,
    ) -> None:
        if route.kind == "proxy":
            local.apply(route)
            vhost.routes.append(route)
            return
        if route.kind == "php":
            vhost.routes.append(route)
            return
        if route.kind == "alias":
            vhost.routes.append(route)
            return
        if rooted is not None and "acme-challenge" in route.path:
            route.kind = "acme"
            route.files = rooted.rstrip("/") + route.path
            route.open_access = open_to_all
            vhost.routes.append(route)
            return
        if rooted is not None:
            route.kind = "alias"
            route.files = rooted.rstrip("/") + route.path
            vhost.routes.append(route)
            return
        if route.redirect is not None:
            route.kind = "redirect"
            vhost.routes.append(route)
            return
        if refused and route.redirect is None:
            route.kind = "deny"
            vhost.routes.append(route)
            return
        if route.headers or route.cache_seconds is not None or route.body_limit is not None:
            route.kind = "attrs"
            vhost.routes.append(route)
            return
        if route.path == "/" and route.match == "prefix" and vhost.fallback:
            return
        vhost.routes.append(route)

    def _location_return(self, route: Route, args: list[str], node: Statement) -> bool:
        status = int(args[0]) if args and args[0].isdigit() else 0
        if status in (301, 302, 303, 307, 308) and len(args) > 1:
            target, unknown = to_apache_variables(args[1])
            for variable in unknown:
                self.findings.variable("return", node.line, variable)
            route.redirect = (status, target)
            return False
        if status in (403, 404, 444) and len(args) == 1:
            return True
        self.findings.unsupported("return", node.line)
        return False

    def _location_try_files(
        self, vhost: Vhost, route: Route, args: list[str], node: Statement
    ) -> None:
        fallback = _fallback_of(args)
        if fallback is None:
            self.findings.unsupported("try_files", node.line)
        elif fallback != "=404" and route.path == "/":
            vhost.fallback = fallback

    def _backend(self, url: str, node: Statement) -> Backend:
        match = re.fullmatch(r"(https?)://([^/]+)(/.*)?", url)
        if url.startswith(("http://unix:", "https://unix:")):
            socket = url.split("unix:", 1)[1]
            path, _, uri = socket.partition(":")
            return Backend(
                "https" if url.startswith("https") else "http",
                path,
                unix=True,
                uri=uri or None,
            )
        if match is None:
            self.findings.unsupported("proxy_pass", node.line)
            return Backend("http", url)
        scheme, host, uri = match.groups()
        group = host if host in self.model.upstreams else None
        return Backend(scheme, host, upstream=group, uri=uri or None)

    def _proxy_setting(self, into: _Proxy, name: str, words: list[str], node: Statement) -> None:
        if name == "proxy_set_header" and len(words) >= 2:
            header, value = words[0], words[1]
            lowered = header.lower()
            if lowered == "host" and value in ("$host", "$http_host"):
                into.preserve_host = True
            elif lowered == "host":
                self.findings.unsupported("proxy_set_header Host", node.line)
            elif lowered == "upgrade":
                into.websocket = True
            elif lowered == "connection":
                into.websocket = into.websocket or value.lower() == "upgrade"
            elif lowered in _FORWARDED_BY_APACHE:
                return
            else:
                converted, unknown = to_apache_variables(value)
                for variable in unknown:
                    self.findings.variable("proxy_set_header", node.line, variable)
                into.headers.append((header, converted))
        elif name == "proxy_set_header":
            return
        elif name == "proxy_read_timeout" and words:
            into.read_timeout = parse_seconds(words[0])
        elif name == "proxy_send_timeout" and words:
            into.send_timeout = parse_seconds(words[0])
        elif name == "proxy_connect_timeout" and words:
            into.connect_timeout = parse_seconds(words[0])
        elif name == "proxy_buffering" and words:
            into.unbuffered = words[0] == "off"

    def _settle(self, vhost: Vhost, defaults: _Proxy) -> None:
        for route in vhost.routes:
            if route.kind == "proxy":
                defaults.apply(route, only_missing=True)
        vhost.preserve_host = vhost.preserve_host or any(
            route.preserve_host for route in vhost.routes
        )
        https_only = [
            route
            for route in vhost.routes
            if route.kind == "redirect"
            and route.path == "/"
            and route.redirect is not None
            and route.redirect[1].startswith("https://%{HTTP_HOST}")
        ]
        for route in https_only:
            vhost.https_redirect = True
            vhost.routes.remove(route)


class _Proxy:
    """The proxy settings of a server or a location, which a location inherits."""

    def __init__(self, inherited: _Proxy | None = None) -> None:
        self.inherited = inherited
        self.preserve_host = False
        self.websocket = False
        self.headers: list[tuple[str, str]] = []
        self.read_timeout: int | None = None
        self.send_timeout: int | None = None
        self.connect_timeout: int | None = None
        self.unbuffered = False

    def apply(self, route: Route, *, only_missing: bool = False) -> None:
        """
        Copy the settings onto a proxy route.

        Args:
            route: The route.
            only_missing: Fill in what the route does not say, instead of overriding.
        """
        sources = [self] if self.inherited is None else [self.inherited, self]
        for source in sources:
            route.preserve_host = route.preserve_host or source.preserve_host
            route.websocket = route.websocket or source.websocket
            for header in source.headers:
                if header not in route.request_headers:
                    route.request_headers.append(header)
            for attribute in ("read_timeout", "send_timeout", "connect_timeout"):
                value = getattr(source, attribute)
                if value is not None and (not only_missing or getattr(route, attribute) is None):
                    setattr(route, attribute, value)
            route.unbuffered = route.unbuffered or source.unbuffered


def _unspecified(address: str) -> bool:
    """
    Args:
        address: What a ``listen`` names before the port.

    Returns:
        Whether it is every address (``*``, ``0.0.0.0`` or ``::``) rather than one in particular.
    """
    if address == "*":
        return True
    try:
        return ipaddress.ip_address(address).is_unspecified
    except ValueError:
        return False


def _fallback_of(words: Sequence[str]) -> str | None:
    """
    Read a ``try_files`` list as the single-page or front-controller fallback it is.

    Args:
        words: The arguments of ``try_files``.

    Returns:
        The fallback (``/index.html``, ``/index.php``), ``=404`` when the list only checks that the
        file exists, None for a list that means something else.
    """
    cleaned = [word for word in words if word not in ("$uri", "$uri/")]
    if not cleaned:
        return None
    if cleaned == ["=404"]:
        return "=404"
    last = cleaned[-1]
    if len(cleaned) == 1 and last.startswith("/"):
        return re.sub(r"\?\$(?:args|query_string)$", "", last)
    return None


def _expires(value: str) -> int | None:
    if value in ("max",):
        return 315360000
    if value in ("epoch", "off"):
        return None
    return parse_seconds(value)


#: nginx variables and the Apache expressions that read the same thing.
_VARIABLES = {
    "host": "%{HTTP_HOST}",
    "http_host": "%{HTTP_HOST}",
    "request_uri": "%{REQUEST_URI}",
    "uri": "%{REQUEST_URI}",
    "scheme": "%{REQUEST_SCHEME}",
    "args": "%{QUERY_STRING}",
    "query_string": "%{QUERY_STRING}",
    "server_name": "%{SERVER_NAME}",
    "server_port": "%{SERVER_PORT}",
    "remote_addr": "%{REMOTE_ADDR}",
    "remote_user": "%{REMOTE_USER}",
    "request_method": "%{REQUEST_METHOD}",
    "ssl_client_s_dn": "%{SSL_CLIENT_S_DN}",
    "ssl_client_serial": "%{SSL_CLIENT_M_SERIAL}",
    "ssl_client_verify": "%{SSL_CLIENT_VERIFY}",
}

_VARIABLE = re.compile(r"\$(\w+)|\$\{(\w+)\}")

#: ``?$args`` and ``$is_args$args`` append the query string, which a rewrite does by itself.
_QUERY_TAIL = re.compile(r"\?\$args|\$is_args\$args")


def to_apache_variables(value: str) -> tuple[str, list[str]]:
    """
    Turn the nginx variables in a value into Apache's.

    Args:
        value: A value as nginx writes it (``https://$host$request_uri``).

    Returns:
        The value with each variable it knows replaced (``https://%{HTTP_HOST}%{REQUEST_URI}``) and
        the variables it does not know, as written. A ``$1`` is a capture of the rule, not a
        variable, and stays. The query string a rewrite appends by itself is left out.
    """
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name.isdigit():
            return match.group(0)
        if name in _VARIABLES:
            return _VARIABLES[name]
        if name.startswith("http_"):
            return "%{HTTP:" + name[5:].replace("_", "-") + "}"
        unknown.append(match.group(0))
        return match.group(0)

    return _VARIABLE.sub(replace, _QUERY_TAIL.sub("", value)), unknown
