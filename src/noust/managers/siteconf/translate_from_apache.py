# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Read an Apache site into the records a translation is made of (:mod:`translate_model`).

The mirror of :mod:`translate_from_nginx`: the vocabulary of Noust's Apache templates in the shapes
they write it (the redirect to HTTPS, the single-page fallback, the hidden-file rule, the PHP
handler, the refusals), plus what an operator adds (a proxy or a directory per path, headers,
redirects, a password, an address list). Everything else is a finding.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from noust.managers.siteconf.translate_model import (
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

#: Settings of Apache that change no answer a visitor gets, or that nginx does by itself.
_TUNING = frozenset(
    {
        "serveradmin",
        "keepalive",
        "keepalivetimeout",
        "maxkeepaliverequests",
        "timeout",
        "loglevel",
        "limitrequestfields",
        "limitrequestfieldsize",
        "limitrequestline",
        "proxyrequests",
        "proxyvia",
        "sslsessiontimeout",
        "sslusestapling",
        "sslstaplingcache",
        "sslcertificatechainfile",
        "sslopensslconfcmd",
        "serversignature",
        "servertokens",
        "usecanonicalname",
        "hostnamelookups",
        "enablesendfile",
        "enablemmap",
        "traceenable",
    }
)

#: What Apache's proxy adds by itself and nginx has to be told to.
_PROXY_OPTIONS_KNOWN = frozenset({"timeout", "connectiontimeout", "flushpackets"})

_TLS_VERSIONS = ("TLSv1", "TLSv1.1", "TLSv1.2", "TLSv1.3")

_ADDRESS = re.compile(r"(?P<host>\[[^\]]+\]|[^:]+):(?P<port>\d+|\*)")

_SPA = "/index.html"

#: An expression that names exactly one path: ``^/xmlrpc\.php$``.
_LITERAL_PATH = re.compile(r"\^(/(?:[A-Za-z0-9_\-/]|\\\.)*)\$")


def _words(node: Statement) -> list[str]:
    return [arg.word.value for arg in node.args]


def _name(node: Statement) -> str:
    return node.name.value.lower()


def _statements(children: Sequence[Node]) -> list[Statement]:
    return [child for child in children if not isinstance(child, Comment)]


def _flags(text: str) -> list[str]:
    return [flag.strip() for flag in text.strip("[]").split(",") if flag.strip()]


class ApacheReader:
    """
    Read one Apache site file.

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
        self.tokens_off = False

    def read(self) -> SiteModel:
        """
        Read the whole file.

        Returns:
            The records.
        """
        self._top(self.tree.children)
        for vhost in self.model.vhosts:
            vhost.tokens_off = self.tokens_off
        return self.model

    def _top(self, children: Sequence[Node]) -> None:
        for node in _statements(children):
            name = _name(node)
            words = _words(node)
            if isinstance(node, Block):
                if name == "virtualhost":
                    self.model.vhosts.append(_Server(self, node).read())
                elif name == "proxy" and words and words[0].lower().startswith("balancer://"):
                    self._balancer(node)
                elif name == "ifmodule":
                    self._top(node.children)
                else:
                    self.findings.unsupported(node.name.value, node.line)
                continue
            if name == "servertokens":
                self.tokens_off = bool(words) and words[0].lower() in ("prod", "productonly")
            elif name == "serversignature":
                continue
            elif name == "logformat" and len(words) >= 2:
                self.model.log_formats[words[1]] = words[0]
            elif name in ("include", "includeoptional"):
                self.findings.included(node.name.value, node.line, words[0] if words else "")
            elif name in _TUNING:
                self.findings.tuning(node.name.value, node.line)
            elif name == "listen":
                self.findings.tuning(node.name.value, node.line)
            else:
                self.findings.unsupported(node.name.value, node.line)

    def _balancer(self, node: Block) -> None:
        name = _words(node)[0][len("balancer://") :].rstrip("/")
        group = Upstream(name)
        for child in _statements(node.children):
            words = _words(child)
            if _name(child) == "balancermember" and words:
                address = re.sub(r"^[a-z]+://", "", words[0])
                weight = 1
                backup = False
                for option in words[1:]:
                    if option.startswith("loadfactor="):
                        weight = int(option.split("=", 1)[1] or 1)
                    elif option.startswith("status=") and "H" in option:
                        backup = True
                group.servers.append((address, weight, backup))
            else:
                self.findings.unsupported(child.name.value, child.line)
        self.model.upstreams[name] = group


class _Server:
    """One ``<VirtualHost>``."""

    def __init__(self, reader: ApacheReader, node: Block) -> None:
        self.reader = reader
        self.findings = reader.findings
        self.node = node
        self.vhost = Vhost(line=node.line)
        self.tls = Tls()
        self.encrypted = False
        self.request_headers: list[tuple[str, str]] = []
        self.timeout: int | None = None
        self.websocket_targets: list[str] = []
        self.conditions: list[list[str]] = []
        self.proxy_options: dict[str, dict[str, str]] = {}

    def read(self) -> Vhost:
        """
        Returns:
            The server.
        """
        self.encrypted = self._speaks_tls(self.node)
        self._listeners()
        self._body(self.node.children, directory=None)
        self._settle()
        return self.vhost

    # -- Addresses and TLS ---------------------------------------------------------------

    @staticmethod
    def _speaks_tls(node: Block) -> bool:
        for child in _statements(node.children):
            if isinstance(child, Block):
                continue
            if _name(child) == "sslengine" and _words(child)[:1] == ["on"]:
                return True
        return False

    def _listeners(self) -> None:
        for spec in _words(self.node):
            match = _ADDRESS.fullmatch(spec)
            if match is None:
                self.findings.unsupported("VirtualHost " + spec, self.node.line)
                continue
            host = match.group("host")
            port = 80 if match.group("port") == "*" else int(match.group("port"))
            address: str | None
            ipv6 = False
            if host in ("*", "_default_"):
                address = None
            elif host.startswith("["):
                address = host[1:-1]
                ipv6 = address == "::"
                address = None if ipv6 else address
            else:
                address = host
            listener = Listener(port=port, address=address, ipv6=ipv6, ssl=self.encrypted)
            self.vhost.listeners.append(listener)
            if address is None and not ipv6:
                self.vhost.listeners.append(
                    Listener(port=port, address=None, ipv6=True, ssl=self.encrypted)
                )

    # -- The body ------------------------------------------------------------------------

    def _body(self, children: Sequence[Node], *, directory: str | None) -> None:
        pending: list[list[str]] = []
        for child in _statements(children):
            name = _name(child)
            words = _words(child)
            if isinstance(child, Block):
                self._section(child, name, words, directory)
                continue
            if name == "rewritecond":
                pending.append(words)
                continue
            if name == "rewriterule":
                self._rewrite_rule(words, pending, child)
                pending = []
                continue
            self._directive(child, name, words)
        if pending:
            for condition in pending:
                self.findings.unsupported("RewriteCond", self.node.line)
                del condition

    def _section(self, node: Block, name: str, words: list[str], directory: str | None) -> None:
        if name == "directory":
            self._directory(node, words[0] if words else "")
        elif name == "ifmodule":
            self._body(node.children, directory=directory)
        elif name in ("filesmatch", "locationmatch", "location", "files"):
            self._location(node, name, words)
        elif name in ("requireany", "requireall", "requirenone"):
            self.findings.protection(node.name.value, node.line)
        else:
            self.findings.unsupported(node.name.value, node.line)

    def _directory(self, node: Block, path: str) -> None:
        files = {route.files.rstrip("/") for route in self.vhost.routes if route.files}
        if path.rstrip("/") in files:
            return
        if self.vhost.root and path.rstrip("/") != self.vhost.root.rstrip("/"):
            self.findings.unsupported("Directory " + path, node.line)
            return
        pending: list[list[str]] = []
        for child in _statements(node.children):
            name = _name(child)
            words = _words(child)
            if isinstance(child, Block):
                self.findings.unsupported(child.name.value, child.line)
            elif name == "fallbackresource" and words:
                self.vhost.fallback = words[0]
            elif name in ("options", "allowoverride", "rewriteengine", "rewritebase"):
                if name == "allowoverride" and words and words[0].lower() != "none":
                    self.findings.approximate(
                        child.name.value,
                        child.line,
                        "nothing: nginx never reads a .htaccess",
                    )
                continue
            elif name == "require":
                if [word.lower() for word in words] != ["all", "granted"]:
                    self.findings.protection(child.name.value, child.line)
            elif name == "rewritecond":
                pending.append(words)
            elif name == "rewriterule":
                self._directory_rule(words, pending, child)
                pending = []
            else:
                self.findings.unsupported(child.name.value, child.line)

    def _directory_rule(
        self, words: list[str], conditions: list[list[str]], node: Statement
    ) -> None:
        """
        Recognise the rules Noust's static template keeps in its document root.

        Args:
            words: The rule.
            conditions: The conditions before it.
            node: The directive.
        """
        pattern, replacement = ([*words, "", ""])[:2]
        flags = _flags(words[2]) if len(words) > 2 else []
        checks = [" ".join(cond[:2]) for cond in conditions]
        if pattern == r"(^|/)\." and replacement == "-" and "F" in flags:
            self._add_deny("regex", r"/\.", node.line)
            self.vhost.routes.append(Route("prefix", "/.well-known/", "empty", shield=True))
            return
        if pattern == r"^index\.html$" and replacement == "-":
            return
        if pattern == "." and replacement.startswith("/") and flags == ["L"]:
            if {"%{REQUEST_FILENAME} !-f", "%{REQUEST_FILENAME} !-d"} <= set(checks):
                self.vhost.fallback = replacement
                return
        self.findings.unsupported("RewriteRule", node.line)

    # -- Directives of the server --------------------------------------------------------

    def _directive(self, node: Statement, name: str, words: list[str]) -> None:
        vhost = self.vhost
        if name in ("rewriteengine", "rewritebase", "serveradmin"):
            # Switches and labels: what the rules below them say is read on its own.
            return
        if name == "servername" and words:
            vhost.names.insert(0, words[0].rsplit(":", 1)[0] if ":" in words[0] else words[0])
        elif name == "serveralias":
            vhost.names.extend(words)
        elif name == "sslengine":
            return
        elif name == "sslcertificatefile" and words:
            self.tls.certificate = words[0]
        elif name == "sslcertificatekeyfile" and words:
            self.tls.key = words[0]
        elif name == "sslprotocol":
            self.tls.protocols = self._protocols(words)
        elif name == "sslciphersuite" and words:
            self.tls.ciphers = words[0]
        elif name == "sslhonorcipherorder" and words:
            self.tls.prefer_server_ciphers = words[0].lower() == "on"
        elif name == "sslsessiontickets" and words:
            self.tls.tickets = words[0].lower() == "on"
        elif name == "sslcacertificatefile" and words:
            self.tls.client_ca = words[0]
        elif name == "sslverifyclient" and words:
            self.tls.verify_client = {"require": "on", "optional": "optional"}.get(
                words[0].lower(), "off"
            )
        elif name == "sslverifydepth" and words and words[0].isdigit():
            self.tls.verify_depth = int(words[0])
        elif name == "protocols":
            for listener in vhost.listeners:
                listener.http2 = listener.ssl and "h2" in words
        elif name == "documentroot" and words:
            vhost.root = words[0]
        elif name == "directoryindex":
            vhost.index = words
        elif name == "limitrequestbody" and words:
            vhost.body_limit = parse_size(words[0]) or None
        elif name == "errorlog" and words:
            vhost.error_log = _nginx_log(words[0])
        elif name == "customlog" and words:
            vhost.access_log = (_nginx_log(words[0]), words[1] if len(words) > 1 else None)
        elif name == "logformat" and len(words) >= 2:
            self.reader.model.log_formats[words[1]] = words[0]
        elif name == "header":
            self._header(vhost.headers, words, node)
        elif name == "requestheader":
            self._request_header(words, node)
        elif name == "addoutputfilterbytype" and words and words[0].upper() == "DEFLATE":
            vhost.gzip_types = [*(vhost.gzip_types or []), *words[1:]]
        elif name == "deflatecompressionlevel" and words and words[0].isdigit():
            vhost.gzip_level = int(words[0])
        elif name == "remoteipheader" and words:
            vhost.real_ip = (words[0], vhost.real_ip[1] if vhost.real_ip else [])
        elif name == "remoteiptrustedproxy":
            header = vhost.real_ip[0] if vhost.real_ip else "X-Forwarded-For"
            vhost.real_ip = (header, [*(vhost.real_ip[1] if vhost.real_ip else []), *words])
        elif name == "alias" and len(words) == 2:
            self._alias(words, node)
        elif name == "proxypass":
            self._proxy_pass(words, node)
        elif name == "proxypassreverse":
            return
        elif name == "proxypreservehost" and words:
            vhost.preserve_host = words[0].lower() == "on"
        elif name == "proxytimeout" and words and words[0].isdigit():
            self.timeout = int(words[0])
        elif name == "sslproxyengine":
            return
        elif name in ("redirect", "redirectmatch"):
            self._redirect(name, words, node)
        elif name == "errordocument" and len(words) == 2 and words[0].isdigit():
            if words[1].startswith("/"):
                vhost.error_pages.append((int(words[0]), words[1]))
            else:
                self.findings.unsupported(node.name.value, node.line)
        elif name in ("authtype", "authname", "authuserfile", "require"):
            self._access(name, words, node)
        elif name in ("include", "includeoptional"):
            self.findings.included(node.name.value, node.line, words[0] if words else "")
        elif name in _TUNING:
            self.findings.tuning(node.name.value, node.line)
        else:
            self.findings.unsupported(node.name.value, node.line)

    @staticmethod
    def _protocols(words: list[str]) -> list[str]:
        allowed = set(_TLS_VERSIONS) - {"TLSv1", "TLSv1.1"}
        for word in words:
            sign, version = (word[0], word[1:]) if word[:1] in "+-" else ("+", word)
            if version.lower() == "all":
                targets = set(_TLS_VERSIONS)
            elif version in _TLS_VERSIONS:
                targets = {version}
            else:
                continue
            allowed = allowed | targets if sign == "+" else allowed - targets
        return [version for version in _TLS_VERSIONS if version in allowed]

    def _header(self, into: list[Header], words: list[str], node: Statement) -> None:
        always = bool(words) and words[0].lower() == "always"
        rest = words[1:] if always else words
        if len(rest) < 3 or rest[0].lower() not in ("set", "append", "add"):
            self.findings.unsupported(node.name.value, node.line)
            return
        value, unknown = to_nginx_variables(rest[2].removeprefix("expr="))
        for variable in unknown:
            self.findings.variable(node.name.value, node.line, variable)
        into.append(Header(rest[1], value, always=always))

    def _request_header(self, words: list[str], node: Statement) -> None:
        if len(words) < 3 or words[0].lower() != "set":
            self.findings.unsupported(node.name.value, node.line)
            return
        value, unknown = to_nginx_variables(words[2].removeprefix("expr="))
        for variable in unknown:
            self.findings.variable(node.name.value, node.line, variable)
        self.request_headers.append((words[1], value))

    def _alias(self, words: list[str], node: Statement) -> None:
        path, files = words
        acme = "acme-challenge" in path
        self.vhost.routes.append(
            Route(
                "prefix",
                path,
                "acme" if acme else "alias",
                shield=True,
                files=files,
                line=node.line,
            )
        )

    def _proxy_pass(self, words: list[str], node: Statement) -> None:
        if len(words) < 2:
            return
        path, target = words[0], words[1]
        if target == "!":
            return
        options: dict[str, str] = {}
        for option in words[2:]:
            name, _, value = option.partition("=")
            options[name] = value
        for option in options:
            if option not in _PROXY_OPTIONS_KNOWN:
                self.findings.tuning("ProxyPass " + option, node.line)
        route = Route("prefix", path, "proxy", line=node.line)
        route.backend = self._backend(target, path)
        route.read_timeout = (
            int(options["timeout"]) if options.get("timeout", "").isdigit() else None
        )
        route.connect_timeout = (
            int(options["connectiontimeout"])
            if options.get("connectiontimeout", "").isdigit()
            else None
        )
        route.unbuffered = options.get("flushpackets", "").lower() == "on"
        self.vhost.routes.append(route)

    def _backend(self, target: str, path: str) -> Backend:
        unix = target.startswith("unix:")
        if unix:
            socket, _, rest = target[len("unix:") :].partition("|")
            return Backend("http", socket, unix=True, uri=_uri_of(rest, path))
        match = re.fullmatch(r"(balancer|https?|wss?)://([^/]+)(/.*)?", target)
        if match is None:
            self.findings.unsupported("ProxyPass", self.node.line)
            return Backend("http", target)
        scheme, host, uri = match.groups()
        if scheme == "balancer":
            return Backend("http", host, upstream=host, uri=_uri_for(uri, path))
        return Backend(scheme, host, uri=_uri_for(uri, path))

    def _redirect(self, name: str, words: list[str], node: Statement) -> None:
        status = 302
        rest = list(words)
        if rest and rest[0].isdigit():
            status = int(rest.pop(0))
        elif rest and rest[0].lower() in ("permanent", "temp", "seeother", "gone"):
            kind = rest.pop(0).lower()
            status = {"permanent": 301, "temp": 302, "seeother": 303, "gone": 410}[kind]
        if len(rest) != 2 or status not in (301, 302, 303, 307, 308):
            self.findings.unsupported(node.name.value, node.line)
            return
        path, target = rest
        target = to_nginx_variables(target)[0]
        if name == "redirectmatch":
            insensitive = path.startswith("(?i)")
            route = Route(
                "iregex" if insensitive else "regex",
                path.removeprefix("(?i)"),
                "redirect",
                line=node.line,
            )
        else:
            route = Route("prefix", path, "redirect", line=node.line)
        route.redirect = (status, target)
        self.vhost.routes.append(route)

    def _access(self, name: str, words: list[str], node: Statement) -> None:
        access = self.vhost.access
        access.line = node.line
        if name == "authtype":
            if words and words[0].lower() != "basic":
                self.findings.protection(node.name.value, node.line)
        elif name == "authname" and words:
            access.realm = words[0]
        elif name == "authuserfile" and words:
            access.users_file = words[0]
        elif name == "require":
            self._require(words, node)

    def _require(self, words: list[str], node: Statement) -> None:
        access = self.vhost.access
        lowered = [word.lower() for word in words]
        if lowered == ["valid-user"]:
            access.realm = access.realm or "Restricted"
        elif lowered[:1] == ["ip"]:
            access.rules.extend((True, address) for address in words[1:])
            if (False, "all") not in access.rules:
                access.rules.append((False, "all"))
        elif lowered[:2] == ["not", "ip"]:
            denied = [(False, address) for address in words[2:]]
            access.rules = [*denied, *[rule for rule in access.rules if rule not in denied]]
            if not access.rules or access.rules[-1] != (True, "all"):
                access.rules.append((True, "all"))
        elif lowered == ["all", "granted"]:
            return
        elif lowered == ["all", "denied"]:
            access.rules = [(False, "all")]
        else:
            self.findings.protection(node.name.value, node.line)

    # -- Rewrites ------------------------------------------------------------------------

    def _rewrite_rule(self, words: list[str], conditions: list[list[str]], node: Statement) -> None:
        vhost = self.vhost
        pattern, replacement = ([*words, "", ""])[:2]
        flags = _flags(words[2]) if len(words) > 2 else []
        checks = [(cond[0], cond[1]) for cond in conditions if len(cond) >= 2]
        redirects = any(flag.startswith("R") for flag in flags)
        if (
            pattern == "^"
            and redirects
            and replacement == "https://%{HTTP_HOST}%{REQUEST_URI}"
            and all(
                check[1].startswith(r"!^/\.well-known/") or check == ("%{HTTPS}", "off")
                for check in checks
            )
        ):
            vhost.https_redirect = True
            return
        if replacement.startswith(("ws://", "wss://")) and "P" in flags:
            host = re.sub(r"^wss?://", "", replacement).split("/", 1)[0]
            self.websocket_targets.append(host)
            return
        if checks:
            self.findings.unsupported("RewriteCond", node.line)
            return
        if replacement == "-" and "F" in flags:
            self._add_deny("regex", pattern, node.line)
            return
        nginx_flag = "last"
        if any(flag.startswith("R") for flag in flags):
            status = next((flag.partition("=")[2] for flag in flags if flag.startswith("R")), "")
            nginx_flag = "permanent" if status == "301" else "redirect"
        elif any(flag in ("P", "F", "G") or flag.startswith(("E", "CO", "T")) for flag in flags):
            self.findings.unsupported("RewriteRule", node.line)
            return
        converted, unknown = to_nginx_variables(replacement)
        for variable in unknown:
            self.findings.variable("RewriteRule", node.line, variable)
        prefix = "(?i)" if "NC" in flags else ""
        vhost.rewrites.append((prefix + pattern, converted, nginx_flag))

    # -- A <Location>, <LocationMatch> or <FilesMatch> -----------------------------------

    def _location(self, node: Block, kind: str, words: list[str]) -> None:
        path = words[0] if words else ""
        regex = kind in ("filesmatch", "locationmatch", "files")
        route = Route(
            "iregex" if regex and path.startswith("(?i)") else ("regex" if regex else "prefix"),
            path.removeprefix("(?i)") if regex else path,
            "empty",
            line=node.line,
        )
        literal = _LITERAL_PATH.fullmatch(route.path) if route.match == "regex" else None
        if literal is not None:
            # ^/xmlrpc\.php$ names one path: nginx's `location = /xmlrpc.php`.
            route.match = "exact"
            route.path = re.sub(r"\\(.)", r"\1", literal.group(1))
        denied = False
        granted = False
        handler: str | None = None
        for child in _statements(node.children):
            name = _name(child)
            args = _words(child)
            if isinstance(child, Block):
                if name in ("requireany", "requireall"):
                    self._whole_site_requirements(child)
                else:
                    self.findings.unsupported(child.name.value, child.line)
            elif name == "sethandler" and args:
                handler = args[0]
            elif name == "require":
                lowered = [word.lower() for word in args]
                if lowered == ["all", "denied"]:
                    denied = True
                elif lowered == ["all", "granted"]:
                    granted = True
                elif path == "/":
                    self._require(args, child)
                else:
                    self.findings.protection(child.name.value, child.line)
            elif name == "header":
                self._route_header(route, args, child)
            elif name in ("authtype", "authname", "authuserfile") and path == "/":
                self._access(name, args, child)
            elif name == "limitrequestbody" and args:
                route.body_limit = parse_size(args[0]) or None
            else:
                self.findings.unsupported(child.name.value, child.line)
        self._settle_location(route, path, denied, granted, handler)

    def _whole_site_requirements(self, node: Block) -> None:
        rules: list[str] = []
        for child in _statements(node.children):
            args = _words(child)
            if _name(child) == "require":
                self._require(args, child)
                rules.append(" ".join(args).lower())
            else:
                self.findings.unsupported(child.name.value, child.line)
        if _name(node) == "requireany" and any(rule.startswith("ip") for rule in rules):
            if "valid-user" in rules:
                self.vhost.access.satisfy = "any"

    def _add_deny(self, match: str, pattern: str, line: int) -> None:
        """
        Add a refusal, and the prefixes nginx must keep it off.

        Args:
            match: ``regex`` or ``iregex``.
            pattern: The expression, possibly wrapped the way the translation to Apache wraps it.
            line: Where it starts in the old file.
        """
        inner, shields = unshield(pattern)
        self.vhost.routes.append(Route(match, inner, "deny", line=line))
        for prefix in shields:
            if not any(r.shield and r.path == prefix for r in self.vhost.routes):
                self.vhost.routes.append(Route("prefix", prefix, "empty", shield=True))

    def _settle_location(
        self, route: Route, path: str, denied: bool, granted: bool, handler: str | None
    ) -> None:
        vhost = self.vhost
        if handler is not None:
            socket = _fcgi_socket(handler)
            if socket is None:
                self.findings.unsupported("SetHandler", route.line)
                return
            route.kind = "php"
            route.php_socket = socket
            vhost.routes.append(route)
            return
        if denied:
            if route.match in ("regex", "iregex"):
                self._add_deny(route.match, route.path, route.line)
            else:
                route.kind = "deny"
                vhost.routes.append(route)
            return
        if granted and "acme-challenge" in path:
            for other in vhost.routes:
                if other.kind == "acme":
                    other.open_access = True
            return
        if route.headers or route.cache_seconds is not None or route.body_limit is not None:
            route.kind = "attrs"
            vhost.routes.append(route)

    def _route_header(self, route: Route, args: list[str], node: Statement) -> None:
        always = bool(args) and args[0].lower() == "always"
        rest = args[1:] if always else args
        if len(rest) < 3 or rest[0].lower() not in ("set", "append", "add"):
            self.findings.unsupported(node.name.value, node.line)
            return
        name, value = rest[1], rest[2]
        if name.lower() != "cache-control":
            self._header(route.headers, args, node)
            return
        tokens = [token.strip() for token in value.split(",") if token.strip()]
        remaining: list[str] = []
        for token in tokens:
            if token.startswith("max-age="):
                route.cache_seconds = parse_seconds(token.removeprefix("max-age="))
            else:
                remaining.append(token)
        if remaining:
            route.headers.append(Header(name, ", ".join(remaining), always=always))

    # -- Putting it together -------------------------------------------------------------

    def _settle(self) -> None:
        vhost = self.vhost
        if self.encrypted or self.tls.certificate:
            vhost.tls = self.tls
        for route in vhost.routes:
            if route.kind != "proxy":
                continue
            route.request_headers.extend(self.request_headers)
            route.preserve_host = vhost.preserve_host
            if route.backend is not None and route.backend.address in self.websocket_targets:
                route.websocket = True
            if route.read_timeout is None:
                route.read_timeout = self.timeout
            if route.send_timeout is None:
                route.send_timeout = self.timeout
        for route in vhost.routes:
            if route.kind == "php" and route.read_timeout is None:
                route.read_timeout = self.timeout
        if not vhost.names:
            vhost.names = ["_"]


#: The expression Noust's Apache templates write to refuse hidden files but not ``/.well-known/``.
_HIDDEN_FILES = r"/\.(?!well-known(?:/|$))"

_SHIELD_HEAD = "^(?!"
_SHIELD_BODY = ")(?=(?s:.*?)(?:"


def unshield(pattern: str) -> tuple[str, list[str]]:
    """
    Undo what the translation to Apache does to keep an expression off the ``^~`` prefixes.

    Apache has no location that wins over the expressions, so the translation writes a lookahead
    into each of them (:meth:`~noust.managers.siteconf.translate_to_apache.ApacheWriter._shielded`).
    Read back, that lookahead is the ``^~`` prefix again and the expression is the one it was.

    Args:
        pattern: An expression of a ``LocationMatch``.

    Returns:
        The expression without the wrapper and the prefixes it refused; the pattern itself and
        none when it is not wrapped.
    """
    if pattern == _HIDDEN_FILES:
        return r"/\.", ["/.well-known/"]
    if pattern.startswith(_SHIELD_HEAD) and pattern.endswith("))") and _SHIELD_BODY in pattern:
        refused, _, rest = pattern[len(_SHIELD_HEAD) :].partition(_SHIELD_BODY)
        prefixes = [re.sub(r"\\(.)", r"\1", prefix) for prefix in refused.split("|")]
        return rest[:-2], prefixes
    return pattern, []


def _uri_for(uri: str | None, path: str) -> str | None:
    """
    Tell whether a proxy target's path says anything the request's own path does not.

    Args:
        uri: The path in the target, None when it has none.
        path: The path the proxy was mounted on.

    Returns:
        None when the target path is the mount path (the request goes through as it came, which is
        what nginx does without a path), else the path.
    """
    if uri is None or uri == path:
        return None
    return uri


def _uri_of(rest: str, path: str) -> str | None:
    match = re.fullmatch(r"https?://[^/]+(/.*)?", rest)
    return _uri_for(match.group(1) if match else None, path)


def _fcgi_socket(handler: str) -> str | None:
    """
    Read where PHP-FPM listens out of a ``SetHandler "proxy:..."`` value.

    Args:
        handler: The handler.

    Returns:
        The socket path or ``host:port``, None when it is not a FastCGI proxy.
    """
    match = re.fullmatch(r"proxy:unix:([^|]+)\|fcgi://[^/]+/?.*", handler)
    if match:
        return match.group(1)
    match = re.fullmatch(r"proxy:fcgi://([^/]+)/?.*", handler)
    return match.group(1) if match else None


def _nginx_log(path: str) -> str:
    for directory in ("${APACHE_LOG_DIR}/", "/var/log/apache2/"):
        if path.startswith(directory):
            return "/var/log/nginx/" + path[len(directory) :]
    return path


_APACHE_VARIABLES = {
    "HTTP_HOST": "$host",
    "REQUEST_URI": "$request_uri",
    "REQUEST_SCHEME": "$scheme",
    "QUERY_STRING": "$args",
    "SERVER_NAME": "$server_name",
    "SERVER_PORT": "$server_port",
    "REMOTE_ADDR": "$remote_addr",
    "REMOTE_USER": "$remote_user",
    "REQUEST_METHOD": "$request_method",
    "SSL_CLIENT_S_DN": "$ssl_client_s_dn",
    "SSL_CLIENT_M_SERIAL": "$ssl_client_serial",
    "SSL_CLIENT_VERIFY": "$ssl_client_verify",
}

_APACHE_VARIABLE = re.compile(r"%\{([A-Za-z_]+)(?::([^}]*))?\}")


def to_nginx_variables(value: str) -> tuple[str, list[str]]:
    """
    Turn the Apache variables in a value into nginx's.

    Args:
        value: A value as Apache writes it (``https://%{HTTP_HOST}%{REQUEST_URI}``).

    Returns:
        The value with each variable it knows replaced (``https://$host$request_uri``) and the
        ones it does not, as written.
    """
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name, argument = match.group(1), match.group(2)
        if name == "HTTP" and argument:
            return "$http_" + argument.lower().replace("-", "_")
        if name in _APACHE_VARIABLES and argument is None:
            return _APACHE_VARIABLES[name]
        unknown.append(match.group(0))
        return match.group(0)

    return _APACHE_VARIABLE.sub(replace, value), unknown
