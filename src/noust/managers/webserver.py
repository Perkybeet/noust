# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One implementation of "manage virtual hosts", parameterised by web server.

``nginx_manager.py`` and ``apache_manager.py`` used to be the same file with
different strings in it: 57 windows of eight identical lines, and two public
APIs that had drifted apart anyway, so a caller could not treat them as
interchangeable even though that is the whole point of having both. Every fix
had to be applied twice, and in practice never was: the nginx side grew
``create_advanced_site``, the apache side grew ``enable_module``, and only one
of them validated anything.

What actually differs between the two backends is data, not behaviour: a unit
name, a configuration directory, a filename suffix, the syntax-check command,
and whether enabling a site means writing a symlink or calling ``a2ensite``.
That set is :class:`WebServerBackend`. Everything else is
:class:`WebServerManager`, and both concrete managers are thin subclasses of it,
so the contract is the same by construction rather than by convention.

Four rules the old code broke and this one keeps:

- **Every operation goes through the runner.** Argv, timeout, no shell.
- **Every change to disk goes through the filesystem seam.** Writing a vhost,
  linking it into ``sites-enabled`` and deleting it again are the three things
  ``--dry-run`` most needs to be honest about, and none of them is a subprocess.
- **Nothing crosses a boundary as a dict with magic keys.** ``get_status`` and
  ``list_sites`` return records whose field names are part of a type.
- **A domain is validated before it becomes a path.** Noust runs as root, so a
  domain that carries a slash is an arbitrary file write, not a typo.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from string import Template
from typing import Any

from jinja2 import Environment, PackageLoader, TemplateNotFound
from jinja2 import TemplateError as JinjaTemplateError

from noust.central import require_server_role
from noust.core import paths
from noust.core.config import (
    APACHE_SITES_AVAILABLE,
    APACHE_SITES_ENABLED,
    NGINX_SITES_AVAILABLE,
    NGINX_SITES_ENABLED,
)
from noust.core.exceptions import (
    ApacheError,
    CertificateError,
    DomainError,
    NginxError,
    NoustError,
    SiteError,
    TemplateError,
    ValidationError,
)
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandRunner
from noust.core.store import DomainKind, NoustStore, Site, WebServer, get_store
from noust.core.utils import domain_to_app_name
from noust.managers.base_manager import BaseManager, MappingRecord
from noust.managers.cert_manager import CertManager
from noust.validators.domain import is_valid_domain, should_include_www
from noust.validators.names import resolve_within, validate_app_name, validate_filename

#: Module logger for the orchestration functions below. They are not manager
#: methods, so they have no ``self.logger``; this is the same standard-library
#: logger the job system uses for the same reason.
_logger = logging.getLogger(__name__)

#: A reload or a syntax check is a local operation; anything slower than this
#: means the web server is wedged and the caller needs to know now.
_CONTROL_TIMEOUT = 30

#: Port a proxy site targets when the caller does not say otherwise.
DEFAULT_PROXY_PORT = 3000

#: What ``Strict-Transport-Security`` says on the sites that opted in
#: (``ssl.hsts``). A year, without ``includeSubDomains`` or ``preload``: those
#: bind every subdomain of the domain, which an operator hosting other sites
#: under it did not ask for.
HSTS_VALUE = "max-age=31536000"


def hsts_header(config: Any) -> str:
    """
    Say what ``Strict-Transport-Security`` the sites Noust writes should send.

    The one place that turns the ``ssl.hsts`` flag into the header's value, so
    the templates only ask whether there is one.

    Args:
        config: The configuration to read ``ssl.hsts`` from.

    Returns:
        :data:`HSTS_VALUE` when the flag is on, else an empty string, which a
        template reads as "send nothing".
    """
    return HSTS_VALUE if config.get("ssl.hsts", False) else ""


#: Mode of a virtual host file. World readable, like the rest of the web server
#: configuration; the secrets live in the environment file, not here.
_CONFIG_MODE = 0o644

#: Where nginx finds the upstream of each application in zero-downtime mode:
#: one file per application, naming the one instance that serves. Outside
#: ``conf.d`` on purpose: it is included by the application's own site, and
#: only while that site exists. ``/etc/nginx/noust-upstreams``, or Noust's
#: ``wasm-upstreams`` on a server whose sites the migration has not rewritten.
NGINX_UPSTREAMS_DIR = paths.nginx_upstreams_dir()

#: Prefix of the upstream name, so it cannot collide with the upstreams an
#: advanced or monorepo site names after its routes and workspaces.
UPSTREAM_PREFIX = "wasm_bg_"

#: Suffix of the file that names the servers of one service of an application,
#: inside the application's own directory of :data:`NGINX_UPSTREAMS_DIR`.
SERVERS_SUFFIX = ".servers"

#: How much of a site file is read to look for Noust's marker. The marker is
#: the first comment of everything Noust renders, so the head is enough.
_MARKER_WINDOW = 2000


def site_name_for(domain: str, *, store: NoustStore | None = None) -> str:
    """
    Name the file in the sites directory that serves a domain.

    Noust names the file after the domain, and that is still the answer for
    every site it wrote. A site the operator already had is not: ``proggest``
    serves ``proggest.es``, and adopting that application records the file name
    so Noust edits that file instead of writing a second site next to it
    (``apps.site_name``). :meth:`WebServerManager.config_path` asks here, which
    makes it the one place a domain becomes a file name.

    Args:
        domain: The domain, as the caller has it; case and surrounding space
            are not significant.
        store: Where to read the recorded name. The process-wide store by
            default; a manager passes its own.

    Returns:
        The recorded site name, or the domain (lowercased and stripped) when
        the application has none, there is no such application, or the store
        cannot be read. The result is a candidate, not yet a safe file name:
        ``config_path`` validates it.
    """
    candidate = domain.strip().lower()
    try:
        app = (store or get_store()).get_app(candidate)
    except (NoustError, sqlite3.Error) as exc:
        # A machine with no readable store yet (a rehearsal, a first run) has
        # no adopted sites either, so the domain is the right answer.
        _logger.debug("Could not read the site name of %s from the store: %s", candidate, exc)
        return candidate
    if app is None or not app.site_name:
        return candidate
    return app.site_name


def config_serves_tls(config: str) -> bool:
    """
    Report whether a rendered configuration serves TLS.

    Args:
        config: The configuration file content.

    Returns:
        True when it carries a certificate directive, nginx's or apache's.
    """
    return "ssl_certificate" in config or "SSLCertificateFile" in config


def _is_noust_text(text: str) -> bool:
    """
    Report whether a site file's text says Noust (or WASM) wrote it.

    Args:
        text: The file's content, or at least its head.

    Returns:
        True when the marker is in the first :data:`_MARKER_WINDOW` characters.
    """
    return paths.carries_unit_marker(text[:_MARKER_WINDOW])


def _read_site_file(path: Path) -> str:
    """
    Read a site file for a listing, tolerating one that cannot be read.

    Args:
        path: The configuration file.

    Returns:
        Its text, or an empty string when it cannot be read (a listing shows
        the row regardless; the log says why it has no details).
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _logger.debug("Could not read %s: %s", path, exc)
        return ""


def _as_port(value: Any) -> int | None:
    """
    Read a port out of a template context.

    The context is a free-form mapping supplied by callers, so a value that is
    not a port is possible. It must not abort a write that has already happened;
    the store row simply records no port.

    Args:
        value: Candidate port from the template context.

    Returns:
        The port, or None when the value is not one.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class WebServerStatus(MappingRecord):
    """
    What a web server is doing right now.

    Attributes:
        name: Backend name, ``nginx`` or ``apache``.
        installed: Whether the binary is on PATH.
        version: Reported version, or None when it could not be parsed.
        active: Whether the unit is running.
        enabled: Whether the unit starts at boot.
    """

    name: str
    installed: bool
    version: str | None
    active: bool
    enabled: bool


@dataclass
class SiteInfo(MappingRecord):
    """
    One virtual host as it exists on disk.

    Attributes:
        domain: Domain the configuration serves.
        enabled: Whether the site is enabled in the web server.
        config_path: Absolute path of the configuration file.
        webserver: Backend that owns the file.
    """

    domain: str
    enabled: bool
    config_path: str
    webserver: str


@dataclass(frozen=True)
class WebServerBackend:
    """
    Everything that differs between one web server and another.

    Attributes:
        name: Short name used in records and messages.
        binary: Executable that must exist for the backend to be installed.
        service: systemd unit to reload, restart and query.
        version_argv: Command that prints the version.
        version_pattern: Pattern whose first group is the version.
        config_test_argv: Command that checks the configuration syntax.
        validation_argv: Command that checks the syntax of an arbitrary main
            configuration file; the path of that file is appended.
        validation_wrapper: :class:`string.Template` body of the throwaway
            main configuration that wraps a single virtual host so
            ``validation_argv`` can check it. ``$snippet`` is the staged
            virtual host file and ``$server_root`` the directory holding the
            backend's own configuration.
        sites_available: Directory holding every virtual host file.
        sites_enabled: Directory holding the enabled ones.
        config_suffix: Suffix appended to the domain to name the file.
        template_package: Package directory holding the Jinja templates.
        default_site_names: Distribution-provided sites that Noust does not own.
        enable_site_program: Program that enables a site, or None when enabling
            means writing a symlink into ``sites_enabled``.
        disable_site_program: Counterpart of ``enable_site_program``.
        module_enable_program: Program that enables a module, or None when the
            backend has no module system.
        module_disable_program: Counterpart of ``module_enable_program``.
        required_modules: Modules that must be enabled before a site works.
        server_name_pattern: Matches a directive naming what a virtual host
            answers on; its first group is the space-separated names.
        upstreams_dir: Directory of the per-application upstream files of
            blue/green activation, or None for a backend that has none.
        error: Exception type raised for failures of this backend, so existing
            callers keep catching what they already catch.
    """

    name: str
    binary: str
    service: str
    version_argv: tuple[str, ...]
    version_pattern: re.Pattern[str]
    config_test_argv: tuple[str, ...]
    validation_argv: tuple[str, ...]
    validation_wrapper: str
    sites_available: Path
    sites_enabled: Path
    config_suffix: str
    template_package: str
    default_site_names: frozenset[str]
    error: type[SiteError]
    enable_site_program: str | None = None
    disable_site_program: str | None = None
    module_enable_program: str | None = None
    module_disable_program: str | None = None
    required_modules: tuple[str, ...] = ()
    webserver_record: str = WebServer.NGINX.value
    server_name_pattern: re.Pattern[str] = re.compile(r"^\s*server_name\s+([^;]*);", re.MULTILINE)
    upstreams_dir: Path | None = None


#: Main configuration wrapping one staged virtual host for ``nginx -t -c``.
#: A server block is only valid inside http{}, and a main configuration is
#: only valid with an events{} block, so the wrapper supplies the minimal
#: skeleton and nothing else: including the live nginx.conf instead would make
#: the new snippet collide with the site it is about to replace.
_NGINX_VALIDATION_WRAPPER = """\
# Written by Noust to check one virtual host without touching the live
# configuration. Deleted as soon as nginx -t has answered.
events {
}
http {
    include $snippet;
}
"""

#: Main configuration wrapping one staged virtual host for apache. The live
#: module set is loaded first: a vhost using ProxyPass is only valid with
#: mod_proxy present, exactly as it will be at the next reload.
_APACHE_VALIDATION_WRAPPER = """\
# Written by Noust to check one virtual host without touching the live
# configuration. Deleted as soon as the syntax check has answered.
ServerRoot "$server_root"
IncludeOptional $server_root/mods-enabled/*.load
IncludeOptional $server_root/mods-enabled/*.conf
Include $snippet
"""

NGINX_BACKEND = WebServerBackend(
    name="nginx",
    binary="nginx",
    service="nginx",
    version_argv=("nginx", "-v"),
    version_pattern=re.compile(r"nginx/(\S+)"),
    config_test_argv=("nginx", "-t"),
    validation_argv=("nginx", "-t", "-c"),
    validation_wrapper=_NGINX_VALIDATION_WRAPPER,
    sites_available=NGINX_SITES_AVAILABLE,
    sites_enabled=NGINX_SITES_ENABLED,
    config_suffix="",
    template_package="templates/nginx",
    default_site_names=frozenset({"default"}),
    error=NginxError,
    webserver_record=WebServer.NGINX.value,
    upstreams_dir=NGINX_UPSTREAMS_DIR,
)

APACHE_BACKEND = WebServerBackend(
    name="apache",
    binary="apache2",
    service="apache2",
    version_argv=("apache2", "-v"),
    version_pattern=re.compile(r"Apache/(\S+)"),
    config_test_argv=("apache2ctl", "configtest"),
    # Not ``configtest``: apache2ctl hard-codes that word to ``-t`` on the live
    # configuration and drops any further arguments. Bare flags fall through to
    # the passthrough branch, which still sources /etc/apache2/envvars, so the
    # ${APACHE_*} variables the module files reference keep resolving.
    validation_argv=("apache2ctl", "-t", "-f"),
    validation_wrapper=_APACHE_VALIDATION_WRAPPER,
    sites_available=APACHE_SITES_AVAILABLE,
    sites_enabled=APACHE_SITES_ENABLED,
    config_suffix=".conf",
    template_package="templates/apache",
    default_site_names=frozenset({"000-default", "default-ssl"}),
    error=ApacheError,
    enable_site_program="a2ensite",
    disable_site_program="a2dissite",
    module_enable_program="a2enmod",
    module_disable_program="a2dismod",
    required_modules=("proxy", "proxy_http", "proxy_wstunnel", "rewrite", "headers"),
    webserver_record=WebServer.APACHE.value,
    server_name_pattern=re.compile(r"^\s*Server(?:Name|Alias)\s+(.+?)\s*$", re.MULTILINE),
)


class WebServerManager(BaseManager):
    """
    Manage virtual hosts for one web server backend.

    The public methods are the contract both backends honour: same names, same
    signatures, same return types. Where a backend cannot do something at all -
    nginx has no runtime module system - the method still exists and reports
    that honestly instead of being absent from one of the two classes.
    """

    def __init__(
        self,
        backend: WebServerBackend,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        """
        Initialize the manager.

        Args:
            backend: The web server this instance drives.
            verbose: Enable verbose logging.
            runner: Command runner to execute with. Defaults to the process-wide
                one.
            fs: Filesystem to write configurations and symlinks through.
                Defaults to the process-wide one.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        self.backend = backend

    # -- Wiring ------------------------------------------------------------

    @cached_property
    def store(self) -> NoustStore:
        """
        The persistence layer, opened on first use.

        Opening it lazily keeps a read-only operation such as rendering a
        template from touching SQLite at all.

        Returns:
            The store singleton.
        """
        return get_store()

    @cached_property
    def jinja_env(self) -> Environment:
        """
        The template environment for this backend.

        Returns:
            A Jinja environment loading from the backend's template directory.

        Raises:
            TemplateError: When the templates cannot be located, which means the
                package was installed without its data files.
        """
        try:
            return Environment(
                loader=PackageLoader("noust", self.backend.template_package),
                trim_blocks=True,
                lstrip_blocks=True,
                autoescape=False,  # noqa: S701 - web server config, not markup
            )
        except (ValueError, ImportError) as exc:
            raise TemplateError(
                f"Could not load {self.backend.name} templates",
                details=(
                    f"Package directory {self.backend.template_package} is missing. "
                    "Reinstall the noust package."
                ),
            ) from exc

    @property
    def sites_available(self) -> Path:
        """
        Directory holding every virtual host file.

        Returns:
            The backend's sites-available directory.
        """
        return self.backend.sites_available

    @property
    def sites_enabled(self) -> Path:
        """
        Directory holding the enabled virtual host files.

        Returns:
            The backend's sites-enabled directory.
        """
        return self.backend.sites_enabled

    # -- Service state -----------------------------------------------------

    def is_installed(self) -> bool:
        """
        Check whether the web server is installed.

        Returns:
            True when the backend binary is on PATH.
        """
        return self.runner.exists(self.backend.binary)

    def get_version(self) -> str | None:
        """
        Get the web server version.

        Returns:
            The version string, or None when it cannot be determined.
        """
        result = self._run(list(self.backend.version_argv), timeout=_CONTROL_TIMEOUT)
        # nginx prints its banner on stderr and apache on stdout; reading both
        # removes a per-backend special case that used to be wrong for one of
        # them after every refactor.
        match = self.backend.version_pattern.search(f"{result.stdout}\n{result.stderr}")
        return match.group(1) if match else None

    def is_running(self) -> bool:
        """
        Check whether the web server is currently running.

        Returns:
            True when the unit reports itself active.
        """
        result = self._run(
            ["systemctl", "is-active", self.backend.service], timeout=_CONTROL_TIMEOUT
        )
        return result.stdout.strip() == "active"

    def is_boot_enabled(self) -> bool:
        """
        Check whether the web server starts at boot.

        Returns:
            True when the unit is enabled.
        """
        result = self._run(
            ["systemctl", "is-enabled", self.backend.service], timeout=_CONTROL_TIMEOUT
        )
        return result.stdout.strip() == "enabled"

    def get_status(self) -> WebServerStatus:
        """
        Get the web server status.

        Returns:
            A record describing installation, version and unit state.
        """
        return WebServerStatus(
            name=self.backend.name,
            installed=self.is_installed(),
            version=self.get_version(),
            active=self.is_running(),
            enabled=self.is_boot_enabled(),
        )

    def test_config(self) -> bool:
        """
        Test the web server configuration syntax.

        Returns:
            True when the configuration is valid.
        """
        return self.config_errors() is None

    def config_errors(self) -> str | None:
        """
        Test the web server configuration and say what the server objected to.

        Returns:
            None when the configuration is valid; otherwise the server's own
            output, verbatim, which is what an operator fixing it needs.
        """
        result = self._run(list(self.backend.config_test_argv), timeout=_CONTROL_TIMEOUT)
        # apache2ctl exits non-zero on warnings it then describes as "Syntax OK".
        if result.success or "Syntax OK" in f"{result.stdout}\n{result.stderr}":
            return None
        output = "\n".join(stream for stream in (result.stderr, result.stdout) if stream.strip())
        return output or (
            f"{' '.join(self.backend.config_test_argv)} exited with status {result.exit_code}"
        )

    def reload(self) -> bool:
        """
        Reload the web server configuration.

        The syntax check runs first: reloading a broken configuration is how a
        deploy takes every other site on the box down with it.

        Returns:
            True when the reload succeeded.
        """
        if not self.test_config():
            self.logger.error(f"{self.backend.name} configuration test failed")
            return False

        return self._run(
            ["systemctl", "reload", self.backend.service], timeout=_CONTROL_TIMEOUT
        ).success

    def restart(self) -> bool:
        """
        Restart the web server.

        Returns:
            True when the restart succeeded.
        """
        return self._run(
            ["systemctl", "restart", self.backend.service], timeout=_CONTROL_TIMEOUT
        ).success

    def enable_module(self, module: str) -> bool:
        """
        Enable a web server module.

        Args:
            module: Module name.

        Returns:
            True when the module was enabled. False when the backend has no
            runtime module system, as with nginx, where modules are compiled in.
        """
        program = self.backend.module_enable_program
        if program is None:
            self.logger.debug(f"{self.backend.name} has no runtime modules; ignoring {module}")
            return False
        return self._run([program, module], timeout=_CONTROL_TIMEOUT).success

    def disable_module(self, module: str) -> bool:
        """
        Disable a web server module.

        Args:
            module: Module name.

        Returns:
            True when the module was disabled. False when the backend has no
            runtime module system.
        """
        program = self.backend.module_disable_program
        if program is None:
            self.logger.debug(f"{self.backend.name} has no runtime modules; ignoring {module}")
            return False
        return self._run([program, module], timeout=_CONTROL_TIMEOUT).success

    # -- Paths -------------------------------------------------------------

    def config_path(self, domain: str) -> Path:
        """
        Resolve the configuration file a domain maps to.

        This is the only place a domain becomes a path, and it is where the
        domain is checked. Noust writes these files as root, so a name carrying a
        slash, a newline or a ``..`` segment is an arbitrary file write; the
        allowlist rejects it before it reaches the filesystem, and
        :func:`resolve_within` catches the case where the name is clean but a
        symlink in the directory is not.

        The file is named after the domain unless the application recorded
        another name (:func:`site_name_for`): an operator's site called
        ``proggest`` for ``proggest.es``. The recorded name is checked as
        strictly as a domain, because it comes from a table that something
        other than this code can write.

        Args:
            domain: Domain name.

        Returns:
            The absolute path of the virtual host file.

        Raises:
            DomainError: When the domain is not a valid domain name.
            ValidationError: When the resulting file name is not a single, inert
                path component, including a recorded site name that is not one.
            SecurityError: When the path escapes the configuration directory.
        """
        candidate = domain.strip().lower()
        valid, reason = is_valid_domain(candidate)
        if not valid:
            raise DomainError(
                f"Invalid domain: {domain!r}",
                details=(
                    f"{reason}. A domain becomes the name of a file in "
                    f"{self.backend.sites_available}, so only letters, digits, "
                    "hyphens and dots are accepted."
                ),
            )

        name = site_name_for(candidate, store=self.store)
        try:
            filename = validate_filename(f"{name}{self.backend.config_suffix}")
        except ValidationError as exc:
            if name == candidate:
                raise
            raise ValidationError(
                f"The site name recorded for {candidate} is not a file name: {name!r}",
                details=(
                    f"A site name is a file in {self.backend.sites_available}: letters, "
                    "digits, hyphens and dots, starting with a letter or a digit."
                ),
                field="site_name",
            ) from exc
        return resolve_within(self.backend.sites_available, filename)

    def _link_path(self, domain: str) -> Path:
        """
        Resolve the enabled-site path a domain maps to.

        Unlike :meth:`config_path` this does not go through
        :func:`resolve_within`: the entry in ``sites-enabled`` is a symlink whose
        whole purpose is to point at another directory, so resolving it and
        demanding that it stay inside would reject every correctly enabled site.
        The safety comes from the name, which :meth:`config_path` has already
        validated as a single inert path component.

        Args:
            domain: Domain name.

        Returns:
            The absolute path inside the enabled-sites directory.

        Raises:
            DomainError: When the domain is not a valid domain name.
        """
        return self.backend.sites_enabled / self.config_path(domain).name

    def site_exists(self, domain: str) -> bool:
        """
        Check whether a site configuration exists.

        Args:
            domain: Domain name.

        Returns:
            True when the configuration file is present.
        """
        return self.config_path(domain).exists()

    def site_is_noust(self, domain: str) -> bool:
        """
        Report whether a site's configuration is one Noust wrote.

        A file without the marker is the operator's own (a repository's
        ``deploy/nginx.conf`` copied in, a hand-written site), which a deploy
        must not replace with its template.

        Args:
            domain: Domain name.

        Returns:
            True when the file exists and carries "Generated by Noust" (or WASM).
        """
        try:
            head = self.config_path(domain).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return _is_noust_text(head)

    def site_enabled(self, domain: str) -> bool:
        """
        Check whether a site is enabled.

        Args:
            domain: Domain name.

        Returns:
            True when the site is enabled.
        """
        link = self._link_path(domain)
        # A dangling symlink is still an enabled site as far as the web server
        # is concerned, and Path.exists() follows the link, so it would say no.
        return link.exists() or link.is_symlink()

    def list_sites(self) -> list[SiteInfo]:
        """
        List the sites this backend serves.

        Returns:
            One record per virtual host file Noust considers its own, in a stable
            alphabetical order.
        """
        sites: list[SiteInfo] = []
        if not self.sites_available.exists():
            return sites

        suffix = self.backend.config_suffix
        for config_file in sorted(self.sites_available.iterdir()):
            if not config_file.is_file():
                continue
            if suffix and config_file.suffix != suffix:
                continue
            domain = config_file.name[: -len(suffix)] if suffix else config_file.name
            if domain in self.backend.default_site_names:
                continue
            link = self.sites_enabled / config_file.name
            sites.append(
                SiteInfo(
                    domain=domain,
                    enabled=link.exists() or link.is_symlink(),
                    config_path=str(config_file),
                    webserver=self.backend.name,
                )
            )
        return sites

    # -- Rendering ---------------------------------------------------------

    def build_context(
        self, domain: str, context: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Merge caller-supplied template variables over the defaults.

        Args:
            domain: Domain name.
            context: Caller overrides.

        Returns:
            The full template context.
        """
        ctx: dict[str, Any] = {
            "domain": domain,
            "port": DEFAULT_PROXY_PORT,
            "app_path": f"/var/www/apps/{domain}",
            "ssl": False,
            "ssl_certificate": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
            "ssl_certificate_key": f"/etc/letsencrypt/live/{domain}/privkey.pem",
            "hsts": hsts_header(self.config),
        }
        if context:
            ctx.update(context)

        # ``server_names`` is what nginx is given verbatim; apache needs the
        # same list split into its ServerName and its ServerAliases, so both
        # are derived here from the one value rather than passed separately
        # and left to disagree.
        served = str(ctx.get("server_names") or domain).split()
        ctx["server_names"] = " ".join(served)
        ctx["server_aliases"] = [name for name in served if name != domain]
        # A name that is served and redirected at once would be two server
        # blocks claiming it; serving it is what the operator can see working.
        ctx["redirect_domains"] = [
            name for name in ctx.get("redirect_domains") or [] if name not in served
        ]
        return ctx

    def _check_names(self, ctx: Mapping[str, Any]) -> None:
        """
        Refuse a context naming anything that is not a domain.

        Every name lands in a ``server_name``, ``ServerAlias`` or redirect
        target directive of a file written as root, where a ``;`` or a newline
        ends the directive and starts one of the caller's choosing. The primary
        has always been checked by :meth:`config_path`; this checks the rest.

        Args:
            ctx: The full template context.

        Raises:
            DomainError: When a served or redirected name is not a domain.
        """
        # build_context already rejoined server_names on single spaces, so a
        # newline in it can no longer end a directive; what is left to refuse
        # is a token that is not a domain, such as ``evil.com;``.
        for name in [*ctx["server_names"].split(), *ctx["redirect_domains"]]:
            valid, reason = is_valid_domain(name)
            if not valid or name != name.strip():
                raise DomainError(
                    f"Invalid domain in the configuration of {ctx['domain']}: {name!r}",
                    details=f"{reason or 'Surrounding whitespace'}. Every name a site "
                    "answers on becomes a directive in its configuration file.",
                )

    def render_config(
        self,
        domain: str,
        template: str = "proxy",
        context: Mapping[str, Any] | None = None,
    ) -> str:
        """
        Render a virtual host configuration without writing anything.

        Rendering is separated from writing so the output can be asserted on in
        a test, which is what makes the templates reviewable at all.

        Args:
            domain: Domain name.
            template: Template name, without the ``.conf.j2`` suffix.
            context: Template variables, merged over the defaults.

        Returns:
            The rendered configuration.

        Raises:
            DomainError: When the domain is not a valid domain name.
            TemplateError: When the template is missing or fails to render.
        """
        # Validating here as well as in config_path keeps a caller that only
        # renders from smuggling a newline into a server_name directive.
        self.config_path(domain)
        ctx = self.build_context(domain, context)
        self._check_names(ctx)

        try:
            template_obj = self.jinja_env.get_template(f"{template}.conf.j2")
            return template_obj.render(**ctx)
        except TemplateNotFound as exc:
            raise TemplateError(
                f"Template not found: {template}.conf.j2",
                details=(
                    f"Available {self.backend.name} templates: "
                    f"{', '.join(self.list_templates()) or 'none'}."
                ),
            ) from exc
        except JinjaTemplateError as exc:
            raise TemplateError(
                f"Template rendering failed: {exc}",
                details=f"Template {template}.conf.j2 for {domain}.",
            ) from exc

    def list_templates(self) -> list[str]:
        """
        List the template names this backend offers.

        Returns:
            Template names without the ``.conf.j2`` suffix, sorted.
        """
        return sorted(
            name.removesuffix(".conf.j2")
            for name in self.jinja_env.list_templates()
            if name.endswith(".conf.j2")
        )

    # -- Site lifecycle ----------------------------------------------------

    def create_site(
        self,
        domain: str,
        template: str = "proxy",
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """
        Create a virtual host configuration.

        Args:
            domain: Domain name.
            template: Template name, without the ``.conf.j2`` suffix.
            context: Template variables, merged over the defaults.

        Returns:
            True when the configuration was written.

        Raises:
            NginxError: When an nginx site already exists or cannot be written.
            ApacheError: When an apache site already exists or cannot be
                written.
            DomainError: When the domain is not a valid domain name.
            DomainConflictError: When the domain is another application's
                alias or redirect.
            TemplateError: When the template is missing or fails to render.
        """
        require_server_role("Sites")
        if self.site_exists(domain):
            raise self.backend.error(
                f"Site already exists: {domain}",
                details=f"Use update_site() to change {self.config_path(domain)}.",
            )

        # A name that is another application's alias or redirect is already
        # in that application's server blocks; a second site claiming it is a
        # conflict nginx settles by file order, with a warning nobody reads.
        try:
            owner = self.store.domain_owner(domain.strip().lower())
        except (NoustError, sqlite3.Error) as exc:
            # No store to ask - a rehearsal on a machine that has none yet.
            self.logger.debug(f"Could not check who owns {domain}: {exc}")
            owner = None
        if owner is not None and owner[1] != DomainKind.PRIMARY.value:
            raise NoustStore.conflict(domain.strip().lower(), owner)

        for module in self.backend.required_modules:
            self.enable_module(module)

        return self._write_site(domain, template, context)

    def update_site(
        self,
        domain: str,
        template: str = "proxy",
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """
        Rewrite an existing virtual host configuration in place.

        The file is replaced atomically and the symlink is left alone, so a site
        is never briefly missing from the web server. The previous implementation
        deleted the site and recreated it, which also dropped its store record
        and its enabled state on the way through.

        Args:
            domain: Domain name.
            template: Template name, without the ``.conf.j2`` suffix.
            context: Template variables, merged over the defaults.

        Returns:
            True when the configuration was rewritten.

        Raises:
            NginxError: When the nginx site does not exist.
            ApacheError: When the apache site does not exist.
            DomainError: When the domain is not a valid domain name.
            TemplateError: When the template is missing or fails to render.
        """
        require_server_role("Sites")
        if not self.site_exists(domain):
            raise self.backend.error(
                f"Site does not exist: {domain}",
                details="Create it first with create_site().",
            )

        return self._write_site(domain, template, context)

    def _write_site(
        self,
        domain: str,
        template: str,
        context: Mapping[str, Any] | None,
    ) -> bool:
        """
        Render a configuration and put it on disk atomically.

        Args:
            domain: Domain name.
            template: Template name.
            context: Template variables.

        Returns:
            True when the file was written.

        Raises:
            NginxError: When the nginx configuration cannot be written.
            ApacheError: When the apache configuration cannot be written.
        """
        config_path = self.config_path(domain)
        names = self._application_names(domain.strip().lower())
        upstream = self._upstream_context(domain.strip().lower())
        ctx = self.build_context(domain, {**(context or {}), **names, **upstream})
        content = self.render_config(domain, template, ctx)

        try:
            # The seam writes through a sibling and renames, so a reload racing
            # this call sees either the old file or the new one, never half of
            # one - and a rehearsal writes neither, including the sibling.
            self.fs.write_text(config_path, content, mode=_CONFIG_MODE)
        except OSError as exc:
            raise self.backend.error(
                f"Failed to write configuration: {config_path}",
                details=str(exc),
            ) from exc

        self._record_site(domain, config_path, ctx)
        self.logger.debug(f"Wrote site configuration: {config_path}")
        return True

    def _application_names(self, domain: str) -> dict[str, Any]:
        """
        Read the names an application's site answers on, from the store.

        This is the one place those names reach a configuration file. The
        deployer, the certificate step and the site endpoints all rewrite the
        same vhost; if each brought its own list, whichever ran last would
        decide which aliases survive, and a redeploy that knew nothing about
        domains would quietly drop them all.

        Args:
            domain: Domain of the site being written.

        Returns:
            ``server_names`` and ``redirect_domains`` for the template when the
            domain is an application's, overriding whatever the caller passed;
            empty for a site that is no application's, which keeps the names
            it was given (``noust site create --www``), and for a store that
            cannot be read, which is reported.
        """
        try:
            records = self.store.list_domains(domain)
        except (NoustError, sqlite3.Error) as exc:
            # A rehearsal on a machine with no database yet has no rows to
            # read; anything worse has already failed whoever called this.
            self.logger.warning(f"Could not read the domains of {domain}: {exc}")
            return {}
        if not records:
            return {}
        served = [r.domain for r in records if r.kind != DomainKind.REDIRECT.value]
        redirects = [r.domain for r in records if r.kind == DomainKind.REDIRECT.value]
        return {"server_names": " ".join(served), "redirect_domains": redirects}

    def _upstream_context(self, domain: str) -> dict[str, Any]:
        """
        Say whether an application's site proxies to its blue/green upstream.

        The one place that decides it, from the store, for the same reason
        :meth:`_application_names` reads the names there: the deployer, the
        certificate step, the domain endpoints and the mode switch all
        rewrite the same site, and one of them rendering the direct
        ``proxy_pass`` would silently undo the mode. Only the proxy template
        uses these variables; every other site renders exactly as before.

        Args:
            domain: Domain of the site being written.

        Returns:
            ``upstream_name`` and ``upstream_file`` when the domain is an
            application in zero-downtime mode on this backend and its
            upstream file is in place; empty otherwise, including for a store
            that cannot be read. The file is looked at first: a site that
            includes a file that is not there is one nginx refuses, and the
            common case (no such file) then costs no query at all.
        """
        if self.backend.upstreams_dir is None or not is_valid_domain(domain)[0]:
            return {}
        path = self.upstream_path(domain)
        if path.is_symlink() or not path.is_file():
            return {}
        try:
            app = self.store.get_app(domain)
        except (NoustError, sqlite3.Error) as exc:
            self.logger.debug(f"Could not read {domain} from the store: {exc}")
            return {}
        if app is None or not app.zero_downtime:
            return {}
        return {
            "upstream_name": self.upstream_name(domain),
            "upstream_file": str(self.upstream_path(domain)),
        }

    # -- Blue/green upstreams ----------------------------------------------

    def _upstreams_dir(self) -> Path:
        """
        Return the directory of the upstream files.

        Returns:
            The backend's directory.

        Raises:
            SiteError: The backend has none (apache): blue/green is nginx-only.
        """
        if self.backend.upstreams_dir is None:
            raise self.backend.error(
                f"{self.backend.name} has no blue/green upstreams",
                details="Zero-downtime activation is nginx-only in WASM 2.2.",
            )
        return self.backend.upstreams_dir

    def upstream_name(self, domain: str) -> str:
        """
        Name the upstream block of an application's instances.

        Args:
            domain: The application's domain.

        Returns:
            ``wasm_bg_`` and the application name with underscores, a valid
            nginx identifier that no other site uses.
        """
        self.config_path(domain)
        return UPSTREAM_PREFIX + domain_to_app_name(domain.strip().lower()).replace("-", "_")

    def upstream_path(self, domain: str) -> Path:
        """
        Return the file that names the instance serving an application.

        Args:
            domain: The application's domain, validated before it becomes a path.

        Returns:
            ``<upstreams dir>/<app name>.conf``.

        Raises:
            DomainError: When the domain is not a valid domain name.
            SiteError: The backend has no upstreams.
        """
        self.config_path(domain)
        return self._upstreams_dir() / f"{domain_to_app_name(domain.strip().lower())}.conf"

    def render_upstream(self, domain: str, port: int) -> str:
        """
        Render the upstream file of an application.

        Args:
            domain: The application's domain.
            port: The port of the instance that serves.

        Returns:
            The file's content: one upstream with one server on loopback.
        """
        return (
            f"# Upstream of {domain.strip().lower()}: the blue/green instance that serves\n"
            f"# {paths.UNIT_MARKER}; rewritten on every activation.\n"
            f"upstream {self.upstream_name(domain)} {{\n"
            f"    server 127.0.0.1:{int(port)};\n"
            "}\n"
        )

    def read_upstream(self, domain: str) -> str | None:
        """
        Read the upstream file of an application as it is on disk.

        Args:
            domain: The application's domain.

        Returns:
            Its content, or None when there is none.
        """
        path = self.upstream_path(domain)
        if path.is_symlink() or not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            self.logger.debug(f"Could not read {path}: {exc}")
            return None

    def upstream_port(self, domain: str) -> int | None:
        """
        Read which port the upstream of an application points at.

        Args:
            domain: The application's domain.

        Returns:
            The port nginx proxies to, or None when there is no upstream file
            or it names no loopback server.
        """
        content = self.read_upstream(domain)
        if content is None:
            return None
        match = re.search(r"^\s*server\s+127\.0\.0\.1:(\d+)\s*;", content, re.MULTILINE)
        return int(match.group(1)) if match else None

    def write_upstream(self, domain: str, port: int) -> str | None:
        """
        Point an application's upstream at one port, atomically.

        Nothing is reloaded: the caller tests the configuration and reloads,
        and puts the previous content back with :meth:`restore_upstream`
        when the test fails.

        Args:
            domain: The application's domain.
            port: The port of the instance that is to serve.

        Returns:
            The previous content, or None when there was no file.

        Raises:
            SiteError: The file could not be written, or a symlink stands
                where it goes (a write through it would land anywhere).
        """
        require_server_role("Sites")
        path = self.upstream_path(domain)
        if path.is_symlink() or (path.parent.exists() and path.parent.is_symlink()):
            raise self.backend.error(
                f"Refusing to write {path}: it is a symlink",
                details=f"Remove the link; Noust writes the upstream of {domain} itself.",
            )
        previous = self.read_upstream(domain)
        try:
            self.fs.make_dir(path.parent)
            self.fs.write_text(path, self.render_upstream(domain, port), mode=_CONFIG_MODE)
        except OSError as exc:
            raise self.backend.error(
                f"Failed to write the upstream of {domain}: {path}", details=str(exc)
            ) from exc
        return previous

    def restore_upstream(self, domain: str, previous: str | None) -> None:
        """
        Put an upstream file back as it was.

        Args:
            domain: The application's domain.
            previous: What :meth:`write_upstream` returned: the old content,
                or None to remove the file.

        Raises:
            SiteError: The file could not be written or removed.
        """
        path = self.upstream_path(domain)
        try:
            if previous is None:
                self.fs.remove(path, missing_ok=True)
            else:
                self.fs.write_text(path, previous, mode=_CONFIG_MODE)
        except OSError as exc:
            raise self.backend.error(
                f"Failed to restore the upstream of {domain}: {path}", details=str(exc)
            ) from exc

    def _remove_servers_files(self, domain: str) -> None:
        """
        Remove the servers files of an application whose site is gone.

        Tidying up is not a reason to fail a deletion that has already removed
        the site, and skipping the reload that follows it would leave nginx
        serving the site that was just deleted, so a refusal is logged.

        Args:
            domain: The application's domain.
        """
        if self.backend.upstreams_dir is None:
            return
        app_name = domain_to_app_name(domain.strip().lower())
        # Most sites never had any; looking first keeps their deletion free of
        # the validation (and its warning) for a name that is not an app's.
        if not os.path.lexists(self.backend.upstreams_dir / app_name):
            return
        try:
            remove_servers(app_name, upstreams_dir=self.backend.upstreams_dir)
        except NoustError as exc:
            self.logger.warning(f"Left the servers files of {domain} in place: {exc}")

    def remove_upstream(self, domain: str) -> bool:
        """
        Remove an application's upstream file, if it has one.

        Call it only once no site includes it any more: nginx refuses a
        configuration that includes a file that is not there.

        Args:
            domain: The application's domain.

        Returns:
            True when a file was removed.
        """
        if self.backend.upstreams_dir is None:
            return False
        path = self.upstream_path(domain)
        if not os.path.lexists(path):
            return False
        try:
            self.fs.remove(path, missing_ok=True)
        except OSError as exc:
            raise self.backend.error(
                f"Failed to remove the upstream of {domain}: {path}", details=str(exc)
            ) from exc
        return True

    def _record_site(self, domain: str, config_path: Path, ctx: Mapping[str, Any]) -> None:
        """
        Register or refresh the site in the store.

        The store is a cache of what is on disk, so a failure to update it is
        logged and swallowed: the configuration file is the source of truth and
        it has already been written.

        Args:
            domain: Domain name.
            config_path: Path of the configuration file.
            ctx: The template context the file was rendered from.
        """
        ssl_enabled = bool(ctx.get("ssl", False))
        proxy_port = _as_port(ctx.get("port"))
        certificate = (
            str(ctx["ssl_certificate"]) if ssl_enabled and ctx.get("ssl_certificate") else None
        )
        key = (
            str(ctx["ssl_certificate_key"])
            if ssl_enabled and ctx.get("ssl_certificate_key")
            else None
        )

        try:
            existing = self.store.get_site(domain)
            if existing is not None:
                existing.webserver = self.backend.webserver_record
                existing.config_path = str(config_path)
                existing.proxy_port = proxy_port
                existing.ssl_enabled = ssl_enabled
                existing.ssl_certificate = certificate
                existing.ssl_key = key
                self.store.update_site(existing)
                return

            self.store.create_site(
                Site(
                    domain=domain,
                    webserver=self.backend.webserver_record,
                    config_path=str(config_path),
                    proxy_port=proxy_port,
                    ssl_enabled=ssl_enabled,
                    ssl_certificate=certificate,
                    ssl_key=key,
                    enabled=self.site_enabled(domain),
                )
            )
        except (NoustError, sqlite3.Error) as exc:
            self.logger.debug(f"Could not register site in store: {exc}")

    def enable_site(self, domain: str) -> bool:
        """
        Enable a site.

        Args:
            domain: Domain name.

        Returns:
            True when the site is enabled, including when it already was.

        Raises:
            NginxError: When the nginx site does not exist or cannot be enabled.
            ApacheError: When the apache site does not exist or cannot be
                enabled.
        """
        require_server_role("Sites")
        if not self.site_exists(domain):
            raise self.backend.error(
                f"Site does not exist: {domain}",
                details=f"Expected {self.config_path(domain)}.",
            )

        if self.site_enabled(domain):
            self.logger.debug(f"Site already enabled: {domain}")
            return True

        link = self._link_path(domain)
        program = self.backend.enable_site_program
        if program is None:
            try:
                self.fs.make_dir(link.parent)
                self.fs.symlink(self.config_path(domain), link)
            except OSError as exc:
                raise self.backend.error(
                    f"Failed to enable site: {domain}",
                    details=str(exc),
                ) from exc
        else:
            result = self._run([program, link.name], timeout=_CONTROL_TIMEOUT)
            if not result.success:
                raise self.backend.error(
                    f"Failed to enable site: {domain}",
                    details=result.stderr or result.stdout,
                )

        self._record_enabled(domain, True)
        self.logger.debug(f"Enabled site: {domain}")
        return True

    def disable_site(self, domain: str) -> bool:
        """
        Disable a site.

        Args:
            domain: Domain name.

        Returns:
            True when the site is disabled, including when it already was.

        Raises:
            NginxError: When the nginx site cannot be disabled.
            ApacheError: When the apache site cannot be disabled.
        """
        if not self.site_enabled(domain):
            self.logger.debug(f"Site already disabled: {domain}")
            return True

        link = self._link_path(domain)
        program = self.backend.disable_site_program
        if program is None:
            try:
                self.fs.remove(link, missing_ok=True)
            except OSError as exc:
                raise self.backend.error(
                    f"Failed to disable site: {domain}",
                    details=str(exc),
                ) from exc
        else:
            result = self._run([program, link.name], timeout=_CONTROL_TIMEOUT)
            if not result.success:
                raise self.backend.error(
                    f"Failed to disable site: {domain}",
                    details=result.stderr or result.stdout,
                )

        self._record_enabled(domain, False)
        self.logger.debug(f"Disabled site: {domain}")
        return True

    def _record_enabled(self, domain: str, enabled: bool) -> None:
        """
        Record the enabled state of a site in the store.

        Args:
            domain: Domain name.
            enabled: New state.
        """
        try:
            site = self.store.get_site(domain)
            if site is not None:
                site.enabled = enabled
                self.store.update_site(site)
        except (NoustError, sqlite3.Error) as exc:
            self.logger.debug(f"Could not update site in store: {exc}")

    def delete_site(self, domain: str) -> bool:
        """
        Delete a site configuration.

        Args:
            domain: Domain name.

        Returns:
            True when nothing is left on disk for this domain.

        Raises:
            NginxError: When the nginx configuration cannot be removed.
            ApacheError: When the apache configuration cannot be removed.
        """
        if self.site_enabled(domain):
            self.disable_site(domain)

        config_path = self.config_path(domain)
        try:
            self.fs.remove(config_path, missing_ok=True)
        except OSError as exc:
            raise self.backend.error(
                f"Failed to delete site: {domain}",
                details=str(exc),
            ) from exc
        # The upstream of a blue/green application belongs to its site: once
        # nothing includes it, it is only a file nginx does not read.
        self.remove_upstream(domain)
        self._remove_servers_files(domain)

        try:
            self.store.delete_site(domain)
        except (NoustError, sqlite3.Error) as exc:
            self.logger.debug(f"Could not remove site from store: {exc}")

        self.logger.debug(f"Deleted site: {domain}")
        return True

    def served_names(self, domain: str) -> list[str]:
        """
        Read which names a site's configuration answers on.

        The file is the only record of what a 1.x deploy served: ``include_www``
        was never stored anywhere else.

        Args:
            domain: Domain of the site.

        Returns:
            Every domain named by a ``server_name`` (nginx) or ``ServerName``
            / ``ServerAlias`` (apache) directive, lowercased, in file order,
            once each. Catch-alls, wildcards and anything else that is not a
            domain are left out. Empty when the site does not exist.
        """
        if not self.site_exists(domain):
            return []
        return self.names_in(self.get_site_config(domain) or "")

    def names_in(self, text: str) -> list[str]:
        """
        Read which names a configuration's text answers on.

        Split from :meth:`served_names` so a listing that has already read the
        file does not read it again by a name that may not even be a domain.

        Args:
            text: A virtual host configuration.

        Returns:
            What :meth:`served_names` returns for a file with this content.
        """
        names: list[str] = []
        for match in self.backend.server_name_pattern.finditer(text):
            for token in match.group(1).split():
                name = token.lower()
                if name not in names and is_valid_domain(name)[0]:
                    names.append(name)
        return names

    def get_site_config(self, domain: str) -> str | None:
        """
        Read a site configuration.

        Args:
            domain: Domain name.

        Returns:
            The file content, or None when the site does not exist or cannot be
            read.
        """
        config_path = self.config_path(domain)
        try:
            return config_path.read_text()
        except OSError as exc:
            self.logger.debug(f"Could not read {config_path}: {exc}")
            return None

    # -- Validating a configuration without installing it --------------------

    def test_config_text(self, config_text: str, *, domain: str) -> tuple[bool, str]:
        """
        Ask the web server whether it would accept a configuration snippet.

        The snippet is staged into a throwaway directory through the
        filesystem seam, together with a minimal main configuration that
        includes it, and the backend's own syntax checker runs against that
        wrapper. The live configuration is never touched and nothing staged
        outlives this call, whichever way it answers - a "try before you
        save" caller and :meth:`validate_config_text` share this one
        implementation of that instead of each staging its own copy.

        Args:
            config_text: The virtual host configuration to check.
            domain: Domain the configuration is meant for. Validated the same
                way as everywhere else before it names a staged file.

        Returns:
            Whether the server accepted the snippet, and its own output
            verbatim. The output is not empty on a pass either: nginx and
            apache2ctl both print a confirmation ("syntax is ok" / "Syntax
            OK") even when there is nothing wrong.

        Raises:
            NginxError: When the nginx snippet cannot be staged.
            ApacheError: When the apache snippet cannot be staged.
            DomainError: When the domain is not a valid domain name.
        """
        snippet_name = self.config_path(domain).name
        # A random directory name for the same reason the filesystem seam uses
        # a random sibling: a predictable path in a world-writable directory is
        # a symlink an attacker can plant, and this code runs as root.
        staging = Path(tempfile.gettempdir()) / f"wasm-validate-{os.urandom(6).hex()}"
        snippet = staging / snippet_name
        wrapper = staging / "wasm-validate.conf"
        wrapper_text = Template(self.backend.validation_wrapper).substitute(
            snippet=str(snippet),
            server_root=str(self.backend.sites_available.parent),
        )

        try:
            try:
                self.fs.write_text(snippet, config_text, mode=_CONFIG_MODE)
                self.fs.write_text(wrapper, wrapper_text, mode=_CONFIG_MODE)
            except OSError as exc:
                raise self.backend.error(
                    f"Could not stage the configuration of {domain} for validation",
                    details=str(exc),
                ) from exc
            result = self._run(
                [*self.backend.validation_argv, str(wrapper)], timeout=_CONTROL_TIMEOUT
            )
        finally:
            if staging.exists():
                self.fs.remove_tree(staging)

        output = "\n".join(stream for stream in (result.stderr, result.stdout) if stream.strip())
        # The same tolerance test_config() needs: apache2ctl exits non-zero on
        # warnings it then describes as "Syntax OK".
        ok = result.success or "Syntax OK" in f"{result.stdout}\n{result.stderr}"
        return ok, output

    def validate_config_text(self, config_text: str, *, domain: str) -> None:
        """
        Ask the web server whether it would accept a configuration snippet.

        Raises rather than answering, for the caller about to install the
        text and needing to stop if it is refused. Built on
        :meth:`test_config_text`, which a caller that only wants to preview
        the answer - never installing anything - calls directly.

        Args:
            config_text: The virtual host configuration to check.
            domain: Domain the configuration is meant for.

        Returns:
            None. Returning at all means the server accepted the snippet.

        Raises:
            ValidationError: When the server rejects the snippet. ``details``
                and ``output`` both carry the server's own output verbatim.
            NginxError: When the nginx snippet cannot be staged.
            ApacheError: When the apache snippet cannot be staged.
            DomainError: When the domain is not a valid domain name.
        """
        ok, output = self.test_config_text(config_text, domain=domain)
        if ok:
            return

        raise ValidationError(
            f"{self.backend.name} rejected the configuration for {domain}",
            details=output,
            output=output,
        )

    def replace_site_config(self, domain: str, config_text: str, *, validate: bool = True) -> Path:
        """
        Validate a hand-edited configuration and install it atomically.

        Args:
            domain: Domain of the site.
            config_text: The new configuration, written verbatim.
            validate: Check it with the web server first. False is only for
                putting back a configuration that was live a moment ago, when
                what just replaced it was refused.

        Returns:
            The path of the configuration file that was replaced.

        Raises:
            NginxError: When the nginx site does not exist or cannot be
                written.
            ApacheError: When the apache site does not exist or cannot be
                written.
            ValidationError: When the server rejects the configuration. The
                file on disk is left exactly as it was.
            DomainError: When the domain is not a valid domain name.
        """
        require_server_role("Sites")
        if not self.site_exists(domain):
            raise self.backend.error(
                f"Site does not exist: {domain}",
                details="Create it first with create_site().",
            )

        if validate:
            self.validate_config_text(config_text, domain=domain)

        config_path = self.config_path(domain)
        try:
            # The seam writes through a sibling and renames, so a reload racing
            # this call sees either the old configuration or the new one.
            self.fs.write_text(config_path, config_text, mode=_CONFIG_MODE)
        except OSError as exc:
            raise self.backend.error(
                f"Failed to write configuration: {config_path}",
                details=str(exc),
            ) from exc

        self.logger.debug(f"Replaced site configuration: {config_path}")
        return config_path


# -- Servers files ---------------------------------------------------------
#
# What a Compose service's site proxies to, when the relay moves traffic between
# two containers, is a file of ``server 127.0.0.1:<port>;`` lines that the site
# includes inside an ``upstream`` block. Nothing else is in it, so the same
# file can be included from a block Noust wrote (``wasm_bg_<app>_<service>``)
# or from one the operator wrote with names of their own. They live under
# ``/etc/nginx/noust-upstreams/<app>/<service>.servers``, next to (and never
# named like) the single ``<app>.conf`` upstream of blue/green activation.
#
# Module functions rather than manager methods because the relay is driven by
# the deployer and knows an application and a service, not a web server.

#: A server line as a servers file holds it. Anything else on the line (a
#: trailing comment) is ignored; a commented-out line does not start with
#: ``server`` and so never matches.
_SERVER_LINE = re.compile(r"^\s*server\s+127\.0\.0\.1:(\d+)\s*;", re.MULTILINE)

#: A comment as nginx reads one: ``#`` where a token starts, to the end of line.
_NGINX_COMMENT = re.compile(r"(?:^|(?<=\s))#.*$", re.MULTILINE)

#: An ``include`` directive, bare or quoted, at the start of a directive: after
#: another one (``;``), a block's opening or closing brace, or the file's start.
#: Matching the word anywhere would take ``add_header X include;`` for one.
_INCLUDE_DIRECTIVE = re.compile(
    r"""(?:\A|[;{}])\s*include\s+(?:"([^"\n]*)"|'([^'\n]*)'|([^\s;{}"']+))\s*;"""
)

#: Characters that make an ``include`` argument a pattern nginx expands.
_GLOB_CHARACTERS = frozenset("*?[")


def _servers_directory(base: Path, app_name: str) -> Path:
    """
    Resolve an application's directory of servers files, contained in ``base``.

    Args:
        base: The upstreams directory.
        app_name: The application's name (its domain with dots as dashes).

    Returns:
        ``base/<app_name>``, unresolved, so a caller can tell a link from a
        directory.

    Raises:
        ValidationError: When the name is not one inert path component.
        SecurityError: When the directory is a link that leaves ``base``.
    """
    validate_app_name(app_name)
    resolve_within(base, app_name)
    return base / app_name


def _servers_path(base: Path, app_name: str, service: str) -> Path:
    """
    Resolve one service's servers file.

    Args:
        base: The upstreams directory.
        app_name: The application's name.
        service: The Compose service's name.

    Returns:
        ``base/<app_name>/<service>.servers``, unresolved. The file itself may
        be a link; each caller refuses to follow it in its own words.

    Raises:
        ValidationError: When either name is not one inert path component.
        SecurityError: When the application's directory is a link that leaves
            ``base``.
    """
    return _servers_directory(base, app_name) / validate_filename(f"{service}{SERVERS_SUFFIX}")


def servers_file(app_name: str, service: str) -> Path:
    """
    Say where the servers of one service of an application are written.

    The path is also what a site must ``include``, so callers that tell the
    operator which line to add use this and not a string of their own.

    Args:
        app_name: The application's name: its domain with dots as dashes.
        service: The Compose service's name.

    Returns:
        ``<upstreams dir>/<app_name>/<service>.servers``.

    Raises:
        ValidationError: When either name is not a single, inert path
            component. They become a path written as root.
        SecurityError: When the application's directory is a link that leaves
            the upstreams directory.
    """
    return _servers_path(NGINX_UPSTREAMS_DIR, app_name, service)


def _checked_ports(ports: Sequence[int]) -> list[int]:
    """
    Validate the ports of a servers file.

    Args:
        ports: The candidates.

    Returns:
        The same ports, as a list.

    Raises:
        ValidationError: When there is none, or one is not an integer between
            1 and 65535 (a bool and a string that looks like a number are not).
    """
    if not ports:
        raise ValidationError(
            "A servers file needs at least one port",
            details=(
                "nginx refuses an upstream with no servers. Remove the file with "
                "remove_servers() when a service has nothing to serve."
            ),
        )
    for port in ports:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValidationError(
                f"Not a port: {port!r}",
                details="A servers file lists integer ports from 1 to 65535.",
            )
    return list(ports)


def write_servers(app_name: str, service: str, ports: list[int]) -> None:
    """
    Point a service's servers file at the given loopback ports, atomically.

    The file is replaced through a sibling and a rename, so a reload racing
    this call reads the old servers or the new ones, never half of either.
    Nothing is reloaded: the caller tests the configuration and reloads, and
    writes the previous ports back when the test fails.

    Args:
        app_name: The application's name.
        service: The Compose service's name.
        ports: The ports to serve from, in order; one line each.

    Raises:
        ValidationError: When a name or a port is not acceptable. Nothing has
            been written.
        SecurityError: When a link would take the file out of the tree.
        NginxError: When a symlink stands where the file or its directory goes
            (a write through it would land anywhere, as root), or the file
            cannot be written.
        RoleError: When this Noust is a hub, which writes no sites.
    """
    require_server_role("Sites")
    path = servers_file(app_name, service)
    lines = "".join(f"server 127.0.0.1:{port};\n" for port in _checked_ports(ports))

    if path.is_symlink() or path.parent.is_symlink():
        raise NginxError(
            f"Refusing to write {path}: it is, or is inside, a symlink",
            details=f"Remove the link; Noust writes the servers of {service} itself.",
        )
    fs = get_fs()
    try:
        fs.make_dir(path.parent)
        fs.write_text(path, lines, mode=_CONFIG_MODE)
    except OSError as exc:
        raise NginxError(
            f"Failed to write the servers of {service} of {app_name}: {path}", details=str(exc)
        ) from exc


def read_servers(app_name: str, service: str) -> list[int]:
    """
    Read which ports a service's servers file points at.

    Args:
        app_name: The application's name.
        service: The Compose service's name.

    Returns:
        The ports in file order; empty when there is no file, it is a link
        (never read through one), or it cannot be read. Comments and anything
        that is not a loopback ``server`` line are ignored.

    Raises:
        ValidationError: When a name is not acceptable.
        SecurityError: When a link would take the path out of the tree.
    """
    path = servers_file(app_name, service)
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning("Could not read the servers file %s: %s", path, exc)
        return []
    ports = (int(match.group(1)) for match in _SERVER_LINE.finditer(text))
    return [port for port in ports if 1 <= port <= 65535]


def remove_servers(
    app_name: str, service: str | None = None, *, upstreams_dir: Path | None = None
) -> bool:
    """
    Remove one servers file of an application, or all of them.

    Call it only once no site includes them any more: nginx refuses a
    configuration that includes a file that is not there.

    Args:
        app_name: The application's name.
        service: The service whose file to remove; None removes the
            application's whole directory.
        upstreams_dir: The upstreams directory, for a manager bound to a tree
            other than the system's. The system's by default.

    Returns:
        True when something was removed (or, in a dry run, would have been).

    Raises:
        ValidationError: When a name is not acceptable.
        SecurityError: When a link would take the path out of the tree.
        NginxError: When the application's directory is a link, which is
            never followed, or the removal fails.
    """
    base = NGINX_UPSTREAMS_DIR if upstreams_dir is None else upstreams_dir
    directory = _servers_directory(base, app_name)
    if directory.is_symlink():
        raise NginxError(
            f"Refusing to remove {directory}: it is a symlink",
            details="Remove the link by hand; Noust does not follow it.",
        )
    target = directory if service is None else _servers_path(base, app_name, service)
    if not os.path.lexists(target):
        return False
    fs = get_fs()
    try:
        if service is None:
            fs.remove_tree(directory)
        else:
            fs.remove(target)
    except OSError as exc:
        raise NginxError(f"Failed to remove {target}", details=str(exc)) from exc
    return True


def _strip_nginx_comments(text: str) -> str:
    """
    Drop the comments of an nginx configuration.

    Args:
        text: The configuration.

    Returns:
        The text without them; lines keep their breaks.
    """
    return _NGINX_COMMENT.sub("", text)


def _include_reaches(argument: str, wanted: PurePosixPath) -> bool:
    """
    Report whether an ``include`` argument names a file, the way nginx reads it.

    Args:
        argument: What follows ``include``, quotes removed.
        wanted: The file's absolute path.

    Returns:
        True when the argument is that path, relative to the nginx prefix or
        absolute, or a wildcard nginx would expand to it. ``*`` and ``?`` match
        within one directory level, as they do for nginx.
    """
    included = PurePosixPath(argument)
    if not included.is_absolute():
        # nginx resolves a relative include against its configuration prefix.
        included = PurePosixPath(NGINX_SITES_AVAILABLE.parent) / included
    if included == wanted:
        return True
    return _GLOB_CHARACTERS.intersection(argument) != set() and wanted.match(str(included))


def site_includes_servers(site: str, path: Path) -> bool:
    """
    Tell whether a site's configuration includes a servers file.

    The check behind "activating the relay needs this line in your site": an
    operator's site that does not include the file would go on serving the old
    container however often the file is rewritten.

    Args:
        site: The site's configuration text, as read from its file.
        path: The servers file (:func:`servers_file`).

    Returns:
        True when an ``include`` directive, anywhere in the text (inside an
        ``upstream`` block or outside), names the file: bare or in single or
        double quotes, by absolute path or relative to ``/etc/nginx``, or by a
        wildcard nginx would expand to it. A commented-out include does not
        count, nor does a different file that merely starts with the same
        name.
    """
    wanted = PurePosixPath(path)
    for match in _INCLUDE_DIRECTIVE.finditer(_strip_nginx_comments(site)):
        argument = next(group for group in match.groups() if group is not None)
        if _include_reaches(argument, wanted):
            return True
    return False


# -- Cross-backend orchestration -------------------------------------------
#
# The two functions below are the chokepoints for "delete a site" and
# "create a secured site". Each used to be written once per caller - the CLI's
# ``site delete``, the CLI's application delete, the panel's site delete - and
# only one of the three checked both nginx and apache, so a site created on
# the backend a given path did not check outlived every deletion path that
# was not the one it happened to use. Same story for "create with SSL": the
# panel rendered ``ssl_certificate`` into the vhost before any certificate had
# been asked for, because writing the vhost and obtaining the certificate were
# two separate call sites that had drifted apart.
#
# Both functions take already-built managers rather than constructing their
# own from scratch: every caller already builds its managers through names it
# owns and that its own tests patch (the CLI through module-level imports, the
# web API through its ``MANAGERS`` registry), and building fresh managers here
# instead would silently stop honouring that.


@dataclass(frozen=True)
class SiteDeletion(MappingRecord):
    """
    What deleting a site actually found and removed.

    Attributes:
        domain: Domain that was targeted.
        nginx_removed: Whether an nginx vhost was found and removed.
        apache_removed: Whether an apache vhost was found and removed.
        certificate_removed: Whether a certificate was found and removed.
        kept_operator: The web servers whose site for the domain was written
            by the operator and kept, when that was asked for.
    """

    domain: str
    nginx_removed: bool = False
    apache_removed: bool = False
    certificate_removed: bool = False
    kept_operator: tuple[str, ...] = ()

    @property
    def removed_anything(self) -> bool:
        """True when at least one vhost or the certificate was removed."""
        return self.nginx_removed or self.apache_removed or self.certificate_removed


def delete_site_completely(
    domain: str,
    *,
    nginx: WebServerManager | None = None,
    apache: WebServerManager | None = None,
    cert_manager: CertManager | None = None,
    delete_certificate: bool = True,
    verbose: bool = False,
    keep_operator_sites: bool = False,
) -> SiteDeletion:
    """
    Remove a domain's virtual host from every backend, and its certificate.

    Each step is independent and best-effort: a web server or certbot failure
    on one step is logged and does not stop the others from being attempted,
    the same tolerance :meth:`ServiceManager.delete_service` already applies
    to stopping and disabling a unit before removing its file. A deletion that
    aborted on the first failure used to leave the other backend's vhost, or
    the certificate, behind.

    Args:
        domain: Domain to remove.
        nginx: Nginx-backed manager to use. Defaults to a fresh one bound to
            the real configuration tree; callers under test inject one bound
            to a sandbox.
        apache: Apache-backed manager to use, same default rule.
        cert_manager: Certificate manager to use, same default rule.
        delete_certificate: Also remove the certificate. False leaves it in
            place, for a caller that only wants the vhosts gone.
        verbose: Enable verbose logging on any manager built by default.
        keep_operator_sites: Leave a site the operator wrote (one without
            Noust's marker) in place, and the certificate with it, since it
            may use it. Deleting an application asks for this; ``noust site
            delete`` does not, being the explicit way to remove such a site.

    Returns:
        What was actually found and removed.
    """
    from noust.deployers.helpers.site import is_operator_site

    nginx = nginx or WebServerManager(NGINX_BACKEND, verbose=verbose)
    apache = apache or WebServerManager(APACHE_BACKEND, verbose=verbose)
    cert_manager = cert_manager or CertManager(verbose=verbose)

    kept_operator = (
        tuple(
            manager.backend.name
            for manager in (nginx, apache)
            if is_operator_site(manager, domain)
        )
        if keep_operator_sites
        else ()
    )

    nginx_removed = False
    if nginx.site_exists(domain) and nginx.backend.name not in kept_operator:
        try:
            nginx.delete_site(domain)
            nginx.reload()
            nginx_removed = True
        except SiteError as exc:
            _logger.warning("Could not remove the nginx site for %s: %s", domain, exc)

    apache_removed = False
    if apache.site_exists(domain) and apache.backend.name not in kept_operator:
        try:
            apache.delete_site(domain)
            apache.reload()
            apache_removed = True
        except SiteError as exc:
            _logger.warning("Could not remove the apache site for %s: %s", domain, exc)

    certificate_removed = False
    if (
        delete_certificate
        and not kept_operator
        and cert_manager.is_installed()
        and cert_manager.cert_exists(domain)
    ):
        try:
            cert_manager.delete(domain)
            certificate_removed = True
        except CertificateError as exc:
            _logger.warning("Could not remove the certificate for %s: %s", domain, exc)

    return SiteDeletion(
        domain=domain,
        nginx_removed=nginx_removed,
        apache_removed=apache_removed,
        certificate_removed=certificate_removed,
        kept_operator=kept_operator,
    )


@dataclass(frozen=True)
class SecuredSite(MappingRecord):
    """
    What :func:`create_secured_site` wrote and whether TLS ended up enabled.

    Attributes:
        domain: Domain that was configured.
        webserver: Backend that now serves it.
        site_existed: Whether the vhost already existed and was updated
            rather than created.
        ssl_requested: Whether TLS was asked for.
        ssl_enabled: Whether the site ended up serving TLS. False whenever
            ``ssl_requested`` is False, and also when it was requested but
            issuance failed - the site still exists, over plain HTTP.
        certificate_reused: Whether an existing, valid certificate already
            covered every requested domain, so nothing was issued.
        certificate_error: Why TLS was not enabled, when it was requested and
            did not end up enabled. None otherwise.
    """

    domain: str
    webserver: str
    site_existed: bool = False
    ssl_requested: bool = False
    ssl_enabled: bool = False
    certificate_reused: bool = False
    certificate_error: str | None = None


def create_secured_site(
    domain: str,
    *,
    manager: WebServerManager,
    webserver: str,
    cert_manager: CertManager | None = None,
    template: str = "proxy",
    port: int = DEFAULT_PROXY_PORT,
    www: bool = False,
    ssl: bool = True,
    enable: bool = True,
) -> SecuredSite:
    """
    Create or update a virtual host and, unless told not to, secure it with TLS.

    The vhost is always written without TLS first: certbot's nginx and apache
    plugins, and the webroot fallback, all need a plain HTTP vhost in place to
    validate the domain against. Only once a certificate is confirmed - reused
    or freshly obtained - is the vhost rewritten with the certificate paths
    and reloaded. ``POST /api/sites`` used to render ``ssl_certificate`` into
    the vhost from the request's ``ssl`` flag alone, before any certificate
    had been asked for, which is a config nginx then refused to reload.

    Args:
        domain: Domain to serve.
        manager: Web server manager to write the vhost through.
        webserver: Name of the backend ``manager`` drives (``nginx`` or
            ``apache``), used to pick the matching certbot plugin. Not read
            off ``manager`` itself, so a caller's own manager double does not
            need to carry a ``backend`` attribute.
        cert_manager: Certificate manager to use when ``ssl`` is true.
            Defaults to a fresh one.
        template: Template name, without the ``.conf.j2`` suffix.
        port: Port the application listens on behind the proxy.
        www: Also serve and certify ``www.<domain>``.
        ssl: Secure the site with a certificate. False writes a plain HTTP
            vhost and does nothing else.
        enable: Enable the site once written, when it did not already exist.

    Returns:
        What was written and whether TLS ended up enabled.

    Raises:
        DomainError: When the domain is not a valid domain name.
        DomainConflictError: When the domain is another application's alias
            or redirect (raised by ``create_site``).
        NginxError: When the nginx configuration cannot be written.
        ApacheError: When the apache configuration cannot be written.
        TemplateError: When the template is missing or fails to render.
    """
    include_www = www and should_include_www(domain)
    server_names = f"{domain} www.{domain}" if include_www else domain
    context: dict[str, Any] = {"port": port, "ssl": False, "server_names": server_names}

    site_existed = manager.site_exists(domain)
    if site_existed:
        manager.update_site(domain, template=template, context=context)
    else:
        manager.create_site(domain, template=template, context=context)
        if enable:
            manager.enable_site(domain)
    manager.reload()

    if not ssl:
        return SecuredSite(
            domain=domain,
            webserver=webserver,
            site_existed=site_existed,
            ssl_requested=False,
        )

    cert_manager = cert_manager or CertManager()
    if not cert_manager.is_installed():
        return SecuredSite(
            domain=domain,
            webserver=webserver,
            site_existed=site_existed,
            ssl_requested=True,
            certificate_error="certbot is not installed",
        )

    additional_domains = [f"www.{domain}"] if include_www else None
    required_domains = [domain, *(additional_domains or [])]

    certificate_reused = False
    if cert_manager.cert_exists(domain):
        test = cert_manager.test_cert(domain)
        if test.get("valid") and cert_manager.cert_covers_domains(domain, required_domains):
            certificate_reused = True

    certificate_error: str | None = None
    if not certificate_reused:
        try:
            cert_manager.obtain(
                domain,
                nginx=webserver == "nginx",
                apache=webserver == "apache",
                additional_domains=additional_domains,
            )
        except NoustError as exc:
            certificate_error = str(exc)

    if certificate_error is not None:
        return SecuredSite(
            domain=domain,
            webserver=webserver,
            site_existed=site_existed,
            ssl_requested=True,
            certificate_error=certificate_error,
        )

    cert_paths = cert_manager.get_cert_path(domain)
    context["ssl"] = True
    context["ssl_certificate"] = str(cert_paths["fullchain"])
    context["ssl_certificate_key"] = str(cert_paths["privkey"])
    manager.update_site(domain, template=template, context=context)
    manager.reload()

    return SecuredSite(
        domain=domain,
        webserver=webserver,
        site_existed=site_existed,
        ssl_requested=True,
        ssl_enabled=True,
        certificate_reused=certificate_reused,
    )


# -- The sites list --------------------------------------------------------


@dataclass(frozen=True)
class SiteRow:
    """
    One site of the server, whoever wrote it.

    Attributes:
        name: The file in the sites directory (``proggest``), without the
            backend's suffix; what the operator sees on disk.
        domain: The domain the site is addressed by: its application's, else
            the one the store recorded, else the file's name. What the API and
            the console call the site.
        webserver: Backend that owns the file, ``nginx`` or ``apache``.
        noust_managed: Whether the file carries Noust's marker, that is, Noust
            wrote it. False for the operator's own, which a deploy leaves
            alone.
        app: Domain of the application the file serves, or None when none
            records it.
        enabled: Whether the site is enabled in the web server.
        config_path: Absolute path of the configuration file.
        has_ssl: Whether the configuration carries certificate directives.
        server_names: Every name the configuration answers on, in file order.
    """

    name: str
    domain: str
    webserver: str
    noust_managed: bool
    app: str | None
    enabled: bool = False
    config_path: str = ""
    has_ssl: bool = False
    server_names: tuple[str, ...] = ()


def _recorded_site_name(site: Site, suffix: str) -> str:
    """
    Name the file a store record says holds its site.

    Args:
        site: The store's record.
        suffix: The backend's file suffix, removed from the name.

    Returns:
        The file name without the suffix; the domain when the record has no
        path.
    """
    name = Path(site.config_path).name if site.config_path else site.domain
    return name.removesuffix(suffix) if suffix else name


def list_all_sites(
    *,
    managers: Sequence[WebServerManager] | None = None,
    store: NoustStore | None = None,
) -> list[SiteRow]:
    """
    List every site on the server: what the store knows and what is on disk.

    The sites directory is the truth about what the web server serves and the
    store is a cache of what Noust wrote, so each file is a row whoever wrote
    it. A listing built from the store alone lost every site the operator
    wrote by hand the moment Noust had written one of its own. A store record
    whose file is gone is still listed, because a site that vanished is
    something to be told about.

    Args:
        managers: The web servers to list. Both backends by default; a caller
            passes the ones it already built (tests, the API's registry).
        store: The store to ask which application serves which file. The
            process-wide one by default.

    Returns:
        One row per site, ordered by domain, then web server, then file.
    """
    store = store or get_store()
    managers = (
        managers
        if managers is not None
        else [WebServerManager(NGINX_BACKEND), WebServerManager(APACHE_BACKEND)]
    )
    by_record = {manager.backend.webserver_record: manager for manager in managers}

    # An application's site is the file its site_name says, else its domain.
    owners = {(app.webserver, app.site_name or app.domain): app.domain for app in store.list_apps()}
    recorded: dict[tuple[str, str], Site] = {}
    for site in store.list_sites():
        manager = by_record.get(site.webserver)
        suffix = manager.backend.config_suffix if manager is not None else ""
        recorded[(site.webserver, _recorded_site_name(site, suffix))] = site

    rows: dict[tuple[str, str], SiteRow] = {}
    for manager in managers:
        record = manager.backend.webserver_record
        for entry in manager.list_sites():
            owner = owners.get((record, entry.domain))
            known = recorded.get((record, entry.domain))
            text = _read_site_file(Path(entry.config_path))
            rows[(record, entry.domain)] = SiteRow(
                name=entry.domain,
                domain=owner or (known.domain if known is not None else entry.domain),
                webserver=entry.webserver,
                noust_managed=_is_noust_text(text),
                app=owner,
                enabled=entry.enabled,
                config_path=entry.config_path,
                has_ssl=config_serves_tls(text),
                server_names=tuple(manager.names_in(text)),
            )

    for key, site in recorded.items():
        if key in rows:
            continue
        rows[key] = SiteRow(
            name=key[1],
            domain=owners.get(key) or site.domain,
            webserver=site.webserver,
            noust_managed=False,
            app=owners.get(key),
            enabled=bool(site.enabled),
            config_path=site.config_path,
            has_ssl=bool(site.ssl_enabled),
        )

    return sorted(rows.values(), key=lambda row: (row.domain, row.webserver, row.name))
