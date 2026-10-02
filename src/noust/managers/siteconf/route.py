"""
Which server and which location answer a request, and why.

This replays the web server's own selection over the structured model, so the
console can show the path a URL takes through a site and explain it in the
web server's terms - the explanation that would have caught a request falling
into a catch-all ``location /api/`` instead of the route written for it.

nginx (``ngx_http_find_virtual_server``, ``ngx_http_core_find_location``):

- servers listening on the port; among them the exact name, then the
  longest leading wildcard (``*.example.com``, ``.example.com``), then the
  longest trailing wildcard (``www.example.*``), then the first regular
  expression; failing all, the ``default_server`` of the port, or the first
  server listening on it;
- locations: an exact ``=`` match ends the search; otherwise the longest
  matching prefix is remembered (and its nested locations searched); ``^~``
  on it skips the regular expressions; otherwise regular expressions are
  tried in file order and the first match wins; if none matches, the
  remembered prefix answers. A proxied prefix ending in ``/`` requested
  without the slash answers a 301 to it.

Apache: the first ``<VirtualHost>`` on the port whose ``ServerName`` or
``ServerAlias`` matches, else the first one on the port; a ``ProxyPass``
inside a matching ``<Location>`` first, then the virtual host's
``ProxyPass``/``ProxyPassMatch`` rules in file order, the first match
winning; every matching ``<Location>`` applies, in order.

Each step is returned as English text and as a code with parameters, so the
console can say it in its own language.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from noust.managers.siteconf.model import Location, Serializable, Server, SiteStructure
from noust.managers.siteconf.regex import BoundedMatcher

_TEXT = {
    "no_server": "No server listens on port {port}.",
    "port": "Servers listening on port {port}: {servers}.",
    "server_exact": "{host} is the exact name {name} of {server}.",
    "server_leading": "{host} matches the wildcard name {name} of {server}: the longest leading wildcard wins.",
    "server_trailing": "{host} matches the wildcard name {name} of {server}: the longest trailing wildcard wins.",
    "server_regex": "{host} matches the regular expression name {name} of {server}, the first one that does.",
    "server_default": "No name matches {host}, so {server} answers: it is the default_server of port {port}.",
    "server_first": "No name matches {host}, so {server} answers: it is the first server listening on port {port}.",
    "regex_skipped": "The regular expression {regex} cannot be evaluated here; it was skipped.",
    "no_tls": "The request is HTTPS but {server} does not speak TLS on port {port}.",
    "uri": "nginx matches the path {path}: no query string, decoded and normalised.",
    "location_exact": "{path} is exactly = {location}: nginx stops searching.",
    "location_prefix": "{path} starts with the prefix {location}.",
    "location_prefixes": "{path} starts with the prefixes {prefixes}: the longest wins, {location}.",
    "location_nested": "nginx looks next at the locations nested in {location}.",
    "location_stop": "^~ on {location} stops the search: regular expressions are not tried.",
    "location_regex": "{path} matches the regular expression {location}, the first one in file order that does: it wins over any prefix.",
    "location_no_regex": "No regular expression location matches {path}, so the prefix {location} answers.",
    "location_none": "No location matches {path}: the server's own directives answer it.",
    "auto_redirect": "{path} is the proxied location {location} without its final slash: nginx answers with a 301 redirect to {redirect}.",
    "target_upstream": "The request goes to upstream {upstream}.",
    "target_proxy": "The request is proxied to {url}.",
    "target_static": "Files are served from {directory}.",
    "target_return": "The web server answers {code} {destination}.",
    "target_fastcgi": "The request goes to FastCGI at {address}.",
    "target_other": "The location answers the request itself.",
    "apache_name": "{host} is a name of {server} ({name}), the first virtual host on the port that has it.",
    "apache_first": "No virtual host on port {port} has the name {host}, so the first one, {server}, answers.",
    "apache_location_proxy": "<Location {location}> matches and its ProxyPass sends the request to {url}.",
    "apache_proxypass": "ProxyPass {location} is the first rule, in file order, that matches {path}.",
    "apache_excluded": "ProxyPass {location} ! is the first rule that matches {path}: it is not proxied, the document root serves it.",
    "apache_sections": "These <Location> sections apply, in order: {locations}.",
    "apache_no_proxy": "No ProxyPass rule matches {path}: the document root serves it.",
}


@dataclass
class RouteStep(Serializable):
    """
    One step of the explanation.

    Attributes:
        code: What happened, as a key the console translates.
        params: The values the sentence names.
        text: The sentence in English.
    """

    code: str
    params: dict[str, Any]
    text: str


@dataclass
class RouteResult(Serializable):
    """
    The answer to "who serves this request".

    Attributes:
        server_id: The server that answers, None when no server listens.
        location_id: The location (or Apache ``ProxyPass`` rule) that
            answers, None when the server answers by itself.
        steps: The explanation, one English sentence per step.
        trace: The same steps as codes and parameters.
        highlight: The ids along the path, server first, for the diagram.
        redirect: Where nginx's automatic 301 sends the client, if it does.
    """

    server_id: str | None
    location_id: str | None
    steps: list[str] = field(default_factory=list)
    trace: list[RouteStep] = field(default_factory=list)
    highlight: list[str] = field(default_factory=list)
    redirect: str | None = None

    def add(self, step: str, /, **params: Any) -> None:
        """
        Record a step.

        Args:
            step: A key of the step catalogue.
            **params: The values the step names.
        """
        text = _TEXT[step].format(**params)
        self.trace.append(RouteStep(step, params, text))
        self.steps.append(text)


def route(
    structure: SiteStructure, *, host: str, path: str, scheme: str, port: int | None
) -> RouteResult:
    """
    Find the server and location that answer a request.

    Args:
        structure: The site's model.
        host: The Host header (a port in it is ignored).
        path: The request path; a query string is ignored.
        scheme: ``http`` or ``https``.
        port: The port the request arrives on; None for the scheme's default.

    Returns:
        The server, the location and the explanation.
    """
    port = port or (443 if scheme == "https" else 80)
    host = _bare_host(host)
    matcher = BoundedMatcher()
    if structure.kind == "apache":
        return _apache(structure, host, path, scheme, port, matcher)
    return _nginx(structure, host, path, scheme, port, matcher)


def _bare_host(host: str) -> str:
    host = host.strip().lower().rstrip(".")
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    name, sep, maybe_port = host.rpartition(":")
    return name if sep and maybe_port.isdigit() else host


def pcre_to_python(pattern: str) -> str:
    """
    Translate the PCRE syntax nginx configurations use into Python's.

    Args:
        pattern: A PCRE pattern.

    Returns:
        The pattern with named groups in Python's spelling.
    """
    return re.sub(
        r"\(\?<(?=[A-Za-z_])", "(?P<", re.sub(r"\(\?'([A-Za-z_]\w*)'", r"(?P<\1>", pattern)
    )


def _search(matcher: BoundedMatcher, pattern: str, subject: str, flags: int = 0) -> bool | None:
    """
    Match a configuration's PCRE pattern, its work bounded (see :mod:`.regex`).

    Args:
        matcher: The explanation's matcher.
        pattern: The pattern as the configuration has it.
        subject: The host or the normalised path.
        flags: ``re`` flags.

    Returns:
        Whether it matches; None when it was not evaluated.
    """
    return matcher.search(pcre_to_python(pattern), subject, flags=flags)


def normalise(path: str) -> str:
    """
    The URI nginx matches locations against.

    Args:
        path: The request path, possibly with a query string.

    Returns:
        The path decoded, with repeated slashes merged and dot segments
        resolved, as nginx does before choosing a location.
    """
    path = path.split("?", 1)[0].split("#", 1)[0]
    decoded = unquote(path if path.startswith("/") else "/" + path)
    segments: list[str] = []
    parts = re.sub("/{2,}", "/", decoded).split("/")[1:]
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "..":
            if segments:
                segments.pop()
            if last:
                segments.append("")
        elif part == ".":
            if last:
                segments.append("")
        else:
            segments.append(part)
    return "/" + "/".join(segments)


def _listens_on(server: Server, port: int) -> bool:
    if not server.listens:
        return port == 80
    return any(listen.port in (port, None) for listen in server.listens)


def _nginx(
    structure: SiteStructure,
    host: str,
    path: str,
    scheme: str,
    port: int,
    matcher: BoundedMatcher,
) -> RouteResult:
    candidates = [server for server in structure.servers if _listens_on(server, port)]
    if not candidates:
        result = RouteResult(None, None)
        result.add("no_server", port=port)
        return result
    result = RouteResult(None, None)
    result.add("port", port=port, servers=", ".join(server.id for server in candidates))
    server = _nginx_server(candidates, host, port, result, matcher)
    result.server_id = server.id
    result.highlight.append(server.id)
    if (
        scheme == "https"
        and server.listens
        and not any(listen.ssl and listen.port == port for listen in server.listens)
    ):
        result.add("no_tls", server=server.id, port=port)
    uri = normalise(path)
    if uri != path:
        result.add("uri", path=uri)
    chain: list[Location] = []
    found, _ = _find(server.locations, uri, result, chain, matcher)
    if found is None:
        result.add("location_none", path=uri)
        return result
    result.location_id = found.id
    result.highlight.extend(location.id for location in chain if location.id != found.id)
    result.highlight.append(found.id)
    if result.redirect is None:
        _describe_target(found, result)
    return result


def _nginx_server(
    candidates: list[Server], host: str, port: int, result: RouteResult, matcher: BoundedMatcher
) -> Server:
    for server in candidates:
        for name in server.names:
            if name.lower() == host:
                result.add("server_exact", host=host, name=name, server=server.id)
                return server
    best: tuple[int, Server, str] | None = None
    for server in candidates:
        for name in server.names:
            lowered = name.lower()
            if lowered.startswith("*."):
                matched = host.endswith(lowered[1:])
            elif lowered.startswith("."):
                matched = host == lowered[1:] or host.endswith(lowered)
            else:
                continue
            if matched and (best is None or len(lowered) > best[0]):
                best = (len(lowered), server, name)
    if best is not None:
        result.add("server_leading", host=host, name=best[2], server=best[1].id)
        return best[1]
    for server in candidates:
        for name in server.names:
            lowered = name.lower()
            if lowered.endswith(".*") and host.startswith(lowered[:-1]):
                if best is None or len(lowered) > best[0]:
                    best = (len(lowered), server, name)
    if best is not None:
        result.add("server_trailing", host=host, name=best[2], server=best[1].id)
        return best[1]
    for server in candidates:
        for name in server.names:
            if not name.startswith("~"):
                continue
            found = _search(matcher, name[1:], host)
            if found is None:
                result.add("regex_skipped", regex=name)
            elif found:
                result.add("server_regex", host=host, name=name, server=server.id)
                return server
    for server in candidates:
        if any(listen.default_server and listen.port in (port, None) for listen in server.listens):
            result.add("server_default", host=host, server=server.id, port=port)
            return server
    result.add("server_first", host=host, server=candidates[0].id, port=port)
    return candidates[0]


def _passes(location: Location) -> bool:
    return location.target.kind in ("proxy", "fastcgi")


def _label(location: Location) -> str:
    return f"{location.modifier} {location.path}".strip()


def _find(
    locations: list[Location],
    uri: str,
    result: RouteResult,
    chain: list[Location],
    matcher: BoundedMatcher,
) -> tuple[Location | None, str]:
    """One level of ``ngx_http_core_find_location``; returns the match and nginx's rc."""
    for location in locations:
        if location.modifier == "=" and location.path == uri:
            result.add("location_exact", path=uri, location=location.path)
            return location, "ok"
    prefixes = [
        location
        for location in locations
        if location.modifier in ("", "^~") and uri.startswith(location.path)
    ]
    if not any(location.path == uri for location in prefixes):
        for location in locations:
            if (
                location.modifier in ("", "^~", "=")
                and location.path == uri + "/"
                and _passes(location)
            ):
                result.redirect = location.path
                result.add("auto_redirect", path=uri, location=location.path, redirect=uri + "/")
                return location, "done"
    current: Location | None = None
    rc = "declined"
    noregex = False
    if prefixes:
        longest = max(prefixes, key=lambda location: len(location.path))
        if len(prefixes) == 1:
            result.add("location_prefix", path=uri, location=_label(longest))
        else:
            ordered = sorted(prefixes, key=lambda location: len(location.path))
            result.add(
                "location_prefixes",
                path=uri,
                prefixes=", ".join(_label(location) for location in ordered),
                location=_label(longest),
            )
        current, rc = longest, "again"
        noregex = longest.modifier == "^~"
        chain.append(longest)
        if longest.locations:
            result.add("location_nested", location=_label(longest))
            nested, rc = _find(longest.locations, uri, result, chain, matcher)
            if nested is not None:
                current = nested
            if rc in ("ok", "done"):
                return current, rc
    if noregex and current is not None:
        result.add("location_stop", location=_label(current))
        return current, rc
    regexes = [location for location in locations if location.modifier in ("~", "~*")]
    for location in regexes:
        matched = _search(
            matcher, location.path, uri, re.IGNORECASE if location.modifier == "~*" else 0
        )
        if matched is None:
            result.add("regex_skipped", regex=_label(location))
            continue
        if matched:
            result.add("location_regex", path=uri, location=_label(location))
            chain.append(location)
            nested, _ = (
                _find(location.locations, uri, result, chain, matcher)
                if location.locations
                else (None, "")
            )
            return nested or location, "ok"
    if regexes and current is not None:
        result.add("location_no_regex", path=uri, location=_label(current))
    return current, rc


def _describe_target(location: Location, result: RouteResult) -> None:
    target = location.target
    if target.kind == "proxy" and target.upstream:
        result.add("target_upstream", upstream=target.upstream)
        result.highlight.append(f"u:{target.upstream}")
    elif target.kind == "proxy":
        result.add("target_proxy", url=target.url or target.address or "")
    elif target.kind == "static":
        result.add("target_static", directory=target.alias or target.root or "")
    elif target.kind == "return":
        result.add("target_return", code=target.code, destination=target.destination or "")
    elif target.kind == "fastcgi":
        result.add("target_fastcgi", address=target.address or "")
    else:
        result.add("target_other")


def _apache(
    structure: SiteStructure,
    host: str,
    path: str,
    scheme: str,
    port: int,
    matcher: BoundedMatcher,
) -> RouteResult:
    result = RouteResult(None, None)
    candidates = [server for server in structure.servers if _listens_on(server, port)]
    if not candidates:
        result.add("no_server", port=port)
        return result
    result.add("port", port=port, servers=", ".join(server.id for server in candidates))
    server = next(
        (s for s in candidates for name in s.names if fnmatch.fnmatchcase(host, name.lower())),
        None,
    )
    if server is not None:
        name = next(n for n in server.names if fnmatch.fnmatchcase(host, n.lower()))
        result.add("apache_name", host=host, server=server.id, name=name)
    else:
        server = candidates[0]
        result.add("apache_first", host=host, server=server.id, port=port)
    result.server_id = server.id
    result.highlight.append(server.id)
    uri = normalise(path)

    def matches(location: Location) -> bool:
        if location.modifier == "~":
            return bool(_search(matcher, location.path, uri))
        return uri.startswith(location.path)

    sections = [
        loc
        for loc in server.locations
        if loc.source in ("Location", "LocationMatch") and matches(loc)
    ]
    chosen: Location | None = None
    in_section = [loc for loc in sections if loc.target.kind == "proxy"]
    if in_section:
        chosen = in_section[-1]
        result.add("apache_location_proxy", location=chosen.path, url=chosen.target.url or "")
    else:
        rules = [loc for loc in server.locations if loc.source.startswith("ProxyPass")]
        chosen = next((loc for loc in rules if matches(loc)), None)
        if chosen is None:
            result.add("apache_no_proxy", path=uri)
        elif chosen.target.kind == "proxy":
            result.add("apache_proxypass", location=chosen.path, path=uri)
        else:
            result.add("apache_excluded", location=chosen.path, path=uri)
    if sections:
        result.add("apache_sections", locations=", ".join(loc.path for loc in sections))
    final = chosen or (sections[-1] if sections else None)
    if final is not None:
        result.location_id = final.id
        result.highlight.append(final.id)
        if final.target.kind == "proxy":
            _describe_target(final, result)
    return result
