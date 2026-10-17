# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Authentication delegated to a service that is not the application: forward-auth.

nginx's ``auth_request`` asks a service about every request before it reaches the application:
a 2xx lets it through, a 401 or 403 stops it, and whatever the service says in its response
headers (who the visitor is) can be handed to the application. That is how an identity provider
or an OAuth2 proxy (oauth2-proxy, authentik's outpost, Authelia, any service that answers like
that) protects an application that knows nothing about it, with the accounts, the second factor
and the single sign-on the provider already has.

The policy keeps only what the web server needs (:class:`ForwardAuth`); the choice of provider
is the operator's, and :data:`PRESETS` are the defaults of the common ones for the console and the
command line to offer, never stored: what is stored is the three values they filled in.

Everything here ends up in a file the web server loads as root, so each value is a closed
alphabet (an address, a header name), and no value can carry a variable of the web server's own
except the ones Noust writes itself.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.runner import CommandRunner
from noust.managers.site_routes import redirect_target

#: The most response headers of the service that are handed on to the application.
MAX_COPIED = 10

_HEADER = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}")

#: Headers that identify a visitor to the application are not handed on from a request, only from
#: the service's answer; these are never copied because they belong to the connection.
_FORBIDDEN = frozenset(
    {
        "connection",
        "content-length",
        "content-type",
        "host",
        "transfer-encoding",
        "upgrade",
        "set-cookie",
        "cookie",
        "authorization",
        "x-forwarded-for",
        "x-real-ip",
    }
)

_FIELDS = frozenset({"url", "sign_in", "headers"})

#: What the common providers answer with, for a form to fill in; the operator's own address goes
#: where ``{host}`` stands. Checked against each project's documentation, and editable.
PRESETS: dict[str, dict[str, Any]] = {
    "oauth2-proxy": {
        "url": "http://127.0.0.1:4180/oauth2/auth",
        "sign_in": "/oauth2/start?rd=",
        "headers": ["X-Auth-Request-User", "X-Auth-Request-Email", "X-Auth-Request-Groups"],
        "note": "Run oauth2-proxy with --set-xauthrequest, and reach its /oauth2/ paths on the "
        "same site (a route handed to its port) or give the full address of its sign-in.",
    },
    "authentik": {
        "url": "http://127.0.0.1:9000/outpost.goauthentik.io/auth/nginx",
        "sign_in": "/outpost.goauthentik.io/start?rd=",
        "headers": [
            "X-authentik-username",
            "X-authentik-groups",
            "X-authentik-email",
            "X-authentik-name",
            "X-authentik-uid",
        ],
        "note": "The outpost's /outpost.goauthentik.io/ paths must be reachable on the same site "
        "(a route handed to authentik's port) for the sign-in redirect to land.",
    },
}


@dataclass(frozen=True)
class ForwardAuth:
    """
    The service that answers for a site's visitors.

    Attributes:
        url: Where each request is asked about: an ``http`` or ``https`` address.
        sign_in: Where a visitor who is not signed in is sent (the original address is
            appended): an address, or a path on this site; empty answers 401 instead.
        headers: Response headers of the service handed on to the application, by name.
    """

    url: str
    sign_in: str = ""
    headers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The fields, JSON-ready.
        """
        return {"url": self.url, "sign_in": self.sign_in, "headers": list(self.headers)}

    @property
    def host_and_port(self) -> tuple[str, int | None]:
        """
        Returns:
            The host the service is asked at, and its port when the address names one.
        """
        rest = self.url.split("://", 1)[1].split("/", 1)[0]
        host, _, port = rest.rpartition(":") if ":" in rest else (rest, "", "")
        return host, int(port) if port.isdigit() else None

    @property
    def header_pairs(self) -> list[dict[str, str]]:
        """
        Returns:
            For each header handed on, its name and the variable that carries it: nginx reads a
            response header of the service as ``$upstream_http_<name, lower case, dashes as underscores>``.
        """
        return [
            {
                "header": name,
                "source": "upstream_http_" + name.lower().replace("-", "_"),
                "variable": f"noust_fa_{index + 1}",
            }
            for index, name in enumerate(self.headers)
        ]


def forward_auth_from(value: Any) -> ForwardAuth | None:
    """
    Check the forward-auth settings and build them.

    Args:
        value: A mapping with ``url``, ``sign_in`` and ``headers``; None or an empty address
            for no forward-auth.

    Returns:
        The settings, or None.

    Raises:
        ValidationError: A field is unknown or holds something the web server's file could not
            safely carry.
    """
    if value is None:
        return None
    if isinstance(value, ForwardAuth):
        return value
    if not isinstance(value, Mapping):
        raise ValidationError("forward_auth is an object with an address", field="forward_auth")
    unknown = sorted(set(value) - _FIELDS)
    if unknown:
        raise ValidationError(
            f"Unknown forward_auth field {unknown[0]!r}",
            details="The fields are: " + ", ".join(sorted(_FIELDS)) + ".",
            field="forward_auth",
        )
    url = value.get("url") or ""
    if url == "":
        return None
    try:
        checked = redirect_target(url, absolute_only=True)
    except ValidationError as exc:
        raise ValidationError(
            f"{url!r} cannot be the address of an authentication service",
            details="Use an address such as http://127.0.0.1:4180/oauth2/auth.",
            field="forward_auth",
        ) from exc
    if "?" in checked:
        raise ValidationError(
            "The address of the authentication service cannot carry a query",
            field="forward_auth",
        )
    sign_in = value.get("sign_in") or ""
    if sign_in:
        try:
            sign_in = redirect_target(sign_in)
        except ValidationError as exc:
            raise ValidationError(
                f"{sign_in!r} cannot be the sign-in address",
                details="Use an address or a path on this site, such as /oauth2/start?rd= ; the "
                "page the visitor asked for is added to it.",
                field="forward_auth",
            ) from exc
    headers = value.get("headers") or []
    if not isinstance(headers, (list, tuple)) or len(headers) > MAX_COPIED:
        raise ValidationError(
            f"headers is a list of at most {MAX_COPIED} header names", field="forward_auth"
        )
    clean: list[str] = []
    for name in headers:
        if not isinstance(name, str) or not _HEADER.fullmatch(name):
            raise ValidationError(f"{name!r} is not a header name", field="forward_auth")
        if name.lower() in _FORBIDDEN:
            raise ValidationError(
                f"{name} belongs to the connection and cannot be handed on", field="forward_auth"
            )
        if name not in clean:
            clean.append(name)
    return ForwardAuth(url=checked, sign_in=sign_in, headers=tuple(clean))


@dataclass(frozen=True)
class ServiceCheck:
    """
    Whether the authentication service answers.

    Attributes:
        reachable: It answered with an HTTP status (any: 401 and 403 are what it says to a
            visitor who is not signed in, and prove it is there).
        status: The HTTP status, when it answered.
        detail: What happened, in a sentence, when it did not answer well.
    """

    reachable: bool
    status: int | None = None
    detail: str = ""


#: What curl's exit code says about a request that did not get an answer.
_CURL_REASONS = {
    6: "its host name does not resolve",
    7: "nothing accepts connections there",
    28: "it did not answer in time",
    35: "the TLS handshake failed",
    51: "its certificate does not match the name",
    60: "its certificate is not trusted",
}


def check_service(auth: ForwardAuth, runner: CommandRunner) -> ServiceCheck:
    """
    Ask the authentication service once, as nginx would for a visitor with no session.

    The request goes through the command runner like every process, with the address after
    ``--`` so it can never be read as an option.

    Args:
        auth: The settings.
        runner: Runs curl.

    Returns:
        Whether it answered, and with what.
    """
    result = runner.run(
        [
            "curl",
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "--max-time",
            "6",
            "--connect-timeout",
            "3",
            "--",
            auth.url,
        ],
        timeout=15,
    )
    code = result.stdout.strip()
    if result.success and code.isdigit() and int(code) > 0:
        status = int(code)
        if status >= 500:
            return ServiceCheck(
                reachable=False,
                status=status,
                detail=f"it answered with an error ({status}), which nginx treats as a failure",
            )
        return ServiceCheck(reachable=True, status=status)
    reason = _CURL_REASONS.get(result.exit_code, (result.stderr or "no answer").strip())
    return ServiceCheck(reachable=False, detail=reason)
