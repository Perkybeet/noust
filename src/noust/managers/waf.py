# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A web application firewall for the sites that ask for one: ModSecurity with the OWASP Core Rule Set.

A site's policy says ``waf`` (``detect`` or ``block``) and which rules to leave out for it; the
site's template then turns ``modsecurity`` on for its server block. What belongs to the server and
not to a site is here:

- **Whether this nginx can.** ModSecurity is a module (``libnginx-mod-http-modsecurity`` on Debian
  and Ubuntu), and the Core Rule Set is a package of rules (``modsecurity-crs``). A site cannot turn
  on a directive nginx does not know, so a policy that asks for the WAF is refused, in words, when
  either is missing. Apache has its own module and is not driven here.
- **The rules, loaded once.** ``modsecurity_rules_file`` at the ``http`` level loads the 900-odd
  rules a single time for every site; a site then only says ``modsecurity on`` (and, for
  ``detect``, ``SecRuleEngine DetectionOnly``, and the rules it leaves out). Loading them in each
  server block would hold a copy in memory per site. The shared file and the one-line site that
  loads it are Noust's own (``noust-waf/rules.conf`` and ``00-noust-waf``), written when the first
  site turns the WAF on and taken away with the last, and tested by nginx before it is reloaded.
- **What the rules are told.** libmodsecurity reads ``Include`` and not ``IncludeOptional``, so the
  distribution's own ``owasp-crs.load`` (made for Apache) cannot be used as it is: the file Noust
  writes names the setup file and the rules directory itself. Request bodies are not kept in the
  audit log (a password typed into a form would be in it), and response bodies are not inspected.

The log of what the rules saw is ModSecurity's JSON audit log (:meth:`Waf.events`), which is what an
operator needs to tell an attack from a false positive and to know which rule to leave out.
"""

from __future__ import annotations

import grp
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.managers.server.errors import UnsupportedHostError
from noust.managers.server.host import PackageFamily, detect_platform
from noust.managers.server.pkg import backend_for
from noust.managers.webserver import WAF_ANCHOR_SITE, WebServerManager

#: The one-line site that loads the rules at the http level; it sorts before the sites.
ANCHOR_NAME = WAF_ANCHOR_SITE

#: The directory under the web server's configuration that holds Noust's rules file.
DIRECTORY_NAME = "noust-waf"

#: The rules file loaded once for every site.
RULES_FILE = "rules.conf"

#: ModSecurity's JSON audit log, where the web server's other logs are. Not named ``*.log``: the
#: web server's own rotation owns that pattern and renames files, which ModSecurity (it opens the
#: log once and never reopens it) does not survive; see :data:`LOGROTATE_RULE`.
AUDIT_LOG = "/var/log/nginx/noust-waf-audit.jsonl"

#: The audit log holds the headers of every request the rules flagged, ``Cookie`` and
#: ``Authorization`` included (the module cannot leave them out), so the other accounts of the
#: server may not read it. ModSecurity creates it with the default mask when it is not there.
AUDIT_LOG_MODE = 0o640

#: The group that reads the server's logs on Debian and Ubuntu, where the packages are installed.
AUDIT_LOG_GROUP = "adm"

#: The rule that rotates the audit log by truncating it in place, relative to the root.
LOGROTATE_RULE = "etc/logrotate.d/noust-waf"

#: The packages of a Debian or Ubuntu server that give nginx ModSecurity and the Core Rule Set.
PACKAGES = ("libnginx-mod-http-modsecurity", "modsecurity-crs")

#: Seconds installing the packages may take.
INSTALL_TIMEOUT = 600

#: What the packages put where, in the order they are looked for (relative to the root).
MODULE_FILES = (
    "usr/lib/nginx/modules/ngx_http_modsecurity_module.so",
    "usr/lib64/nginx/modules/ngx_http_modsecurity_module.so",
    "etc/nginx/modules/ngx_http_modsecurity_module.so",
)
BASE_CONFIGS = ("etc/nginx/modsecurity.conf", "etc/modsecurity/modsecurity.conf")
CRS_SETUPS = (
    "etc/modsecurity/crs/crs-setup.conf",
    "usr/share/modsecurity-crs/crs-setup.conf",
    "etc/nginx/modsecurity-crs/crs-setup.conf",
)
CRS_RULES = (
    "usr/share/modsecurity-crs/rules",
    "etc/modsecurity/crs/rules",
    "usr/share/modsecurity-crs/owasp-crs/rules",
)

#: The CRS file every version of the rule set has, which tells a rules directory from another.
CRS_MARKER = "REQUEST-901-INITIALIZATION.conf"

#: The rule that adds the scores up and refuses a request that reaches the threshold: when it
#: matched and the site answered 403 the request was blocked, when it matched and the site did not
#: it is one block mode would have refused.
SCORER = "949110"

#: The tail of the audit log read to find recent events, in bytes.
EVENTS_TAIL_BYTES = 2 * 1024 * 1024

#: Events returned at most.
MAX_EVENTS = 200

_LOAD_MODULE = re.compile(r"^\s*load_module\s+\S*ngx_http_modsecurity_module\.so\s*;", re.MULTILINE)


@dataclass(frozen=True)
class WafSupport:
    """
    Whether this server can run the WAF, and what stands in the way.

    Attributes:
        available: nginx has the module loaded and the rules are there.
        webserver: ``nginx`` or ``apache``.
        module_loaded: nginx has loaded the ModSecurity module.
        module_installed: The module's file is on disk.
        rules: Whether the Core Rule Set was found.
        problem: What is missing, when something is.
        hint: How to fix it.
        installable: Noust can install the packages here (Debian and Ubuntu).
        packages: The packages it would install.
        anchor: Whether the shared rules are in place.
    """

    available: bool
    webserver: str
    module_loaded: bool = False
    module_installed: bool = False
    rules: bool = False
    problem: str = ""
    hint: str = ""
    installable: bool = False
    packages: tuple[str, ...] = field(default_factory=tuple)
    anchor: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The fields, JSON-ready.
        """
        return {
            "available": self.available,
            "webserver": self.webserver,
            "module_loaded": self.module_loaded,
            "module_installed": self.module_installed,
            "rules": self.rules,
            "problem": self.problem,
            "hint": self.hint,
            "installable": self.installable,
            "packages": list(self.packages),
            "anchor": self.anchor,
        }


@dataclass(frozen=True)
class WafEvent:
    """
    One request the rules judged.

    Attributes:
        time: When, as ModSecurity wrote it.
        client: The client address.
        host: The ``Host`` the request named.
        method: The HTTP method.
        uri: The request target.
        status: The status the site answered (403 when the rules refused it).
        rules: The rules that matched: ``(id, message, severity)``.
    """

    time: str
    client: str
    host: str
    method: str
    uri: str
    status: int
    rules: tuple[tuple[str, str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The fields, JSON-ready.
        """
        scored = any(r[0] == SCORER for r in self.rules)
        blocked = scored and self.status == 403
        return {
            "time": self.time,
            "client": self.client,
            "host": self.host,
            "method": self.method,
            "uri": self.uri,
            "status": self.status,
            "blocked": blocked,
            "would_block": scored and not blocked,
            "rules": [{"id": i, "message": m, "severity": s} for i, m, s in self.rules],
        }


def parse_events(text: str, hosts: set[str], limit: int = MAX_EVENTS) -> list[WafEvent]:
    """
    Read ModSecurity's JSON audit log for the transactions of some sites.

    Args:
        text: The log, one JSON document per line (the first line of a tail may be cut).
        hosts: The names to keep; a request for any other host is left out.
        limit: The most events returned.

    Returns:
        The newest first. A line that is not a transaction is skipped.
    """
    found: list[WafEvent] = []
    for line in reversed(text.splitlines()):
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        transaction = document.get("transaction") if isinstance(document, dict) else None
        if not isinstance(transaction, dict):
            continue
        request = transaction.get("request") or {}
        headers = request.get("headers") or {}
        host = str(headers.get("Host") or headers.get("host") or "").split(":")[0].lower()
        if hosts and host not in hosts:
            continue
        rules = tuple(
            (
                str((message.get("details") or {}).get("ruleId") or ""),
                str(message.get("message") or ""),
                str((message.get("details") or {}).get("severity") or ""),
            )
            for message in transaction.get("messages") or []
            if isinstance(message, dict)
        )
        found.append(
            WafEvent(
                time=str(transaction.get("time_stamp") or ""),
                client=str(transaction.get("client_ip") or ""),
                host=host,
                method=str(request.get("method") or ""),
                uri=str(request.get("uri") or ""),
                status=int((transaction.get("response") or {}).get("http_code") or 0),
                rules=rules,
            )
        )
        if len(found) >= limit:
            break
    return found


def noisy_rules(events: list[WafEvent]) -> list[tuple[str, str, int]]:
    """
    Args:
        events: Events the rules judged.

    Returns:
        ``(id, message, count)`` for each rule that matched, most often first, without the
        rule that only adds the scores up (949110): the ones to look at when a legitimate
        request is refused.
    """
    counts: Counter[tuple[str, str]] = Counter()
    for event in events:
        for rule_id, message, _severity in event.rules:
            if rule_id and rule_id not in (SCORER, "980130"):
                counts[(rule_id, message)] += 1
    return [(rule_id, message, count) for (rule_id, message), count in counts.most_common()]


class Waf:
    """
    Check what this server has for a WAF, install it, and keep the shared rules.

    Args:
        manager: The web server's manager.
        root: The file system root the packages' files are looked for under; ``/`` on a server.
    """

    def __init__(self, manager: WebServerManager, root: Path = Path("/")) -> None:
        self._web = manager
        self._root = root

    @property
    def directory(self) -> Path:
        """Where Noust's rules file is kept."""
        return self._web.backend.sites_available.parent / DIRECTORY_NAME

    @property
    def rules_file(self) -> Path:
        """The rules loaded once for every site."""
        return self.directory / RULES_FILE

    @property
    def file(self) -> Path:
        """Where the one-line site that loads them is kept."""
        return self._web.backend.sites_available / ANCHOR_NAME

    @property
    def link(self) -> Path:
        """Where it is enabled."""
        return self._web.backend.sites_enabled / ANCHOR_NAME

    @property
    def audit_log(self) -> Path:
        """Where ModSecurity writes what the rules judged."""
        return self._root / AUDIT_LOG.lstrip("/")

    @property
    def rotation_rule(self) -> Path:
        """Where the rule that rotates the audit log is kept."""
        return self._root / LOGROTATE_RULE

    def anchored(self) -> bool:
        """
        Returns:
            Whether the shared rules are loaded: their file and the site that loads it.
        """
        return (
            self.file.exists()
            and self.rules_file.exists()
            and (self.link.exists() or self.link.is_symlink())
        )

    def _first(self, candidates: tuple[str, ...], *, marker: str = "") -> Path | None:
        """
        Args:
            candidates: Paths relative to the root, most likely first.
            marker: A file the path must hold when it is a directory.

        Returns:
            The first that exists (and holds the marker), or None.
        """
        for candidate in candidates:
            path = self._root / candidate
            if marker and not (path / marker).is_file():
                continue
            if not marker and not path.is_file():
                continue
            return path
        return None

    def _module_loaded(self) -> bool:
        """
        Returns:
            Whether nginx has loaded the ModSecurity module (from the configuration it
            would load now, which is what ``nginx -T`` prints).
        """
        result = self._web.runner.run(["nginx", "-T"], timeout=30)
        return bool(_LOAD_MODULE.search(f"{result.stdout}\n{result.stderr}"))

    def support(self) -> WafSupport:
        """
        Say whether this server can run the WAF.

        Returns:
            The answer, with what is missing and how to fix it.
        """
        name = self._web.backend.name
        if name != "nginx":
            return WafSupport(
                available=False,
                webserver=name,
                problem="The WAF is nginx's here: Noust drives ModSecurity through nginx",
                hint="Serve this site with nginx to use the WAF.",
            )
        installed = self._first(MODULE_FILES) is not None
        loaded = self._module_loaded()
        rules = self._first(CRS_RULES, marker=CRS_MARKER) is not None
        problem = hint = ""
        if not loaded:
            problem = (
                "The ModSecurity module is installed but nginx does not load it"
                if installed
                else "nginx has no ModSecurity module"
            )
            hint = (
                "Load it with a load_module line in nginx.conf "
                "(Debian and Ubuntu: ln -s /usr/share/nginx/modules-available/mod-http-modsecurity.conf "
                "/etc/nginx/modules-enabled/50-mod-http-modsecurity.conf)."
                if installed
                else "Install it: noust app policy waf install (Debian and Ubuntu: "
                "libnginx-mod-http-modsecurity and modsecurity-crs)."
            )
        elif not rules:
            problem = "The OWASP Core Rule Set is not installed"
            hint = "Install it: noust app policy waf install (Debian and Ubuntu: modsecurity-crs)."
        elif self._first(BASE_CONFIGS) is None or self._first(CRS_SETUPS) is None:
            problem = "ModSecurity's base settings or the Core Rule Set's setup file are missing"
            hint = "Reinstall the packages: libnginx-mod-http-modsecurity and modsecurity-crs."
        return WafSupport(
            available=not problem,
            webserver=name,
            module_loaded=loaded,
            module_installed=installed or loaded,
            rules=rules,
            problem=problem,
            hint=hint,
            installable=self._installable(),
            packages=PACKAGES,
            anchor=self.anchored(),
        )

    def _installable(self) -> bool:
        """
        Returns:
            Whether the packages are installed by Noust here: the Debian and Ubuntu family.
        """
        platform = detect_platform(self._web.runner, None)
        return platform.family is PackageFamily.APT and not platform.transactional

    def install(self) -> list[str]:
        """
        Install the ModSecurity module and the Core Rule Set from the distribution.

        Returns:
            The packages that were installed.

        Raises:
            UnsupportedHostError: This is not a Debian or Ubuntu server, where the packages
                are known to exist.
            ValidationError: The package manager failed; its words are the error.
        """
        require_server_role("Sites")
        if not self._installable():
            raise UnsupportedHostError(
                "Noust installs the WAF on Debian and Ubuntu only",
                "Elsewhere install nginx's ModSecurity module and the OWASP Core Rule Set from "
                "your distribution or from nginx.org, then turn the WAF on.",
            )
        platform = detect_platform(self._web.runner, None)
        backend = backend_for(platform, runner=self._web.runner, fs=self._web.fs)
        result = backend.install(list(PACKAGES), timeout=INSTALL_TIMEOUT)
        if not result.success:
            raise ValidationError(
                "The packages could not be installed",
                details="The package manager's output is below.",
                output="\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p),
            )
        audit.record("sites.waf", target="server", details={"action": "install"})
        return list(PACKAGES)

    def render_rules(self) -> str:
        """
        Returns:
            The rules file: ModSecurity's base settings, ours over them, and the Core Rule Set.

        Raises:
            ValidationError: The packages' files are not where they are looked for.
        """
        base = self._first(BASE_CONFIGS)
        setup = self._first(CRS_SETUPS)
        rules = self._first(CRS_RULES, marker=CRS_MARKER)
        if base is None or setup is None or rules is None:
            raise ValidationError(
                "The WAF's packages are not complete",
                details="Reinstall libnginx-mod-http-modsecurity and modsecurity-crs.",
                field="waf",
            )
        return (
            self._web.jinja_env.get_template("waf_rules.j2").render(
                base=str(base), setup=str(setup), rules=str(rules), audit_log=AUDIT_LOG
            )
            + "\n"
        )

    def render_anchor(self) -> str:
        """
        Returns:
            The one-line site that loads the rules.
        """
        return (
            self._web.jinja_env.get_template("waf_anchor.j2").render(
                rules_file=str(self.rules_file)
            )
            + "\n"
        )

    def ensure_anchor(self) -> bool:
        """
        Write the shared rules and the site that loads them, if they are not there.

        Returns:
            True when this call wrote them.

        Raises:
            ValidationError: The WAF is not available, or nginx refused the rules (its own words
                are the error; nothing is left changed).
        """
        require_server_role("Sites")
        support = self.support()
        if not support.available:
            raise ValidationError(support.problem, details=support.hint, field="waf")
        self._protect_audit_log()
        if self.anchored():
            return False
        web = self._web
        fs = web.fs
        had_rules = self.rules_file.exists()
        had_site = self.file.exists()
        had_rotation = self.rotation_rule.exists()
        try:
            fs.make_dir(self.directory, parents=True)
            fs.write_text(self.rules_file, self.render_rules(), mode=0o644)
            fs.write_text(self.file, self.render_anchor(), mode=0o644)
            self._write_rotation_rule()
            fs.make_dir(self.link.parent, parents=True)
            if not (self.link.exists() or self.link.is_symlink()):
                fs.symlink(self.file, self.link)
            errors = web.config_errors()
            if errors is not None:
                raise ValidationError(
                    "nginx refused the WAF's rules",
                    details=errors,
                    output=errors,
                    field="waf",
                )
        except (ValidationError, OSError):
            fs.remove(self.link, missing_ok=True)
            if not had_site:
                fs.remove(self.file, missing_ok=True)
            if not had_rules:
                fs.remove(self.rules_file, missing_ok=True)
            if not had_rotation:
                fs.remove(self.rotation_rule, missing_ok=True)
            raise
        audit.record("sites.waf", target="server", details={"action": "rules written"})
        return True

    def _protect_audit_log(self) -> None:
        """
        Make the audit log before ModSecurity does, readable by root and the log group alone.

        The module creates the file with the default mask, which leaves every flagged request's
        ``Cookie`` and ``Authorization`` header readable by every account on the server, and keeps
        the mode it was created with. A log an earlier run left open is closed too; what it holds
        stays.
        """
        fs = self._web.fs
        log = self.audit_log
        fs.make_dir(log.parent, parents=True)
        if not log.exists():
            fs.write_text(log, "", mode=AUDIT_LOG_MODE)
        try:
            grp.getgrnam(AUDIT_LOG_GROUP)
            group = AUDIT_LOG_GROUP
        except KeyError:
            group = "root"
        fs.set_owner(log, user="root", group=group, mode=AUDIT_LOG_MODE)

    def _write_rotation_rule(self) -> None:
        """
        Rotate the audit log by truncating it in place, where logrotate is.

        A server without ``/etc/logrotate.d`` rotates nothing and gets no rule: Noust does not
        make the directory, which would be the first sign of a logrotate nobody installed.
        """
        if not self.rotation_rule.parent.is_dir():
            return
        text = self._web.jinja_env.get_template("waf_logrotate.j2").render(audit_log=AUDIT_LOG)
        self._web.fs.write_text(self.rotation_rule, text, mode=0o644)

    def release_anchor(self) -> bool:
        """
        Take the shared rules away, when no site uses the WAF any more.

        The caller has checked that none does. nginx is reloaded here, after the files are gone:
        the reload of the site that stopped using the WAF came first, while the rules were still
        loaded, so without this one nginx kept all of them in memory until some later reload.

        Returns:
            True when this call removed them.
        """
        if not self.file.exists() and not self.link.is_symlink() and not self.rules_file.exists():
            return False
        self._web.fs.remove(self.link, missing_ok=True)
        self._web.fs.remove(self.file, missing_ok=True)
        self._web.fs.remove(self.rules_file, missing_ok=True)
        self._web.fs.remove(self.rotation_rule, missing_ok=True)
        audit.record("sites.waf", target="server", details={"action": "rules removed"})
        if not self._web.reload():
            self._web.logger.warning(
                "nginx did not reload after the firewall's rules were removed: "
                "it keeps them loaded until it does"
            )
        return True

    def events(self, hosts: set[str], limit: int = 50, log: Path | None = None) -> list[WafEvent]:
        """
        Read what the rules judged lately for some sites.

        Args:
            hosts: The names of the application (its domains).
            limit: The most events returned.
            log: The audit log; ModSecurity's own beside the web server's by default.

        Returns:
            The newest first; empty when nothing was logged yet.
        """
        path = log or Path(AUDIT_LOG)
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                handle.seek(max(0, size - EVENTS_TAIL_BYTES))
                text = handle.read().decode("utf-8", "replace")
        except OSError:
            return []
        return parse_events(text, {host.lower() for host in hosts}, min(limit, MAX_EVENTS))
