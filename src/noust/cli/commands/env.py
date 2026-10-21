# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Environment variables of a deployed application.

The three actions share one rule: a ``.env`` holds credentials, so nothing here
prints a value in clear unless the operator asked for it with ``--unmask``, and
every file written goes through
:meth:`~noust.deployers.helpers.env_manager.EnvManager.write_env_file`,
which creates it 0600 rather than letting the process umask decide.

Which file is the application's ``.env`` is not decided here: on the release
layout it is ``shared/.env``, and :mod:`noust.deployers.helpers.app_env` reads
and writes it wherever it is, for this command and the panel alike.

Both entry points, the Click commands below and the legacy :func:`handle_env`,
run the same private functions, so the two paths cannot drift apart.
``noust.cli.parser`` is gone and nothing calls :func:`handle_env` in
production anymore; it is kept, and tested directly, for the same reason.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.config import REDACTED
from noust.core.exceptions import EnvConfigError, NoustError
from noust.core.logger import Logger
from noust.core.secret_detection import Secrecy, classify
from noust.core.store import App, get_store
from noust.deployers.helpers.app_env import (
    find_app,
    read_app_env,
    read_comments_of,
    read_environment_of,
    refresh_app_env,
    write_app_env,
)
from noust.deployers.helpers.env_manager import EnvConfig, EnvManager, redact_url_credentials
from noust.deployers.helpers.env_sync import MAX_FILE_BYTES, EnvSyncPlan, plan_sync
from noust.deployers.helpers.env_variables import predefined_for
from noust.deployers.helpers.layout import code_path_for, env_file_for

#: Historical spellings of the subcommand names. They are in scripts and in the
#: published documentation, so dropping one is a breaking change.
ENV_ALIASES: dict[str, str] = {
    "config": "configure",
    "setup": "configure",
    "list": "show",
    "ls": "show",
}


class AliasedGroup(NoustGroup):
    """
    A group that also answers to the previous names of its commands.

    Attributes:
        aliases: Alternative spelling to the canonical command name.
    """

    def __init__(self, *args: Any, aliases: dict[str, str] | None = None, **kwargs: Any) -> None:
        """
        Args:
            *args: Passed to click.Group.
            aliases: Alternative spelling to the canonical command name.
            **kwargs: Passed to click.Group.
        """
        super().__init__(*args, **kwargs)
        self.aliases = aliases or {}

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        """
        Look a command up, resolving an alias first.

        Args:
            ctx: Click context.
            name: Name the user typed.

        Returns:
            The command, or None if there is no such name.
        """
        return super().get_command(ctx, self.aliases.get(name, name))

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        """
        Resolve a command, reporting the name the user actually typed.

        Args:
            ctx: Click context.
            args: Remaining arguments.

        Returns:
            The typed name, the command and the arguments left to parse.
        """
        _, command, remaining = super().resolve_command(ctx, args)
        return (command.name if command else None), command, remaining


def _redact(values: Mapping[str, str], marks: Mapping[str, bool] | None = None) -> dict[str, str]:
    """
    Replace every secret value with a placeholder.

    Uses :func:`~noust.core.secret_detection.classify`, the one classifier for
    the question, honouring this application's own marks (see
    ``noust env mark``): a value marked secret is redacted even if nothing
    about its name or value would otherwise say so, and one marked not
    secret is shown even if it would. A value that is secret only because of
    a credential embedded in it - the user-less ``redis://:password@host``
    form included - keeps the rest of the value readable.

    The placeholder is fixed width, so the output never reveals the length of a
    secret nor whether one is set at all.

    Args:
        values: Variable name to value.
        marks: Operator overrides for this application.

    Returns:
        A new mapping safe to print.
    """
    safe: dict[str, str] = {}
    for key, value in values.items():
        verdict = classify(key, value, marks)
        if not verdict.secret:
            safe[key] = value
        elif verdict.reason == "url credentials":
            safe[key] = redact_url_credentials(value)
        else:
            safe[key] = REDACTED
    return safe


def _app(domain: str) -> App:
    """
    Locate a deployed application.

    Args:
        domain: Domain the application is served on.

    Returns:
        The application, as the store records it or as its directory shows.

    Raises:
        EnvConfigError: If the domain is empty or nothing is deployed there.
    """
    if not domain:
        raise EnvConfigError(
            "No domain given",
            details="Name the application, for example: noust env show example.com",
        )

    app = find_app(domain)
    if app is None:
        raise EnvConfigError(
            f"Application not found: {domain}",
            details="Nothing is deployed at that domain. Run 'noust list' to see what is.",
        )
    return app


def _env_configure(domain: str, verbose: bool) -> int:
    """
    Ask for every variable the application declares and write its .env.

    Args:
        domain: Domain the application is served on.
        verbose: Print the detail of each step.

    Returns:
        Exit code.

    Raises:
        EnvConfigError: If nothing is deployed at this domain.
    """
    logger = Logger(verbose=verbose)
    app = _app(domain)
    manager = EnvManager(verbose=verbose)

    logger.header(f"Environment Configuration: {domain}")

    # The variables are declared by the code (.env.example), which on the
    # release layout is the active release; the values live in the .env,
    # which is in shared/.
    variables = manager.discover(code_path_for(app))
    if not variables:
        logger.info("No .env.example files found")
        return 0

    logger.info(f"Found {len(variables)} variables")

    existing = read_app_env(app, manager=manager)
    values = manager.prompt_variables(variables, existing)

    # Written 0600 and owned by the service account, never left readable by
    # the web server user.
    logger.success(f"Written: {write_app_env(app, values, manager=manager, logger=logger)}")

    manager.save_config(env_file_for(app).parent, EnvConfig(variables=variables))
    return 0


def _secrets_payload(secrets: Mapping[str, Secrecy]) -> dict[str, dict[str, Any]]:
    """
    Render a classification map the way ``--json`` prints it.

    Args:
        secrets: Variable name to its verdict.

    Returns:
        The same, as plain JSON-serialisable dictionaries.
    """
    return {
        name: {"secret": verdict.secret, "reason": verdict.reason, "marked": verdict.marked}
        for name, verdict in secrets.items()
    }


def _env_show(domain: str, unmask: bool, verbose: bool, *, json_output: bool = False) -> int:
    """
    Print the variables currently set for an application.

    Args:
        domain: Domain the application is served on.
        unmask: Print secret values in clear.
        verbose: Print the detail of each step.
        json_output: Print the variables as JSON instead of a report. Secrets
            are redacted exactly as they are for a human, unless ``unmask``
            says otherwise.

    Returns:
        Exit code.

    Raises:
        EnvConfigError: If nothing is deployed at this domain.
    """
    logger = Logger(verbose=verbose)
    app = _app(domain)
    environment = read_environment_of(app, manager=EnvManager(verbose=verbose))
    values = environment.values
    marks = app.env_secret_marks
    secrets = {key: classify(key, value, marks) for key, value in values.items()}
    comments = read_comments_of(app, manager=EnvManager(verbose=verbose))

    shown = dict(values) if unmask else _redact(values, marks)

    if json_output:
        click.echo(
            json.dumps(
                {
                    "domain": domain,
                    "variables": shown,
                    "redacted": not unmask,
                    "secrets": _secrets_payload(secrets),
                    "audience": environment.audience,
                    "references": environment.expressions,
                    "predefined": predefined_for(app),
                    "comments": comments,
                }
            )
        )
        return 0

    if not values:
        logger.info(f"No environment variables found for {domain}")
        return 0

    logger.header(f"Environment: {domain}")

    if unmask:
        logger.warning("Printing secrets in clear. Check who can see this terminal.")

    for key in sorted(shown):
        notes = [secrets[key].reason]
        if environment.audience_of(key) != "both":
            notes.append(f"{environment.audience_of(key)} only")
        if key in environment.expressions:
            notes.append(f"from {environment.expressions[key]}")
        logger.key_value(f"  {key} ({', '.join(notes)})", shown[key])
        for note in comments.get(key, "").splitlines():
            logger.info(f"      # {note}")

    if not unmask:
        logger.blank()
        logger.info(f"Secrets are shown as {REDACTED}. Add --unmask to read them.")

    predefined = predefined_for(app)
    if predefined:
        logger.blank()
        logger.info("Noust also gives the application, on its own:")
        for key in sorted(predefined):
            logger.key_value(f"  {key}", predefined[key])

    return 0


def _parse_assignments(items: tuple[str, ...], from_stdin: bool) -> dict[str, str]:
    """
    Read ``NAME=VALUE`` pairs from the arguments or from standard input.

    Args:
        items: The arguments.
        from_stdin: Read one pair per line from standard input instead.

    Returns:
        Name to value.

    Raises:
        click.UsageError: A pair without ``=``, or both sources given.
    """
    if from_stdin == bool(items):
        raise click.UsageError("Give NAME=VALUE pairs as arguments, or with --stdin, not both.")
    lines = sys.stdin.read().splitlines() if from_stdin else list(items)
    pairs: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep:
            raise click.UsageError(f"{line!r} is not NAME=VALUE")
        pairs[name.strip()] = value
    return pairs


#: The largest file ``noust env set --file`` reads into a variable.
MAX_VALUE_FILE_BYTES = 64 * 1024


def _read_value_files(items: tuple[str, ...]) -> dict[str, str]:
    """
    Read ``NAME=PATH`` pairs: a variable whose value is the content of a file.

    Args:
        items: The arguments.

    Returns:
        Name to the file's text, one trailing line feed (the one an editor adds) removed.

    Raises:
        click.UsageError: A pair without ``=``, a file that cannot be read as text, or one that
            is larger than :data:`MAX_VALUE_FILE_BYTES`.
    """
    values: dict[str, str] = {}
    for item in items:
        name, sep, location = item.partition("=")
        if not sep or not location:
            raise click.UsageError(f"{item!r} is not NAME=PATH")
        path = Path(location).expanduser()
        try:
            if path.stat().st_size > MAX_VALUE_FILE_BYTES:
                raise click.UsageError(
                    f"{path} is larger than {MAX_VALUE_FILE_BYTES // 1024} KiB: keep large "
                    "files in a mounted file (noust app mount), not in a variable"
                )
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise click.UsageError(f"{path} could not be read as text: {exc}") from exc
        values[name.strip()] = text.removesuffix("\n")
    return values


def _env_set(
    domain: str,
    pairs: Mapping[str, str],
    audience: str | None,
    unset: tuple[str, ...],
    verbose: bool,
    comment: str | None = None,
) -> int:
    """
    Add, change and remove variables of an application, keeping the rest.

    Args:
        domain: Domain the application is served on.
        pairs: Variables to set.
        audience: ``both``, ``build`` or ``runtime`` for these, or None to keep each one's.
        unset: Names to remove.
        verbose: Print the detail of each step.
        comment: A comment to keep above each variable set, or None to leave the ones
            they have.

    Returns:
        Exit code.

    Raises:
        EnvConfigError: If nothing is deployed at this domain, or a name to
            remove is not there.
    """
    logger = Logger(verbose=verbose)
    app = _app(domain)
    manager = EnvManager(verbose=verbose)
    values = read_app_env(app, manager=manager)
    missing = [name for name in unset if name not in values]
    if missing:
        raise EnvConfigError(
            f"{domain} has no variable {', '.join(missing)}",
            details="Run 'noust env show' to see its variables.",
        )
    for name in unset:
        values.pop(name)
    values.update(pairs)
    wanted = dict.fromkeys(pairs, audience) if audience else None
    path = write_app_env(
        app,
        values,
        manager=manager,
        logger=logger,
        audience=wanted,
        comments=dict.fromkeys(pairs, comment) if comment is not None else None,
    )
    changed = [*pairs, *unset]
    logger.success(f"{', '.join(changed)}: written to {path}")
    logger.info(f"A running process keeps the values it started with: noust restart {domain}")
    return 0


def _read_dotenv(path: str) -> str:
    """
    Read the ``.env`` file to bring in.

    Args:
        path: A file, or ``-`` for standard input.

    Returns:
        Its text.

    Raises:
        click.UsageError: It cannot be read as text or is larger than a ``.env`` is.
    """
    try:
        if path == "-":
            text = sys.stdin.read(MAX_FILE_BYTES + 1)
        else:
            with Path(path).expanduser().open(encoding="utf-8") as handle:
                text = handle.read(MAX_FILE_BYTES + 1)
    except (OSError, UnicodeDecodeError) as exc:
        raise click.UsageError(f"{path} could not be read as text: {exc}") from exc
    if len(text) > MAX_FILE_BYTES:
        raise click.UsageError(f"{path} is larger than {MAX_FILE_BYTES // 1024} KiB: not a .env")
    return text


def _env_sync(
    domain: str,
    text: str,
    *,
    prune: bool,
    audience: str | None,
    dry_run: bool,
    verbose: bool,
) -> EnvSyncPlan:
    """
    Bring a ``.env`` file into an application's variables, creating and updating, never deleting.

    Args:
        domain: Domain the application is served on.
        text: The file's text.
        prune: Also remove the variables the file does not hold.
        audience: ``both``, ``build`` or ``runtime`` for the variables the file sets, or None to
            keep each one's.
        dry_run: Only work out what it would do.
        verbose: Print the detail of each step.

    Returns:
        What it did, or would do.

    Raises:
        EnvConfigError: If nothing is deployed at this domain.
        ValidationError: The file is not a ``.env``, or a name or a value cannot be written.
    """
    app = _app(domain)
    manager = EnvManager(verbose=verbose)
    current = read_environment_of(app, manager=manager)
    plan = plan_sync(current.values, text, expressions=current.expressions, prune=prune)
    if dry_run or not (plan.changed or plan.comments):
        return plan
    chosen = (
        dict.fromkeys((*plan.created, *plan.updated, *plan.unchanged), audience)
        if audience
        else None
    )
    write_app_env(
        app,
        plan.values,
        manager=manager,
        logger=Logger(verbose=verbose),
        audience=chosen,
        comments=plan.comments,
    )
    return plan


def _env_resolve(domain: str, verbose: bool) -> int:
    """
    Resolve an application's references again, without a deploy.

    Args:
        domain: Domain the application is served on.
        verbose: Print the detail of each step.

    Returns:
        Exit code.

    Raises:
        EnvConfigError: If nothing is deployed at this domain.
    """
    logger = Logger(verbose=verbose)
    app = _app(domain)
    plan = refresh_app_env(app, manager=EnvManager(verbose=verbose), logger=logger)
    if plan.changed:
        logger.success(f"Resolved again: {', '.join(plan.changed)}")
        logger.info(f"A running process keeps the values it started with: noust restart {domain}")
    else:
        logger.info(f"Nothing changed for {domain}")
    return 0


def _env_mark(domain: str, name: str, mark: str, verbose: bool) -> int:
    """
    Set or clear an operator override for one variable's secrecy.

    Not audited: no command in this CLI tree writes to
    :class:`~noust.web.auth.AuditLogger` (that logger is installed by the web
    server's own process and answers only its API; nothing here is a
    parallel implementation of it). ``noust`` runs as root at an operator's own
    terminal, which is its own record of who acted, the same as every other
    CLI mutation - ``noust app delete``, ``noust config set``. Auditing CLI
    mutations at all is a decision for the CLI as a whole, not one command.

    Args:
        domain: Domain the application is served on.
        name: Environment variable name.
        mark: ``"secret"``, ``"not_secret"`` or ``"auto"`` (remove any
            existing mark and judge the variable automatically again).
        verbose: Print the detail of each step.

    Returns:
        Exit code.

    Raises:
        EnvConfigError: If nothing is deployed at this domain.
        ValidationError: If ``name`` is not a valid environment variable name.
    """
    logger = Logger(verbose=verbose)
    app = _app(domain)

    marks = dict(app.env_secret_marks)
    if mark == "auto":
        marks.pop(name, None)
    else:
        marks[name] = mark == "secret"

    get_store().set_env_secret_marks(app.domain, marks)

    values = read_app_env(app, manager=EnvManager(verbose=verbose))
    verdict = classify(name, values.get(name, ""), marks)
    state = "secret" if verdict.secret else "not secret"
    if verdict.marked:
        logger.success(f"{name} is now marked {state} for {domain}.")
    else:
        logger.success(
            f"{name} is judged automatically again for {domain}: {state} ({verdict.reason})."
        )
    return 0


def _env_migrate(domain: str | None, every: bool, verbose: bool) -> int:
    """
    Move inline unit variables into the .env, one application or all of them.

    Args:
        domain: The application, unless ``every``.
        every: Every application whose unit still carries variables.
        verbose: Whether to log verbosely.

    Returns:
        The exit code.

    Raises:
        click.UsageError: Neither or both of a domain and ``--all``.
    """
    from noust.deployers import unit_environment

    if (domain is None) == (not every):
        raise click.UsageError("Name one application, or pass --all.")
    logger = Logger(verbose=verbose)
    domains = [domain] if domain else [name for name, _ in unit_environment.candidates()]
    if not domains:
        logger.success("No application's unit carries variables inline")
        return 0
    failed = 0
    for name in domains:
        try:
            result = unit_environment.migrate(name, logger=logger)
        except NoustError as exc:
            failed += 1
            logger.error(exc.message)
            if exc.details:
                logger.info(exc.details)
            continue
        if result is None:
            logger.info(f"{name}: its unit carries no variables to move")
            continue
        logger.success(
            f"{name}: {len(result.moved)} variable(s) moved to {result.env_file}; "
            f"{result.unit}.service loads them from there and answers"
        )
        if result.kept:
            logger.info(
                f"{name}: kept the .env's value for {', '.join(result.kept)}: builds read it, "
                "so it is the one the application was built with"
            )
        if result.previous is not None:
            logger.info(f"{name}: the .env as it was is kept in {result.previous}")
        if result.replaced:
            logger.warning(
                f"{name}: the .env had another value for {', '.join(result.replaced)}; "
                "it now has the one the application was running with"
            )
    if failed:
        raise SystemExit(1)
    return 0


def _env_export(domain: str, output: str, verbose: bool) -> int:
    """
    Copy an application's variables into a file.

    Args:
        domain: Domain the application is served on.
        output: Destination path.
        verbose: Print the detail of each step.

    Returns:
        Exit code.

    Raises:
        EnvConfigError: If nothing is deployed at this domain.
        SecurityError: If the destination is a symlink.
    """
    logger = Logger(verbose=verbose)
    manager = EnvManager(verbose=verbose)
    values = read_app_env(_app(domain), manager=manager)

    if not values:
        logger.info(f"No environment variables found for {domain}")
        return 0

    output_path = Path(output)
    # The export carries the secrets in clear, so it is written through the
    # same 0600 seam as the deployed .env rather than with a plain write.
    manager.write_env_file(output_path, values)
    logger.success(f"Exported {len(values)} variables to {output_path}")
    logger.info("The file holds secrets in clear and is readable by its owner only.")
    return 0


@click.group(
    cls=AliasedGroup,
    aliases=ENV_ALIASES,
    name="env",
    epilog="Also accepts: configure as 'config' or 'setup', show as 'list' or 'ls'.",
)
def cli() -> None:
    """Read and set the environment variables of a deployed application."""


@cli.command("configure")
@click.argument("domain")
@pass_context
def configure(state: Context, domain: str) -> None:
    """Ask for each variable the application declares and save its .env."""
    _env_configure(domain, state.verbose)


# Hiding the secrets, it only reads; --unmask prints them, which is on record.
@cli.command("show", read_only=lambda params: not params.get("unmask"))
@click.argument("domain")
@click.option(
    "--unmask",
    is_flag=True,
    # Explicit, not implied: click 8.5 reports an unset flag default as a
    # sentinel, and the guarantee that secrets stay masked must not depend on
    # which click a distribution ships.
    default=False,
    help="Print secret values in clear instead of hiding them.",
)
@json_option("Print the variables as JSON.")
@pass_context
def show(state: Context, domain: str, unmask: bool) -> None:
    """List the variables an application runs with, secrets hidden."""
    _env_show(domain, unmask, state.verbose, json_output=state.json_output)


@cli.command("export")
@click.argument("domain")
@click.option(
    "-o",
    "--output",
    default=".env",
    show_default=True,
    type=click.Path(dir_okay=False, writable=True, path_type=str),
    help="Where to write the file.",
)
@pass_context
def export(state: Context, domain: str, output: str) -> None:
    """Write an application's variables to a file, owner-readable only."""
    _env_export(domain, output, state.verbose)


@cli.command("migrate")
@click.argument("domain", required=False)
@click.option(
    "--all", "every", is_flag=True, help="Every application whose unit still carries variables."
)
@pass_context
def migrate(state: Context, domain: str | None, every: bool) -> None:
    """Move an application's variables from its systemd unit into its .env (units written by 1.x)."""
    _env_migrate(domain, every, state.verbose)


@cli.command("set")
@click.argument("domain")
@click.argument("pairs", nargs=-1, metavar="NAME=VALUE...")
@click.option(
    "--stdin", "from_stdin", is_flag=True, help="Read NAME=VALUE lines from standard input."
)
@click.option("--build-only", "build_only", is_flag=True, help="Only the install and the build.")
@click.option("--runtime-only", "runtime_only", is_flag=True, help="Not the install or the build.")
@click.option("--both", "both", is_flag=True, help="The build and the running application.")
@click.option(
    "--file",
    "files",
    multiple=True,
    metavar="NAME=PATH",
    help="Set NAME to the content of the file at PATH (a private key, a certificate, a JSON "
    "document): a value of several lines. Repeat it.",
)
@click.option(
    "--comment",
    "comment",
    metavar="TEXT",
    help="Keep this comment above each variable set here (empty takes the comment away).",
)
@pass_context
def set_command(
    state: Context,
    domain: str,
    pairs: tuple[str, ...],
    from_stdin: bool,
    build_only: bool,
    runtime_only: bool,
    both: bool,
    files: tuple[str, ...],
    comment: str | None,
) -> None:
    """
    Add or change variables, keeping the others.

    A value may refer to a shared variable ({{project.NAME}}), to the deployment
    ({{app.URL}}) or to a secret manager ({{secret:vault/path#field}}); it is
    resolved again at every deploy. A secret on the command line is visible to
    every user of this machine: pass it with --stdin.

    A value of several lines comes from a file: --file PRIVATE_KEY=key.pem. It
    is written between single quotes, which systemd, Compose and dotenv read as
    one value, and it may not hold a single quote.
    """
    chosen = [flag for flag in (build_only, runtime_only, both) if flag]
    if len(chosen) > 1:
        raise click.UsageError("Pass at most one of --build-only, --runtime-only and --both.")
    audience = "build" if build_only else "runtime" if runtime_only else "both" if both else None
    values = _parse_assignments(pairs, from_stdin) if (pairs or from_stdin) else {}
    values.update(_read_value_files(files))
    if not values:
        raise click.UsageError("Give at least one NAME=VALUE, or --file NAME=PATH.")
    _env_set(domain, values, audience, (), state.verbose, comment)


@cli.command("sync")
@click.argument("domain")
@click.option(
    "--file",
    "-f",
    "path",
    required=True,
    metavar="PATH",
    help="The .env file to bring in; - reads it from standard input.",
)
@click.option(
    "--prune",
    is_flag=True,
    help="Also remove the variables the file does not hold. Without it nothing is deleted.",
)
@click.option("--build-only", "build_only", is_flag=True, help="Only the install and the build.")
@click.option("--runtime-only", "runtime_only", is_flag=True, help="Not the install or the build.")
@click.option("--both", "both", is_flag=True, help="The build and the running application.")
@json_option("Print what changed (names, never values) as JSON.")
@pass_context
def sync_command(
    state: Context,
    domain: str,
    path: str,
    prune: bool,
    build_only: bool,
    runtime_only: bool,
    both: bool,
) -> None:
    """
    Bring a real .env file into an application's variables.

    Quotes, 'export', comments and values of several lines are read as the application's own
    .env is. Every variable the file sets is created or updated and the others are kept,
    unless --prune. PORT and NODE_ENV (the unit sets them) and the NOUST_* names are skipped
    with the reason rather than refusing the file. With the global --dry-run, only says what
    would change. A running process keeps its values until it is restarted.
    """
    chosen = [flag for flag in (build_only, runtime_only, both) if flag]
    if len(chosen) > 1:
        raise click.UsageError("Pass at most one of --build-only, --runtime-only and --both.")
    audience = "build" if build_only else "runtime" if runtime_only else "both" if both else None
    text = _read_dotenv(path)
    plan = _env_sync(
        domain,
        text,
        prune=prune,
        audience=audience,
        dry_run=state.dry_run,
        verbose=state.verbose,
    )
    if state.json_output:
        click.echo(json.dumps({"domain": domain, "dry_run": state.dry_run, **plan.to_dict()}))
        return
    logger = state.logger
    prefix = "Would " if state.dry_run else ""
    for label, names in (
        (f"{prefix}create" if state.dry_run else "Created", plan.created),
        (f"{prefix}update" if state.dry_run else "Updated", plan.updated),
        (f"{prefix}remove" if state.dry_run else "Removed", plan.removed),
    ):
        if names:
            logger.key_value(label, ", ".join(names))
    if plan.unchanged:
        logger.key_value("Already as the file says", ", ".join(plan.unchanged))
    for name, reason in plan.skipped.items():
        logger.warning(f"{name} skipped: {reason}")
    if plan.duplicates:
        logger.warning(
            f"Set more than once in the file (the last counts): {', '.join(plan.duplicates)}"
        )
    if not plan.changed:
        logger.info(f"{domain} already has what the file says")
    elif not state.dry_run:
        logger.success(
            f"{len(plan.changed)} variable(s) of {domain} written. A running process keeps "
            f"its values: noust restart {domain}"
        )


@cli.command("comment")
@click.argument("domain")
@click.argument("name")
@click.argument("text", required=False)
@pass_context
def comment_command(state: Context, domain: str, name: str, text: str | None) -> None:
    """
    Show, set or take away the comment above a variable.

    The comment is kept in the .env, above the variable, and survives every
    write of the file. Without TEXT it is printed; with an empty TEXT ('') it
    is taken away.
    """
    logger = Logger(verbose=state.verbose)
    app = _app(domain)
    manager = EnvManager(verbose=state.verbose)
    values = read_app_env(app, manager=manager)
    if name not in values:
        raise EnvConfigError(
            f"{domain} has no variable {name}",
            details="Run 'noust env show' to see its variables.",
        )
    if text is None:
        found = read_comments_of(app, manager=manager).get(name, "")
        click.echo(found if found else f"{name} has no comment")
        return
    write_app_env(app, values, manager=manager, logger=logger, comments={name: text})
    logger.success(f"{name}: comment {'kept' if text.strip() else 'taken away'}")


@cli.command("unset")
@click.argument("domain")
@click.argument("names", nargs=-1, required=True)
@pass_context
def unset_command(state: Context, domain: str, names: tuple[str, ...]) -> None:
    """Remove variables, keeping the others."""
    _env_set(domain, {}, None, names, state.verbose)


@cli.command("audience")
@click.argument("domain")
@click.argument("name")
@click.option("--build-only", "build_only", is_flag=True, help="Only the install and the build.")
@click.option("--runtime-only", "runtime_only", is_flag=True, help="Not the install or the build.")
@click.option("--both", "both", is_flag=True, help="The build and the running application.")
@pass_context
def audience_command(
    state: Context, domain: str, name: str, build_only: bool, runtime_only: bool, both: bool
) -> None:
    """Say whether a variable is for the build, the running application, or both."""
    chosen = [flag for flag in (build_only, runtime_only, both) if flag]
    if len(chosen) != 1:
        raise click.UsageError("Pass exactly one of --build-only, --runtime-only and --both.")
    kind = "build" if build_only else "runtime" if runtime_only else "both"
    app = _app(domain)
    values = read_app_env(app, manager=EnvManager(verbose=state.verbose))
    if name not in values:
        raise EnvConfigError(
            f"{domain} has no variable {name}", details="Run 'noust env show' to see them."
        )
    write_app_env(app, values, manager=EnvManager(verbose=state.verbose), audience={name: kind})
    state.logger.success(f"{name} is for {kind} now")


@cli.command("resolve")
@click.argument("domain")
@pass_context
def resolve_command(state: Context, domain: str) -> None:
    """Resolve the references in an application's variables again, without a deploy."""
    _env_resolve(domain, state.verbose)


@cli.command("mark")
@click.argument("domain")
@click.argument("name")
@click.option("--secret", "as_secret", is_flag=True, help="Always treat this variable as a secret.")
@click.option(
    "--not-secret",
    "as_not_secret",
    is_flag=True,
    help="Never treat this variable as a secret.",
)
@click.option(
    "--auto",
    "as_auto",
    is_flag=True,
    help="Remove any override and judge this variable by its name and value again.",
)
@pass_context
def mark(
    state: Context, domain: str, name: str, as_secret: bool, as_not_secret: bool, as_auto: bool
) -> None:
    """Override whether one environment variable is treated as a secret."""
    chosen = [flag for flag in (as_secret, as_not_secret, as_auto) if flag]
    if len(chosen) != 1:
        raise click.UsageError("Pass exactly one of --secret, --not-secret or --auto.")
    value = "secret" if as_secret else "not_secret" if as_not_secret else "auto"
    _env_mark(domain, name, value, state.verbose)


def handle_env(args: Namespace) -> int:
    """
    Run an env action from the argparse namespace.

    ``noust.cli.parser`` is gone and nothing calls this in production; it is
    kept, and tested directly, sharing every private function with the Click
    commands above so the two cannot drift apart.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for error).
    """
    verbose = getattr(args, "verbose", False)
    logger = Logger(verbose=verbose)

    action = getattr(args, "action", None)
    if not action:
        logger.error("env requires an action", details="Use: noust env --help")
        return 1

    canonical = ENV_ALIASES.get(action, action)
    domain = getattr(args, "domain", "")

    try:
        if canonical == "configure":
            return _env_configure(domain, verbose)
        if canonical == "show":
            return _env_show(domain, getattr(args, "unmask", False), verbose)
        if canonical == "export":
            return _env_export(domain, getattr(args, "output", ".env"), verbose)
    except NoustError as exc:
        logger.error(exc.message, details=exc.details)
        return 1

    logger.error(f"Unknown env action: {action}", details="Use: noust env --help")
    return 1
