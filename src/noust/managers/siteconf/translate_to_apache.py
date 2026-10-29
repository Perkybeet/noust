# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Write the records of a site (:mod:`translate_model`) as an Apache site.

The layout is the one Noust's own Apache templates write: the server-wide lines first, one
``<VirtualHost>`` per group of addresses, and inside it the names, the TLS lines, the logs, the
headers, what the visitor is sent to and what is refused. What Apache needs a module for is
collected as the writer goes, so the move can enable them before the new site is tested.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from noust.managers.siteconf.apache import quote
from noust.managers.siteconf.translate_model import (
    Backend,
    Findings,
    Header,
    Listener,
    Route,
    SiteModel,
    Vhost,
    regex_escape,
)

#: Where Apache writes the logs of a site that nginx wrote to ``/var/log/nginx``. The directory
#: itself, not ``${APACHE_LOG_DIR}``: the web server is asked to test the site before it is
#: installed, and it is not given a path it cannot read.
_NGINX_LOGS = "/var/log/nginx/"
_APACHE_LOGS = "/var/log/apache2/"

#: The mark that a header in the new site is an expression, not text.
_EXPRESSION = "expr="

_ACME = "/.well-known/acme-challenge/"

#: What a nginx log format variable becomes in an Apache one.
_LOG_VARIABLES = {
    "remote_addr": "%a",
    "remote_user": "%u",
    "request": "%r",
    "status": "%>s",
    "body_bytes_sent": "%B",
    "bytes_sent": "%O",
    "request_length": "%I",
    "request_method": "%m",
    "request_uri": "%U%q",
    "uri": "%U",
    "server_name": "%v",
    "server_port": "%p",
    "host": "%{Host}i",
    "http_referer": "%{Referer}i",
    "http_user_agent": "%{User-Agent}i",
    "http_x_forwarded_for": "%{X-Forwarded-For}i",
    "request_time": "%D",
    "msec": "%{%s}t",
    "time_local": "%{%d/%b/%Y:%H:%M:%S %z}t",
    "pid": "%P",
}

#: ``CanonicalLog`` gives the line of a log format that Noust's own templates write.
CanonicalLog = Callable[[str], str | None]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _pattern(value: str) -> str:
    """
    Write a regular expression as an argument.

    Inside quotes Apache reads ``\\\\`` and ``\\"`` as escapes and every other backslash as itself,
    so an expression with neither is written as it is, which is how the templates write theirs.

    Args:
        value: The expression.

    Returns:
        The argument.
    """
    if '"' in value or "\\\\" in value or value.endswith("\\"):
        return quote(value)
    return f'"{value}"'


def _minimal(prefixes: list[str]) -> list[str]:
    """
    Drop the prefixes that another one in the list already covers.

    Args:
        prefixes: Paths.

    Returns:
        The shortest set that covers the same requests, in the order given.
    """
    return [
        prefix
        for prefix in dict.fromkeys(prefixes)
        if not any(other != prefix and prefix.startswith(other) for other in prefixes)
    ]


def _value(value: str) -> str:
    """
    Write a header value: text as it is, a variable as the expression that reads it.

    Args:
        value: The value, with variables already in Apache's words (``%{HTTP_HOST}``).

    Returns:
        The quoted argument.
    """
    if "%{" not in value:
        return '"' + _escape(value) + '"'
    return '"' + _EXPRESSION + _escape(value) + '"'


class ApacheWriter:
    """
    Write one site.

    Args:
        model: What the site says.
        findings: Where what cannot be written is recorded.
        domain: The site's domain.
        canonical_log: The line of the log format Noust's templates write for the site, so the
            traffic page reads the new site's log as it read the old one's.
    """

    def __init__(
        self,
        model: SiteModel,
        findings: Findings,
        domain: str,
        canonical_log: CanonicalLog | None = None,
    ) -> None:
        self.model = model
        self.findings = findings
        self.domain = domain
        self.canonical_log = canonical_log or (lambda name: None)
        self.modules: set[str] = set()
        self.names: list[str] = []

    def write(self) -> str:
        """
        Returns:
            The Apache site.
        """
        lines: list[str] = []
        if any(vhost.tokens_off for vhost in self.model.vhosts):
            lines += [
                "# Do not announce the Apache version or the modules in headers and error pages.",
                "ServerTokens Prod",
                "ServerSignature Off",
                "",
            ]
        lines += self._balancers()
        for vhost in self.model.vhosts:
            for name in vhost.names:
                if name not in self.names and name != "_":
                    self.names.append(name)
            for block in self._vhost(vhost):
                lines += [*block, ""]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines) + "\n"

    # -- Groups of backends --------------------------------------------------------------

    def _balancers(self) -> list[str]:
        lines: list[str] = []
        for group in self.model.upstreams.values():
            if len(group.servers) < 2:
                continue
            self.modules.update(
                {"proxy", "proxy_http", "proxy_balancer", "lbmethod_byrequests", "slotmem_shm"}
            )
            lines.append(f'<Proxy "balancer://{group.name}">')
            for address, weight, backup in group.servers:
                member = f'    BalancerMember "http://{address}" loadfactor={weight}'
                lines.append(member + (" status=+H" if backup else ""))
            lines += ["</Proxy>", ""]
        return lines

    def _target(self, backend: Backend) -> str | None:
        """
        Name where a proxy sends, in the words ``ProxyPass`` takes.

        Args:
            backend: The target.

        Returns:
            The URL without its path, None when it names a group that has no members here.
        """
        if backend.unix:
            return f"unix:{backend.address}|http://localhost"
        if backend.upstream is not None:
            group = self.model.upstreams.get(backend.upstream)
            if group is None or not group.servers:
                return None
            if len(group.servers) == 1:
                return f"{backend.scheme}://{group.servers[0][0]}"
            return f"balancer://{backend.upstream}"
        return f"{backend.scheme}://{backend.address}"

    # -- A server ------------------------------------------------------------------------

    def _vhost(self, vhost: Vhost) -> list[list[str]]:
        groups: dict[bool, list[Listener]] = {}
        for listener in vhost.listeners:
            groups.setdefault(listener.ssl, []).append(listener)
        if not groups:
            groups[False] = [Listener(80)]
        blocks: list[list[str]] = []
        for tls in sorted(groups):
            blocks.append(self._group(vhost, groups[tls], tls))
        return blocks

    @staticmethod
    def _addresses(listeners: list[Listener]) -> str:
        seen: list[str] = []
        covered = {
            (item.port, item.ssl) for item in listeners if not item.ipv6 and item.address is None
        }
        for listener in listeners:
            if listener.ipv6 and (listener.port, listener.ssl) in covered:
                continue
            if listener.address is None:
                spec = f"[::]:{listener.port}" if listener.ipv6 else f"*:{listener.port}"
            elif ":" in listener.address:
                spec = f"[{listener.address}]:{listener.port}"
            else:
                spec = f"{listener.address}:{listener.port}"
            if spec not in seen:
                seen.append(spec)
        return " ".join(seen)

    def _group(self, vhost: Vhost, listeners: list[Listener], tls: bool) -> list[str]:
        out = [f"<VirtualHost {self._addresses(listeners)}>"]
        body: list[str] = []
        names = [name for name in vhost.names if name != "_"]
        if names:
            body.append(f"ServerName {names[0]}")
            if len(names) > 1:
                body.append("ServerAlias " + " ".join(names[1:]))
        elif vhost.names:
            self.findings.approximate(
                "server_name _", vhost.line, "a virtual host with no ServerName"
            )
        if tls:
            body += self._tls(vhost, listeners)
        body += self._files(vhost)
        body += self._logs(vhost)
        body += self._headers(vhost.headers)
        body += self._compression(vhost)
        body += self._real_ip(vhost)
        body += self._routes(vhost, tls)
        body += self._access(vhost)
        body += self._rules(vhost)
        out += [("    " + line) if line else "" for line in body]
        out.append("</VirtualHost>")
        return out

    # -- TLS, files and logs -------------------------------------------------------------

    def _tls(self, vhost: Vhost, listeners: list[Listener]) -> list[str]:
        self.modules.add("ssl")
        lines = ["SSLEngine on"]
        tls = vhost.tls
        if tls is None:
            return lines
        if tls.certificate:
            lines.append(f"SSLCertificateFile {quote(tls.certificate)}")
        if tls.key:
            lines.append(f"SSLCertificateKeyFile {quote(tls.key)}")
        if tls.protocols:
            lines.append("SSLProtocol -all " + " ".join(f"+{item}" for item in tls.protocols))
        if tls.ciphers:
            lines.append(f"SSLCipherSuite {tls.ciphers}")
        if tls.prefer_server_ciphers is not None:
            lines.append("SSLHonorCipherOrder " + ("on" if tls.prefer_server_ciphers else "off"))
        if tls.tickets is not None:
            lines.append("SSLSessionTickets " + ("on" if tls.tickets else "off"))
        if tls.client_ca:
            lines.append(f"SSLCACertificateFile {quote(tls.client_ca)}")
        if tls.verify_client:
            mode = {"on": "require", "optional": "optional", "optional_no_ca": "optional_no_ca"}
            lines.append("SSLVerifyClient " + mode.get(tls.verify_client, "none"))
        if tls.verify_depth is not None:
            lines.append(f"SSLVerifyDepth {tls.verify_depth}")
        if any(listener.http2 for listener in listeners):
            self.modules.add("http2")
            lines += ["<IfModule http2_module>", "    Protocols h2 http/1.1", "</IfModule>"]
        return lines

    def _files(self, vhost: Vhost) -> list[str]:
        lines: list[str] = []
        if vhost.root:
            lines.append(f"DocumentRoot {quote(vhost.root)}")
        if vhost.index:
            lines.append("DirectoryIndex " + " ".join(vhost.index))
        if vhost.root:
            lines += [
                f"<Directory {quote(vhost.root)}>",
                "    Options -Indexes +FollowSymLinks",
                "    AllowOverride None",
                "    Require all granted",
            ]
            if vhost.fallback:
                lines.append(f"    FallbackResource {vhost.fallback}")
            lines.append("</Directory>")
        if vhost.body_limit:
            lines.append(f"LimitRequestBody {vhost.body_limit}")
        for status, page in vhost.error_pages:
            lines.append(f"ErrorDocument {status} {page}")
        return lines

    def _log_path(self, path: str) -> str:
        if path.startswith(_NGINX_LOGS):
            return _APACHE_LOGS + path[len(_NGINX_LOGS) :]
        return path

    def _logs(self, vhost: Vhost) -> list[str]:
        lines: list[str] = []
        if vhost.error_log:
            lines.append(f"ErrorLog {self._log_path(vhost.error_log)}")
        if vhost.access_log is None:
            return lines
        path, name = vhost.access_log
        if name is None or name == "combined":
            lines.append(f"CustomLog {self._log_path(path)} combined")
            return lines
        definition = self.canonical_log(name)
        if definition is None:
            written = self.model.log_formats.get(name)
            if written is None:
                self.findings.unsupported(f"access_log {name}", vhost.line)
                lines.append(f"CustomLog {self._log_path(path)} combined")
                return lines
            definition = f'LogFormat "{self._log_format(written, vhost.line)}" {name}'
        lines += [definition.strip(), f"CustomLog {self._log_path(path)} {name}"]
        return lines

    def _log_format(self, written: str, line: int) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2)
            if name in _LOG_VARIABLES:
                if name == "request_time":
                    self.findings.approximate(
                        "log_format", line, "the time in microseconds, where nginx writes seconds"
                    )
                return _LOG_VARIABLES[name]
            if name.startswith("http_"):
                return "%{" + name[5:].replace("_", "-").title() + "}i"
            self.findings.variable("log_format", line, match.group(0))
            return "-"

        return re.sub(r"\$(\w+)|\$\{(\w+)\}", replace, _escape(written)).replace(
            "[%{%d/%b/%Y:%H:%M:%S %z}t]", "%t"
        )

    def _headers(self, headers: list[Header]) -> list[str]:
        if not headers:
            return []
        self.modules.add("headers")
        lines = []
        for header in headers:
            action = "set"
            lines.append(
                "Header "
                + ("always " if header.always else "")
                + f"{action} {header.name} {_value(header.value)}"
            )
        return lines

    def _compression(self, vhost: Vhost) -> list[str]:
        if vhost.gzip_types is None:
            return []
        self.modules.add("deflate")
        types = " ".join(vhost.gzip_types) or "text/html"
        lines = ["<IfModule mod_deflate.c>", f"    AddOutputFilterByType DEFLATE {types}"]
        if vhost.gzip_level:
            lines.append(f"    DeflateCompressionLevel {vhost.gzip_level}")
        lines.append("</IfModule>")
        return lines

    def _real_ip(self, vhost: Vhost) -> list[str]:
        if vhost.real_ip is None:
            return []
        self.modules.add("remoteip")
        header, networks = vhost.real_ip
        return [f"RemoteIPHeader {header}", *(f"RemoteIPTrustedProxy {net}" for net in networks)]

    # -- What the visitor is sent to -----------------------------------------------------

    def _routes(self, vhost: Vhost, tls: bool) -> list[str]:
        lines: list[str] = []
        acme = [route for route in vhost.routes if route.kind == "acme"]
        proxies = [route for route in vhost.routes if route.kind == "proxy"]
        shields = _minimal(
            [route.path for route in vhost.routes if route.shield and route.kind != "deny"]
        )
        for route in acme:
            lines += self._alias(route)
        for route in vhost.routes:
            if route.kind == "alias":
                lines += self._alias(route)
                if proxies and route.match == "prefix":
                    lines.append(f"ProxyPass {quote(route.path)} !")
        if proxies:
            lines += self._proxies(vhost, proxies, tls)
        for route in vhost.routes:
            if route.kind == "php":
                lines += self._php(route)
            elif route.kind == "redirect":
                lines += self._redirect(route)
            elif route.kind == "deny":
                lines += self._deny(route, shields)
            elif route.kind == "attrs":
                lines += self._attributes(route)
            elif route.kind == "alias" and (route.headers or route.cache_seconds is not None):
                lines += self._attributes(route)
        if vhost.https_redirect and not tls:
            lines += self._https_redirect(acme)
        return lines

    def _alias(self, route: Route) -> list[str]:
        files = route.files or ""
        if route.kind == "alias" and route.match != "prefix":
            self.findings.unsupported("location " + route.path, route.line)
            return []
        return [
            f"Alias {quote(route.path)} {quote(files)}",
            f"<Directory {quote(files)}>",
            "    Options " + ("None" if route.kind == "acme" else "-Indexes +FollowSymLinks"),
            "    AllowOverride None",
            "    Require all granted",
            "</Directory>",
        ]

    def _https_redirect(self, acme: list[Route]) -> list[str]:
        self.modules.add("rewrite")
        lines = ["RewriteEngine On", "RewriteCond %{HTTPS} off"]
        for route in acme:
            lines.append(f"RewriteCond %{{REQUEST_URI}} !^{regex_escape(route.path)}")
        lines.append("RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]")
        return lines

    def _proxies(self, vhost: Vhost, proxies: list[Route], tls: bool) -> list[str]:
        self.modules.update({"proxy", "proxy_http"})
        lines: list[str] = []
        if vhost.preserve_host:
            lines.append("ProxyPreserveHost On")
        lines.append("ProxyRequests Off")
        if any(route.backend and route.backend.scheme == "https" for route in proxies):
            self.modules.add("ssl")
            lines.append("SSLProxyEngine on")
        pairs: list[tuple[Route, Backend, str]] = []
        for route in proxies:
            backend = route.backend
            if route.match != "prefix" or backend is None:
                self.findings.unsupported("proxy_pass in location " + route.path, route.line)
                continue
            target = self._target(backend)
            if target is None:
                self.findings.unsupported("proxy_pass " + backend.address, route.line)
                continue
            pairs.append((route, backend, target))
        for route, _, _ in pairs:
            lines += self._request_headers(route, scoped=route.path != "/")
        websocket = [pair for pair in pairs if pair[0].websocket]
        if websocket:
            self.modules.update({"proxy_wstunnel", "rewrite"})
            lines.append("RewriteEngine On")
            for route, backend, target in websocket:
                lines += self._websocket(route, backend, target)
        # The first rule that matches wins in Apache and the longest prefix wins in nginx.
        for route, backend, target in sorted(pairs, key=lambda pair: -len(pair[0].path)):
            destination = self._destination(route, backend, target)
            options = self._proxy_options(route)
            lines.append(f"ProxyPass {quote(route.path)} {quote(destination)}{options}")
            lines.append(f"ProxyPassReverse {quote(route.path)} {quote(destination)}")
            if route.path != "/" and (route.headers or route.cache_seconds is not None):
                lines += self._attributes(route)
        for route, _, _ in pairs:
            if route.path == "/" and (route.headers or route.cache_seconds is not None):
                lines += self._attributes(route)
        return lines

    @staticmethod
    def _destination(route: Route, backend: Backend, target: str) -> str:
        if target.startswith("balancer://") and backend.uri is None:
            return f"{target}{route.path}"
        uri = backend.uri if backend.uri is not None else route.path
        return f"{target}{uri}"

    @staticmethod
    def _proxy_options(route: Route) -> str:
        options = []
        if route.read_timeout:
            options.append(f"timeout={route.read_timeout}")
        if route.connect_timeout:
            options.append(f"connectiontimeout={route.connect_timeout}")
        if route.unbuffered:
            options.append("flushpackets=on")
        return (" " + " ".join(options)) if options else ""

    def _websocket(self, route: Route, backend: Backend, target: str) -> list[str]:
        scheme = "wss" if target.startswith("https://") else "ws"
        host = re.sub(r"^[a-z]+://", "", target.split("|")[0])
        if target.startswith(("unix:", "balancer://")):
            return []
        uri = backend.uri if backend.uri is not None else route.path
        return [
            "RewriteCond %{HTTP:Upgrade} websocket [NC]",
            "RewriteCond %{HTTP:Connection} upgrade [NC]",
            f'RewriteRule ^{regex_escape(route.path)}(.*)$ "{scheme}://{host}{uri}$1" [P,L]',
        ]

    def _request_headers(self, route: Route, *, scoped: bool) -> list[str]:
        if not route.request_headers:
            return []
        self.modules.add("headers")
        lines = [
            f"RequestHeader set {name} {_value(value)}" for name, value in route.request_headers
        ]
        if not scoped:
            return lines
        return [
            f"<Location {quote(route.path)}>",
            *("    " + line for line in lines),
            "</Location>",
        ]

    # -- PHP, redirects, refusals, caching ------------------------------------------------

    def _section(self, route: Route, *, deny_shield: list[str] | None = None) -> tuple[str, str]:
        if route.match in ("regex", "iregex"):
            pattern = route.path
            if deny_shield:
                pattern = self._shielded(pattern, deny_shield)
            if route.match == "iregex":
                pattern = "(?i)" + pattern
            return f"<LocationMatch {_pattern(pattern)}>", "</LocationMatch>"
        if route.match == "exact":
            exact = "^" + regex_escape(route.path) + "$"
            return f"<LocationMatch {_pattern(exact)}>", "</LocationMatch>"
        return f"<Location {quote(route.path)}>", "</Location>"

    @staticmethod
    def _shielded(pattern: str, shields: list[str]) -> str:
        """
        Keep a pattern off the prefixes nginx kept regular expressions from (``^~``).

        Args:
            pattern: The expression.
            shields: The prefixes.

        Returns:
            The expression with a lookahead that refuses those prefixes.
        """
        if pattern == r"/\." and shields == ["/.well-known/"]:
            return r"/\.(?!well-known(?:/|$))"
        refused = "|".join(regex_escape(prefix) for prefix in shields)
        return f"^(?!{refused})(?=(?s:.*?)(?:{pattern}))"

    def _php(self, route: Route) -> list[str]:
        self.modules.update({"proxy", "proxy_fcgi"})
        socket = route.php_socket or ""
        handler = (
            f"proxy:unix:{socket}|fcgi://localhost"
            if socket.startswith("/")
            else f"proxy:fcgi://{socket}"
        )
        opening, closing = self._section(route)
        lines = [opening, f'    SetHandler "{handler}"', closing]
        if route.read_timeout:
            lines.append(f"ProxyTimeout {route.read_timeout}")
        return lines

    def _redirect(self, route: Route) -> list[str]:
        if route.redirect is None:
            return []
        status, target = route.redirect
        if route.match in ("regex", "iregex"):
            pattern = ("(?i)" if route.match == "iregex" else "") + route.path
        elif route.match == "exact":
            pattern = f"^{regex_escape(route.path)}$"
        else:
            pattern = f"^{regex_escape(route.path)}"
        if "%{" not in target:
            return [f"RedirectMatch {status} {_pattern(pattern)} {quote(target)}"]
        self.modules.add("rewrite")
        return [
            "RewriteEngine On",
            f"RewriteRule {_pattern(pattern)} {quote(target)} [R={status},L]",
        ]

    def _deny(self, route: Route, shields: list[str]) -> list[str]:
        opening, closing = self._section(route, deny_shield=shields)
        return [opening, "    Require all denied", closing]

    def _attributes(self, route: Route) -> list[str]:
        lines: list[str] = []
        if route.cache_seconds is not None:
            lines.append(f'Header set Cache-Control "max-age={route.cache_seconds}"')
        for header in route.headers:
            appends = header.name.lower() == "cache-control" and route.cache_seconds is not None
            action = "append" if appends else "set"
            lines.append(
                "Header "
                + ("always " if header.always else "")
                + f"{action} {header.name} {_value(header.value)}"
            )
        if route.body_limit:
            lines.append(f"LimitRequestBody {route.body_limit}")
        if not lines:
            return []
        self.modules.add("headers")
        opening, closing = self._section(route)
        return [opening, *("    " + line for line in lines), closing]

    # -- Who may reach the site, and the rewrites ----------------------------------------

    def _access(self, vhost: Vhost) -> list[str]:
        access = vhost.access
        if access.empty:
            return []
        lines = ["<Location />"]
        if access.realm is not None:
            self.modules.update({"auth_basic", "authn_file", "authz_user"})
            lines += ["    AuthType Basic", f"    AuthName {_value(access.realm)}"]
            if access.users_file:
                lines.append(f"    AuthUserFile {quote(access.users_file)}")
        allowed = [addr for allow, addr in access.rules if allow and addr != "all"]
        denied = [addr for allow, addr in access.rules if not allow and addr != "all"]
        ends_denying = bool(access.rules) and access.rules[-1] == (False, "all")
        requires: list[str] = []
        if allowed and ends_denying and not denied:
            requires.append("Require ip " + " ".join(allowed))
        elif denied and not ends_denying:
            requires += ["Require all granted", "Require not ip " + " ".join(denied)]
        elif ends_denying and not allowed:
            requires.append("Require all denied")
        elif access.rules:
            self.findings.protection("allow/deny", access.line or vhost.line)
        if access.realm is not None:
            requires.append("Require valid-user")
        if len(requires) > 1:
            combine = "RequireAny" if access.satisfy == "any" else "RequireAll"
            lines += [
                f"    <{combine}>",
                *("        " + line for line in requires),
                f"    </{combine}>",
            ]
        else:
            lines += ["    " + line for line in requires]
        lines.append("</Location>")
        for route in vhost.routes:
            if route.kind == "acme" and route.open_access:
                lines += [
                    f"<Location {quote(route.path)}>",
                    "    Require all granted",
                    "</Location>",
                ]
        return lines

    def _rules(self, vhost: Vhost) -> list[str]:
        lines: list[str] = []
        if vhost.redirect_all is not None:
            status, target = vhost.redirect_all
            self.modules.add("rewrite")
            lines += ["RewriteEngine On", f"RewriteRule ^ {quote(target)} [L,R={status}]"]
        flags = {
            "last": "[L]",
            "break": "[L]",
            "redirect": "[R=302,L]",
            "permanent": "[R=301,L]",
        }
        if vhost.rewrites:
            self.modules.add("rewrite")
            if "RewriteEngine On" not in lines:
                lines.append("RewriteEngine On")
            for pattern, replacement, flag in vhost.rewrites:
                lines.append(f"RewriteRule {_pattern(pattern)} {quote(replacement)} {flags[flag]}")
        return lines
