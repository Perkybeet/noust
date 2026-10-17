# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's site does beyond serving it: who may reach it, how, and how fast.

One policy per application, read by the web server manager every time it writes
the site (:meth:`~noust.managers.webserver.WebServerManager.build_context`), so a
redeploy, a new alias or a certificate order renders the same policy again and
the deployers know nothing of it (rule 3). It holds:

- **Basic authentication** with accounts Noust manages. Only the salted hash of
  a password is kept (``openssl passwd``, the password on its standard input,
  never in an argument); the file the web server reads is derived from those
  rows whenever the site is written, readable by the web server's group and no
  one else.
- **IP allow and deny lists**, alone or together with the password
  (``access: any`` lets either through). The address of the operator who saves
  the policy is checked against it first: a list that would lock them out is
  refused unless they say they know.
- **The real client address** behind a CDN: Cloudflare's published ranges
  (:mod:`noust.managers.trusted_proxies`), or ranges the operator names. The
  header is believed only from those networks.
- **Rate and connection limits**, per site (nginx only; Apache has no
  equivalent in its core).
- **Response headers**: the security defaults, an operator's overrides, HSTS and
  ``X-Robots-Tag``.
- **Which spelling is canonical**, ``www.example.com`` or ``example.com``: the
  other one redirects.

Everything that lands in a directive is checked to be exactly what that
directive takes, here, before it is stored: the values are written into a file
nginx loads as root.
"""

from __future__ import annotations

import grp
import ipaddress
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from noust.core.exceptions import NoustError, ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.core.site_policy_store import AuthUser, SitePolicyStore
from noust.core.store import NoustStore
from noust.core.utils import domain_to_app_name, nginx_zone_name
from noust.managers.site_forward_auth import ForwardAuth, forward_auth_from
from noust.managers.site_headers import DEFAULT_HEADERS, check_headers
from noust.managers.site_routes import SiteRoute, needs_password, regex_of, routes_from
from noust.managers.site_waf import checked_exclusions, checked_mode
from noust.managers.siteconf import ParseError, parse
from noust.managers.siteconf.tree import Directive
from noust.managers.trusted_proxies import current_ranges

_log = logging.getLogger(__name__)

#: Directory, next to the web server's configuration, holding the account files.
AUTH_DIR_NAME = "noust-auth"

#: Suffix of an account file. Not ``.htpasswd``: the editor refuses to let a site
#: name a file that looks like a password file, and this one is Noust's own.
AUTH_SUFFIX = ".users"

#: Realm shown by the browser's sign-in prompt.
DEFAULT_REALM = "Restricted"

#: Header that carries the address behind Cloudflare.
CLOUDFLARE_HEADER = "CF-Connecting-IP"

#: Headers a CDN or proxy may carry a client's address in.
REAL_IP_HEADERS = ("CF-Connecting-IP", "True-Client-IP", "X-Real-IP", "X-Forwarded-For")

#: Where a client's address is believed from: nowhere, Cloudflare, or ranges named.
REAL_IP_MODES = ("off", "cloudflare", "custom")

#: Which of the two checks decides: both must pass, or either.
ACCESS_MODES = ("all", "any")

#: Which spelling is canonical: as the primary is, ``www.`` or the bare name.
CANONICAL_CHOICES = ("", "www", "apex")

#: The most entries an address list may hold.
MAX_ADDRESSES = 200

#: The most accounts one application's basic authentication may have.
MAX_USERS = 100

#: Shortest password an account may have.
MIN_PASSWORD = 8

#: Longest password an account may have.
MAX_PASSWORD = 256

_USERNAME = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
_REALM = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
_RATE = re.compile(r"^([1-9][0-9]{0,5})r/([sm])$")

NOINDEX_VALUE = "noindex, nofollow"


@dataclass(frozen=True)
class SitePolicy:
    """
    The policy of one application's site. Immutable; changes make a new one.

    Attributes:
        basic_auth: Ask for a name and password.
        realm: The sign-in prompt's title.
        access: ``all``: the password and the address list must both pass;
            ``any``: either lets the visitor in.
        allow_ips: Addresses and networks allowed; everyone else is refused when there are any.
        deny_ips: Addresses and networks refused, checked before the allow list.
        real_ip: ``off``, ``cloudflare`` or ``custom``.
        real_ip_header: The header carrying the client's address (``custom``;
            Cloudflare's is fixed).
        trusted_proxies: The networks the header is believed from (``custom``).
        rate_limit: Requests per client address, ``10r/s`` or ``300r/m``; empty for none.
        rate_burst: How many requests above the rate are let through before refusing.
        connection_limit: Concurrent connections per client address; 0 for none.
        headers: Response headers to set, overriding the defaults; a value of
            None removes a default.
        hsts: Send ``Strict-Transport-Security``: True, False, or None for the
            server's own setting (``ssl.hsts``).
        noindex: Tell search engines not to index the site.
        canonical: ``www`` or ``apex`` to make that spelling the one served,
            redirecting the other; empty to serve both as they are.
        forward_auth: An authentication service every request is asked about before it reaches
            the application (:mod:`noust.managers.site_forward_auth`); None for none.
        http3: Serve the site over HTTP/3 (QUIC) as well, on UDP 443 (nginx 1.25 or later
            built with the HTTP/3 module; :mod:`noust.managers.http3`).
        client_certs: Answer only a visitor that presents a certificate this site's own
            authority signed (:mod:`noust.managers.client_certs`); the check is the TLS
            handshake, before any HTTP. nginx and a site served over TLS.
        waf: The web application firewall (:mod:`noust.managers.waf`): ``detect`` writes down what
            the OWASP rules see, ``block`` also refuses the requests that score too high; empty
            for none (nginx with the ModSecurity module).
        waf_exclusions: Numbers of rules the WAF leaves out for this site, for the false
            positives its application causes.
        routes: What happens at prefixes of the site (:mod:`noust.managers.site_routes`):
            a password asked or lifted there, response headers, another service
            behind the prefix, a redirect. Longest path first.
    """

    basic_auth: bool = False
    realm: str = DEFAULT_REALM
    access: str = "all"
    allow_ips: tuple[str, ...] = ()
    deny_ips: tuple[str, ...] = ()
    real_ip: str = "off"
    real_ip_header: str = ""
    trusted_proxies: tuple[str, ...] = ()
    rate_limit: str = ""
    rate_burst: int = 20
    connection_limit: int = 0
    headers: Mapping[str, str | None] = field(default_factory=dict)
    hsts: bool | None = None
    noindex: bool = False
    canonical: str = ""
    forward_auth: ForwardAuth | None = None
    http3: bool = False
    client_certs: bool = False
    waf: str = ""
    waf_exclusions: tuple[int, ...] = ()
    routes: tuple[SiteRoute, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the policy for the API, the CLI and the stored document.

        Returns:
            Every field, JSON-ready.
        """
        return {
            "basic_auth": self.basic_auth,
            "realm": self.realm,
            "access": self.access,
            "allow_ips": list(self.allow_ips),
            "deny_ips": list(self.deny_ips),
            "real_ip": self.real_ip,
            "real_ip_header": self.real_ip_header,
            "trusted_proxies": list(self.trusted_proxies),
            "rate_limit": self.rate_limit,
            "rate_burst": self.rate_burst,
            "connection_limit": self.connection_limit,
            "headers": dict(self.headers),
            "hsts": self.hsts,
            "noindex": self.noindex,
            "canonical": self.canonical,
            "forward_auth": self.forward_auth.to_dict() if self.forward_auth else None,
            "http3": self.http3,
            "client_certs": self.client_certs,
            "waf": self.waf,
            "waf_exclusions": list(self.waf_exclusions),
            "routes": [route.to_dict() for route in self.routes],
        }

    @property
    def is_default(self) -> bool:
        """Whether nothing differs from what a site does without a policy."""
        return self == SitePolicy()

    @property
    def uses_accounts(self) -> bool:
        """Whether the site, or any prefix of it, asks for the password."""
        return self.basic_auth or needs_password(self.routes)

    @property
    def uses_ip_rules(self) -> bool:
        """Whether an address list applies."""
        return bool(self.allow_ips or self.deny_ips)

    @classmethod
    def from_document(cls, text: str | None) -> SitePolicy:
        """
        Read a stored document.

        Args:
            text: The JSON, or None for the defaults.

        Returns:
            The policy. A document that no longer validates (the code's rules
            tightened) falls back to the defaults and is logged, rather than
            making the site impossible to render.
        """
        if not text:
            return cls()
        try:
            data = json.loads(text)
            return validated(cls(), data) if isinstance(data, dict) else cls()
        except (ValueError, ValidationError) as exc:
            _log.warning("A stored site policy is not valid any more and is ignored: %s", exc)
            return cls()


def _normalised_address(value: Any, field_name: str) -> str:
    """
    Check one address or network.

    Args:
        value: The text.
        field_name: The field, to name in an error.

    Returns:
        The canonical text; a single host without its prefix length.

    Raises:
        ValidationError: It is not an address or a network.
    """
    try:
        network = ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError:
        raise ValidationError(
            f"{value!r} is not an address or a network",
            details="Use an address such as 203.0.113.9 or a network such as 10.0.0.0/8.",
            field=field_name,
        ) from None
    if network.prefixlen == network.max_prefixlen:
        return str(network.network_address)
    return str(network)


def _addresses(value: Any, field_name: str) -> tuple[str, ...]:
    """
    Check a list of addresses and networks.

    Args:
        value: The list.
        field_name: The field, to name in an error.

    Returns:
        The canonical entries, in order, without repeats.

    Raises:
        ValidationError: It is not a list of addresses, or holds too many.
    """
    if not isinstance(value, (list, tuple)):
        raise ValidationError(f"{field_name} is a list of addresses", field=field_name)
    if len(value) > MAX_ADDRESSES:
        raise ValidationError(
            f"{field_name} holds {len(value)} entries; the most is {MAX_ADDRESSES}",
            field=field_name,
        )
    seen: dict[str, None] = {}
    for item in value:
        seen[_normalised_address(item, field_name)] = None
    return tuple(seen)


def _flag(value: Any, field_name: str) -> bool:
    """
    Args:
        value: What was given.
        field_name: The field.

    Returns:
        The boolean.

    Raises:
        ValidationError: It is not a boolean.
    """
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} is true or false", field=field_name)
    return value


def _count(value: Any, field_name: str, upper: int) -> int:
    """
    Args:
        value: What was given.
        field_name: The field.
        upper: The most it may be.

    Returns:
        The whole number.

    Raises:
        ValidationError: It is not a whole number from 0 to ``upper``.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= upper:
        raise ValidationError(f"{field_name} is a whole number from 0 to {upper}", field=field_name)
    return int(value)


def validated(base: SitePolicy, changes: Mapping[str, Any]) -> SitePolicy:
    """
    Apply a set of changes to a policy, checking every value.

    Args:
        base: The policy before.
        changes: Field to new value; a field not named keeps its value.

    Returns:
        The new policy.

    Raises:
        ValidationError: A field is unknown or a value is not acceptable; the
            error names the field.
    """
    known = set(SitePolicy.__dataclass_fields__)
    unknown = sorted(set(changes) - known)
    if unknown:
        raise ValidationError(
            f"Unknown policy field {unknown[0]!r}",
            details="The fields are: " + ", ".join(sorted(known)) + ".",
            field=unknown[0],
        )
    values: dict[str, Any] = {}
    for key, value in changes.items():
        if key in ("basic_auth", "noindex", "http3", "client_certs"):
            values[key] = _flag(value, key)
        elif key == "hsts":
            values[key] = None if value is None else _flag(value, key)
        elif key == "realm":
            if not isinstance(value, str) or not _REALM.fullmatch(value):
                raise ValidationError(
                    "The realm is 1 to 64 letters, digits, spaces, dots, dashes or underscores",
                    field=key,
                )
            values[key] = value
        elif key == "access":
            if value not in ACCESS_MODES:
                raise ValidationError(f"access is one of {', '.join(ACCESS_MODES)}", field=key)
            values[key] = value
        elif key in ("allow_ips", "deny_ips", "trusted_proxies"):
            values[key] = _addresses(value, key)
        elif key == "real_ip":
            if value not in REAL_IP_MODES:
                raise ValidationError(f"real_ip is one of {', '.join(REAL_IP_MODES)}", field=key)
            values[key] = value
        elif key == "real_ip_header":
            if value not in ("", *REAL_IP_HEADERS):
                raise ValidationError(
                    f"real_ip_header is one of {', '.join(REAL_IP_HEADERS)}", field=key
                )
            values[key] = value
        elif key == "rate_limit":
            if value not in ("", None) and not _RATE.fullmatch(str(value)):
                raise ValidationError(
                    "rate_limit is a number of requests per second or minute, such as 10r/s or 300r/m",
                    field=key,
                )
            values[key] = str(value or "")
        elif key == "rate_burst":
            values[key] = _count(value, key, 100000)
        elif key == "connection_limit":
            values[key] = _count(value, key, 100000)
        elif key == "headers":
            values[key] = check_headers(value)
        elif key == "routes":
            values[key] = routes_from(value)
        elif key == "forward_auth":
            values[key] = forward_auth_from(value)
        elif key == "waf":
            values[key] = checked_mode(value)
        elif key == "waf_exclusions":
            values[key] = checked_exclusions(value)
        elif key == "canonical":
            if value not in CANONICAL_CHOICES:
                raise ValidationError(
                    "canonical is www, apex or empty (serve both as they are)", field=key
                )
            values[key] = value
    result = replace(base, **values)
    _check_consistency(result)
    return result


def _check_consistency(policy: SitePolicy) -> None:
    """
    Refuse a combination that cannot work.

    Args:
        policy: The policy about to be kept.

    Raises:
        ValidationError: The combination makes no sense.
    """
    if policy.real_ip == "custom" and not policy.trusted_proxies:
        raise ValidationError(
            "Naming the networks the client address is believed from is required",
            details="With real_ip custom, trusted_proxies lists the proxies in front of the site. "
            "Believing the header from anyone lets any client choose its own address.",
            field="trusted_proxies",
        )
    if policy.real_ip == "custom" and not policy.real_ip_header:
        raise ValidationError(
            "Naming the header that carries the client address is required",
            details=f"With real_ip custom, real_ip_header is one of {', '.join(REAL_IP_HEADERS)}.",
            field="real_ip_header",
        )
    ways = sum((policy.basic_auth, bool(policy.allow_ips), policy.forward_auth is not None))
    if policy.access == "any" and ways < 2:
        raise ValidationError(
            "access any needs two ways in: a password, an address list or an authentication service",
            details="It lets a visitor in by either one; with only one of them there is nothing "
            "to choose between.",
            field="access",
        )
    for network in policy.trusted_proxies:
        parsed = ipaddress.ip_network(network, strict=False)
        floor = 8 if parsed.version == 4 else 16
        if parsed.prefixlen < floor:
            raise ValidationError(
                f"{network} is too wide to trust",
                details="Trusting it would let a large part of the internet name its own address.",
                field="trusted_proxies",
            )


def permits(policy: SitePolicy, address: str) -> bool:
    """
    Tell whether the address lists let a client through.

    Evaluated as the web server does: the deny entries first, then the allow
    entries, and when there are allow entries everyone else is refused.

    Args:
        policy: The policy.
        address: The client's address.

    Returns:
        True when the lists let it through. The password is not considered.
    """
    try:
        client = ipaddress.ip_address(address.strip())
    except ValueError:
        return True
    for entry in policy.deny_ips:
        if client in ipaddress.ip_network(entry, strict=False):
            return False
    if not policy.allow_ips:
        return True
    return any(client in ipaddress.ip_network(entry, strict=False) for entry in policy.allow_ips)


def locks_out(policy: SitePolicy, addresses: Iterable[str]) -> list[str]:
    """
    Name the addresses the policy would refuse that the operator is using.

    A password the operator holds is a way in, so with ``access: any`` or no
    address list there is nothing to lock them out of.

    Args:
        policy: The policy about to be kept.
        addresses: The operator's addresses (their request, their SSH session).

    Returns:
        The addresses that would be refused; empty when none or when unknown.
    """
    if not policy.uses_ip_rules or (
        policy.access == "any" and (policy.basic_auth or policy.forward_auth is not None)
    ):
        return []
    return [address for address in addresses if address and not permits(policy, address)]


def canonical_host(primary: str, canonical: str) -> str:
    """
    Name the spelling of a domain that is the one served.

    Args:
        primary: The application's primary domain.
        canonical: ``www``, ``apex`` or empty.

    Returns:
        The primary itself when nothing is asked, else it with ``www.`` put on or taken off.
    """
    if canonical == "www":
        return primary if primary.startswith("www.") else f"www.{primary}"
    if canonical == "apex":
        return primary[4:] if primary.startswith("www.") else primary
    return primary


def counterpart(host: str) -> str:
    """
    Args:
        host: A host name.

    Returns:
        The other spelling: with ``www.`` taken off, or put on.
    """
    return host[4:] if host.startswith("www.") else f"www.{host}"


def check_canonical_possible(primary: str) -> None:
    """
    Refuse a primary a www pair makes no sense for.

    Args:
        primary: The application's primary domain.

    Raises:
        ValidationError: The name has a single label (``localhost``), which has no www spelling.
    """
    bare = primary[4:] if primary.startswith("www.") else primary
    if "." not in bare:
        raise ValidationError(
            f"{primary} has no www spelling",
            details="Only a name with a domain, such as example.com, has a www pair.",
            field="canonical",
        )


@dataclass(frozen=True)
class SiteNames:
    """
    What a site answers on and redirects, worked out from the domain rows and the policy.

    Attributes:
        served: The names the application is served on, the canonical one first.
        redirects: The names redirected permanently to ``target``.
        target: Where the redirects go: the canonical host.
        ports: The name to port overrides of served names (a listener of its own).
    """

    served: tuple[str, ...]
    redirects: tuple[str, ...]
    target: str
    ports: Mapping[str, int] = field(default_factory=dict)


def site_names(primary: str, records: Sequence[Any], canonical: str = "") -> SiteNames:
    """
    Work out the names of a site: the one place that decides them.

    Args:
        primary: The application's primary domain.
        records: Its domain rows (``domain``, ``kind`` and ``port``), primary first.
        canonical: The policy's choice: ``www``, ``apex`` or empty.

    Returns:
        The names, with the canonical host first and its counterpart redirected
        when a spelling is canonical.
    """
    host = canonical_host(primary, canonical)
    served = [r.domain for r in records if r.kind != "redirect"]
    redirects = [r.domain for r in records if r.kind == "redirect"]
    if canonical:
        other = counterpart(host)
        served = [name for name in served if name != other]
        redirects = [name for name in redirects if name != host]
        if host not in served:
            served.append(host)
        if other not in redirects:
            redirects.append(other)
        served.sort(key=lambda name: name != host)
    ports = {
        r.domain: int(r.port)
        for r in records
        if getattr(r, "port", None) and r.domain in served and r.domain != host
    }
    return SiteNames(tuple(served), tuple(redirects), host, ports)


def response_headers(
    policy: SitePolicy,
    *,
    base: Mapping[str, str] | None = None,
    hsts: str = "",
    ssl: bool = False,
) -> list[tuple[str, str]]:
    """
    Work out the response headers a site sends.

    Args:
        policy: The site's policy.
        base: Headers a ``noust.nginx.yaml`` set, over the defaults.
        hsts: The ``Strict-Transport-Security`` value the server's own setting asks for.
        ssl: Whether the site serves TLS; HSTS is only ever sent over it.

    Returns:
        ``(name, value)`` in the order they are written.
    """
    merged: dict[str, str | None] = dict(DEFAULT_HEADERS)
    merged.update(base or {})
    merged.update(policy.headers)
    if policy.hsts is True:
        hsts = hsts or "max-age=31536000"
    elif policy.hsts is False:
        hsts = ""
    if ssl and hsts and "Strict-Transport-Security" not in policy.headers:
        merged["Strict-Transport-Security"] = hsts
    if policy.noindex:
        merged["X-Robots-Tag"] = NOINDEX_VALUE
    return [(name, value) for name, value in merged.items() if value is not None]


def auth_directory(conf_root: Path) -> Path:
    """
    Args:
        conf_root: The web server's configuration directory.

    Returns:
        The directory Noust keeps the account files in.
    """
    return conf_root / AUTH_DIR_NAME


def auth_file(conf_root: Path, domain: str) -> Path:
    """
    Args:
        conf_root: The web server's configuration directory.
        domain: The application's primary domain.

    Returns:
        The account file of the application.
    """
    return auth_directory(conf_root) / f"{domain_to_app_name(domain)}{AUTH_SUFFIX}"


def policy_context(
    policy: SitePolicy,
    *,
    domain: str,
    conf_root: Path,
    kind: str,
    site_headers: Mapping[str, str] | None = None,
    client_certs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Turn a policy into the variables the site templates read.

    Args:
        policy: The site's policy.
        domain: The application's primary domain.
        conf_root: The web server's configuration directory.
        kind: ``nginx`` or ``apache``.
        site_headers: The response headers the whole site sends (as
            :func:`response_headers` works them out), which a route's header of the
            same name replaces under its prefix. The policy's own by default.
        client_certs: When the policy asks for client certificates: the authority's file
            (``ca``) and the serials the site refuses (``revoked``).

    Returns:
        ``policy``: the blocks to render (empty when the policy has nothing for
        the template to say), ``policy_zones``: the nginx zones to declare
        before the server block, and ``policy_maps``: the nginx ``map`` blocks
        that make the password and the headers depend on the path.
    """
    block: dict[str, Any] = {}
    zones: list[dict[str, str]] = []
    maps: list[dict[str, Any]] = []
    prefix = nginx_zone_name(domain)
    password_routes = needs_password(policy.routes)

    # nginx asks for the password at the server and a map of the path decides
    # whether to (auth_basic takes a variable, and the value "off" lifts it), which
    # works the same in every template whatever its locations look like. Apache has
    # <Location> sections for the same job, and the site-wide one only when the site
    # as a whole asks (``site``).
    if policy.basic_auth or password_routes:
        block["auth"] = {
            "realm": policy.realm,
            "file": str(auth_file(conf_root, domain)),
            "satisfy": policy.access if policy.basic_auth else "all",
            "site": policy.basic_auth,
        }
    if policy.uses_ip_rules:
        block["allow"] = list(policy.allow_ips)
        block["deny"] = list(policy.deny_ips)
        block["ip_rules"] = [
            *(f"deny {entry}" for entry in policy.deny_ips),
            *(f"allow {entry}" for entry in policy.allow_ips),
            *(["deny all"] if policy.allow_ips else []),
        ]
        block["require"] = _apache_requires(policy)
    if policy.real_ip == "cloudflare":
        block["real_ip"] = {
            "header": CLOUDFLARE_HEADER,
            "proxies": list(current_ranges().networks),
            "recursive": False,
        }
    elif policy.real_ip == "custom":
        block["real_ip"] = {
            "header": policy.real_ip_header,
            "proxies": list(policy.trusted_proxies),
            "recursive": policy.real_ip_header == "X-Forwarded-For",
        }
    if kind == "nginx" and policy.rate_limit:
        name = f"{prefix}_site"
        block["rate"] = {"zone": name, "burst": policy.rate_burst}
        zones.append({"directive": "limit_req_zone", "name": name, "arg": policy.rate_limit})
    if kind == "nginx" and policy.connection_limit:
        name = f"{prefix}_conn"
        block["conn"] = {"zone": name, "limit": policy.connection_limit}
        zones.append({"directive": "limit_conn_zone", "name": name, "arg": ""})
    if kind == "nginx" and policy.forward_auth is not None:
        auth = policy.forward_auth
        block["forward_auth"] = {
            "url": auth.url,
            "sign_in": auth.sign_in,
            "https": auth.url.startswith("https://"),
            "headers": auth.header_pairs,
        }
    # With more than one way in, nginx has to be told whether all of them or any is wanted.
    block["ways_in"] = sum(
        (
            policy.uses_accounts,
            policy.uses_ip_rules,
            kind == "nginx" and policy.forward_auth is not None,
        )
    )
    block["satisfy"] = (
        policy.access if (policy.basic_auth or policy.forward_auth is not None) else "all"
    )
    if kind == "nginx" and policy.http3:
        block["http3"] = True
    if kind == "nginx" and policy.waf:
        block["waf"] = {"mode": policy.waf, "exclusions": list(policy.waf_exclusions)}
    if kind == "nginx" and policy.client_certs and client_certs is not None:
        variable = f"noust_cc_revoked_{prefix}"
        block["client_certs"] = {
            "ca": str(client_certs["ca"]),
            "variable": variable,
            "revoked": list(client_certs.get("revoked") or []),
        }
        if block["client_certs"]["revoked"]:
            maps.append(
                {
                    "source": "$ssl_client_serial",
                    "variable": variable,
                    "default": "0",
                    "entries": [
                        {"key": serial, "value": "1"} for serial in client_certs["revoked"]
                    ],
                }
            )
    if policy.routes:
        if site_headers is None:
            site_headers = dict(response_headers(policy))
        _route_context(
            policy, prefix=prefix, kind=kind, block=block, maps=maps, site_headers=site_headers
        )
    return {"policy": block, "policy_zones": zones, "policy_maps": maps}


def _map_key(path: str) -> str:
    """
    Args:
        path: A checked route path.

    Returns:
        The regular expression an nginx ``map`` entry matches the normalised URI with.
    """
    return "~" + regex_of(path)


def _route_context(
    policy: SitePolicy,
    *,
    prefix: str,
    kind: str,
    block: dict[str, Any],
    maps: list[dict[str, Any]],
    site_headers: Mapping[str, str],
) -> None:
    """
    Add what a policy's routes say to the blocks the templates read.

    Args:
        policy: The site's policy, with routes.
        prefix: The site's name as nginx variables and zones spell it.
        kind: ``nginx`` or ``apache``.
        block: The policy block being built.
        maps: The nginx maps being built.
        site_headers: The headers the site sends, by name.
    """
    routes = policy.routes  # longest path first: the first entry that matches is the most specific
    proxies = [route for route in routes if route.kind == "proxy"]
    redirects = [route for route in routes if route.kind == "redirect"]
    block["proxies"] = [
        {
            "path": route.path,
            "pattern": route.path.replace(".", "[.]"),
            "port": route.port,
            "strip": route.strip,
            # Lifts the authentication service's check there too: the sign-in it redirects
            # to is behind it otherwise.
            "open": route.auth == "open",
        }
        for route in proxies
    ]
    block["redirects"] = [
        {
            "path": route.path,
            "regex": "^" + route.path.replace(".", "[.]") + "(/.*)?$",
            "code": route.code,
            "target": _redirect_target(route),
            "keep_path": route.keep_path,
        }
        for route in redirects
    ]
    if kind == "apache":
        block["locations"] = _apache_locations(policy)
        return

    auth_routes = [route for route in routes if route.auth != "inherit"]
    # An open prefix of a site that asks for no password has nothing to lift: no decision to make.
    if auth_routes and "auth" in block:
        variable = f"noust_auth_{prefix}"
        block["auth"]["variable"] = variable
        # A map is evaluated once per request: after try_files sent the request to a fallback page
        # (a single-page site) it still says what it said for the path asked for. So the password
        # is asked when that decision asks for it, or when the path being served would: set and
        # if run again at every internal redirect. Shortest prefix first, so a longer one
        # overrides it, the opposite of the map's first-match order.
        default = f'"{policy.realm}"' if policy.basic_auth else "off"
        block["auth"]["judge"] = {
            "now": f"noust_now_{prefix}",
            "realm": f"noust_realm_{prefix}",
            "default": default,
            "entries": [
                {
                    "regex": regex_of(route.path),
                    "value": "off" if route.auth == "open" else f'"{policy.realm}"',
                }
                for route in reversed(auth_routes)
            ],
        }
        maps.append(
            {
                "variable": variable,
                "default": f'"{policy.realm}"' if policy.basic_auth else "off",
                "entries": [
                    {
                        "key": _map_key(route.path),
                        "value": "off" if route.auth == "open" else f'"{policy.realm}"',
                    }
                    for route in auth_routes
                ],
            }
        )
    names: dict[str, str] = {}
    for route in routes:
        for name in route.headers:
            names.setdefault(name, f"noust_hdr_{prefix}_{len(names) + 1}")
    for name, variable in names.items():
        maps.append(
            {
                "variable": variable,
                "default": f'"{site_headers[name]}"' if name in site_headers else '""',
                "entries": [
                    {"key": _map_key(route.path), "value": f'"{route.headers[name]}"'}
                    for route in routes
                    if name in route.headers
                ],
            }
        )
    if names:
        block["header_vars"] = names


def _redirect_target(route: SiteRoute) -> str:
    """
    Args:
        route: A redirect route.

    Returns:
        What the web server answers with: the target, with the rest of the path
        (the regular expression's first group) after it when the route keeps it.
    """
    if not route.keep_path:
        return route.to
    base = route.to.rstrip("/") if route.to != "/" else ""
    return f"{base}$1"


def _apache_locations(policy: SitePolicy) -> list[dict[str, Any]]:
    """
    Write the ``<Location>`` sections of the routes that change the password or the headers.

    Args:
        policy: The site's policy, with routes.

    Returns:
        One entry per route: ``path``, ``auth`` (whether it needs the account file),
        ``require`` lines and ``headers``. Longest path first, which Apache merges
        after the site's own ``<Location />``.
    """
    locations: list[dict[str, Any]] = []
    for route in policy.routes:
        if route.kind == "redirect" or (route.auth == "inherit" and not route.headers):
            continue
        require: list[str] = []
        if route.auth == "password":
            require = _apache_requires(replace(policy, basic_auth=True, access="all"))
        elif route.auth == "open":
            require = _apache_requires(replace(policy, basic_auth=False, access="all"))
        locations.append(
            {
                "path": route.path,
                "auth": route.auth == "password",
                "require": require,
                "headers": list(route.headers.items()),
            }
        )
    return locations


def _apache_requires(policy: SitePolicy) -> list[str]:
    """
    Write the ``Require`` lines of an Apache ``<Location>``.

    Args:
        policy: A policy with address rules.

    Returns:
        Lines, relative indentation included.
    """
    inner: list[str] = []
    if policy.allow_ips:
        inner = [f"Require ip {entry}" for entry in policy.allow_ips]
    denies = [f"Require not ip {entry}" for entry in policy.deny_ips]
    if policy.basic_auth and policy.access == "all":
        lines = ["<RequireAll>", "    Require valid-user"]
        if inner:
            lines += [
                "    <RequireAny>",
                *(f"        {line}" for line in inner),
                "    </RequireAny>",
            ]
        lines += [f"    {line}" for line in denies]
        return [*lines, "</RequireAll>"]
    if policy.basic_auth:  # access any: the password or an address
        lines = ["<RequireAny>", "    Require valid-user"]
        lines += [f"    {line}" for line in inner]
        return [*lines, "</RequireAny>"]
    lines = ["<RequireAll>"]
    if inner:
        lines += ["    <RequireAny>", *(f"        {line}" for line in inner), "    </RequireAny>"]
    else:
        lines += ["    Require all granted"]
    lines += [f"    {line}" for line in denies]
    return [*lines, "</RequireAll>"]


def worker_group(conf_root: Path, kind: str) -> str:
    """
    Name the group the web server's workers run as, which must read the account file.

    Args:
        conf_root: The web server's configuration directory.
        kind: ``nginx`` or ``apache``.

    Returns:
        The group of nginx's ``user`` directive when the main configuration
        names one, else the usual one of the distribution that exists here.
    """
    if kind == "nginx":
        try:
            tree = parse((conf_root / "nginx.conf").read_text(encoding="utf-8"), "nginx")
        except (OSError, UnicodeDecodeError, ParseError):
            tree = None
        if tree is not None:
            for node in tree.children:
                if not isinstance(node, Directive) or node.name.value != "user":
                    continue
                args = [str(arg.word.value) for arg in node.args]
                if args:
                    return args[1] if len(args) > 1 else args[0]
    for candidate in ("www-data", "nginx", "apache", "http", "wwwrun"):
        try:
            grp.getgrnam(candidate)
        except KeyError:
            continue
        return candidate
    return "root"


def hash_password(password: str, runner: CommandRunner | None = None) -> str:
    """
    Hash a password the way the web server reads it, without it reaching a command line.

    Args:
        password: The password.
        runner: Runs openssl; the process-wide runner by default.

    Returns:
        A salted ``$6$`` crypt hash, readable by nginx and Apache.

    Raises:
        ValidationError: The password is not acceptable.
        NoustError: openssl could not hash it.
    """
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD or any(
        ord(char) < 32 or ord(char) == 127 for char in password
    ):
        raise ValidationError(
            f"A password is {MIN_PASSWORD} to {MAX_PASSWORD} characters, on one line",
            field="password",
        )
    result = (runner or get_runner()).run(
        ["openssl", "passwd", "-6", "-stdin"],
        input=password + "\n",
        secrets=[password],
        timeout=20,
    )
    hashed = result.stdout.strip()
    if not result.success or not hashed.startswith("$6$"):
        raise NoustError(
            "Could not hash the password",
            details="openssl passwd -6 failed; check that openssl is installed.",
            output=(result.stderr or result.stdout).strip() or None,
        )
    return hashed


def account_file_text(users: Sequence[AuthUser]) -> str:
    """
    Args:
        users: The accounts.

    Returns:
        The file the web server reads: ``name:hash`` per line.
    """
    return "".join(f"{user.username}:{user.password_hash}\n" for user in users)


def check_username(username: str) -> str:
    """
    Args:
        username: The login name.

    Returns:
        The name.

    Raises:
        ValidationError: It cannot be a name in the account file (a colon, a space, a control character).
    """
    if not _USERNAME.fullmatch(username):
        raise ValidationError(
            f"{username!r} cannot be a user name",
            details="Use 1 to 64 letters, digits, dots, dashes, underscores or @.",
            field="username",
        )
    return username


class SitePolicyManager:
    """
    Read and change the policy of applications' sites.

    Args:
        store: The store; the process-wide one by default.
        runner: Hashes passwords; the process-wide runner by default.
        fs: Writes the account files; the process-wide filesystem by default.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        self._records = SitePolicyStore(store)
        self._runner = runner
        self._fs = fs

    @property
    def records(self) -> SitePolicyStore:
        """The stored documents and accounts."""
        return self._records

    def get(self, domain: str) -> SitePolicy:
        """
        Read an application's policy.

        Args:
            domain: Its primary domain.

        Returns:
            The policy; the defaults when it has none.
        """
        return SitePolicy.from_document(self._records.get_document(domain))

    def users(self, domain: str) -> list[AuthUser]:
        """
        Args:
            domain: The application's primary domain.

        Returns:
            Its basic-authentication accounts.
        """
        return self._records.list_users(domain)

    def save(self, domain: str, policy: SitePolicy, *, actor: str | None) -> None:
        """
        Keep a policy, without rendering the site.

        Args:
            domain: The application's primary domain.
            policy: The policy.
            actor: Who changed it.
        """
        document = None if policy.is_default else json.dumps(policy.to_dict(), sort_keys=True)
        self._records.set_document(domain, document, updated_by=actor)

    def sync_auth_file(self, domain: str, conf_root: Path, kind: str) -> Path | None:
        """
        Write the account file the web server reads, from the accounts in the store.

        Called every time the site is written, so the file is never the thing
        that is out of date. It is removed when basic authentication is off or
        has no accounts.

        Args:
            domain: The application's primary domain.
            conf_root: The web server's configuration directory.
            kind: ``nginx`` or ``apache``.

        Returns:
            The file, or None when none is needed.

        Raises:
            ValidationError: Basic authentication is on and has no accounts, which
                would lock everyone out.
            OSError: The file cannot be written.
        """
        fs = self._fs or get_fs()
        path = auth_file(conf_root, domain)
        policy = self.get(domain)
        if not policy.uses_accounts:
            if path.exists():
                fs.remove(path, missing_ok=True)
            return None
        users = self._records.list_users(domain)
        if not users:
            raise ValidationError(
                f"{domain} asks for a password and has no accounts",
                details="Add an account first, or nobody can sign in.",
                field="basic_auth",
            )
        group = worker_group(conf_root, kind)
        fs.make_dir(path.parent, mode=0o750, parents=True)
        # The workers open the file at request time, as the group: a directory only root can
        # enter makes every request to the site a 500 (open() failed, 13: Permission denied).
        fs.set_owner(path.parent, user="root", group=group, mode=0o750)
        fs.write_text(path, account_file_text(users), mode=0o640)
        fs.set_owner(path, user="root", group=group, mode=0o640)
        return path

    def remove_auth_file(self, domain: str, conf_root: Path) -> None:
        """
        Remove the account file of an application whose site is gone.

        Args:
            domain: The application's primary domain.
            conf_root: The web server's configuration directory.
        """
        (self._fs or get_fs()).remove(auth_file(conf_root, domain), missing_ok=True)

    def hash_password(self, password: str) -> str:
        """
        Args:
            password: The password.

        Returns:
            Its hash (see :func:`hash_password`).
        """
        return hash_password(password, self._runner)

    def forget(self, domain: str) -> None:
        """
        Drop everything kept for an application that is gone.

        Args:
            domain: The application's primary domain.
        """
        self._records.forget(domain)
