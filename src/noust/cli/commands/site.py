# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust site`` command group.

Virtual hosts on nginx and apache: create, list, enable, disable, delete and
show. The work lives in the private ``_site_*`` functions, so the Click
commands and the legacy :func:`handle_site` argparse entry point run exactly
the same code and only differ in how the parameters arrive. ``noust.cli.parser``
is gone; that entry point stays because :mod:`noust.cli.interactive` still
builds a ``Namespace`` and calls it for the interactive menu.

Every manager this module needs is imported here, at module level. Importing
CertManager inside the create path meant ``noust site delete`` raised NameError
on every run, and the broad handler around it turned that into a warning about
a certificate that had in fact never been touched.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.site_unknown_hosts import unknown_hosts as unknown_hosts_group
from noust.cli.commands.team_common import owner_options
from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.redact import scrubber_for
from noust.core.teams import scoped_store
from noust.managers.apache_manager import ApacheManager
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.site_logs import KINDS as SITE_LOG_KINDS
from noust.managers.site_logs import SiteLogReader
from noust.managers.webserver import create_secured_site, delete_site_completely
from noust.validators.domain import validate_domain
from noust.validators.port import MAX_PORT, MIN_PORT

#: Alternative spellings of the subcommands. They predate the group and live in
#: scripts and in muscle memory, so dropping one is a breaking change.
COMMAND_ALIASES: dict[str, str] = {
    "cat": "show",
    "ls": "list",
    "remove": "delete",
    "rm": "delete",
}

DEFAULT_PORT = 3000
DEFAULT_TEMPLATE = "proxy"
WEBSERVERS = ("nginx", "apache")

_NOT_FOUND_HINT = "Run 'noust site list' to see the virtual hosts this server knows about."


class SiteGroup(NoustGroup):
    """
    The ``site`` group, resolving the historical spellings of its subcommands.

    Aliases resolve but are not listed, so the help stays one line per
    operation instead of ten lines for six operations.
    """

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        """
        Look a subcommand up by its name or by one of its aliases.

        Args:
            ctx: The Click context.
            name: Name the user typed.

        Returns:
            The command, or None when nothing answers to that name.
        """
        return super().get_command(ctx, COMMAND_ALIASES.get(name, name))


def _get_manager(webserver: str, verbose: bool = False) -> ApacheManager | NginxManager:
    """
    Get the appropriate site manager.

    Args:
        webserver: Either "apache" or "nginx".
        verbose: Enable verbose logging.

    Returns:
        The manager for the requested web server.
    """
    if webserver == "apache":
        return ApacheManager(verbose=verbose)
    return NginxManager(verbose=verbose)


def _site_create(
    *,
    domain: str,
    webserver: str,
    template: str,
    port: int,
    no_ssl: bool,
    www: bool,
    logger: Logger,
    verbose: bool,
) -> None:
    """
    Create or update a virtual host and, unless asked not to, secure it.

    The site is written without SSL first because certbot validates over plain
    HTTP; the certificate paths are only added to the configuration once a
    certificate really exists.

    Args:
        domain: Domain the site serves.
        webserver: Either "nginx" or "apache".
        template: Template name, without the ``.conf.j2`` suffix.
        port: Port the application listens on behind the proxy.
        no_ssl: Skip certificate issuance entirely.
        www: Also serve and certify ``www.<domain>``.
        logger: Logger for progress output.
        verbose: Enable verbose logging in the managers.

    Raises:
        DomainError: When the domain is not a valid domain name.
        NginxError: When the nginx configuration cannot be written.
        ApacheError: When the apache configuration cannot be written.
    """
    domain = validate_domain(domain)
    manager = _get_manager(webserver, verbose=verbose)
    cert_manager = CertManager(verbose=verbose)

    logger.info(f"Creating site: {domain}")
    outcome = create_secured_site(
        domain,
        manager=manager,
        webserver=webserver,
        cert_manager=cert_manager,
        template=template,
        port=port,
        www=www,
        ssl=not no_ssl,
    )

    if outcome.site_existed:
        logger.info("Site config already existed, updated it")

    if outcome.ssl_enabled:
        if outcome.certificate_reused:
            logger.info("Valid SSL certificate found covering all domains")
        logger.success(f"Site created with SSL: {domain}")
        return

    if outcome.ssl_requested and outcome.certificate_error:
        if not cert_manager.is_installed():
            logger.warning("Certbot not installed, skipping SSL")
            logger.info("Install with: sudo apt install certbot")
        else:
            logger.warning(f"SSL certificate failed: {outcome.certificate_error}")
            logger.info("Site created without SSL. You can add it later with: noust cert create")

    logger.success(f"Site created: {domain}")


def _site_list(
    *,
    webserver: str,
    logger: Logger,
    verbose: bool,
    json_output: bool = False,
    team: str | None = None,
    project: str | None = None,
    environment: str | None = None,
) -> None:
    """
    Print every virtual host on the requested web servers.

    Args:
        webserver: "nginx", "apache" or "all".
        logger: Logger for the table.
        verbose: Enable verbose logging in the managers.
        json_output: Print the sites as JSON instead of a table.
        team: Only this team's sites.
        project: Only the projects with this slug.
        environment: Only the environments with this slug.
    """
    all_sites = []

    if webserver in ("nginx", "all"):
        nginx = NginxManager(verbose=verbose)
        if nginx.is_installed():
            for site in nginx.list_sites():
                site["webserver"] = "nginx"
                all_sites.append(site)

    if webserver in ("apache", "all"):
        apache = ApacheManager(verbose=verbose)
        if apache.is_installed():
            for site in apache.list_sites():
                site["webserver"] = "apache"
                all_sites.append(site)

    if team or project or environment:
        # A file the store never recorded has no team, so a filter leaves it out.
        owned = {
            site.domain
            for site in scoped_store().list_sites(
                project=project, environment=environment, team=team
            )
        }
        all_sites = [site for site in all_sites if site["domain"] in owned]

    if json_output:
        # Same outer shape as GET /api/sites (SiteListResponse); "name" rather
        # than "domain", matching that response's own field.
        click.echo(
            json.dumps(
                {
                    "sites": [
                        {
                            "name": site["domain"],
                            "webserver": site["webserver"],
                            "enabled": site["enabled"],
                            "config_path": site["config_path"],
                        }
                        for site in all_sites
                    ],
                    "total": len(all_sites),
                    "webserver": webserver,
                }
            )
        )
        return

    logger.header("Web Server Sites")

    if not all_sites:
        logger.info("No sites found")
        return

    headers = ["Domain", "Enabled", "Web Server"]
    rows = []

    for site in all_sites:
        status = "✓" if site["enabled"] else "✗"
        rows.append([site["domain"], status, site["webserver"]])

    logger.table(headers, rows)


def _site_enable(*, domain: str, logger: Logger, verbose: bool) -> None:
    """
    Enable a virtual host on whichever web server owns it.

    Args:
        domain: Domain of the site.
        logger: Logger for progress output.
        verbose: Enable verbose logging in the managers.

    Raises:
        NoustError: When neither web server has a configuration for the domain.
        DomainError: When the domain is not a valid domain name.
    """
    domain = validate_domain(domain)

    # Try Nginx first, then Apache
    nginx = NginxManager(verbose=verbose)
    apache = ApacheManager(verbose=verbose)

    if nginx.site_exists(domain):
        nginx.enable_site(domain)
        nginx.reload()
        logger.success(f"Site enabled (nginx): {domain}")
    elif apache.site_exists(domain):
        apache.enable_site(domain)
        apache.reload()
        logger.success(f"Site enabled (apache): {domain}")
    else:
        raise NoustError(f"Site not found: {domain}", details=_NOT_FOUND_HINT)


def _site_disable(*, domain: str, logger: Logger, verbose: bool) -> None:
    """
    Disable a virtual host, leaving its configuration file in place.

    Args:
        domain: Domain of the site.
        logger: Logger for progress output.
        verbose: Enable verbose logging in the managers.

    Raises:
        DomainError: When the domain is not a valid domain name.
    """
    domain = validate_domain(domain)

    nginx = NginxManager(verbose=verbose)
    apache = ApacheManager(verbose=verbose)

    if nginx.site_enabled(domain):
        nginx.disable_site(domain)
        nginx.reload()
        logger.success(f"Site disabled (nginx): {domain}")
    elif apache.site_enabled(domain):
        apache.disable_site(domain)
        apache.reload()
        logger.success(f"Site disabled (apache): {domain}")
    else:
        logger.warning(f"Site not enabled: {domain}")


def _site_delete(*, domain: str, logger: Logger, verbose: bool) -> None:
    """
    Delete a virtual host and the certificate issued for it.

    Confirmation belongs to the caller: this function always deletes.

    Args:
        domain: Domain of the site.
        logger: Logger for progress output.
        verbose: Enable verbose logging in the managers.

    Raises:
        NoustError: When neither web server has a configuration for the domain.
        DomainError: When the domain is not a valid domain name.
    """
    domain = validate_domain(domain)

    deletion = delete_site_completely(
        domain,
        nginx=NginxManager(verbose=verbose),
        apache=ApacheManager(verbose=verbose),
        cert_manager=CertManager(verbose=verbose),
    )

    if not deletion.removed_anything:
        raise NoustError(f"Site not found: {domain}", details=_NOT_FOUND_HINT)

    if deletion.nginx_removed:
        logger.success(f"Site deleted (nginx): {domain}")
    if deletion.apache_removed:
        logger.success(f"Site deleted (apache): {domain}")
    if deletion.certificate_removed:
        logger.success(f"Certificate deleted: {domain}")


def _site_show(*, domain: str, verbose: bool) -> None:
    """
    Print the configuration file of a virtual host.

    Args:
        domain: Domain of the site.
        verbose: Enable verbose logging in the managers.

    Raises:
        NoustError: When neither web server has a configuration for the domain.
        DomainError: When the domain is not a valid domain name.
    """
    domain = validate_domain(domain)

    nginx = NginxManager(verbose=verbose)
    apache = ApacheManager(verbose=verbose)

    config = None

    if nginx.site_exists(domain):
        config = nginx.get_site_config(domain)
    elif apache.site_exists(domain):
        config = apache.get_site_config(domain)

    if not config:
        raise NoustError(f"Site not found: {domain}", details=_NOT_FOUND_HINT)

    click.echo(config)


@click.group(cls=SiteGroup, name="site")
def cli() -> None:
    """
    Manage the nginx and apache virtual hosts on this server.

    A site is one domain, its web server configuration and its certificate.
    Aliases: ls for list, rm or remove for delete, cat for show.
    """


cli.add_command(unknown_hosts_group)


@cli.command("create")
@click.option("--domain", "-d", required=True, help="Domain to serve, such as example.com.")
@click.option(
    "--webserver",
    "-w",
    type=click.Choice(WEBSERVERS),
    default="nginx",
    show_default=True,
    help="Web server to configure.",
)
@click.option(
    "--template",
    "-t",
    default=DEFAULT_TEMPLATE,
    show_default=True,
    help="Configuration template to render.",
)
@click.option(
    "--port",
    "-p",
    type=click.IntRange(MIN_PORT, MAX_PORT),
    default=DEFAULT_PORT,
    show_default=True,
    help="Port the application listens on behind the proxy.",
)
@click.option("--no-ssl", is_flag=True, help="Serve plain HTTP and request no certificate.")
@click.option(
    "--www",
    is_flag=True,
    help="Also serve www.<domain> and cover it with the certificate.",
)
@pass_context
def create(
    state: Context,
    domain: str,
    webserver: str,
    template: str,
    port: int,
    no_ssl: bool,
    www: bool,
) -> None:
    """Create a site and secure it with a certificate."""
    _site_create(
        domain=domain,
        webserver=webserver,
        template=template,
        port=port,
        no_ssl=no_ssl,
        www=www,
        logger=state.logger,
        verbose=state.verbose,
    )


@cli.command("list")
@click.option(
    "--webserver",
    "-w",
    type=click.Choice([*WEBSERVERS, "all"]),
    default="all",
    show_default=True,
    help="Only show sites from this web server.",
)
@owner_options
@json_option("Print the site list as JSON.")
@pass_context
def list_sites(
    state: Context, webserver: str, team: str | None, project: str | None, env: str | None
) -> None:
    """List the sites configured on this server."""
    _site_list(
        webserver=webserver,
        logger=state.logger,
        verbose=state.verbose,
        json_output=state.json_output,
        team=team,
        project=project,
        environment=env,
    )


@cli.command("enable")
@click.argument("domain")
@pass_context
def enable(state: Context, domain: str) -> None:
    """Start serving a site that is configured but off."""
    _site_enable(domain=domain, logger=state.logger, verbose=state.verbose)


@cli.command("disable")
@click.argument("domain")
@pass_context
def disable(state: Context, domain: str) -> None:
    """Stop serving a site, keeping its configuration."""
    _site_disable(domain=domain, logger=state.logger, verbose=state.verbose)


@cli.command("delete")
@click.argument("domain")
@click.option(
    "--force",
    "-f",
    "-y",
    is_flag=True,
    help="Delete without asking for confirmation.",
)
@pass_context
def delete(state: Context, domain: str, force: bool) -> None:
    """Delete a site, its configuration and its certificate."""
    # Validated before the prompt so that the question names the domain exactly
    # as it will be acted on, and so a typo fails without asking anything.
    domain = validate_domain(domain)

    if not force and not click.confirm(
        f"Delete the site {domain}, its web server configuration "
        f"and the SSL certificate issued for it?",
        default=False,
    ):
        state.logger.info("Aborted")
        return

    _site_delete(domain=domain, logger=state.logger, verbose=state.verbose)


@cli.command("logs", read_only=True)
@click.argument("domain")
@click.option(
    "--kind",
    type=click.Choice(SITE_LOG_KINDS),
    default="access",
    show_default=True,
    help="The web server's access log or its error log.",
)
@click.option("-n", "--lines", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--contains", metavar="TEXT", help="Only lines with this text, whatever its case.")
@json_option("Print the log as JSON.")
@pass_context
def logs_command(ctx: Context, domain: str, kind: str, lines: int, contains: str | None) -> None:
    """
    Show the end of what the web server logged for a site.

    The access log has one line per request; the error log is nginx's or
    Apache's own account of what went wrong (a refused upstream, a missing
    file). Tokens, passwords and credentials in a line are masked, as in
    every log Noust shows.
    """
    app = scoped_store().get_app(domain)
    if app is None:
        ctx.logger.error(f"Application not found: {domain}", details=_NOT_FOUND_HINT)
        raise SystemExit(1)
    log = SiteLogReader(app.webserver, scrub=scrubber_for(domain).scrub).read(
        domain, kind, lines=lines, contains=contains
    )
    if ctx.json_output:
        click.echo(json.dumps(log.to_dict()))
        return
    if not log.available:
        ctx.logger.warning(f"The web server has not written {log.path} for {domain}.")
        return
    for line in log.lines:
        click.echo(line)
    if log.truncated:
        ctx.logger.info(f"(showing the last {len(log.lines)} of {log.matched} lines read)")


@cli.command("show")
@click.argument("domain")
@pass_context
def show(state: Context, domain: str) -> None:
    """Print the configuration file of a site."""
    _site_show(domain=domain, verbose=state.verbose)


def handle_site(args: Namespace) -> int:
    """
    Handle site commands coming from an argparse-shaped ``Namespace``.

    ``noust.cli.parser`` is gone; :mod:`noust.cli.interactive` is what still
    builds one of these and calls this for its site menu. It calls the same
    private functions as the Click commands.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    action = args.action

    handlers = {
        "create": _handle_create,
        "list": _handle_list,
        "ls": _handle_list,
        "enable": _handle_enable,
        "disable": _handle_disable,
        "delete": _handle_delete,
        "remove": _handle_delete,
        "rm": _handle_delete,
        "show": _handle_show,
        "cat": _handle_show,
    }

    handler = handlers.get(action)
    if not handler:
        print(f"Unknown action: {action}", file=sys.stderr)
        return 1

    try:
        return handler(args)
    except NoustError as e:
        logger = Logger(verbose=args.verbose)
        logger.error(str(e))
        return 1
    except Exception as e:
        logger = Logger(verbose=args.verbose)
        logger.error(f"Unexpected error: {e}")
        logger.debug(f"Unhandled {type(e).__name__} in site {action}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def _handle_create(args: Namespace) -> int:
    """Handle site create command."""
    _site_create(
        domain=args.domain,
        webserver=getattr(args, "webserver", "nginx"),
        template=getattr(args, "template", DEFAULT_TEMPLATE),
        port=getattr(args, "port", DEFAULT_PORT),
        no_ssl=getattr(args, "no_ssl", False),
        www=getattr(args, "www", False),
        logger=Logger(verbose=args.verbose),
        verbose=args.verbose,
    )
    return 0


def _handle_list(args: Namespace) -> int:
    """Handle site list command."""
    _site_list(
        webserver=getattr(args, "webserver", "all"),
        logger=Logger(verbose=args.verbose),
        verbose=args.verbose,
    )
    return 0


def _handle_enable(args: Namespace) -> int:
    """Handle site enable command."""
    _site_enable(domain=args.domain, logger=Logger(verbose=args.verbose), verbose=args.verbose)
    return 0


def _handle_disable(args: Namespace) -> int:
    """Handle site disable command."""
    _site_disable(domain=args.domain, logger=Logger(verbose=args.verbose), verbose=args.verbose)
    return 0


def _handle_delete(args: Namespace) -> int:
    """Handle site delete command."""
    logger = Logger(verbose=args.verbose)
    domain = validate_domain(args.domain)

    if not getattr(args, "force", False):
        response = input(f"Delete site '{domain}'? [y/N] ")
        if response.lower() != "y":
            logger.info("Aborted")
            return 0

    _site_delete(domain=domain, logger=logger, verbose=args.verbose)
    return 0


def _handle_show(args: Namespace) -> int:
    """Handle site show command."""
    _site_show(domain=args.domain, verbose=args.verbose)
    return 0
