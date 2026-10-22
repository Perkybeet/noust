# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
HTTP/3 (QUIC) for the sites that ask for it.

A site's policy says ``http3``; the site's template then adds ``listen 443 quic;`` and the
``Alt-Svc`` header that tells browsers. What belongs to the server and not to a site is here:

- **Whether this nginx can.** QUIC is in nginx since 1.25.0 and only when it was built with
  ``--with-http_v3_module`` (a distribution's nginx older than that, or built without it, has
  neither). Apache has nothing equivalent in its stock builds.
- **One socket for every site.** nginx accepts ``reuseport`` once for an address and port, and
  QUIC needs it to work with more than one worker, so no site can carry it: a small site of
  Noust's own (``00-noust-quic``, which sorts first and so is the default for the socket) holds
  the one ``listen 443 quic reuseport`` and refuses the handshake of any name nobody serves.
  It is written when the first site turns HTTP/3 on and taken away with the last.
- **The firewall.** QUIC travels over UDP 443. Noust reports whether the firewall lets it in and
  names the command to open it; it does not open ports behind the operator's back for a protocol
  a browser falls back from anyway (a blocked UDP 443 costs a browser one failed attempt, then
  HTTP/2 over TCP as before).

The anchor is tested by the web server before it is reloaded, and put away when refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.managers.server.security_firewall import Firewall
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.webserver import WebServerManager

#: The anchor's file name: it sorts before the sites, so it is the default for the QUIC socket.
ANCHOR_NAME = "00-noust-quic"

#: The oldest nginx with QUIC.
MINIMUM = (1, 25, 0)

_VERSION = re.compile(r"nginx/(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class Http3Support:
    """
    Whether this server can serve HTTP/3, and what stands in the way.

    Attributes:
        available: nginx is new enough and was built with the HTTP/3 module.
        webserver: ``nginx`` or ``apache``.
        version: nginx's version, when it could be read.
        problem: What is missing, when something is.
        hint: How to fix it.
        anchor: Whether the socket's own site is in place.
        udp_allowed: Whether the firewall lets UDP 443 in: None when there is no firewall
            Noust can read, or it filters nothing.
        firewall_hint: The command that opens UDP 443, when it is closed.
    """

    available: bool
    webserver: str
    version: str = ""
    problem: str = ""
    hint: str = ""
    anchor: bool = False
    udp_allowed: bool | None = None
    firewall_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The fields, JSON-ready.
        """
        return {
            "available": self.available,
            "webserver": self.webserver,
            "version": self.version,
            "problem": self.problem,
            "hint": self.hint,
            "anchor": self.anchor,
            "udp_allowed": self.udp_allowed,
            "firewall_hint": self.firewall_hint,
        }


def parse_nginx_v(output: str) -> tuple[tuple[int, ...], bool]:
    """
    Read what ``nginx -V`` prints.

    Args:
        output: Its output (nginx prints it on standard error).

    Returns:
        The version's numbers (empty when unreadable) and whether the HTTP/3 module is built in.
    """
    match = _VERSION.search(output)
    numbers = tuple(int(part) for part in match.group(1).split(".")) if match else ()
    return numbers, "--with-http_v3_module" in output


class Http3:
    """
    Check what this server offers for HTTP/3 and keep the socket's own site.

    Args:
        manager: The web server's manager.
        firewall: Reads the firewall; built from the manager's runner by default.
    """

    def __init__(self, manager: WebServerManager, firewall: Firewall | None = None) -> None:
        self._web = manager
        self._firewall = firewall

    @property
    def file(self) -> Path:
        """Where the anchor is kept."""
        return self._web.backend.sites_available / ANCHOR_NAME

    @property
    def link(self) -> Path:
        """Where it is enabled."""
        return self._web.backend.sites_enabled / ANCHOR_NAME

    def anchored(self) -> bool:
        """
        Returns:
            Whether the anchor is in place.
        """
        return self.file.exists() and (self.link.exists() or self.link.is_symlink())

    def support(self) -> Http3Support:
        """
        Say whether this server can serve HTTP/3.

        Returns:
            The answer, with what is missing and how to fix it.
        """
        name = self._web.backend.name
        if name != "nginx":
            return Http3Support(
                available=False,
                webserver=name,
                problem="Apache's stock builds have no HTTP/3",
                hint="Serve this site with nginx 1.25 or later to use HTTP/3.",
            )
        result = self._web.runner.run(["nginx", "-V"], timeout=10)
        numbers, built = parse_nginx_v(f"{result.stderr}\n{result.stdout}")
        version = ".".join(str(part) for part in numbers)
        problem = hint = ""
        if not numbers:
            problem = "nginx's version could not be read"
            hint = "Run: nginx -V"
        elif numbers < MINIMUM:
            problem = f"nginx {version} is older than 1.25.0, the first with HTTP/3"
            hint = (
                "Install a newer nginx: the nginx.org packages (https://nginx.org/en/linux_packages.html) "
                "carry it on Debian, Ubuntu and RPM systems."
            )
        elif not built:
            problem = f"This nginx {version} was built without --with-http_v3_module"
            hint = "Install the build from nginx.org, which includes it."
        allowed, command = self._udp_allowed()
        return Http3Support(
            available=not problem,
            webserver=name,
            version=version,
            problem=problem,
            hint=hint,
            anchor=self.anchored(),
            udp_allowed=allowed,
            firewall_hint=command,
        )

    def _udp_allowed(self) -> tuple[bool | None, str]:
        """
        Ask the firewall whether UDP 443 may come in.

        Returns:
            ``(allowed, command)``: None for allowed when no firewall filters traffic here;
            the command that opens the port when it is closed.
        """
        firewall = self._firewall or Firewall(
            SecurityProbe(runner=self._web.runner), None, actor="http3"
        )
        state = firewall.state()
        if not state.active:
            return None, ""
        covering = [rule for rule in state.rules if rule.covers(443, "udp")]
        if any(rule.action in ("allow", "limit") for rule in covering):
            return True, ""
        # Without a rule that names the port the default decides: Docker puts nftables rules on
        # a plain server that all end in "accept", and that firewall filters nothing.
        if state.default_incoming == "allow" and not covering:
            return True, ""
        commands = {
            "ufw": "ufw allow 443/udp",
            "firewalld": "firewall-cmd --permanent --add-port=443/udp",
        }
        return False, commands.get(
            state.backend, "Allow 443/udp in your firewall's rules; Noust does not edit them"
        )

    def render_anchor(self) -> str:
        """
        Returns:
            The anchor's configuration.
        """
        return self._web.jinja_env.get_template("quic_anchor.j2").render()

    def ensure_anchor(self) -> bool:
        """
        Write the socket's own site if it is not there.

        Returns:
            True when this call wrote it.

        Raises:
            ValidationError: HTTP/3 is not available, or the web server refused the site (its
                own words are the error; nothing is left changed).
        """
        require_server_role("Sites")
        support = self.support()
        if not support.available:
            raise ValidationError(support.problem, details=support.hint, field="http3")
        if self.anchored():
            return False
        web = self._web
        fs = web.fs
        had_file = self.file.exists()
        try:
            fs.make_dir(self.file.parent, parents=True)
            fs.write_text(self.file, self.render_anchor(), mode=0o644)
            fs.make_dir(self.link.parent, parents=True)
            if not (self.link.exists() or self.link.is_symlink()):
                fs.symlink(self.file, self.link)
            errors = web.config_errors()
            if errors is not None:
                raise ValidationError(
                    "nginx refused the site that holds the QUIC socket",
                    details=errors,
                    output=errors,
                    field="http3",
                )
        except (ValidationError, OSError):
            fs.remove(self.link, missing_ok=True)
            if not had_file:
                fs.remove(self.file, missing_ok=True)
            raise
        audit.record("sites.http3", target="server", details={"anchor": "written"})
        return True

    def release_anchor(self) -> bool:
        """
        Take the socket's own site away, when no site uses HTTP/3 any more.

        The caller has checked that none does. nginx is reloaded here, after the file is gone: the
        reload of the site that stopped using QUIC came first, while the socket's own site was
        still there, so without this one the UDP sockets stayed open (and the server block that
        refuses handshakes stayed loaded) until some later reload.

        Returns:
            True when this call removed it.
        """
        if not self.file.exists() and not self.link.is_symlink():
            return False
        self._web.fs.remove(self.link, missing_ok=True)
        self._web.fs.remove(self.file, missing_ok=True)
        audit.record("sites.http3", target="server", details={"anchor": "removed"})
        if not self._web.reload():
            self._web.logger.warning(
                "nginx did not reload after the QUIC socket's site was removed: "
                "the UDP port stays open until it does"
            )
        return True
