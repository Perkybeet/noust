# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Domains and certificates page, an application's Domains tab and the sites they list."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .constants import REPO
from .machine import Unit
from .sandbox import Sandbox

# ---------------------------------------------------------------------------
# Console: the Domains and certificates page, an application's Domains tab and
# the new-app wizard need a web server, certificates and DNS that behave, and
# sources to point the wizard at. Every seeded site gets a real configuration
# file rendered by the managers' own templates, so the editor has something to
# load; nginx's syntax check reads the files it is pointed at, so a broken edit
# fails the way nginx fails; certbot issues, expands, renews, revokes and
# deletes lineages on disk, and refuses a name the modelled DNS does not point
# here; that DNS answers from a fixed zone instead of the real network; and
# /var/www/src holds a Next.js project and a static site to inspect and deploy.
# ---------------------------------------------------------------------------

#: This machine's public addresses, as the modelled DNS knows them.
DOMAINS_MACHINE_ADDRESSES = ("203.0.113.10", "2001:db8::10")

#: Where a name that points at another server resolves.
DOMAINS_ELSEWHERE_ADDRESSES = ("198.51.100.23",)

#: Names under a seeded zone starting with these point at another server.
DOMAINS_ELSEWHERE_PREFIXES = ("old.", "legacy.")

#: Names under a seeded zone starting with these have no record yet.
DOMAINS_UNRESOLVED_PREFIXES = ("new.", "soon.")

#: The wizard's sources, under the sandbox's stand-in for /var/www/src.
WIZARD_SOURCES = ("storefront", "landing")

_DOMAINS_PEM = "-----BEGIN CERTIFICATE-----\nc2FuZGJveA==\n-----END CERTIFICATE-----\n"

_DOMAINS_CERTBOT_LOG = "Saving debug log to /var/log/letsencrypt/letsencrypt.log"

#: What certbot adds after a failed challenge, per authenticator.
_DOMAINS_CERTBOT_HINTS = {
    "nginx": "The Certificate Authority failed to verify the temporary {server} configuration "
    "changes made by Certbot. Ensure the listed domains point to this {server} server and "
    "that it is accessible from the internet.",
    "webroot": "The Certificate Authority failed to download the temporary challenge files "
    "created by Certbot. Ensure that the listed domains serve their content from the provided "
    "--webroot-path/-w and that files created there can be downloaded from the internet.",
    "standalone": "The Certificate Authority failed to download the challenge files from the "
    "temporary standalone webserver started by Certbot on port 80. Ensure that the listed "
    "domains point to this machine and that it can accept inbound connections from the "
    "internet.",
}
_DOMAINS_CERTBOT_HINTS["apache"] = _DOMAINS_CERTBOT_HINTS["nginx"]


def _domains_resolve(name: str, zones: frozenset[str]) -> tuple[str, ...]:
    """
    Answer a name from the modelled DNS.

    Args:
        name: The name to resolve.
        zones: The registrable domains the machine's applications live under.

    Returns:
        The addresses it resolves to; empty when it has no record.
    """
    name = name.strip().lower().rstrip(".")
    if not any(name == zone or name.endswith(f".{zone}") for zone in zones):
        return ()
    if name.startswith(DOMAINS_UNRESOLVED_PREFIXES):
        return ()
    if name.startswith(DOMAINS_ELSEWHERE_PREFIXES):
        return DOMAINS_ELSEWHERE_ADDRESSES
    return DOMAINS_MACHINE_ADDRESSES


def _domains_points_here(name: str, zones: frozenset[str]) -> bool:
    """
    Args:
        name: A domain.
        zones: The modelled DNS zones.

    Returns:
        True when every address the name resolves to is this machine's.
    """
    resolved = _domains_resolve(name, zones)
    return bool(resolved) and set(resolved) <= set(DOMAINS_MACHINE_ADDRESSES)


#: Arguments the simple directives of Noust's templates take: (fewest, most).
#: A lost semicolon joins two statements into one, which nginx reports as the
#: wrong number of arguments of the first.
_DOMAINS_NGINX_ARITY: dict[str, tuple[int, int]] = {
    "access_log": (1, 4),
    "add_header": (2, 3),
    "allow": (1, 1),
    "client_max_body_size": (1, 1),
    "deny": (1, 1),
    "error_log": (1, 2),
    "expires": (1, 2),
    "gzip": (1, 1),
    "gzip_comp_level": (1, 1),
    "gzip_proxied": (1, 9),
    "gzip_types": (1, 64),
    "gzip_vary": (1, 1),
    "include": (1, 1),
    "index": (1, 16),
    "listen": (1, 16),
    "proxy_cache_bypass": (1, 16),
    "proxy_connect_timeout": (1, 1),
    "proxy_http_version": (1, 1),
    "proxy_pass": (1, 1),
    "proxy_read_timeout": (1, 1),
    "proxy_send_timeout": (1, 1),
    "proxy_set_header": (2, 2),
    "return": (1, 2),
    "root": (1, 1),
    "server_name": (1, 64),
    "ssl_certificate": (1, 1),
    "ssl_certificate_key": (1, 1),
    "ssl_ciphers": (1, 1),
    "ssl_prefer_server_ciphers": (1, 1),
    "ssl_protocols": (1, 8),
    "ssl_session_cache": (1, 2),
    "ssl_session_timeout": (1, 1),
    "ssl_stapling": (1, 1),
    "ssl_stapling_verify": (1, 1),
    "try_files": (2, 16),
}


def _domains_nginx_syntax(text: str) -> tuple[str, int] | None:
    """
    Check a configuration the way nginx's parser does.

    The grammar - statements end in ``;``, blocks open with ``{`` after a
    directive and close with ``}``, strings are quoted, ``#`` starts a
    comment, ``${var}`` belongs to its token - and the argument count of the
    simple directives Noust's templates use. That is enough for the mistakes
    a hand edit makes (a lost semicolon, a lost brace), reported in nginx's
    own words.

    Args:
        text: The configuration.

    Returns:
        ``(message, line)`` for the first error, or None when it parses.
    """
    depth = 0
    statement: list[str] = []
    line = 1
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\n":
            line += 1
            index += 1
        elif char in " \t\r":
            index += 1
        elif char == "#":
            while index < length and text[index] != "\n":
                index += 1
        elif char == ";":
            if not statement:
                return 'unexpected ";"', line
            fewest, most = _DOMAINS_NGINX_ARITY.get(statement[0], (0, 1024))
            if not fewest <= len(statement) - 1 <= most:
                return f'invalid number of arguments in "{statement[0]}" directive', line
            statement = []
            index += 1
        elif char == "{":
            if not statement:
                return 'unexpected "{"', line
            depth += 1
            statement = []
            index += 1
        elif char == "}":
            if statement or depth == 0:
                return 'unexpected "}"', line
            depth -= 1
            index += 1
        elif char in "\"'":
            start, start_line = index, line
            index += 1
            while index < length and text[index] != char:
                if text[index] == "\\":
                    index += 1
                if index < length and text[index] == "\n":
                    line += 1
                index += 1
            if index >= length:
                return 'unexpected end of file, expecting ";" or "}"', start_line
            index += 1
            statement.append(text[start:index])
        else:
            start = index
            while index < length and text[index] not in " \t\r\n;{}\"'":
                if text.startswith("${", index):
                    close = text.find("}", index)
                    index = length if close == -1 else close + 1
                    continue
                index += 1
            statement.append(text[start:index])
    if statement:
        return 'unexpected end of file, expecting ";" or "}"', line
    if depth > 0:
        return 'unexpected end of file, expecting "}"', line
    return None


class _DomainsWebTools:
    """
    nginx's configuration test and certbot, answered from the sandbox's files.

    Attributes:
        sandbox: The sandbox.
        zones: The modelled DNS zones.
        lineages: Certificate name to the domains it covers.
        expiry: Certificate name to when it expires.
        revoked: Certificate names revoked but not deleted.
    """

    def __init__(self, sandbox: Sandbox, zones: frozenset[str]) -> None:
        """
        Args:
            sandbox: The sandbox.
            zones: The modelled DNS zones.
        """
        self.sandbox = sandbox
        self.zones = zones
        self.lineages: dict[str, list[str]] = {}
        self.expiry: dict[str, datetime] = {}
        self.revoked: set[str] = set()
        self._lock = threading.Lock()

    @property
    def live_dir(self) -> Path:
        """The sandbox's /etc/letsencrypt/live."""
        return self.sandbox.etc / "letsencrypt" / "live"

    def shown(self, path: Path | str) -> str:
        """
        Args:
            path: A path inside the sandbox.

        Returns:
            The path as the machine it stands in for would print it.
        """
        text = str(path)
        root = str(self.sandbox.root)
        return text[len(root) :] if text.startswith(root) else text

    def seed(
        self,
        names: Sequence[str],
        *,
        first_expired: bool = False,
        alt_names: dict[str, str] | None = None,
        soonest_days: int = 12,
    ) -> None:
        """
        Issue the seeded certificates: the name and its www, the expiry
        :func:`make_runner` always gave them (12, 29, 46... days).

        Args:
            names: Certificate names, in the order the store seeded them.
            first_expired: Make the first one expired three days ago instead
                (``--expired-certificate``), for a health report that is critical.
            soonest_days: Days the first one has left, the others 17 more each: the product
                video's servers with no certificate due start well past the renewal window.
            alt_names: A name's own alias, for the extra SAN it shows instead of
                "www.<name>" (``--showcase`` only). A bare, two-label domain still gets
                "www.<name>" regardless - that is a real certificate's own shape - but
                "www.api.kestrelworks.io" is not: a subdomain not in this mapping gets no
                second SAN at all, rather than one nobody would actually request
                (coordinator review, on the domains page). None (every other caller) keeps
                the original "name and its www" for every certificate, whatever domain it
                is.
        """
        for offset, name in enumerate(names):
            days = -3 if first_expired and offset == 0 else soonest_days + offset * 17
            if alt_names is None or name.count(".") == 1:
                domains = [name, f"www.{name}"]
            elif name in alt_names:
                domains = [name, alt_names[name]]
            else:
                domains = [name]
            self._issue(name, domains, datetime.now() + timedelta(days=days))

    def _issue(self, name: str, domains: list[str], expires: datetime) -> None:
        """
        Record a lineage and write its files.

        Args:
            name: Certificate name.
            domains: Domains it covers.
            expires: When it expires.
        """
        self.lineages[name] = list(domains)
        self.expiry[name] = expires
        self.revoked.discard(name)
        directory = self.live_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        for file in ("fullchain.pem", "cert.pem", "chain.pem", "privkey.pem"):
            (directory / file).write_text(_DOMAINS_PEM, encoding="utf-8")

    def _forget(self, name: str) -> None:
        """
        Drop a lineage and its files.

        Args:
            name: Certificate name.
        """
        self.lineages.pop(name, None)
        self.expiry.pop(name, None)
        self.revoked.discard(name)
        shutil.rmtree(self.live_dir / name, ignore_errors=True)

    def answer(self, args: tuple[str, ...]) -> Any:
        """
        Answer a command this model owns.

        Args:
            args: The argv.

        Returns:
            A CommandResult, or None for a command it leaves to the runner.
        """
        if args[:1] == ("nginx",) and "-t" in args:
            return self._nginx_test(args)
        if args[:1] == ("certbot",) and len(args) > 1:
            with self._lock:
                return self._certbot(args)
        return None

    # -- nginx -----------------------------------------------------------

    def _nginx_test(self, args: tuple[str, ...]) -> Any:
        """
        ``nginx -t``, or ``nginx -t -c <wrapper>`` for a staged configuration.

        Args:
            args: The argv.

        Returns:
            nginx's answer.
        """
        from noust.core.runner import CommandResult

        if "-c" in args and args.index("-c") + 1 < len(args):
            main = Path(args[args.index("-c") + 1])
            try:
                wrapper = main.read_text(encoding="utf-8")
            except OSError as exc:
                return CommandResult(
                    args,
                    1,
                    "",
                    f'nginx: [emerg] open() "{self.shown(main)}" failed ({exc.strerror})\n',
                )
            files = [Path(match) for match in re.findall(r"include\s+([^;\s]+);", wrapper)]
            main_shown = self.shown(main)
        else:
            enabled = self.sandbox.etc / "nginx" / "sites-enabled"
            files = sorted(enabled.iterdir()) if enabled.is_dir() else []
            main_shown = "/etc/nginx/nginx.conf"
            live = self.sandbox.etc / "nginx" / "nginx.conf"
            wrapper = live.read_text(encoding="utf-8") if live.is_file() else ""
        texts: list[tuple[Path, str]] = []
        for file in files:
            try:
                text = file.read_text(encoding="utf-8")
            except OSError:
                continue
            texts.append((file, text))
            problem = _domains_nginx_syntax(text)
            if problem is not None:
                message, line = problem
                return CommandResult(
                    args,
                    1,
                    "",
                    f"nginx: [emerg] {message} in {self.shown(file)}:{line}\n"
                    f"nginx: configuration file {main_shown} test failed\n",
                )
        problem_text = _sites_context_problem(wrapper, texts, self.shown)
        if problem_text is not None:
            return CommandResult(
                args, 1, "", f"{problem_text}\nnginx: configuration file {main_shown} test failed\n"
            )
        return CommandResult(
            args,
            0,
            "",
            f"nginx: the configuration file {main_shown} syntax is ok\n"
            f"nginx: configuration file {main_shown} test is successful\n",
        )

    # -- certbot ---------------------------------------------------------

    def _certbot(self, args: tuple[str, ...]) -> Any:
        """
        Args:
            args: A certbot argv.

        Returns:
            certbot's answer, or None for a verb this model does not own.
        """
        verb = args[1]
        if verb == "certificates":
            return self._certificates(args)
        if verb == "certonly":
            return self._certonly(args)
        if verb == "renew":
            return self._renew(args)
        if verb == "revoke":
            return self._revoke(args)
        if verb == "delete":
            return self._delete(args)
        return None

    @staticmethod
    def _options(args: tuple[str, ...], flag: str) -> list[str]:
        """
        Args:
            args: The argv.
            flag: An option that takes a value.

        Returns:
            Every value given to it, in order.
        """
        return [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == flag]

    def _certificates(self, args: tuple[str, ...]) -> Any:
        from noust.core.runner import CommandResult

        now = datetime.now()
        blocks = [_DOMAINS_CERTBOT_LOG, "", "-" * 79]
        if not self.lineages:
            blocks = [_DOMAINS_CERTBOT_LOG, "", "No certificates found."]
            return CommandResult(args, 0, "\n".join(blocks) + "\n", "")
        blocks.append("Found the following certs:")
        for name, domains in self.lineages.items():
            expiry = self.expiry[name]
            days = (expiry - now).days
            state = (
                "INVALID: REVOKED"
                if name in self.revoked
                else ("INVALID: EXPIRED" if days < 0 else f"VALID: {days} days")
            )
            blocks += [
                f"  Certificate Name: {name}",
                "    Serial Number: 4a3f9c2e1b7d",
                "    Key Type: ECDSA",
                f"    Domains: {' '.join(domains)}",
                f"    Expiry Date: {expiry.strftime('%Y-%m-%d %H:%M:%S')}+00:00 ({state})",
                f"    Certificate Path: /etc/letsencrypt/live/{name}/fullchain.pem",
                f"    Private Key Path: /etc/letsencrypt/live/{name}/privkey.pem",
            ]
        blocks.append("-" * 79)
        return CommandResult(args, 0, "\n".join(blocks) + "\n", "")

    def _authenticator(self, args: tuple[str, ...]) -> str:
        for flag in ("--nginx", "--apache", "--webroot", "--standalone"):
            if flag in args:
                return flag.removeprefix("--")
        return "nginx"

    def _certonly(self, args: tuple[str, ...]) -> Any:
        from noust.core.runner import CommandResult

        domains = self._options(args, "-d")
        names = self._options(args, "--cert-name")
        name = names[0] if names else (domains[0] if domains else "")
        if not domains:
            return CommandResult(
                args, 1, "", "certbot: error: at least one domain must be given with -d\n"
            )
        requested = (
            f"{domains[0]}"
            if len(domains) == 1
            else f"{domains[0]} and {len(domains) - 1} more domains"
        )
        problems = []
        for domain in domains:
            resolved = _domains_resolve(domain, self.zones)
            if not resolved:
                problems.append(
                    f"  Domain: {domain}\n  Type:   dns\n"
                    f"  Detail: DNS problem: NXDOMAIN looking up A for {domain} - check that "
                    "a DNS record exists for this domain; DNS problem: NXDOMAIN looking up "
                    f"AAAA for {domain} - check that a DNS record exists for this domain"
                )
            elif not _domains_points_here(domain, self.zones):
                problems.append(
                    f"  Domain: {domain}\n  Type:   unauthorized\n"
                    f"  Detail: {resolved[0]}: Invalid response from "
                    f"http://{domain}/.well-known/acme-challenge/"
                    "b3NfUvw9GQeD1Yq8hZk2xJpTcLmR: 404"
                )
        if problems:
            authenticator = self._authenticator(args)
            hint = _DOMAINS_CERTBOT_HINTS.get(authenticator, _DOMAINS_CERTBOT_HINTS["nginx"])
            hint = hint.replace("{server}", authenticator)
            return CommandResult(
                args,
                1,
                "",
                f"{_DOMAINS_CERTBOT_LOG}\nRequesting a certificate for {requested}\n\n"
                f"Certbot failed to authenticate some domains (authenticator: {authenticator})."
                " The Certificate Authority reported these problems:\n"
                + "\n\n".join(problems)
                + f"\n\nHint: {hint}\n\nSome challenges have failed.\nAsk for help or search "
                "for solutions at https://community.letsencrypt.org. See the logfile "
                "/var/log/letsencrypt/letsencrypt.log or re-run Certbot with -v for more "
                "details.\n",
            )
        if "--dry-run" in args:
            return CommandResult(
                args,
                0,
                f"{_DOMAINS_CERTBOT_LOG}\nSimulating a certificate request for {requested}\n"
                "The dry run was successful.\n",
                "",
            )
        expires = datetime.now() + timedelta(days=90)
        self._issue(name, domains, expires)
        return CommandResult(
            args,
            0,
            f"{_DOMAINS_CERTBOT_LOG}\nRequesting a certificate for {requested}\n\n"
            "Successfully received certificate.\n"
            f"Certificate is saved at: /etc/letsencrypt/live/{name}/fullchain.pem\n"
            f"Key is saved at:         /etc/letsencrypt/live/{name}/privkey.pem\n"
            f"This certificate expires on {expires.strftime('%Y-%m-%d')}.\n"
            "These files will be updated when the certificate renews.\n"
            "Certbot has set up a scheduled task to automatically renew this certificate "
            "in the background.\n",
            "",
        )

    def _renew(self, args: tuple[str, ...]) -> Any:
        from noust.core.runner import CommandResult

        names = self._options(args, "--cert-name") or list(self.lineages)
        unknown = [name for name in names if name not in self.lineages]
        if unknown:
            return CommandResult(
                args,
                1,
                "",
                f"No certificate found with name {unknown[0]} (expected "
                f"/etc/letsencrypt/renewal/{unknown[0]}.conf).\n",
            )
        force = "--force-renewal" in args
        dry = "--dry-run" in args
        renewed, skipped = [], []
        for name in names:
            days = (self.expiry[name] - datetime.now()).days
            if force or days < 30:
                if not dry:
                    self._issue(name, self.lineages[name], datetime.now() + timedelta(days=90))
                renewed.append(name)
            else:
                skipped.append(name)
        rule = "- " * 39 + "-"
        lines = [_DOMAINS_CERTBOT_LOG, ""]
        for name in names:
            lines += [rule, f"Processing /etc/letsencrypt/renewal/{name}.conf", rule]
            if name in renewed:
                lines.append(
                    f"Renewing an existing certificate for {' and '.join(self.lineages[name][:2])}"
                )
            else:
                lines.append("Certificate not yet due for renewal")
            lines.append("")
        lines.append(rule)
        if skipped:
            lines.append("The following certificates are not due for renewal yet:")
            lines += [
                f"  /etc/letsencrypt/live/{name}/fullchain.pem expires on "
                f"{self.expiry[name].strftime('%Y-%m-%d')} (skipped)"
                for name in skipped
            ]
        if renewed:
            lines.append(
                "Congratulations, all simulated renewals succeeded:"
                if dry
                else "Congratulations, all renewals succeeded:"
            )
            lines += [f"  /etc/letsencrypt/live/{name}/fullchain.pem (success)" for name in renewed]
        else:
            lines.append("No renewals were attempted.")
        lines.append(rule)
        return CommandResult(args, 0, "\n".join(lines) + "\n", "")

    def _revoke(self, args: tuple[str, ...]) -> Any:
        from noust.core.runner import CommandResult

        paths = self._options(args, "--cert-path")
        name = Path(paths[0]).parent.name if paths else ""
        if name not in self.lineages:
            return CommandResult(
                args,
                1,
                "",
                f"{_DOMAINS_CERTBOT_LOG}\nCould not read the certificate at {paths[:1]}.\n",
            )
        if "--delete-after-revoke" in args:
            self._forget(name)
        else:
            self.revoked.add(name)
        return CommandResult(
            args,
            0,
            f"{_DOMAINS_CERTBOT_LOG}\nCongratulations! You have successfully revoked the "
            f"certificate that was located at /etc/letsencrypt/live/{name}/fullchain.pem.\n",
            "",
        )

    def _delete(self, args: tuple[str, ...]) -> Any:
        from noust.core.runner import CommandResult

        names = self._options(args, "--cert-name")
        name = names[0] if names else ""
        if name not in self.lineages:
            return CommandResult(
                args,
                1,
                "",
                f"No certificate found with name {name} (expected "
                f"/etc/letsencrypt/renewal/{name}.conf).\n",
            )
        self._forget(name)
        return CommandResult(
            args,
            0,
            f"{_DOMAINS_CERTBOT_LOG}\nDeleted all files relating to certificate {name}.\n",
            "",
        )


def _sites_context_problem(
    main: str, files: list[tuple[Path, str]], shown: Callable[[Path], str]
) -> str | None:
    """
    What nginx says of a site that only fails beside the others (spec 3.2, section 2.2).

    The site API tests a candidate inside a copy of nginx.conf with every enabled
    site, so the modelled nginx reads them together the way the real one does: a
    ``limit_req`` zone nobody declared, and an upstream name two files declare.

    Args:
        main: The main configuration's text (the staged copy, or the live one).
        files: Each included file and its text.
        shown: Prints a sandbox path as the modelled machine would.

    Returns:
        nginx's ``[emerg]`` line, or None when the files fit together.
    """
    everything = "\n".join([main, *(text for _, text in files)])
    declared = set(re.findall(r"limit_req_zone\s[^;]*zone=([\w-]+):", everything))
    for zone in re.findall(r"limit_req\s+zone=([\w-]+)", everything):
        if zone not in declared:
            return f'nginx: [emerg] zero size shared memory zone "{zone}"'
    seen: set[str] = set()
    for file, text in files:
        for match in re.finditer(r"^\s*upstream\s+(\S+)\s*\{", text, re.MULTILINE):
            if match.group(1) in seen:
                line = text.count("\n", 0, match.start(1)) + 1
                return (
                    f'nginx: [emerg] duplicate upstream "{match.group(1)}" in {shown(file)}:{line}'
                )
            seen.add(match.group(1))
    return None


def _domains_redirect_managers() -> None:
    """
    Point the nginx and Apache managers' default backends at the sandbox.

    :func:`redirect_system_paths` rebinds the backends on
    ``noust.managers.webserver``, but ``NginxManager`` and ``ApacheManager``
    imported them by name before that, so a manager built without a backend
    (the sites API, a deployer rendering its site) still read and wrote the
    real /etc/nginx.
    """
    import noust.managers.apache_manager as apache_module
    import noust.managers.nginx_manager as nginx_module
    import noust.managers.webserver as webserver_module

    nginx_module.NGINX_BACKEND = webserver_module.NGINX_BACKEND  # type: ignore[attr-defined]
    apache_module.APACHE_BACKEND = webserver_module.APACHE_BACKEND  # type: ignore[attr-defined]
    for manager, backend in (
        (nginx_module.NginxManager, webserver_module.NGINX_BACKEND),
        (apache_module.ApacheManager, webserver_module.APACHE_BACKEND),
    ):
        manager.SITES_AVAILABLE = backend.sites_available
        manager.SITES_ENABLED = backend.sites_enabled


def _domains_site_files(store: Any) -> None:
    """
    Give every seeded site that has no configuration file one, rendered by
    the manager's own template, and enable the ones the store says are.

    Args:
        store: The seeded store.
    """
    from noust.managers.nginx_manager import NginxManager

    manager = NginxManager(verbose=False)
    apps = {app.id: app for app in store.list_apps()}
    for site in store.list_sites():
        if site.webserver != "nginx":
            continue
        app = apps.get(site.app_id)
        static = app is not None and app.app_type == "static"
        if not manager.site_exists(site.domain):
            manager.create_site(
                site.domain,
                "static" if static else "proxy",
                {
                    "port": site.proxy_port or 3000,
                    "ssl": bool(site.ssl_certificate),
                    "app_path": f"/var/www/apps/{site.domain}",
                },
            )
        if site.enabled and not manager.site_enabled(site.domain):
            manager.enable_site(site.domain)


def seed_sites_server_names(domain: str, extra: Sequence[str]) -> None:
    """
    Give an already-written site's configuration more names to answer on.

    Every seeded site otherwise answers on its bare domain only, which never
    exercises ``SiteInfo.server_names`` beyond a single-element list. This
    edits the file ``_domains_site_files`` already wrote, in place, the same
    way an operator's own ``server_name`` edit would: no store row is added,
    since these are extra names of a *site*, not an application's own domains
    (``noust.web.api.domains``, seeded separately).

    Args:
        domain: Domain of an already-written nginx site.
        extra: Names to add beside it, unqualified (``www``, not
            ``www.<domain>``).
    """
    from noust.managers.nginx_manager import NginxManager

    manager = NginxManager(verbose=False)
    if not manager.site_exists(domain):
        return
    config = manager.get_site_config(domain) or ""
    names = " ".join([domain, *(f"{name}.{domain}" for name in extra)])
    updated = config.replace(f"server_name {domain};", f"server_name {names};")
    if updated != config:
        manager.replace_site_config(domain, updated)


#: The http-level settings the Proggest sites rely on, as its server's nginx.conf
#: declares them: the request-rate zones its auth and API locations name.
SITES_NGINX_CONF = """\
user www-data;
worker_processes auto;
pid /run/nginx.pid;

events {
    worker_connections 768;
}

http {
    sendfile on;
    tcp_nopush on;
    types_hash_max_size 2048;
    server_tokens off;

    # Request-rate zones the sites' limit_req directives name.
    limit_req_zone $binary_remote_addr zone=auth_limit:10m rate=5r/s;
    limit_req_zone $binary_remote_addr zone=api_general:10m rate=50r/s;

    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;

    gzip on;

    include sites-enabled/*;
}
"""

#: Proggest's Compose stack, as ``docker ps --format '{{json .}}'`` lists it: the
#: two services its upstreams reach (nextjs_upstream, nestjs_upstream), so the
#: site's diagram names a Compose service as each port's owner. On its server they
#: publish 3001 and 3000; here the seeded applications already hold 3000 and up,
#: so the sites are seeded with each port moved to the one beside it here.
SITES_PROGGEST_CONTAINERS = (
    ("proggest-frontend-1", "frontend", 3001, 3201),
    ("proggest-backend-1", "backend", 3000, 3200),
)


def seed_proggest_site(sandbox: Sandbox, tools: Any) -> None:
    """
    Seed the operator-written Proggest sites, for the site page's structure and diagram.

    ``proggest.es`` and ``modulos.proggest.es`` are the real files the analyzer's
    corpus keeps (tests/fixtures/siteconf/proggest): two upstreams, 25 locations,
    rate limits whose zones live in nginx.conf, and a second site that proxies to
    the first one's upstreams. They are written without Noust's marker, as the
    operator wrote them, and enabled; nginx.conf gains the zones so testing either
    site in its real context passes. The certificate they name is issued, and
    ``docker ps`` answers with the stack whose ports their upstreams reach.

    Args:
        sandbox: The sandbox.
        tools: The modelled web tools (:class:`_DomainsWebTools`), for the certificate.
    """
    from noust.core.runner import CommandResult, get_runner

    nginx = sandbox.etc / "nginx"
    (nginx / "nginx.conf").write_text(SITES_NGINX_CONF, encoding="utf-8")
    corpus = REPO / "tests" / "fixtures" / "siteconf" / "proggest"
    for name in ("proggest.es", "modulos.proggest.es"):
        available = nginx / "sites-available" / name
        text = (corpus / name).read_text(encoding="utf-8")
        for _, _, real, here in SITES_PROGGEST_CONTAINERS:
            text = text.replace(f"127.0.0.1:{real};", f"127.0.0.1:{here};")
        available.write_text(text, encoding="utf-8")
        enabled = nginx / "sites-enabled" / name
        if not enabled.is_symlink():
            enabled.symlink_to(available)
    tools._issue(
        "proggest.es",
        [
            "proggest.es",
            "www.proggest.es",
            "clock.proggest.es",
            "reports.proggest.es",
            "worklogs.proggest.es",
            "docs.proggest.es",
            "portal.proggest.es",
        ],
        datetime.now() + timedelta(days=75),
    )

    listing = "".join(
        json.dumps(
            {
                "Names": container,
                "Ports": f"127.0.0.1:{port}->{inside}/tcp",
                "Labels": ",".join(
                    (
                        "com.docker.compose.project=proggest",
                        f"com.docker.compose.service={service}",
                        "com.docker.compose.project.working_dir=/opt/proggest",
                    )
                ),
            }
        )
        + "\n"
        for container, service, inside, port in SITES_PROGGEST_CONTAINERS
    )
    runner = get_runner()
    original = runner.run

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        # Only the topology's own listing: Docker stays "not installed" for every
        # page that asks whether it exists first.
        if args == ("docker", "ps", "--format", "{{json .}}"):
            runner.calls.append(args)
            return CommandResult(args, 0, listing, "")
        return original(argv, **kwargs)

    runner.run = run  # type: ignore[method-assign]

    # The stack's ports answer; nothing in the sandbox listens on them for real.
    import noust.core.app_state as app_state_module
    import noust.managers.site_topology as topology_module

    stack_ports = {port for _, _, _, port in SITES_PROGGEST_CONTAINERS}

    def connect(port: int, host: str, timeout: float) -> bool:
        return port in stack_ports or app_state_module.port_answers(port, host, timeout)

    topology_module._DEFAULT = topology_module.TopologyProbe(connect=connect)


def _domains_wizard_sources(sandbox: Sandbox) -> None:
    """
    Write the projects the new-app wizard is pointed at, in /var/www/src.

    ``storefront`` is a Next.js project whose ``.env.example`` has required
    values, defaults and credentials; ``landing`` is a static site.

    Args:
        sandbox: The sandbox.
    """
    root = sandbox.var / "www" / "src"
    storefront = root / WIZARD_SOURCES[0]
    (storefront / "app").mkdir(parents=True, exist_ok=True)
    (storefront / "public" / "uploads").mkdir(parents=True, exist_ok=True)
    (storefront / "package.json").write_text(
        json.dumps(
            {
                "name": "storefront",
                "version": "1.4.2",
                "private": True,
                "scripts": {"dev": "next dev", "build": "next build", "start": "next start"},
                "dependencies": {"next": "15.2.4", "react": "19.0.0", "react-dom": "19.0.0"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (storefront / "package-lock.json").write_text(
        '{\n  "name": "storefront",\n  "lockfileVersion": 3,\n  "requires": true,\n'
        '  "packages": {}\n}\n',
        encoding="utf-8",
    )
    (storefront / "next.config.js").write_text(
        "/** @type {import('next').NextConfig} */\nmodule.exports = { output: 'standalone' };\n",
        encoding="utf-8",
    )
    (storefront / ".env.example").write_text(
        "# Orders and customers\n"
        "DATABASE_URL=\n"
        "# Signs session cookies\n"
        "NEXTAUTH_SECRET=\n"
        "NEXTAUTH_URL=https://example.com\n"
        "STRIPE_SECRET_KEY=\n"
        "SMTP_HOST=smtp.example.com\n"
        "SMTP_PASSWORD=\n"
        "UPLOADS_DIR=public/uploads\n"
        "LOG_LEVEL=info\n",
        encoding="utf-8",
    )
    (storefront / "app" / "page.js").write_text(
        "export default function Home() {\n  return <h1>Storefront</h1>;\n}\n", encoding="utf-8"
    )
    (storefront / "public" / "uploads" / ".gitkeep").write_text("", encoding="utf-8")

    landing = root / WIZARD_SOURCES[1]
    landing.mkdir(parents=True, exist_ok=True)
    (landing / "index.html").write_text(
        '<!doctype html>\n<html lang="en">\n<head><meta charset="utf-8"><title>Landing</title>'
        '<link rel="stylesheet" href="styles.css"></head>\n<body><h1>Coming soon</h1></body>\n'
        "</html>\n",
        encoding="utf-8",
    )
    (landing / "styles.css").write_text("body { font-family: system-ui; }\n", encoding="utf-8")


def seed_domains_and_sources(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    cert_domains: list[str],
    *,
    expired_certificate: bool = False,
    server_names: tuple[str, Sequence[str]] | None = ("example.net", ("www", "shop", "status")),
    cert_alt_names: dict[str, str] | None = None,
    proggest: bool = True,
    extra_zones: Sequence[str] = (),
    soonest_certificate_days: int = 12,
) -> None:
    """
    Seed the web server, certificates, DNS and sources the domain pages and the wizard need.

    Temporary files move into the sandbox too: the wizard's inspection clones
    into one, and the web server check stages the configuration it tests in
    another, which the modelled nginx has to be able to read. nginx itself
    runs, as a system unit Noust does not own: a deploy's pre-flight checks
    refuse to start on a machine whose web server is down.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, given the nginx one.
        cert_domains: The domains the store seeded with a certificate.
        expired_certificate: Seed the first certificate as expired.
        server_names: A site's domain, and the extra names to give it (unqualified: "www",
            not "www.<domain>") - see :func:`seed_sites_server_names`. Defaults to the
            default machine's own "example.net"; ``--showcase`` passes one of its own; ``None``
            (``--empty``) seeds no site at all.
        cert_alt_names: See :meth:`_DomainsWebTools.seed`'s own ``alt_names``
            (``--showcase`` only).
        proggest: Seed the operator's Proggest sites (:func:`seed_proggest_site`);
            the showcase's invented agency has none.
        extra_zones: Registrable domains the modelled DNS answers for besides those of the
            seeded applications: ``--empty`` has none, and answers for ``sslip.io`` as the real
            service does, so a temporary address resolves and its certificate is issued.
        soonest_certificate_days: See :meth:`_DomainsWebTools.seed`'s ``soonest_days``.
    """
    import functools
    import socket as socket_module

    import noust.deployers.domains as domains_core
    import noust.web.api.domains as domains_api
    from noust.core.runner import get_runner

    units.setdefault("nginx", Unit(active="active", pid=880, managed=False))
    scratch = sandbox.root / "tmp"
    scratch.mkdir(mode=0o700, exist_ok=True)
    tempfile.tempdir = str(scratch)
    _domains_redirect_managers()

    zones = frozenset(
        [*(".".join(app.domain.split(".")[-2:]) for app in store.list_apps()), *extra_zones]
    )
    tools = _DomainsWebTools(sandbox, zones)
    tools.seed(
        cert_domains,
        first_expired=expired_certificate,
        alt_names=cert_alt_names,
        soonest_days=soonest_certificate_days,
    )

    runner = get_runner()
    original = runner.run

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        answer = tools.answer(args)
        if answer is None:
            return original(argv, **kwargs)
        runner.calls.append(args)
        return answer

    runner.run = run  # type: ignore[method-assign]

    def resolver(host: str, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        addresses = _domains_resolve(host, zones)
        if not addresses:
            raise socket_module.gaierror(socket_module.EAI_NONAME, "Name or service not known")
        return [
            (
                socket_module.AF_INET6 if ":" in address else socket_module.AF_INET,
                socket_module.SOCK_STREAM,
                6,
                "",
                (address, 0),
            )
            for address in addresses
        ]

    # Patched where it is defined, not only where the API imported it: the certificate
    # diagnosis imports it lazily from noust.deployers.domains and must see the same
    # modelled DNS, never this machine's real resolver and addresses.
    modelled = functools.partial(
        domains_core.check_dns,
        resolver=resolver,
        local_addresses=lambda: DOMAINS_MACHINE_ADDRESSES,
    )
    domains_core.check_dns = modelled  # type: ignore[assignment]
    domains_api.check_dns = modelled  # type: ignore[assignment]

    _domains_site_files(store)
    if server_names is not None:
        seed_sites_server_names(*server_names)
    _domains_wizard_sources(sandbox)
    if proggest:
        seed_proggest_site(sandbox, tools)
