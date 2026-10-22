# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading and writing the environment of a deployed application.

``noust env`` and the panel's environment editor are two surfaces over the same
file, so they share this module rather than each joining a directory with
``".env"``. That join is how the release layout broke both: its ``.env`` lives
in ``shared/``, and ``<app>/.env`` read nothing and wrote a stray file the
application never saw. Where the file is comes from
:func:`~noust.deployers.helpers.layout.env_file_for`; what is done with it is
here.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path

from noust.core.config import Config
from noust.core.exceptions import DeploymentError
from noust.core.fs import SECRET_MODE, FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.store import App, AppType, get_store, is_adopted
from noust.core.utils import domain_to_app_name
from noust.core.variables.placement import (
    app_builds,
    require_buildable,
    with_predefined,
    without_predefined,
)
from noust.core.variables.resolver import Resolver
from noust.core.variables.store import VariableStore
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.env_variables import (
    AppEnvironment,
    EnvironmentPlan,
    check_audience_request,
    plan_environment,
    read_environment,
    refresh_plan,
    resolver_for,
    rows_for,
    secret_marks_for,
)
from noust.deployers.helpers.layout import (
    RELEASES,
    app_root,
    env_file_for,
    layout_on_disk,
)
from noust.deployers.helpers.permissions import hand_over_file
from noust.deployers.helpers.workspace_env import propagate
from noust.deployers.releases import ReleaseManager
from noust.managers.app_identity import service_account
from noust.validators.environment import (
    EnvironmentValidationError,
    validate_env_comment,
    validate_environment,
)

#: Variables the unit sets inline with ``Environment=`` (see
#: :meth:`~noust.deployers.base.BaseDeployer._unit_environment`). systemd lets
#: ``EnvironmentFile=`` override ``Environment=``, so an edit here would
#: silently take over from what the unit - and nginx, which proxies to the
#: port Noust recorded - expect. A fresh deploy already keeps them out of the
#: file it generates; this is the guard for every edit made after that, where
#: silently dropping the value would look like it was accepted.
_MANAGED_ENV_VAR_HINTS: dict[str, str] = {
    "PORT": (
        "PORT is managed by Noust: the unit loads it with Environment=, which "
        "EnvironmentFile= would override if the .env file set it too, silently "
        "moving the application off the port nginx and systemd expect. Change "
        "the port by redeploying, for example 'noust create -d <domain> -s <source> "
        "--port <port>' (or POST /api/apps with the new port)."
    ),
    "NODE_ENV": (
        "NODE_ENV is managed by Noust and fixed to 'production' in the unit; "
        "EnvironmentFile= would override that Environment= the same way, so it "
        "cannot be set from the environment file either."
    ),
}


def _reject_managed_vars(values: Mapping[str, str], current: Mapping[str, str]) -> None:
    """
    Refuse a write that would introduce or change a variable the unit already owns.

    An application deployed before this guard existed, or one whose
    ``.env.example`` declares PORT, can already have PORT or NODE_ENV sitting
    in its ``.env``. Refusing every future edit of that file over a setting
    nobody is trying to change would make the environment of every such
    application permanently uneditable - the operator adding an unrelated
    ``API_KEY`` would be blocked by a PORT they never touched. So only a save
    that adds the key or gives it a value different from what is already on
    disk is refused; the value already there, carried through unchanged,
    passes.

    Args:
        values: The complete set of variables about to be written.
        current: What the file holds right now.

    Raises:
        EnvironmentValidationError: If values adds PORT or NODE_ENV, or gives
            either one a value that differs from what is already stored.
    """
    offenders = [
        name
        for name in ("PORT", "NODE_ENV")
        if name in values and values[name] != current.get(name)
    ]
    if not offenders:
        return
    raise EnvironmentValidationError(
        f"{' and '.join(offenders)} cannot be set from the environment file",
        details=" ".join(_MANAGED_ENV_VAR_HINTS[name] for name in offenders),
    )


def refuse_adopted_environment(app: App) -> None:
    """
    Refuse to write the environment of a site Noust adopted rather than built.

    The environment of an adopted site is whatever its operator's unit loads, and a ``.env`` that
    Noust created beside it would be read by nothing or, in a web root, by anybody who asks the
    web server for it.

    Args:
        app: The application.

    Raises:
        EnvironmentValidationError: The application is an adopted site; nothing was written.
    """
    if is_adopted(app.app_type):
        raise EnvironmentValidationError(
            f"{app.domain} is an adopted site: Noust does not manage its environment",
            details=(
                "Its variables are whatever its own unit loads (Environment= or EnvironmentFile= "
                "in the unit file). Edit them there and restart the application."
            ),
        )


def find_app(domain: str) -> App | None:
    """
    Find a deployed application by domain, with or without a store row.

    Args:
        domain: The application's domain.

    Returns:
        The store row; failing that, a stand-in for a directory deployed under
        the conventional name whose store row is missing (the store moved, or
        it predates the store), on the layout its directory shows. None when
        neither exists.
    """
    app = get_store().get_app(domain)
    if app is not None:
        return app
    root = Config().apps_directory / domain_to_app_name(domain)
    if not root.is_dir():
        return None
    return App(domain=domain, app_path=str(root), layout=layout_on_disk(root))


def read_runtime_env(app: App, *, manager: EnvManager | None = None) -> dict[str, str]:
    """
    Read the variables in an application's ``.env``: what the running process is given.

    Args:
        app: The application.
        manager: Parser to read with. Defaults to a new one.

    Returns:
        Variable name to value; empty when the application has no ``.env``.
        A build-only variable is not in the file and is not here, and neither are the names
        Noust itself writes there for a Compose stack or a container (``NOUST_URL``...): they
        are Noust's, not the operator's to show, export or set.
    """
    if is_adopted(app.app_type):
        return {}
    return without_predefined(
        app.app_type, (manager or EnvManager()).read_env_file(env_file_for(app))
    )


def read_comments_of(app: App, *, manager: EnvManager | None = None) -> dict[str, str]:
    """
    Read the comment above each variable of an application's ``.env``.

    Args:
        app: The application.
        manager: Parser to read with. Defaults to a new one.

    Returns:
        Variable name to its comment, only for the ones that have one.
    """
    return (manager or EnvManager()).read_env_comments(env_file_for(app))


def check_comments(comments: Mapping[str, str], names: Collection[str]) -> dict[str, str]:
    """
    Check the comments a change gives to variables.

    Args:
        comments: Variable name to its comment; empty text takes the comment away.
        names: The variables written to the ``.env`` after the change.

    Returns:
        The comments, trimmed.

    Raises:
        EnvironmentValidationError: A comment that is not plain short text, or one for a
            variable that is not in the ``.env`` (a build-only variable is in no file).
    """
    clean = {name: validate_env_comment(text, name=name) for name, text in comments.items()}
    stray = sorted(name for name, text in clean.items() if text and name not in names)
    if stray:
        raise EnvironmentValidationError(
            f"A comment was given for variables that are not in the .env: {', '.join(stray)}",
            details="A comment is kept above its variable in the file. A variable only the "
            "build is given is in no file, so it has none.",
        )
    return clean


def check_app_env(
    app: App,
    values: Mapping[str, str],
    *,
    manager: EnvManager | None = None,
    audience: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """
    Check what :func:`write_app_env` would refuse, without writing anything.

    The same rules in the same order - every name and value safe to reach a
    systemd unit, neither PORT nor NODE_ENV added or changed, and an audience only
    for a variable that is there - so a dry run of a change and the change cannot
    disagree.

    Args:
        app: The application.
        values: The complete set of variables that would be written.
        manager: Reader to use. Defaults to a new one.
        audience: The audiences the write would be given.

    Returns:
        The validated variables.

    Raises:
        EnvironmentValidationError: If :func:`write_app_env` would raise it.
    """
    refuse_adopted_environment(app)
    reader = manager or EnvManager()
    current = reader.read_env_file(env_file_for(app))
    clean = validate_environment(values, multiline=True)
    _reject_managed_vars(clean, current)
    check_audience_request(clean, audience)
    return clean


def read_environment_of(app: App, *, manager: EnvManager | None = None) -> AppEnvironment:
    """
    Read an application's variables with their audience and the expressions they come from.

    Args:
        app: The application.
        manager: Parser to read with. Defaults to a new one.

    Returns:
        The variables, build-only ones included.
    """
    return read_environment(app, read_runtime_env(app, manager=manager))


def read_app_env(app: App, *, manager: EnvManager | None = None) -> dict[str, str]:
    """
    Read an application's variables: the ``.env`` and the ones only its build is given.

    The whole set, so a caller that reads it, changes it and writes it back
    (a database linked, a recipe updated) keeps the build-only variables
    instead of dropping them. What the process runs with is
    :func:`read_runtime_env`.

    Args:
        app: The application.
        manager: Parser to read with. Defaults to a new one.

    Returns:
        Variable name to value; empty when the application has none.
    """
    return read_environment_of(app, manager=manager).values


def write_app_env(
    app: App,
    values: Mapping[str, str],
    *,
    manager: EnvManager | None = None,
    fs: FileSystem | None = None,
    logger: Logger | None = None,
    audience: Mapping[str, str] | None = None,
    link: bool = True,
    comments: Mapping[str, str] | None = None,
) -> Path:
    """
    Replace an application's variables with exactly these.

    The runtime ones are written to ``.env``: 0600 and handed to the account
    the service runs as (written by root and left root's, it is a file the
    application cannot read, and the edit it carries never takes effect). On
    the release layout it is written to ``shared/`` and, when the active
    release has no link to it yet (it was deployed before there was a ``.env``),
    linked in, so a restart picks it up without a redeploy.

    Every name and value is validated against what can safely reach a
    systemd unit and the ``EnvironmentFile=`` it is loaded from - a POSIX
    identifier with no control character in its value, a newline included -
    before anything is written, so a caller that skips its own check (an
    older CLI path did) cannot smuggle a second directive into the file this
    unit and every other reader of it trusts.

    A value may hold ``{{...}}`` references (:mod:`noust.core.variables`): it
    is resolved here, the file holds the result and the store keeps the text it
    came from, so a deploy resolves it again. A value equal to what such a
    variable resolved to keeps its text, which is what lets a client that read
    the environment and wrote it back change nothing. A variable whose
    audience is ``build`` is written to no file: the store keeps it for the
    install and the build.

    A mark an operator set on a name this write drops is pruned here too,
    the one place every writer - ``noust env configure``, the panel's editor -
    shares. Left in the store, a "not secret" mark would silently reattach
    itself to a later variable that reused the same name and was, unlike the
    one that earned the mark, an actual secret.

    Args:
        app: The application.
        values: The complete set of variables.
        manager: Writer to use. Defaults to a new one.
        fs: Filesystem the ownership change and the release link go through.
        logger: Where a failed hand-over or link is reported.
        audience: Names to ``both``, ``build`` or ``runtime``; a name left out
            keeps the audience it has.
        link: Link the file into the active release when it has no link yet.
        comments: Variable name to the comment kept above it, over the ones the file has;
            empty text takes one away. None leaves every comment as it is.

    Returns:
        The file written.

    Raises:
        EnvironmentValidationError: If a name or value is not safe to write
            (see :func:`~noust.validators.environment.validate_environment`),
            or if values adds PORT or NODE_ENV, or changes one already on
            disk; both are loaded into the unit inline and
            ``EnvironmentFile=`` would let this file silently override them;
            or a name Noust sets itself (``NOUST_URL``...).
        ValidationError: A reference that is not well formed.
        UnresolvedReferenceError: A reference that cannot be resolved.
        SecretProviderError: A secret manager could not give a secret.
        SecurityError: If the destination is a symlink.
        OSError: If the file cannot be written.
    """
    writer = manager or EnvManager(fs=fs)
    previous = read_environment_of(app, manager=writer)
    plan = plan_environment(
        values, previous=previous, audience=audience, resolver=resolver_for(app)
    )
    return commit_environment(
        app, plan, manager=writer, fs=fs, logger=logger, link=link, comments=comments
    )


def commit_environment(
    app: App,
    plan: EnvironmentPlan,
    *,
    manager: EnvManager | None = None,
    fs: FileSystem | None = None,
    logger: Logger | None = None,
    link: bool = True,
    comments: Mapping[str, str] | None = None,
) -> Path:
    """
    Write what :func:`~noust.deployers.helpers.env_variables.plan_environment` decided.

    Args:
        app: The application.
        plan: The variables as they will be.
        manager: Writer to use. Defaults to a new one.
        fs: Filesystem the ownership change and the release link go through.
        logger: Where a failed hand-over or link is reported.
        link: Link the file into the active release when it has no link yet.
        comments: Variable name to the comment kept above it, over the ones the file has;
            empty text takes one away. None leaves every comment as it is.

    Returns:
        The file written.

    Raises:
        EnvironmentValidationError: PORT or NODE_ENV added or changed, or a variable only the
            build is given on an application that builds nothing.
        SecurityError: If the destination is a symlink.
        OSError: If the file cannot be written.
    """
    refuse_adopted_environment(app)
    log = logger or Logger()
    env_file = env_file_for(app)
    writer = manager or EnvManager(fs=fs)
    current = writer.read_env_file(env_file)
    environment = plan.environment
    require_buildable(app.domain, environment.build_only, builds=app_builds(app))
    runtime = environment.runtime
    _reject_managed_vars(runtime, current)
    kept: dict[str, str] | None = None
    if comments is not None:
        asked = check_comments(comments, runtime)
        kept = {
            name: text
            for name, text in {**writer.read_env_comments(env_file), **asked}.items()
            if text
        }
    # What Noust wrote there for the containers of a stack is not the operator's to edit, and an
    # edit must not take it away.
    writer.write_env_file(env_file, with_predefined(app.app_type, runtime, current), kept)

    if app.id is not None:
        VariableStore().set_app_variables(app.id, rows_for(environment))

    # A value a secret manager gave is a secret whatever it looks like, unless
    # the operator said otherwise; a mark for a name this write drops goes too.
    wanted = {
        name: mark
        for name, mark in {
            **secret_marks_for(environment.expressions),
            **app.env_secret_marks,
        }.items()
        if name in environment.values
    }
    if wanted != app.env_secret_marks:
        get_store().set_env_secret_marks(app.domain, wanted)

    config = Config()
    hand_over_file(
        env_file,
        user=service_account(app, config)[0],
        group=service_account(app, config)[1],
        mode=SECRET_MODE,
        fs=fs or get_fs(),
        logger=log,
    )

    if app.app_type == AppType.MONOREPO.value:
        # Its units load a file in each workspace, not this one: the change goes to them too.
        propagate(
            app,
            changed={name: value for name, value in runtime.items() if current.get(name) != value},
            removed=[name for name in current if name not in runtime],
            manager=writer,
            fs=fs,
            logger=log,
        )

    if link and app.layout == RELEASES:
        releases = ReleaseManager(app_root(app), fs=fs or get_fs(), logger=log)
        active = releases.current()
        if active is not None:
            try:
                releases.link_shared(active.path, [])
            except DeploymentError as exc:
                log.warning(f"The new .env was not linked into release {active.id}: {exc}")
    return env_file


def refresh_app_env(
    app: App,
    *,
    resolver: Resolver | None = None,
    manager: EnvManager | None = None,
    fs: FileSystem | None = None,
    logger: Logger | None = None,
) -> EnvironmentPlan:
    """
    Resolve an application's references again and write what changed.

    What a deploy does before it builds, and ``noust env resolve`` does on
    request: a shared variable that changed, a secret that was rotated, the
    commit or the release now being built, reach the files the application is
    built and run from.

    Args:
        app: The application.
        resolver: The deployment's resolver; one for the application as the
            store has it by default.
        manager: Writer to use.
        fs: Filesystem seam.
        logger: Where progress is reported.

    Returns:
        The plan; ``changed`` names the variables whose value is new. Nothing
        is written when none is.

    Raises:
        UnresolvedReferenceError: A reference no longer resolves.
        SecretProviderError: A secret manager could not give a secret.
        EnvironmentValidationError: The new values cannot be written.
    """
    writer = manager or EnvManager(fs=fs)
    previous = read_environment_of(app, manager=writer)
    plan = refresh_plan(previous, resolver=resolver or resolver_for(app))
    if plan.changed:
        commit_environment(app, plan, manager=writer, fs=fs, logger=logger, link=False)
    return plan
