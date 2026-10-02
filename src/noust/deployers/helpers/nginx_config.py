# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Advanced Nginx configuration builder for Noust.

Supports multi-route path-based proxying, WebSocket upgrade, rate limiting,
request body limits, unbuffered routes, files served straight from the
application and redirects, and custom security headers via noust.nginx.yaml
(or the wasm.nginx.yaml a repository written for WASM carries).

The file comes from the repository, so it is untrusted input: its schema is
closed (a key nothing reads is reported, not ignored), every value that lands
in a directive is checked to be exactly what that directive takes, and a
``static`` path stays inside the application. :meth:`NginxConfigBuilder.validate`
names every problem with its field; a deployer does not use a file that has
any.

Two shapes:

- Routes with a ``port`` give the site routes of its own (the ``advanced``
  template).
- Otherwise the file tunes the plain proxy site (the ``proxy`` template):
  server options, routes that serve files (``static``) or redirect
  (``return``), and a ``/`` route without a port carrying options for the
  application's own location (:attr:`NginxAdvancedConfig.proxies_the_app`).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

import yaml

from noust.core import paths
from noust.core.logger import Logger

#: Keys a noust.nginx.yaml may have at the top level.
TOP_LEVEL_KEYS = frozenset(
    {"routes", "rate_limit", "security_headers", "custom_directives", "max_body_size"}
)

#: Keys a route may have.
ROUTE_KEYS = frozenset(
    {
        "path",
        "port",
        "name",
        "websocket",
        "rate_limit",
        "rate_limit_burst",
        "buffer_size",
        "timeout",
        "read_timeout",
        "send_timeout",
        "strip_prefix",
        "max_body_size",
        "buffering",
        "static",
        "cache",
        "return",
    }
)

#: ``client_max_body_size``: a number with an optional k/m/g unit.
_BODY_SIZE = re.compile(r"^\d+[kKmMgG]?$")
#: ``expires``: a number with an nginx time unit, or ``max``/``off``.
_CACHE = re.compile(r"^(?:\d+(?:ms|s|m|h|d|w|M|y)|max|off|epoch)$")
#: ``limit_req_zone`` rate.
_RATE = re.compile(r"^\d+r/[sm]$")
#: A location path or a redirect target: no whitespace, quotes, braces or ';'.
_DIRECTIVE_SAFE = re.compile(r"^[^\s;{}'\"\\]+$")
#: The status codes a ``return`` route may send.
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
#: Longest a proxy timeout may be, in seconds (a day).
_MAX_TIMEOUT = 86400


@dataclass
class NginxRoute:
    """A single Nginx location route."""

    path: str = "/"
    upstream_port: int = 3000
    upstream_name: str = ""
    websocket: bool = False
    rate_limit: str = ""
    rate_limit_burst: int = 5
    buffer_size: str = ""
    timeout: int = 60
    strip_prefix: bool = False
    #: ``client_max_body_size`` for this route only.
    max_body_size: str = ""
    #: False turns ``proxy_buffering`` off (server-sent events, streams).
    buffering: bool = True
    #: ``proxy_read_timeout``; ``timeout`` when unset.
    read_timeout: int | None = None
    #: ``proxy_send_timeout``; ``timeout`` when unset.
    send_timeout: int | None = None
    #: A directory of the application served as files (an ``alias``).
    static: str = ""
    #: ``expires`` for a static route.
    cache: str = ""
    #: ``(code, target)`` for a redirect route.
    redirect: tuple[int, str] | None = None
    #: The route gave a port, rather than defaulting to 3000.
    port_given: bool = True

    @property
    def proxies(self) -> bool:
        """Whether this route proxies to a port, rather than serving files or redirecting."""
        return not self.static and self.redirect is None


@dataclass
class NginxAdvancedConfig:
    """Advanced Nginx configuration with multiple routes."""

    routes: list[NginxRoute] = field(default_factory=list)
    global_rate_limit: str = ""
    security_headers: dict[str, str] = field(default_factory=dict)
    custom_directives: list[str] = field(default_factory=list)
    #: ``client_max_body_size`` for the whole server.
    max_body_size: str = ""
    #: What the closed schema found wrong while reading, reported by validate().
    problems: list[str] = field(default_factory=list)

    @property
    def proxies_the_app(self) -> bool:
        """
        Whether this file tunes the plain proxy site instead of giving it routes.

        True when no route names a port: the routes only serve files,
        redirect, or carry options for ``/``. A route elsewhere without a
        port keeps meaning port 3000, as it always did.
        """
        if any(route.port_given for route in self.routes):
            return False
        return all(route.path == "/" for route in self.routes if route.proxies)


class NginxConfigBuilder:
    """
    Builder for advanced Nginx configurations.

    Reads noust.nginx.yaml project files.
    """

    CONFIG_FILENAMES: ClassVar = list(paths.NGINX_OVERRIDE_FILES)

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.logger = Logger(verbose=verbose)

    def detect(self, app_path: Path) -> Path | None:
        """
        Find the nginx overrides file: noust.nginx.yaml, or wasm.nginx.yaml.

        Args:
            app_path: Application root path.

        Returns:
            Path to config file, or None if not found.
        """
        for name in self.CONFIG_FILENAMES:
            config_path = app_path / name
            if config_path.exists():
                return config_path
        return None

    def parse(self, config_path: Path) -> NginxAdvancedConfig:
        """
        Parse a noust.nginx.yaml config file.

        What the closed schema finds wrong is collected in the result's
        ``problems`` and reported by :meth:`validate`, so every problem is
        named at once rather than the first one.

        Args:
            config_path: Path to the YAML config file.

        Returns:
            Parsed NginxAdvancedConfig.

        Raises:
            ValueError: If the file is not YAML, or not a mapping.
        """
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {config_path}: {e}") from e

        if not data or not isinstance(data, dict):
            raise ValueError(f"Empty or invalid config in {config_path}")

        config = NginxAdvancedConfig()
        for key in sorted(set(map(str, data)) - TOP_LEVEL_KEYS):
            config.problems.append(
                f"Unknown key {key!r}; a noust.nginx.yaml has: {', '.join(sorted(TOP_LEVEL_KEYS))}"
            )

        routes = data.get("routes") or []
        if not isinstance(routes, list):
            config.problems.append("routes must be a list")
            routes = []
        for index, route_data in enumerate(routes):
            if not isinstance(route_data, dict):
                config.problems.append(f"routes[{index}] must be a mapping")
                continue
            config.routes.append(self._parse_route(route_data, f"routes[{index}]", config))

        config.global_rate_limit = str(data.get("rate_limit") or "")
        config.security_headers = data.get("security_headers") or {}
        config.custom_directives = data.get("custom_directives") or []
        config.max_body_size = str(data.get("max_body_size") or "")
        return config

    def _parse_route(
        self, data: dict[str, Any], where: str, config: NginxAdvancedConfig
    ) -> NginxRoute:
        """
        Read one route, recording what the schema refuses.

        Args:
            data: The route's mapping.
            where: ``routes[<index>]``, to name it in a problem.
            config: Where problems are recorded.

        Returns:
            The route.
        """
        for key in sorted(set(map(str, data)) - ROUTE_KEYS):
            config.problems.append(f"{where}: unknown key {key!r}")
        redirect: tuple[int, str] | None = None
        raw_return = data.get("return")
        if raw_return is not None:
            if isinstance(raw_return, dict) and set(raw_return) <= {"code", "to"}:
                try:
                    redirect = (int(raw_return.get("code", 301)), str(raw_return.get("to") or ""))
                except (TypeError, ValueError):
                    redirect = (0, "")
            else:
                config.problems.append(f"{where}.return must be {{code, to}}")
        route = NginxRoute(
            path=str(data.get("path", "/")),
            upstream_port=data.get("port", 3000),
            upstream_name=str(data.get("name", "")),
            websocket=bool(data.get("websocket", False)),
            rate_limit=str(data.get("rate_limit") or ""),
            rate_limit_burst=data.get("rate_limit_burst", 5),
            buffer_size=str(data.get("buffer_size") or ""),
            timeout=data.get("timeout", 60),
            strip_prefix=bool(data.get("strip_prefix", False)),
            max_body_size=str(data.get("max_body_size") or ""),
            buffering=data.get("buffering", True) is not False,
            read_timeout=data.get("read_timeout"),
            send_timeout=data.get("send_timeout"),
            static=str(data.get("static") or ""),
            cache=str(data.get("cache") or ""),
            redirect=redirect,
            port_given="port" in data,
        )
        kinds = [k for k in ("port", "static", "return") if data.get(k) is not None]
        if len(kinds) > 1:
            config.problems.append(
                f"{where} has {' and '.join(kinds)}: a route is one of port, static or return"
            )
        if not route.upstream_name:
            route.upstream_name = route.path.strip("/").replace("/", "-") or "default"
        return route

    def build_context(
        self,
        config: NginxAdvancedConfig,
        domain: str,
        ssl: bool = False,
        app_path: str = "",
    ) -> dict[str, Any]:
        """
        Build Jinja2 template context from configuration.

        Args:
            config: Advanced Nginx configuration.
            domain: Target domain.
            ssl: Whether SSL is enabled.
            app_path: Application path.

        Returns:
            Dictionary for Jinja2 template rendering.
        """
        app_path = app_path or f"/var/www/apps/{domain}"
        site = _zone_prefix(domain)

        # Collect unique upstreams (by name), for the routes that proxy
        upstreams = {}
        for route in config.routes:
            key = f"upstream_{route.upstream_name}"
            if route.proxies and key not in upstreams:
                upstreams[key] = {
                    "name": route.upstream_name,
                    "port": route.upstream_port,
                }

        # Collect unique rate limit zones; limit_req_zone is http-wide, so the
        # names carry the site's or two sites would stop nginx from loading.
        rate_limit_zones = {}
        if config.global_rate_limit:
            rate_limit_zones[f"{site}_global"] = config.global_rate_limit
        for route in config.routes:
            if route.rate_limit:
                rate_limit_zones[f"{site}_{_zone_name(route.upstream_name)}"] = route.rate_limit

        route_contexts = [
            self._route_context(route, site, config, app_path) for route in config.routes
        ]

        # Default security headers
        security_headers = {
            "X-Frame-Options": "SAMEORIGIN",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        }
        security_headers.update(config.security_headers)

        return {
            "domain": domain,
            "ssl": ssl,
            "ssl_certificate": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
            "ssl_certificate_key": f"/etc/letsencrypt/live/{domain}/privkey.pem",
            "app_path": app_path,
            "upstreams": upstreams,
            "routes": route_contexts,
            "rate_limit_zones": rate_limit_zones,
            "security_headers": security_headers,
            "custom_directives": config.custom_directives,
            "max_body_size": config.max_body_size,
        }

    def proxy_context(
        self, config: NginxAdvancedConfig, domain: str, app_path: str = ""
    ) -> dict[str, Any]:
        """
        Build what a file that tunes the plain proxy site adds to its template context.

        Args:
            config: A configuration for which ``proxies_the_app`` is true.
            domain: Target domain.
            app_path: Application path, where ``static`` routes are served from.

        Returns:
            ``max_body_size``, ``rate_limit_zones``, ``root`` (the options of
            the ``/`` location) and ``extra_locations`` (static and return
            routes); empty values render the proxy site exactly as without a
            file.
        """
        app_path = app_path or f"/var/www/apps/{domain}"
        site = _zone_prefix(domain)
        root = next((r for r in config.routes if r.proxies and r.path == "/"), None)
        zones: dict[str, str] = {}
        zone = ""
        if root is not None and root.rate_limit:
            zone = f"{site}_root"
            zones[zone] = root.rate_limit
        elif config.global_rate_limit:
            zone = f"{site}_global"
            zones[zone] = config.global_rate_limit
        root_ctx: dict[str, Any] = {}
        if root is not None:
            root_ctx = {
                "max_body_size": root.max_body_size,
                "buffering": root.buffering,
                "read_timeout": root.read_timeout,
                "send_timeout": root.send_timeout,
            }
        if zone:
            root_ctx["rate_limit_zone"] = zone
            root_ctx["rate_limit_burst"] = root.rate_limit_burst if root is not None else 5
        return {
            "max_body_size": config.max_body_size,
            "rate_limit_zones": zones,
            "root": root_ctx,
            "extra_locations": [
                self._route_context(route, site, config, app_path)
                for route in config.routes
                if not route.proxies
            ],
        }

    def _route_context(
        self, route: NginxRoute, site: str, config: NginxAdvancedConfig, app_path: str
    ) -> dict[str, Any]:
        """
        Build the template context of one route.

        Args:
            route: The route.
            site: The prefix of the site's rate limit zones.
            config: The whole configuration, for its global rate limit.
            app_path: Where static routes are served from.

        Returns:
            The route's variables.
        """
        if route.rate_limit:
            zone = f"{site}_{_zone_name(route.upstream_name)}"
        elif config.global_rate_limit and route.proxies:
            # The global limit applies to every route without its own; it
            # used to declare a zone no location used.
            zone = f"{site}_global"
        else:
            zone = ""
        static_dir = ""
        if route.static:
            static_dir = f"{app_path.rstrip('/')}/{route.static.strip('/')}/"
        return {
            "path": route.path,
            "upstream_name": route.upstream_name,
            "upstream_port": route.upstream_port,
            "websocket": route.websocket,
            "rate_limit": bool(zone),
            "rate_limit_burst": route.rate_limit_burst,
            "rate_limit_zone": zone,
            "buffer_size": route.buffer_size,
            "timeout": route.timeout,
            "read_timeout": route.read_timeout or route.timeout,
            "send_timeout": route.send_timeout or route.timeout,
            "strip_prefix": route.strip_prefix,
            "max_body_size": route.max_body_size,
            "buffering": route.buffering,
            "static": static_dir,
            "cache": route.cache,
            "return_code": route.redirect[0] if route.redirect else None,
            "return_to": route.redirect[1] if route.redirect else "",
        }

    def validate(self, config: NginxAdvancedConfig) -> list[str]:
        """
        Validate an advanced Nginx configuration.

        Args:
            config: Configuration to validate.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors = list(config.problems)

        if not config.routes and not (config.max_body_size or config.global_rate_limit):
            errors.append("No routes defined")
            return errors

        # Check for duplicate paths
        seen = set()
        for route in config.routes:
            if route.path in seen:
                errors.append(f"Duplicate route path: {route.path}")
            seen.add(route.path)

        proxy_mode = config.proxies_the_app
        for index, route in enumerate(config.routes):
            errors.extend(self._route_errors(route, f"routes[{index}]", proxy_mode=proxy_mode))

        # Validate rate limit format (e.g., "100r/s", "10r/m")
        if config.global_rate_limit and not _RATE.match(config.global_rate_limit):
            errors.append(
                f"Invalid rate limit format: {config.global_rate_limit} (expected: NNr/s or NNr/m)"
            )
        if config.max_body_size and not _BODY_SIZE.match(config.max_body_size):
            errors.append(f"max_body_size {config.max_body_size!r} is not a size such as 20m or 1g")

        return errors

    def _route_errors(self, route: NginxRoute, where: str, *, proxy_mode: bool) -> list[str]:
        """
        Check one route's values against what their directives take.

        Args:
            route: The route.
            where: ``routes[<index>]``.
            proxy_mode: The file tunes the plain proxy site.

        Returns:
            One message per problem.
        """
        errors = []
        if not _DIRECTIVE_SAFE.match(route.path) or not route.path.startswith("/"):
            errors.append(f"{where}.path {route.path!r} is not a location path")
        if route.proxies and not (proxy_mode and route.path == "/"):
            if not isinstance(route.upstream_port, int) or not (1 <= route.upstream_port <= 65535):
                errors.append(f"Invalid port {route.upstream_port} for path {route.path}")
        if route.rate_limit and not _RATE.match(route.rate_limit):
            errors.append(f"Invalid rate limit format for {route.path}: {route.rate_limit}")
        if route.max_body_size and not _BODY_SIZE.match(route.max_body_size):
            errors.append(
                f"{where}.max_body_size {route.max_body_size!r} is not a size such as 20m"
            )
        for name in ("timeout", "read_timeout", "send_timeout"):
            value = getattr(route, name)
            if value is None and name != "timeout":
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= _MAX_TIMEOUT
            ):
                errors.append(f"{where}.{name} must be seconds, 1 to {_MAX_TIMEOUT}")
        if route.static:
            relative = PurePosixPath(route.static)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not _DIRECTIVE_SAFE.match(route.static)
            ):
                errors.append(
                    f"{where}.static {route.static!r} must be a directory inside the application, "
                    "relative to its root, without '..'"
                )
        if route.cache and (not route.static or not _CACHE.match(route.cache)):
            errors.append(
                f"{where}.cache {route.cache!r} must be a time such as 1y or 30d, on a static route"
            )
        if route.redirect is not None:
            code, target = route.redirect
            if code not in _REDIRECT_CODES:
                errors.append(
                    f"{where}.return code {code} is not a redirect "
                    f"({', '.join(map(str, sorted(_REDIRECT_CODES)))})"
                )
            if not target or not _DIRECTIVE_SAFE.match(target):
                errors.append(f"{where}.return to {target!r} is not a URL or a path")
        return errors


def _zone_prefix(domain: str) -> str:
    """
    Name a site's rate limit zones.

    Args:
        domain: The site's domain.

    Returns:
        The domain with every character a zone name should not hold as ``_``.
    """
    return _zone_name(domain)


def _zone_name(text: str) -> str:
    """
    Turn text into a zone name.

    Args:
        text: The text.

    Returns:
        Lower case, ``[a-z0-9_]`` only.
    """
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "default"
