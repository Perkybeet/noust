# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What answers a request for a name no site on this server serves.

A web server with several sites answers a request whose ``Host`` matches none of them with
the first site it loaded, so a stranger who points a name at this address (or asks for the
bare address) is shown an application that never agreed to be shown there, and a scanner
learns what runs here. This manager writes one more site, ahead of the others, that is the
default for ports 80 and 443 and says one of three things:

- ``close``: nothing (nginx closes the connection with its non-standard 444; Apache, which
  cannot, answers 421 Misdirected Request).
- ``refuse``: 503.
- ``redirect``: a permanent redirect to an address the operator names.

Over TLS there is no certificate to show for a name nobody serves, so nginx refuses the
handshake itself (``ssl_reject_handshake``, nginx 1.19.4 or later); a client that sends no
server name is refused with it. Apache has no such directive and gets the plain HTTP
answer only.

What keeps it from breaking anything:

- The webroot the certificate authority is sent to (``/.well-known/acme-challenge/``)
  still answers, as it does in every site Noust writes, so a certificate can be ordered
  for a name before its site exists.
- Only one server may be the default for a port. Where another is declared (the web
  server's own ``default`` site, or one the operator wrote) nothing is written: the
  answer names the file. The distribution's ``default`` site may be switched off with the
  operator's say-so, and is switched on again when this is removed. A site the operator
  wrote is never touched.
- The new site is tested by the web server before it is reloaded, and put away again when
  the web server refuses it.

The file is the record: its header says the action, the address and which distribution
site it switched off.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.site_routes import redirect_target
from noust.managers.siteconf import ParseError, parse
from noust.managers.siteconf.model import structure
from noust.managers.webserver import UNKNOWN_HOSTS_SITE, WebServerManager

#: What the web server does with an unknown name.
ACTIONS = ("close", "refuse", "redirect")

#: The site's name: it sorts before the others, which Apache takes as the default vhost.
SITE_NAME = UNKNOWN_HOSTS_SITE

#: The ports a default site is the default of.
PORTS = (80, 443)

#: The oldest nginx that can refuse a TLS handshake for an unknown name.
REJECT_HANDSHAKE_SINCE = (1, 19, 4)

_HEADER = "# Noust unknown hosts: "
_DUMP_SECTION = re.compile(r"^# configuration file (.+):$", re.MULTILINE)


@dataclass(frozen=True)
class DefaultHolder:
    """
    A server that already declares itself the default for a port.

    Attributes:
        file: The configuration file it is in.
        port: The port.
        distribution: Whether it is the web server's own ``default`` site, which may be
            switched off, rather than something the operator wrote.
    """

    file: str
    port: int
    distribution: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The fields, JSON-ready.
        """
        return {"file": self.file, "port": self.port, "distribution": self.distribution}


@dataclass(frozen=True)
class UnknownHostsStatus:
    """
    What answers an unknown name, and what stands in the way of changing it.

    Attributes:
        webserver: ``nginx`` or ``apache``.
        enabled: Whether Noust's site is in place.
        action: ``close``, ``refuse`` or ``redirect``; empty when not enabled.
        redirect_to: The redirect's address.
        tls: Whether a TLS handshake for an unknown name is refused too.
        file: Where the site is (or would be).
        disabled_distribution_site: The distribution site switched off to make room.
        holders: The servers that already declare themselves the default.
        version: The web server's version, when it could be read.
        notes: Things the operator should know.
    """

    webserver: str
    enabled: bool = False
    action: str = ""
    redirect_to: str = ""
    tls: bool = False
    file: str = ""
    disabled_distribution_site: str = ""
    holders: tuple[DefaultHolder, ...] = ()
    version: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The fields, JSON-ready.
        """
        return {
            "webserver": self.webserver,
            "enabled": self.enabled,
            "action": self.action,
            "redirect_to": self.redirect_to,
            "tls": self.tls,
            "file": self.file,
            "disabled_distribution_site": self.disabled_distribution_site,
            "holders": [holder.to_dict() for holder in self.holders],
            "version": self.version,
            "notes": list(self.notes),
        }


def _version(text: str | None) -> tuple[int, ...]:
    """
    Args:
        text: A version such as ``1.24.0``.

    Returns:
        Its numbers; empty when it cannot be read.
    """
    match = re.match(r"(\d+(?:\.\d+)*)", text or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def defaults_in(dump: str, *, skip: str = "") -> list[DefaultHolder]:
    """
    Find the servers of a configuration that are the default for port 80 or 443.

    Args:
        dump: What ``nginx -T`` prints: every file, each after a ``# configuration file``
            line.
        skip: A file name to leave out (Noust's own).

    Returns:
        One entry per file and port, in the order the files were read. A file that does
        not parse is left out: the web server would have refused it before this.
    """
    parts = _DUMP_SECTION.split(dump)
    found: list[DefaultHolder] = []
    for index in range(1, len(parts) - 1, 2):
        name, text = parts[index], parts[index + 1]
        if skip and Path(name).name == skip:
            continue
        try:
            model = structure(parse(text, "nginx"))
        except ParseError:
            continue
        seen: set[int] = set()
        for server in model.servers:
            for listen in server.listens:
                if (
                    listen.default_server
                    and not listen.quic
                    and listen.port in PORTS
                    and listen.port not in seen
                ):
                    seen.add(listen.port)
                    found.append(
                        DefaultHolder(
                            file=name,
                            port=int(listen.port),
                            distribution=Path(name).name == "default" and "sites-" in name,
                        )
                    )
    return found


def web_manager(webserver: str | None = None) -> WebServerManager:
    """
    Pick the web server this concerns.

    Args:
        webserver: ``nginx`` or ``apache``; the one installed (nginx first) when None.

    Returns:
        Its manager.

    Raises:
        ValidationError: It is not a web server Noust drives.
    """
    managers: dict[str, Callable[[], WebServerManager]] = {
        "nginx": NginxManager,
        "apache": ApacheManager,
    }
    if webserver is not None:
        if webserver.lower() not in managers:
            raise ValidationError(
                f"Unknown web server: {webserver!r}",
                details="Use one of: " + ", ".join(sorted(managers)) + ".",
                field="webserver",
            )
        return managers[webserver.lower()]()
    for cls in managers.values():
        candidate = cls()
        if candidate.is_installed():
            return candidate
    return NginxManager()


class UnknownHosts:
    """
    Read and set what answers an unknown name.

    Args:
        manager: The web server's manager (nginx or Apache).
    """

    def __init__(self, manager: WebServerManager) -> None:
        self._web = manager

    # -- paths ----------------------------------------------------------------

    @property
    def _name(self) -> str:
        return f"{SITE_NAME}{self._web.backend.config_suffix}"

    @property
    def file(self) -> Path:
        """Where the site is kept."""
        return self._web.backend.sites_available / self._name

    @property
    def link(self) -> Path:
        """Where it is enabled."""
        return self._web.backend.sites_enabled / self._name

    # -- reading --------------------------------------------------------------

    def _header(self) -> dict[str, str]:
        """
        Returns:
            The record kept in the file's header, empty when there is no file.
        """
        try:
            text = self.file.read_text(encoding="utf-8")
        except OSError:
            return {}
        record: dict[str, str] = {}
        for line in text.splitlines():
            if line.startswith(_HEADER):
                key, _, value = line[len(_HEADER) :].partition("=")
                record[key.strip()] = value.strip()
        return record

    def holders(self) -> tuple[DefaultHolder, ...]:
        """
        Find the servers other than Noust's that are the default for port 80 or 443.

        Returns:
            Them, for nginx; Apache has no such declaration, so none.
        """
        if self._web.backend.name != "nginx":
            return ()
        result = self._web.runner.run(["nginx", "-T"], timeout=30)
        if not result.success:
            return ()
        return tuple(defaults_in(result.stdout, skip=self._name))

    def tls_possible(self) -> bool:
        """
        Returns:
            Whether the web server can refuse a TLS handshake for an unknown name.
        """
        return (
            self._web.backend.name == "nginx"
            and _version(self._web.get_version()) >= REJECT_HANDSHAKE_SINCE
        )

    def status(self) -> UnknownHostsStatus:
        """
        Say what answers an unknown name now.

        Returns:
            The status; ``holders`` lists whatever else claims to be the default.
        """
        version = self._web.get_version() or ""
        record = self._header()
        enabled = bool(record) and (self.link.exists() or self.link.is_symlink())
        notes: list[str] = []
        if self._web.backend.name == "apache":
            notes.append(
                "Apache has no way to refuse a TLS handshake for a name nobody serves, so "
                "this answers over plain HTTP only; the first HTTPS site still answers on 443."
            )
        elif not self.tls_possible():
            notes.append(
                "This nginx is older than 1.19.4 and cannot refuse a TLS handshake for an "
                "unknown name: only plain HTTP is covered."
            )
        return UnknownHostsStatus(
            webserver=self._web.backend.name,
            enabled=enabled,
            action=record.get("action", "") if enabled else "",
            redirect_to=record.get("redirect", "") if enabled else "",
            tls=enabled and record.get("tls") == "yes",
            file=str(self.file),
            disabled_distribution_site=record.get("distribution", "") if enabled else "",
            holders=self.holders(),
            version=version,
            notes=tuple(notes),
        )

    # -- changing -------------------------------------------------------------

    def render(self, action: str, redirect_to: str, *, tls: bool, distribution: str = "") -> str:
        """
        Write the site's text.

        Args:
            action: ``close``, ``refuse`` or ``redirect``.
            redirect_to: The address a redirect goes to.
            tls: Whether a TLS handshake for an unknown name is refused.
            distribution: The distribution site this one switched off.

        Returns:
            The configuration file.
        """
        template = self._web.jinja_env.get_template("unknown_hosts.j2")
        return template.render(
            action=action,
            redirect_to=redirect_to,
            tls=tls,
            distribution=distribution,
        )

    def set(
        self,
        action: str,
        redirect_to: str = "",
        *,
        switch_off_distribution_site: bool = False,
    ) -> UnknownHostsStatus:
        """
        Make the web server answer an unknown name as asked.

        Args:
            action: ``close``, ``refuse`` or ``redirect``.
            redirect_to: Where a redirect goes: an ``http`` or ``https`` address.
            switch_off_distribution_site: Switch off the web server's own ``default`` site
                if it is the one in the way.

        Returns:
            The status after the change.

        Raises:
            ValidationError: The action or address is not acceptable, another server is the
                default for a port (the answer names the file), or the web server refused
                the new site (its own words are the error; nothing is left changed).
        """
        require_server_role("Sites")
        if action not in ACTIONS:
            raise ValidationError(
                f"{action!r} is not something to answer an unknown name with",
                details="Use one of: " + ", ".join(ACTIONS) + ".",
                field="action",
            )
        target = self._redirect_address(action, redirect_to)

        holders = self.holders()
        blocking = [holder for holder in holders if not holder.distribution]
        if blocking:
            first = blocking[0]
            raise ValidationError(
                f"{first.file} is already the default server for port {first.port}",
                details="Only one server can be. Remove default_server from that site "
                "(Noust never rewrites a site you wrote), or leave unknown names to it.",
                field="action",
            )
        distribution = [holder for holder in holders if holder.distribution]
        previous = self._header()
        kept_distribution = previous.get("distribution", "")
        if distribution and not switch_off_distribution_site:
            raise ValidationError(
                f"{distribution[0].file} is the web server's own default site and answers unknown names",
                details="Switch it off to put this in its place (it is put back when this is "
                "removed): --switch-off-default-site, or the confirmation in the console.",
                field="switch_off_distribution_site",
            )

        tls = self.tls_possible()
        text = self.render(
            action,
            target,
            tls=tls,
            distribution=kept_distribution or self._switched_off(distribution),
        )
        self._install(text, distribution if distribution else [])
        audit.record(
            "sites.unknown_hosts",
            target="server",
            details={"action": action, "redirect_to": target, "tls": tls},
        )
        return self.status()

    def _redirect_address(self, action: str, redirect_to: str) -> str:
        """
        Check what a redirect goes to.

        Args:
            action: The action.
            redirect_to: The address given.

        Returns:
            The address for a redirect, empty otherwise.

        Raises:
            ValidationError: A redirect has no acceptable address, or another action has one.
        """
        if action != "redirect":
            if redirect_to:
                raise ValidationError("Only a redirect has an address", field="redirect_to")
            return ""
        return redirect_target(redirect_to, absolute_only=True)

    def _switched_off(self, holders: list[DefaultHolder]) -> str:
        """
        Args:
            holders: The distribution servers being switched off.

        Returns:
            The name of the site file that is, empty when none.
        """
        return Path(holders[0].file).name if holders else ""

    def _install(self, text: str, switch_off: list[DefaultHolder]) -> None:
        """
        Put the site in place, switching off what is in the way, and undo it all if refused.

        Args:
            text: The configuration.
            switch_off: The distribution sites to switch off.

        Raises:
            ValidationError: The web server refused the configuration.
        """
        web = self._web
        fs = web.fs
        before = self.file.read_text(encoding="utf-8") if self.file.exists() else None
        had_link = self.link.exists() or self.link.is_symlink()
        removed: list[tuple[Path, Path]] = []
        try:
            fs.make_dir(self.file.parent, parents=True)
            fs.write_text(self.file, text, mode=0o644)
            fs.make_dir(self.link.parent, parents=True)
            if not had_link:
                fs.symlink(self.file, self.link)
            for holder in switch_off:
                enabled = web.backend.sites_enabled / Path(holder.file).name
                if enabled.is_symlink():
                    removed.append((enabled, Path(enabled.readlink())))
                    fs.remove(enabled, missing_ok=True)
            errors = web.config_errors()
            if errors is not None:
                raise ValidationError(
                    f"{web.backend.name} refused the site for unknown names",
                    details=errors,
                    output=errors,
                )
            web.reload()
        except (ValidationError, OSError):
            self._put_back(before, had_link, removed)
            raise

    def _put_back(
        self, before: str | None, had_link: bool, removed: list[tuple[Path, Path]]
    ) -> None:
        """
        Return the files to what they were before a change that was refused.

        Args:
            before: The site's earlier text, or None when there was none.
            had_link: Whether it was enabled.
            removed: The links that were taken away, each with where it pointed.
        """
        fs = self._web.fs
        if before is None:
            fs.remove(self.file, missing_ok=True)
        else:
            fs.write_text(self.file, before, mode=0o644)
        if not had_link:
            fs.remove(self.link, missing_ok=True)
        for link, target in removed:
            if not link.exists() and not link.is_symlink():
                fs.symlink(target, link)

    def reset(self) -> UnknownHostsStatus:
        """
        Take Noust's site away; the first site answers an unknown name again.

        The distribution site it switched off is switched on again.

        Returns:
            The status after the change.

        Raises:
            ValidationError: The web server refused the configuration after the change
                (nothing is left changed).
        """
        require_server_role("Sites")
        record = self._header()
        if not record:
            return self.status()
        web = self._web
        fs = web.fs
        text = self.file.read_text(encoding="utf-8")
        had_link = self.link.exists() or self.link.is_symlink()
        restored: list[Path] = []
        try:
            fs.remove(self.link, missing_ok=True)
            fs.remove(self.file, missing_ok=True)
            site = record.get("distribution", "")
            if site:
                available = web.backend.sites_available / site
                enabled = web.backend.sites_enabled / site
                if available.exists() and not enabled.exists() and not enabled.is_symlink():
                    fs.symlink(available, enabled)
                    restored.append(enabled)
            errors = web.config_errors()
            if errors is not None:
                raise ValidationError(
                    f"{web.backend.name} refused the configuration without the site for unknown names",
                    details=errors,
                    output=errors,
                )
            web.reload()
        except (ValidationError, OSError):
            fs.write_text(self.file, text, mode=0o644)
            if had_link and not self.link.is_symlink():
                fs.symlink(self.file, self.link)
            for link in restored:
                fs.remove(link, missing_ok=True)
            raise
        audit.record("sites.unknown_hosts", target="server", details={"action": "reset"})
        return self.status()
