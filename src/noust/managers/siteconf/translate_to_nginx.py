# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Write the records of a site (:mod:`translate_model`) as an nginx site.

The layout is the one Noust's own nginx templates write: the log format and the groups of backends
first, then one ``server`` per virtual host, with the names, the TLS lines, the logs, the headers
and a ``location`` for each path that has something of its own.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from noust.managers.siteconf.nginx import quote
from noust.managers.siteconf.translate_model import (
    Findings,
    Header,
    Listener,
    Route,
    SiteModel,
    Vhost,
    format_size,
    regex_escape,
)

_APACHE_LOGS = ("${APACHE_LOG_DIR}/", "/var/log/apache2/")
_NGINX_LOGS = "/var/log/nginx/"

#: Where the certificate authority's challenge is looked for when the old site did not say.
_ACME = "/.well-known/acme-challenge/"
_ACME_ROOT = "/var/www/html"

#: What an Apache log format code becomes in an nginx one.
_LOG_CODES = {
    "h": "$remote_addr",
    "a": "$remote_addr",
    "l": "-",
    "u": "$remote_user",
    "r": "$request",
    ">s": "$status",
    "s": "$status",
    "b": "$body_bytes_sent",
    "B": "$body_bytes_sent",
    "O": "$bytes_sent",
    "I": "$request_length",
    "m": "$request_method",
    "v": "$server_name",
    "p": "$server_port",
    "U": "$uri",
    "q": "$is_args$args",
    "T": "$request_time",
    "D": "$request_time",
    "t": "[$time_local]",
}

#: The statuses a ``rewrite`` can send without a ``location``.
_REWRITE_FLAGS = {301: "permanent", 302: "redirect"}

CanonicalLog = Callable[[str], str | None]


class NginxWriter:
    """
    Write one site.

    Args:
        model: What the site says.
        findings: Where what cannot be written is recorded.
        domain: The site's domain.
        canonical_log: The definition of the log format Noust's templates write for the site.
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
        self.names: list[str] = []
        self._formats: dict[str, str] = {}

    def write(self) -> str:
        """
        Returns:
            The nginx site.
        """
        servers = [self._server(vhost) for vhost in self.model.vhosts]
        top: list[str] = []
        for definition in self._formats.values():
            top += [definition.strip(), ""]
        top += self._upstreams()
        lines = [*top]
        for block in servers:
            lines += [*block, ""]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines) + "\n"

    def _upstreams(self) -> list[str]:
        lines: list[str] = []
        for group in self.model.upstreams.values():
            if not group.servers:
                continue
            lines.append(f"upstream {group.name} {{")
            for address, weight, backup in group.servers:
                options = (f" weight={weight}" if weight != 1 else "") + (
                    " backup" if backup else ""
                )
                lines.append(f"    server {address}{options};")
            lines += ["    keepalive 64;", "}", ""]
        return lines

    # -- A server ------------------------------------------------------------------------

    def _server(self, vhost: Vhost) -> list[str]:
        for name in vhost.names:
            if name not in self.names and name != "_":
                self.names.append(name)
        body: list[str] = []
        for listener in vhost.listeners or [Listener(80)]:
            body.append(self._listen(listener))
        if vhost.names:
            body.append("server_name " + " ".join(vhost.names) + ";")
        if vhost.tokens_off:
            body.append("server_tokens off;")
        body += self._tls(vhost)
        body += self._access(vhost)
        if vhost.root:
            body.append(f"root {quote(vhost.root)};")
        if vhost.index:
            body.append("index " + " ".join(vhost.index) + ";")
        if vhost.body_limit:
            body.append(f"client_max_body_size {format_size(vhost.body_limit)};")
        body += self._logs(vhost)
        body += self._headers(vhost.headers)
        body += self._compression(vhost)
        body += self._real_ip(vhost)
        for status, page in vhost.error_pages:
            body.append(f"error_page {status} {page};")
        body += self._rewrites(vhost)
        body += self._locations(vhost)
        out = ["server {"]
        out += [("    " + line) if line else "" for line in body]
        out.append("}")
        return out

    @staticmethod
    def _listen(listener: Listener) -> str:
        if listener.ipv6:
            spec = f"[::]:{listener.port}"
        elif listener.address is None:
            spec = str(listener.port)
        elif ":" in listener.address:
            spec = f"[{listener.address}]:{listener.port}"
        else:
            spec = f"{listener.address}:{listener.port}"
        flags = (" ssl" if listener.ssl else "") + (" http2" if listener.http2 else "")
        return f"listen {spec}{flags};"

    def _tls(self, vhost: Vhost) -> list[str]:
        tls = vhost.tls
        if tls is None:
            return []
        lines = []
        if tls.certificate:
            lines.append(f"ssl_certificate {quote(tls.certificate)};")
        if tls.key:
            lines.append(f"ssl_certificate_key {quote(tls.key)};")
        if tls.protocols:
            lines.append("ssl_protocols " + " ".join(tls.protocols) + ";")
        if tls.ciphers:
            lines.append(f"ssl_ciphers {tls.ciphers};")
        if tls.prefer_server_ciphers is not None:
            lines.append(
                "ssl_prefer_server_ciphers " + ("on" if tls.prefer_server_ciphers else "off") + ";"
            )
        if tls.tickets is not None:
            lines.append("ssl_session_tickets " + ("on" if tls.tickets else "off") + ";")
        if tls.client_ca:
            lines.append(f"ssl_client_certificate {quote(tls.client_ca)};")
        if tls.verify_client:
            lines.append(f"ssl_verify_client {tls.verify_client};")
        if tls.verify_depth is not None:
            lines.append(f"ssl_verify_depth {tls.verify_depth};")
        return lines

    # -- Who may reach the site ----------------------------------------------------------

    def _access(self, vhost: Vhost) -> list[str]:
        access = vhost.access
        if access.empty:
            return []
        lines = []
        if access.realm is not None and access.rules:
            lines.append(f"satisfy {access.satisfy};")
        for allow, address in access.rules:
            lines.append(f"{'allow' if allow else 'deny'} {address};")
        if access.realm is not None:
            lines.append(f"auth_basic {quote(access.realm)};")
            if access.users_file:
                lines.append(f"auth_basic_user_file {quote(access.users_file)};")
        return lines

    # -- Logs, headers, compression --------------------------------------------------------

    @staticmethod
    def _log_path(path: str) -> str:
        for directory in _APACHE_LOGS:
            if path.startswith(directory):
                return _NGINX_LOGS + path[len(directory) :]
        return path

    def _logs(self, vhost: Vhost) -> list[str]:
        lines: list[str] = []
        if vhost.access_log is not None:
            path, name = vhost.access_log
            suffix = ""
            if name and name != "combined":
                definition = self.canonical_log(name)
                if definition is None and name in self.model.log_formats:
                    definition = self._log_format(name, self.model.log_formats[name], vhost.line)
                if definition is not None:
                    self._formats[name] = definition
                    suffix = f" {name}"
                else:
                    self.findings.unsupported(f"CustomLog {name}", vhost.line)
            elif name == "combined":
                suffix = " combined"
            lines.append(f"access_log {quote(self._log_path(path))}{suffix};")
        if vhost.error_log:
            lines.append(f"error_log {quote(self._log_path(vhost.error_log))};")
        return lines

    def _log_format(self, name: str, written: str, line: int) -> str:
        def replace(match: re.Match[str]) -> str:
            code = match.group(2) or match.group(1)
            argument = match.group(1) if match.group(2) else None
            if argument is not None:
                if code == "i":
                    return "$http_" + argument.lower().replace("-", "_")
                if code == "t":
                    return "$time_local"
                self.findings.variable("LogFormat", line, match.group(0))
                return "-"
            if code in _LOG_CODES:
                if code in ("D",):
                    self.findings.approximate(
                        "LogFormat", line, "the time in seconds, where Apache writes microseconds"
                    )
                return _LOG_CODES[code]
            self.findings.variable("LogFormat", line, match.group(0))
            return "-"

        converted = re.sub(r"%(?:\{([^}]*)\})?(>?[A-Za-z])", replace, written)
        converted = converted.replace('\\"', '"').replace("'", "\\'")
        return f"log_format {name} '{converted}';"

    def _headers(self, headers: list[Header]) -> list[str]:
        return [
            f"add_header {quote(item.name)} {quote(item.value)}{' always' if item.always else ''};"
            for item in headers
        ]

    @staticmethod
    def _compression(vhost: Vhost) -> list[str]:
        if vhost.gzip_types is None:
            return []
        lines = ["gzip on;", "gzip_vary on;", "gzip_proxied any;"]
        if vhost.gzip_level:
            lines.append(f"gzip_comp_level {vhost.gzip_level};")
        # nginx always compresses text/html and warns about the type being named again.
        types = [item for item in vhost.gzip_types if item != "text/html"]
        if types:
            lines.append("gzip_types " + " ".join(types) + ";")
        return lines

    @staticmethod
    def _real_ip(vhost: Vhost) -> list[str]:
        if vhost.real_ip is None:
            return []
        header, networks = vhost.real_ip
        return [*(f"set_real_ip_from {net};" for net in networks), f"real_ip_header {header};"]

    def _rewrites(self, vhost: Vhost) -> list[str]:
        lines = []
        if vhost.redirect_all is not None:
            status, target = vhost.redirect_all
            lines.append(f"return {status} {quote(target)};")
        for pattern, replacement, flag in vhost.rewrites:
            lines.append(f"rewrite {quote(pattern)} {quote(replacement)} {flag};")
        return lines

    # -- Locations -----------------------------------------------------------------------

    def _locations(self, vhost: Vhost) -> list[str]:
        lines: list[str] = []
        acme = [route for route in vhost.routes if route.kind == "acme"]
        if vhost.https_redirect and not acme:
            self.findings.approximate(
                "RewriteRule",
                vhost.line,
                "the redirect to HTTPS, with the certificate authority's directory beside it",
            )
            acme = [
                Route(
                    "prefix",
                    _ACME,
                    "acme",
                    shield=True,
                    files=_ACME_ROOT + _ACME,
                    open_access=True,
                )
            ]
            vhost.routes.insert(0, acme[0])
        for route in vhost.routes:
            lines += self._location(vhost, route)
        if vhost.https_redirect:
            lines += ["", "location / {", "    return 301 https://$host$request_uri;", "}"]
        elif vhost.fallback:
            lines += ["", "location / {", f"    try_files $uri $uri/ {self._fallback(vhost)};", "}"]
        return lines

    @staticmethod
    def _fallback(vhost: Vhost) -> str:
        fallback = vhost.fallback or ""
        return fallback + "?$args" if fallback.endswith(".php") else fallback

    @staticmethod
    def _modifier(route: Route) -> str:
        return {
            "prefix": "^~ " if route.shield else "",
            "exact": "= ",
            "regex": "~ ",
            "iregex": "~* ",
        }[route.match]

    def _location(self, vhost: Vhost, route: Route) -> list[str]:
        head = f"location {self._modifier(route)}{quote(route.path)} {{"
        body: list[str] = []
        if route.kind == "acme":
            body = self._acme(route)
        elif route.kind == "alias":
            body = [f"alias {quote(route.files or '')};", *self._cache(route)]
        elif route.kind == "proxy":
            body = self._proxy(route)
        elif route.kind == "php":
            body = self._php(route)
        elif route.kind == "redirect":
            return self._redirect(route)
        elif route.kind == "deny":
            body = ["deny all;"]
        elif route.kind == "attrs":
            body = self._cache(route)
            if route.body_limit:
                body.append(f"client_max_body_size {format_size(route.body_limit)};")
        elif route.kind != "empty":
            return []
        return ["", head, *("    " + line for line in body), "}"]

    @staticmethod
    def _acme(route: Route) -> list[str]:
        files, path = route.files or "", route.path
        body = [
            f"root {quote(files[: -len(path)])};"
            if files.endswith(path)
            else f"alias {quote(files)};"
        ]
        body.append("allow all;")
        if route.open_access:
            body += ["auth_basic off;"]
        return body

    @staticmethod
    def _cache(route: Route) -> list[str]:
        lines = []
        if route.cache_seconds is not None:
            lines.append(f"expires {route.cache_seconds}s;")
        lines += [
            f"add_header {quote(item.name)} {quote(item.value)}{' always' if item.always else ''};"
            for item in route.headers
        ]
        return lines

    def _proxy(self, route: Route) -> list[str]:
        backend = route.backend
        if backend is None:
            return []
        if backend.unix:
            target = f"http://unix:{backend.address}:{backend.uri or ''}"
        elif backend.upstream is not None:
            target = f"{backend.scheme}://{backend.upstream}{backend.uri or ''}"
        else:
            target = f"{backend.scheme}://{backend.address}{backend.uri or ''}"
        body = [f"proxy_pass {target};", "proxy_http_version 1.1;", ""]
        if route.preserve_host:
            body.append("proxy_set_header Host $host;")
        body += [
            "proxy_set_header X-Real-IP $remote_addr;",
            "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            "proxy_set_header X-Forwarded-Proto $scheme;",
            "proxy_set_header X-Forwarded-Host $host;",
            "proxy_set_header X-Forwarded-Port $server_port;",
        ]
        if route.websocket:
            body += [
                "proxy_set_header Upgrade $http_upgrade;",
                'proxy_set_header Connection "upgrade";',
            ]
        for name, value in route.request_headers:
            if name.lower() in ("x-real-ip", "x-forwarded-proto", "x-forwarded-port"):
                continue
            body.append(f"proxy_set_header {quote(name)} {quote(value)};")
        body.append("")
        body.append(f"proxy_connect_timeout {route.connect_timeout or 60}s;")
        body.append(f"proxy_send_timeout {route.send_timeout or route.read_timeout or 60}s;")
        body.append(f"proxy_read_timeout {route.read_timeout or 60}s;")
        if route.unbuffered:
            body.append("proxy_buffering off;")
        body += self._cache(route)
        return body

    @staticmethod
    def _php(route: Route) -> list[str]:
        socket = route.php_socket or ""
        pass_to = f"unix:{socket}" if socket.startswith("/") else socket
        body = [
            "try_files $uri =404;",
            f"fastcgi_pass {pass_to};",
            "include fastcgi_params;",
            "fastcgi_param SCRIPT_FILENAME $realpath_root$fastcgi_script_name;",
            "fastcgi_param DOCUMENT_ROOT $realpath_root;",
        ]
        if route.read_timeout:
            body.append(f"fastcgi_read_timeout {route.read_timeout};")
        return body

    def _redirect(self, route: Route) -> list[str]:
        if route.redirect is None:
            return []
        status, target = route.redirect
        flag = _REWRITE_FLAGS.get(status)
        if route.match == "prefix":
            pattern = f"^{regex_escape(route.path)}(.*)$"
            destination = f"{target}$1"
        else:
            pattern = ("(?i)" if route.match == "iregex" else "") + route.path
            destination = target
        if flag is not None:
            return ["", f"rewrite {quote(pattern)} {quote(destination)} {flag};"]
        return [
            "",
            f"location {self._modifier(route)}{quote(route.path)} {{",
            f"    return {status} {quote(target)};",
            "}",
        ]
