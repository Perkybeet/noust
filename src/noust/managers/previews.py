# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pull request previews (2.2).

An application that allows previews gets a short-lived copy of itself for
every pull request opened against its repository: a child application at
``pr-<n>-<app-name>.<base domain>``, deployed from the pull request's branch
on the release layout, with small resource limits, no cron jobs and no backup
schedule. New commits on the branch update it; closing or merging the pull
request removes it, and so does its time-to-live running out without a push.

The child is an ordinary application. It is created by the same job the
console's "new application" runs (:func:`noust.web.jobs.deploy_app_job`),
updated by :func:`noust.deployers.lifecycle.update_app` and removed by
:func:`noust.deployers.lifecycle.delete_app`; nothing here builds anything.
What this module adds is the bookkeeping: which pull request a preview is
for, its status, when it expires, the comment on the pull request, and the
hourly timer that sweeps the expired ones.

What a preview is given is the application's settings to decide
(:mod:`noust.managers.preview_env`). Since 3.4 the default is the safe one: only
the variables the settings name, then the set defined for previews alone, and an
empty database of its own (dropped with it) for an application that has one. The
2.2 way (every variable but the excluded, and the parent's own databases) is a
setting an operator chooses, and is said out loud wherever it is in force. Either
way a preview runs code nobody has reviewed, so only people trusted with the
repository get one: a pull request whose branch lives in a fork never does (unless
an operator makes it by hand and says so), on GitHub its author must be an owner,
member or collaborator, and a bot's (Dependabot, Renovate) only when the settings
allow bots.

A preview can also be made by hand from a branch, a tag or an open pull request, and
managed one at a time (rebuilt, stopped, started, read the logs of, removed).

A Docker Compose application has previews too. Its preview is a stack of its own:
the same repository in a directory of its own, so Compose gives it containers, a
network and named volumes apart from the original's, run from a copy of the compose
file that cannot collide with the original's (one published web port on the preview's
own loopback port, no fixed container names, host paths redirected under its
directory; :mod:`noust.deployers.helpers.compose_preview`). Its volumes are removed
with it. A monorepo has none yet: its workspaces each take a port, a unit and a site.

Since 3.1 a preview is built in the build sandbox, as the unprivileged
``noust-build`` account (:mod:`noust.deployers.helpers.sandbox`). It builds
with the network, as previews always did, and without the production secrets
among the variables it copied (only the unit gets those). The strict network
profile is opt-in: a preview takes it when its parent builds strict (``noust
app sandbox enable <parent> --network strict``), and then its dependencies
install with the network and without the parent's variables, and its build
runs with them and without a network. The deployment decides this when it
creates the preview's application (``preview_parent``); nothing here has to.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections.abc import Callable, Collection, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from jinja2 import Environment, PackageLoader, TemplateError

from noust.central import require_server_role
from noust.core import paths
from noust.core.config import SYSTEMD_DIR as _SYSTEMD_DIR
from noust.core.exceptions import (
    DeploymentError,
    IntegrationError,
    NoustError,
    QueueFullError,
    ServiceError,
    ValidationError,
)
from noust.core.forge_events import Forge, PullRequestAction, PullRequestEvent
from noust.core.fs import FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.preview_store import (
    DATA_EMPTY,
    DATA_SHARED,
    INHERIT_ALL,
    INHERIT_ALLOWLIST,
    KIND_BRANCH,
    KIND_PR,
    KIND_TAG,
    MANUAL_BASE,
    PreviewOptions,
    PreviewStore,
    validate_data_mode,
    validate_inherit,
)
from noust.core.repositories import repository_key
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import (
    App,
    AppType,
    DeploymentTrigger,
    PreviewRecord,
    PreviewSettings,
    get_store,
)
from noust.core.terminal_context import TerminalContext
from noust.core.utils import domain_to_app_name, find_noust_executable
from noust.integrations.github import pulls
from noust.managers import preview_env
from noust.managers.database.linking import EXTRA_VARIABLES
from noust.managers.source_manager import validate_git_ref
from noust.validators.domain import validate_domain
from noust.validators.environment import ENV_NAME_PATTERN
from noust.validators.names import MAX_APP_NAME_LENGTH, resolve_within
from noust.validators.port import find_available_port

if TYPE_CHECKING:
    from noust.deployers.lifecycle import PhaseReporter
    from noust.web.jobs import Job, JobContext

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits and names
# ---------------------------------------------------------------------------

#: How many previews an application may allow at once, at most and at least.
MIN_PREVIEWS = 1
MAX_PREVIEWS = 20

#: How long a preview may live without a push: one hour to ninety days.
MIN_TTL_HOURS = 1
MAX_TTL_HOURS = 90 * 24

#: Defaults of ``noust preview enable``, the same the store has.
DEFAULT_MAX_PREVIEWS = 3
DEFAULT_TTL_HOURS = 7 * 24

#: Resource limits every preview is created with. A preview is a throwaway
#: copy on a production server: it must not be able to starve its parent.
PREVIEW_MEMORY_MB = 256
PREVIEW_CPU_PERCENT = 50

#: The largest pull request number a base domain is checked against when
#: previews are turned on, so a long base domain is refused then rather than
#: at the first busy repository's pull request.
_LARGEST_NUMBER_CHECKED = 999999

#: Characters of the application's name a preview domain keeps at least.
#: Below this the name is a hash with a letter in front, which still tells
#: previews of different applications apart but no longer reads as a name.
_MIN_NAME_ROOM = 8

#: Length of the hash that keeps two truncated names apart.
_HASH_LENGTH = 6

#: Variables the unit sets itself (see
#: :data:`noust.deployers.helpers.app_env._MANAGED_ENV_VAR_HINTS`): a preview
#: runs on its own port, so the parent's PORT must not follow it.
_MANAGED_ENV_VARS = frozenset({"PORT", "NODE_ENV"})

#: How many variable names a preview's settings may exclude.
MAX_EXCLUDED_ENV = 100

#: GitHub's author associations of the people a preview is built for: the
#: repository's owner, its organisation's members and its collaborators.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

#: The executable the sweep unit runs when ``noust`` is not on PATH while
#: the unit is written: where the distribution packages put it.
_DEFAULT_NOUST = "/usr/bin/noust"

#: A preview left ``removing`` this long (its removal failed, or the process
#: running it died) is removed by the sweep. Long enough that a removal job
#: still queued in the console is not raced by the timer.
_REMOVING_GRACE = timedelta(minutes=30)

#: Port range a preview's port is chosen from, the same as
#: :func:`noust.validators.port.find_available_port`'s.
_PORT_RANGE = (3000, 9000)

# Statuses a preview goes through, as the store and the API spell them.
PENDING = "pending"
DEPLOYING = "deploying"
READY = "ready"
FAILED = "failed"
REMOVING = "removing"
STOPPED = "stopped"

#: The unit pair that removes expired previews.
SWEEP_UNIT = paths.PREVIEWS_UNIT

#: Where the sweep units are written. A module attribute so tests point it
#: elsewhere, as they do the backup scheduler's.
SYSTEMD_DIR = _SYSTEMD_DIR

_UNIT_MODE = 0o644
_SYSTEMCTL_TIMEOUT = 60

#: ``noust preview enable --ttl``: a number of hours, days or weeks.
_TTL_PATTERN = re.compile(r"^\s*(\d{1,6})\s*([hdw]?)\s*$", re.IGNORECASE)
_TTL_UNITS = {"": 1, "h": 1, "d": 24, "w": 24 * 7}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_ttl(value: str | int) -> int:
    """
    Read a time-to-live as ``noust preview enable --ttl`` takes it.

    Args:
        value: Hours as a number, or a number followed by ``h``, ``d`` or
            ``w`` (``12h``, ``7d``, ``2w``).

    Returns:
        The time-to-live in hours.

    Raises:
        ValidationError: The value is not one of those, or outside one hour
            to ninety days.
    """
    if isinstance(value, bool):
        raise ValidationError("The time-to-live must be a duration", details="For example 7d.")
    if isinstance(value, int):
        hours = value
    else:
        match = _TTL_PATTERN.match(value)
        if not match:
            raise ValidationError(
                f"Not a duration: {value!r}",
                details="Give hours, days or weeks: 12h, 7d, 2w.",
            )
        hours = int(match.group(1)) * _TTL_UNITS[match.group(2).lower()]
    return _checked_ttl(hours)


def _checked_ttl(hours: int) -> int:
    """
    Check a time-to-live in hours.

    Args:
        hours: The value.

    Returns:
        The value.

    Raises:
        ValidationError: Outside one hour to ninety days.
    """
    if isinstance(hours, bool) or not isinstance(hours, int):
        raise ValidationError("The time-to-live must be a whole number of hours")
    if not MIN_TTL_HOURS <= hours <= MAX_TTL_HOURS:
        raise ValidationError(
            f"A preview lives from 1 hour to 90 days, not {hours} hours",
            details="Previews that outlive their pull request only hold a port and a certificate.",
        )
    return hours


def _checked_max(max_previews: int) -> int:
    """
    Check how many previews an application may have at once.

    Args:
        max_previews: The value.

    Returns:
        The value.

    Raises:
        ValidationError: Not a whole number from 1 to 20.
    """
    if isinstance(max_previews, bool) or not isinstance(max_previews, int):
        raise ValidationError("The number of previews must be a whole number")
    if not MIN_PREVIEWS <= max_previews <= MAX_PREVIEWS:
        raise ValidationError(
            f"An application may have 1 to 20 previews at once, not {max_previews}",
            details="Each is a running copy of the application with its own certificate; "
            "Let's Encrypt allows 50 certificates a week per registered domain.",
        )
    return max_previews


def _label_prefix(number: int) -> str:
    """
    Say how a preview's first label begins.

    Args:
        number: The preview's number.

    Returns:
        ``pr-<n>-`` for a pull request; ``x-<n>-`` for one made by hand from a
        branch or a tag, counted from 1 (its number is above every pull request's).
    """
    if number >= MANUAL_BASE:
        return f"x-{number - MANUAL_BASE}-"
    return f"pr-{number}-"


def _name_room(base_domain: str, number: int) -> int:
    """
    Say how many characters of the application's name fit in a preview domain.

    The whole domain is bounded, not just its first label: it becomes the
    preview's application name and unit name, dots turned into dashes, and
    those are at most :data:`MAX_APP_NAME_LENGTH` characters.

    Args:
        base_domain: The base domain previews answer under.
        number: The preview's number.

    Returns:
        The room left for the name.
    """
    return MAX_APP_NAME_LENGTH - len(base_domain) - 1 - len(_label_prefix(number))


def preview_domain_for(parent_domain: str, number: int, base_domain: str) -> str:
    """
    Name the preview of a pull request.

    ``pr-<n>-<app-name>.<base domain>`` (``x-<n>-...`` for one made by hand
    from a branch or a tag): a single label under the base domain, so one
    wildcard record (and one wildcard certificate, if the operator has one)
    covers every preview. A name that does not fit is cut and given a short
    hash of the whole name, so two long names that share a beginning still give
    different domains.

    Args:
        parent_domain: The application previewed.
        number: The pull request number, or the number of a preview made by hand.
        base_domain: Where previews answer, such as ``previews.example.com``.

    Returns:
        The preview's domain.

    Raises:
        ValidationError: The base domain leaves no room for a name.
    """
    name = domain_to_app_name(parent_domain)
    room = _name_room(base_domain, number)
    if room < _MIN_NAME_ROOM:
        raise ValidationError(
            f"No room for the preview {_label_prefix(number)[:-1]} under {base_domain}",
            details=f"Preview domains are at most {MAX_APP_NAME_LENGTH} characters. "
            "Use a shorter base domain.",
        )
    if len(name) > room:
        digest = hashlib.sha256(name.encode()).hexdigest()[:_HASH_LENGTH]
        name = f"{name[: room - _HASH_LENGTH - 1].rstrip('-')}-{digest}"
    return validate_domain(f"{_label_prefix(number)}{name}.{base_domain}")


def _now() -> datetime:
    """
    Read the clock. A function so tests can move it.

    Returns:
        The current time in UTC.
    """
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    """
    Format a moment the way the store writes its timestamps.

    The store compares ``expires_at`` as text, so both sides must be spelled
    the same way: UTC, seconds, with the offset.

    Args:
        moment: An aware datetime.

    Returns:
        ISO 8601 text.
    """
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def preview_url(domain: str) -> str:
    """
    Give the address a preview answers on.

    Args:
        domain: The preview's domain.

    Returns:
        Its URL: https once it has a certificate, http when the certificate
        could not be obtained and it was deployed without one (the deploy
        goes on without TLS then, and an https link would not open). Before
        its first deployment finishes, https: what it will be when the
        certificate works.
    """
    app = get_store().get_app(domain)
    if app is not None and not app.ssl_enabled:
        return f"http://{domain}"
    return f"https://{domain}"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _base_domain(value: str, parent: App) -> str:
    """
    Check the domain previews answer under.

    Args:
        value: As the operator typed it; a leading ``*.`` is accepted, since
            that is how the wildcard record it needs is written.
        parent: The application previewed.

    Returns:
        The base domain, normalised.

    Raises:
        ValidationError: Not a domain of at least two labels, an address, or
            too long to leave room for preview names.
    """
    text = (value or "").strip().lower()
    text = text.removeprefix("*.")
    try:
        base = validate_domain(text)
    except NoustError as exc:
        raise ValidationError(
            f"Not a base domain for previews: {value!r}",
            details=f"{exc}. Give the domain a wildcard record points at this server, "
            "such as previews.example.com for *.previews.example.com.",
        ) from exc
    labels = base.split(".")
    if len(labels) < 2 or all(label.isdigit() for label in labels):
        raise ValidationError(
            f"Not a base domain for previews: {value!r}",
            details="Give a domain name such as previews.example.com, not an address or a "
            "single label.",
        )
    if _name_room(base, _LARGEST_NUMBER_CHECKED) < _MIN_NAME_ROOM:
        longest = MAX_APP_NAME_LENGTH - 1 - len(f"pr-{_LARGEST_NUMBER_CHECKED}-") - _MIN_NAME_ROOM
        raise ValidationError(
            f"{base} is too long to put previews under",
            details=f"Preview domains are at most {MAX_APP_NAME_LENGTH} characters; a base "
            f"domain may have up to {longest}.",
        )
    # The application's own domain is allowed on purpose: pr-1-x.shop.example.com
    # needs *.shop.example.com, which is a perfectly good wildcard.
    return base


def _require_previewable(domain: str) -> App:
    """
    Find an application that can have previews.

    Args:
        domain: Its domain.

    Returns:
        Its store row.

    Raises:
        ValidationError: It is not deployed, is a preview itself, is not
            deployed from git, or its type neither uses the release layout nor
            is a Docker Compose stack.
    """
    domain = validate_domain(domain)
    app = get_store().get_app(domain)
    if app is None:
        raise ValidationError(
            f"Application not found: {domain}",
            details="Previews copy a deployed application. Run 'noust list' for the exact domain.",
        )
    if app.preview_parent:
        raise ValidationError(
            f"{domain} is a preview of {app.preview_parent}",
            details="Turn previews on for the application itself.",
        )
    if repository_key(app.source) is None:
        raise ValidationError(
            f"{domain} is not deployed from a git repository",
            details="A preview is built from a pull request's branch, so the application's "
            "source has to be the repository the pull requests are opened against.",
        )
    if not (_supports_releases(app.app_type) or _is_stack(app)):
        raise ValidationError(
            f"{app.app_type or 'unknown'} applications cannot have previews yet",
            details="Previews are deployed on the release layout, which this type does not "
            "support, or as a Docker Compose stack of their own. A monorepo's workspaces each "
            "take a port, a unit and a site of their own, which previews do not isolate yet.",
        )
    return app


def _is_stack(app: App) -> bool:
    """
    Say whether an application is a Docker Compose stack.

    A preview of a stack is a stack of its own: the same repository in a directory of
    its own (so Compose gives it its own containers, network and volumes), run from a
    copy of the compose file that cannot collide with the original's
    (:mod:`noust.deployers.helpers.compose_preview`).

    Args:
        app: The application.

    Returns:
        True for a Docker Compose application.
    """
    return app.app_type == AppType.DOCKER_COMPOSE.value


def _supports_releases(app_type: str | None) -> bool:
    """
    Say whether a type deploys on the release layout.

    Args:
        app_type: The stored type.

    Returns:
        True for the types built on ``BaseDeployer``.
    """
    from noust.deployers.registry import get_deployer

    if not app_type:
        return False
    try:
        deployer = get_deployer(app_type)
    except ValueError:
        return False
    return bool(getattr(deployer, "SUPPORTS_RELEASES", False))


def checked_env_names(names: Iterable[str], *, field: str = "exclude_env") -> list[str]:
    """
    Check a list of variable names a preview's settings name.

    Args:
        names: Environment variable names.
        field: The setting they are for, for the error.

    Returns:
        The names, without repeats, in the order given.

    Raises:
        ValidationError: A name is not an environment variable name, or
            there are more than :data:`MAX_EXCLUDED_ENV`.
    """
    if isinstance(names, str):
        raise ValidationError("Variables must be a list of names", field=field)
    checked: list[str] = []
    for name in names:
        text = name.strip() if isinstance(name, str) else name
        if not isinstance(text, str) or not ENV_NAME_PATTERN.match(text):
            raise ValidationError(
                f"Not an environment variable name: {name!r}",
                details="Names are letters, digits and underscores, not starting with a digit.",
                field=field,
            )
        if text not in checked:
            checked.append(text)
    if len(checked) > MAX_EXCLUDED_ENV:
        raise ValidationError(
            f"At most {MAX_EXCLUDED_ENV} variables can be named, not {len(checked)}",
            field=field,
        )
    return checked


def enable_previews(
    app_domain: str,
    base_domain: str | None,
    *,
    max_previews: int | None = None,
    ttl_hours: int | None = None,
    allow_bots: bool | None = None,
    exclude_env: Iterable[str] | None = None,
    inherit_env: str | None = None,
    allow_env: Iterable[str] | None = None,
    data_mode: str | None = None,
) -> PreviewSettings:
    """
    Turn previews on for an application, or change their settings.

    A lower limit does not remove previews that already exist; it only stops
    new ones. A new time-to-live applies from each preview's next push, and
    so do the variables a preview is given: the ones the settings no longer
    give are taken out of every existing preview when it is next built.

    Previews turned on from 3.4 are given only the variables named in
    ``allow_env`` (plus the set defined for previews) and, when the application
    has a database, one of their own. Previews turned on before keep the way
    they were made until one of ``inherit_env`` or ``data_mode`` is given.

    Args:
        app_domain: The application previewed.
        base_domain: The domain a wildcard record points at this server; None
            keeps the one set before.
        max_previews: How many may exist at once, 1 to 20; None keeps the
            current value (3 when previews are being turned on).
        ttl_hours: How long one lives without a push, 1 hour to 90 days; None
            keeps the current value (7 days when they are being turned on).
        allow_bots: Whether bot accounts' pull requests get previews; None
            keeps the current value (off when they are being turned on).
        exclude_env: Variables never copied to a preview when they are given
            ``all`` of the application's; None keeps the current list (empty
            when they are being turned on).
        inherit_env: ``allowlist`` (only ``allow_env``) or ``all`` (every
            variable, less ``exclude_env``); None keeps the current way
            (``allowlist`` when previews are being turned on).
        allow_env: The variables an ``allowlist`` preview copies; None keeps
            the current list (none when previews are being turned on).
        data_mode: ``shared`` (the application's databases), ``empty`` (a
            database of its own); None keeps the current way
            (``empty`` when previews are being turned on for an application
            that has a database, ``shared`` otherwise).

    Returns:
        The settings as stored.

    Raises:
        ValidationError: A value is refused (see :func:`_require_previewable`,
            :func:`_base_domain`, :func:`checked_env_names`), or no base
            domain is given for an application without previews.
        ServiceError: The sweep timer could not be installed; the settings
            are put back as they were.
    """
    require_server_role("Preview environments")
    parent = _require_previewable(app_domain)
    store = get_store()
    extras = PreviewStore(store)
    before = store.get_preview_settings(parent.domain)
    before_options = extras.options(parent.domain) if extras.has_options(parent.domain) else None
    current = before or PreviewSettings(
        app_domain=parent.domain,
        base_domain="",
        max_previews=DEFAULT_MAX_PREVIEWS,
        ttl_hours=DEFAULT_TTL_HOURS,
    )
    if base_domain is None and before is None:
        raise ValidationError(
            f"Previews are off for {parent.domain}",
            details="Give the base domain previews answer under to turn them on.",
            field="base_domain",
        )
    settings = PreviewSettings(
        app_domain=parent.domain,
        base_domain=current.base_domain
        if base_domain is None
        else _base_domain(base_domain, parent),
        max_previews=_checked_max(current.max_previews if max_previews is None else max_previews),
        ttl_hours=_checked_ttl(current.ttl_hours if ttl_hours is None else ttl_hours),
        allow_bots=current.allow_bots if allow_bots is None else bool(allow_bots),
        exclude_env=list(current.exclude_env)
        if exclude_env is None
        else checked_env_names(exclude_env),
    )
    options = _options_for(
        parent,
        before,
        before_options,
        inherit_env=inherit_env,
        allow_env=allow_env,
        data_mode=data_mode,
    )
    stored = store.save_preview_settings(settings)
    if options is not None:
        extras.save_options(options)
    try:
        sync_sweep_timer()
    except NoustError:
        # Without the timer nothing would ever expire: previews stay off.
        if before is None:
            store.delete_preview_settings(parent.domain)
            extras.forget_options(parent.domain)
        else:
            store.save_preview_settings(before)
            if before_options is None:
                extras.forget_options(parent.domain)
            else:
                extras.save_options(before_options)
        raise
    return stored


def _options_for(
    parent: App,
    before: PreviewSettings | None,
    before_options: PreviewOptions | None,
    *,
    inherit_env: str | None,
    allow_env: Iterable[str] | None,
    data_mode: str | None,
) -> PreviewOptions | None:
    """
    Decide how the previews of an application are made after a change of settings.

    Args:
        parent: The application previewed.
        before: Its settings before, or None when previews were off.
        before_options: Its options before, or None when it had none.
        inherit_env: The way to give variables asked for, or None.
        allow_env: The variables to copy asked for, or None.
        data_mode: The way to give data asked for, or None.

    Returns:
        The options to store; None when nothing changes (previews turned on
        before 3.4 that no option was given for keep behaving as they did).

    Raises:
        ValidationError: A way that does not exist, or a variable name that is not one.
    """
    asked = inherit_env is not None or allow_env is not None or data_mode is not None
    if before is not None and before_options is None and not asked:
        return None
    if before_options is not None:
        base = before_options
    elif before is not None:
        # Turned on before 3.4 and now asked to change: start from what it did.
        base = PreviewOptions(parent.domain)
    else:
        base = PreviewOptions(
            parent.domain,
            INHERIT_ALLOWLIST,
            (),
            DATA_EMPTY if _has_database(parent) else DATA_SHARED,
        )
    mode = validate_inherit(inherit_env) if inherit_env is not None else base.inherit_env
    allowed = (
        tuple(checked_env_names(allow_env, field="allow_env"))
        if allow_env is not None
        else base.allow_env
    )
    data = validate_data_mode(data_mode) if data_mode is not None else base.data_mode
    return PreviewOptions(parent.domain, mode, allowed, data)


def _has_database(parent: App) -> bool:
    """
    Args:
        parent: The application previewed.

    Returns:
        Whether Noust has a database recorded for it.
    """
    return parent.id is not None and bool(get_store().list_databases(app_id=parent.id))


def has_database(app_domain: str) -> bool:
    """
    Args:
        app_domain: The application previewed.

    Returns:
        Whether Noust has a database recorded for it, which its previews would
        share unless they are given one of their own.
    """
    parent = get_store().get_app(app_domain)
    return parent is not None and _has_database(parent)


def disable_previews(app_domain: str, *, logger: Logger | None = None) -> list[str]:
    """
    Turn previews off for an application and remove the ones it has.

    Args:
        app_domain: The application previewed.
        logger: Where progress is reported.

    Returns:
        The domains of the previews removed.

    Raises:
        DeploymentError: Some previews could not be removed (the rest were,
            and previews are off either way).
    """
    return remove_previews_of(validate_domain(app_domain), logger=logger)


def previews_in_use() -> bool:
    """
    Say whether anything needs the sweep timer.

    Returns:
        True while an application allows previews or a preview still exists.
    """
    store = get_store()
    if store.list_previews():
        return True
    return any(store.get_preview_settings(app.domain) is not None for app in store.list_apps())


# ---------------------------------------------------------------------------
# Which applications preview a repository
# ---------------------------------------------------------------------------


def _same_repository(source: str, event: PullRequestEvent, *, check_host: bool) -> bool:
    """
    Decide whether an application is deployed from the repository of a pull request.

    Args:
        source: The application's source.
        event: The pull request.
        check_host: Compare the host too. A delivery to one application's own
            webhook was authenticated with that application's secret, so the
            host its forge calls itself (an internal Gitea name, say) need
            not match the one in the source.

    Returns:
        True when they are the same repository.
    """
    app_key = repository_key(source)
    if app_key is None:
        return False
    if app_key[1] != event.repository.strip("/").lower():
        return False
    if not check_host:
        return True
    event_key = repository_key(event.clone_url)
    return event_key is None or event_key[0] == app_key[0]


def previewing_apps(
    event: PullRequestEvent, *, app_domain: str | None = None
) -> list[tuple[App, PreviewSettings]]:
    """
    Find the applications that preview a pull request's repository.

    Args:
        event: The pull request.
        app_domain: Only this application.

    Returns:
        Each application with previews on whose source is the repository,
        with its settings. Previews themselves never have previews.
    """
    store = get_store()
    if app_domain is not None:
        candidates = [store.get_app(validate_domain(app_domain))]
    else:
        candidates = list(store.list_apps())
    found: list[tuple[App, PreviewSettings]] = []
    for app in candidates:
        if app is None or app.preview_parent:
            continue
        settings = store.get_preview_settings(app.domain)
        if settings is None:
            continue
        if not _same_repository(app.source, event, check_host=app_domain is None):
            continue
        found.append((app, settings))
    return found


# ---------------------------------------------------------------------------
# Pull request comments
# ---------------------------------------------------------------------------

_STATUS_LABELS = {
    PENDING: "Queued",
    DEPLOYING: "Building",
    READY: "Ready",
    FAILED: "Failed",
    REMOVING: "Being removed",
    STOPPED: "Stopped",
}


def _inheritance_note(parent: str) -> str:
    """
    Say what a preview shares with its parent, for every comment about one.

    Args:
        parent: The application previewed.

    Returns:
        One paragraph of Markdown.
    """
    return (
        f"This preview runs with a copy of `{parent}`'s environment variables, "
        "production secrets included (except the ones excluded from previews), "
        "and uses the same databases."
    )


def preview_comment(record: PreviewRecord, *, removed_because: str | None = None) -> str:
    """
    Write the comment a pull request shows about its preview.

    Why a build failed is deliberately not in it: a pull request may be
    public, and a failing build's output can echo the production secrets the
    preview was given. The reason is in the job log and the console.

    Args:
        record: The preview.
        removed_because: Set once the preview is gone: ``closed``,
            ``expired`` or ``removed``.

    Returns:
        Markdown.
    """
    heading = f"**Noust preview** of `{record.parent_domain}`"
    if removed_because is not None:
        reasons = {
            "closed": "the pull request was closed",
            "expired": "nothing was pushed to it before it expired",
            "removed": "an operator removed it",
            "disabled": "previews were turned off for the application",
        }
        if removed_because not in reasons:
            # A removal retried by the sweep: why it was asked for is not kept.
            return f"{heading}\n\nThe preview was removed."
        return f"{heading}\n\nThe preview was removed: {reasons[removed_because]}."
    status = _STATUS_LABELS.get(record.status, record.status)
    rows = [
        "| | |",
        "|---|---|",
        f"| Status | {status} |",
        f"| URL | {preview_url(record.domain)} |",
    ]
    if record.head_sha:
        rows.append(f"| Commit | `{record.head_sha[:12]}` |")
    rows.append(f"| Expires | {record.expires_at} unless pushed to again |")
    lines = [heading, "", *rows, ""]
    if record.status == FAILED:
        lines += ["The build failed. The job log in the Noust console says why.", ""]
    lines.append(_inheritance_note(record.parent_domain))
    return "\n".join(lines)


def _upsert_comment(
    provider: str,
    repository: str | None,
    number: int,
    body: str,
    comment_ref: str | None = None,
) -> str | None:
    """
    Post or refresh a pull request comment, never failing the caller.

    Only GitHub is written to (through this server's GitHub App); GitLab and
    Gitea deliveries reach an application's own webhook, which has no
    credential to answer with.

    Args:
        provider: The forge the pull request is on.
        repository: ``owner/repo``.
        number: The pull request number.
        body: Markdown.
        comment_ref: The comment to update, if one was posted before.

    Returns:
        The comment's id: the new one, or ``comment_ref`` when nothing was
        posted or posting failed.
    """
    if provider != Forge.GITHUB.value or not repository:
        return comment_ref
    from noust.integrations.github import comments

    try:
        posted = comments.upsert_pr_comment(repository, number, body, comment_ref)
    except (IntegrationError, OSError) as exc:
        # A comment is a courtesy: the preview itself must not fail over it.
        _log.warning("Could not comment on %s#%d: %s", repository, number, exc)
        return comment_ref
    return posted or comment_ref


def _refresh_comment(record: PreviewRecord, *, removed_because: str | None = None) -> PreviewRecord:
    """
    Bring a preview's pull request comment up to date and remember its id.

    Args:
        record: The preview, as stored.
        removed_because: See :func:`preview_comment`.

    Returns:
        The preview, with the comment id stored when it changed. Only that
        column is written: the rest of ``record`` may be older than the
        store's by now.
    """
    ref = _upsert_comment(
        record.provider,
        record.repository,
        record.number,
        preview_comment(record, removed_because=removed_because),
        record.comment_ref,
    )
    if ref == record.comment_ref or removed_because is not None:
        return record
    stored = get_store().update_preview(record.parent_domain, record.number, {"comment_ref": ref})
    return stored if stored is not None else replace(record, comment_ref=ref)


# ---------------------------------------------------------------------------
# Serialising work on one preview
# ---------------------------------------------------------------------------

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


@contextmanager
def _preview_lock(domain: str) -> Iterator[None]:
    """
    Hold the in-process lock of one preview.

    Up to three jobs run at once, and a pull request can be pushed to twice
    in a minute: the second build must wait for the first rather than fail on
    the application's lock (:func:`noust.core.applock.app_lock`, which refuses
    at once and is what guards against other processes).

    Args:
        domain: The preview's domain.

    Yields:
        Nothing; the lock is held until the block exits.
    """
    with _locks_guard:
        lock = _locks.setdefault(domain, threading.Lock())
    with lock:
        yield


#: Held from counting an application's previews to recording a new one, so
#: two pull requests opened at once cannot both take the last place.
_quota_guard = threading.Lock()

#: Guards :data:`_building`.
_build_guard = threading.Lock()

#: Previews a build job of this process is building now. A push to one of
#: them does not take a job slot waiting for it: its job returns at once and
#: the running build builds again once it is done (see
#: :func:`preview_deploy_job`).
_building: set[str] = set()

_ports_guard = threading.Lock()
_reserved_ports: set[int] = set()


def _reserve_port() -> int:
    """
    Pick a free port for a new preview and hold it until its deployment ends.

    A port nothing listens on can still belong to an application that is
    stopped, or to another preview whose build has not started its unit yet,
    so both are skipped.

    Returns:
        The port.

    Raises:
        DeploymentError: No port in the range is free.
    """
    taken = get_store().ports_owned_by_apps()
    with _ports_guard:
        start, end = _PORT_RANGE
        while start < end:
            port = find_available_port(start=start, end=end)
            if port is None:
                break
            if port not in taken and port not in _reserved_ports:
                _reserved_ports.add(port)
                return port
            start = port + 1
    raise DeploymentError(
        "No free port for a preview",
        details=f"Every port from {_PORT_RANGE[0]} to {_PORT_RANGE[1] - 1} is in use or "
        "belongs to an application. Remove previews or applications that are not needed.",
    )


def _release_port(port: int) -> None:
    """
    Stop holding a port reserved by :func:`_reserve_port`.

    Args:
        port: The port.
    """
    with _ports_guard:
        _reserved_ports.discard(port)


# ---------------------------------------------------------------------------
# What the jobs call: seams over the one implementation of each step
# ---------------------------------------------------------------------------


def _sequence(number: int) -> int:
    """
    Say which preview of an application a number is, for names made for it.

    Args:
        number: The preview's number.

    Returns:
        The pull request number, or the count of previews made by hand.
    """
    return number - MANUAL_BASE if number >= MANUAL_BASE else number


def _sync_from_parent(domain: str, parent: App, settings: PreviewSettings) -> None:
    """
    Bring an existing preview in line with its application's settings before it is rebuilt.

    The variables the settings gave it and no longer give are taken out; the
    set defined for previews is applied again (it is what an operator changed
    on purpose); what the preview already has of the application's is left as
    it is, and what it lacks is added. Its secret marks follow.

    Args:
        domain: The preview's domain.
        parent: The application previewed.
        settings: Its preview settings.

    Raises:
        NoustError: The variables could not be written.
        OSError: The ``.env`` could not be written.
    """
    from noust.deployers.helpers.app_env import read_environment_of, write_app_env

    store = get_store()
    child = store.get_app(domain)
    if child is None:
        return
    extras = PreviewStore(store)
    options = extras.options(parent.domain)
    origin = extras.origin(domain)
    wanted = preview_env.compose(parent, settings, options)
    current = read_environment_of(child)
    kept = set(wanted.values) | set(origin.owned_env)
    revoked = {name for name in origin.given_env if name not in kept}
    if options.inherit_env == INHERIT_ALL:
        # Before 3.4 a variable excluded since the preview was made was taken out here.
        revoked |= {name for name in settings.exclude_env if name not in origin.owned_env}
    explicit = set(preview_env.preview_set(parent))
    values = {name: value for name, value in current.values.items() if name not in revoked}
    audience = {name: kind for name, kind in current.audience.items() if name in values}
    for name, value in wanted.values.items():
        if name in explicit or name not in current.values:
            values[name] = value
            if name in wanted.audience:
                audience[name] = wanted.audience[name]
            else:
                audience.pop(name, None)
    marks = {name: mark for name, mark in child.env_secret_marks.items() if name not in revoked}
    marks.update({name: mark for name, mark in wanted.marks.items() if name in values})
    store.set_env_secret_marks(domain, marks)
    if values != current.values or audience != current.audience:
        write_app_env(child, values, audience=audience)
    if tuple(wanted.given) != origin.given_env:
        extras.save_origin(replace(origin, given_env=tuple(wanted.given)))


def _deploy(
    parent: App, record: PreviewRecord, context: JobContext, settings: PreviewSettings
) -> dict[str, Any]:
    """
    Create a preview's application, the way the console creates any application.

    Its row is created already linked to the parent and carrying the secret
    marks of the variables it is given, so the deployment's own events (GitHub
    deployment statuses, notifications) call it a preview and its log is
    scrubbed from the first line. It is given the variables its settings give
    (:func:`noust.managers.preview_env.compose`) and, when they ask for it, a
    database of its own, which is recorded for the preview before the job makes
    it, so it is dropped with the preview even when the job fails halfway.

    Args:
        parent: The application previewed.
        record: The preview.
        context: The running job's context.
        settings: The application's preview settings.

    Returns:
        What the deploy job returned.
    """
    from noust.web.jobs import deploy_app_job

    extras = PreviewStore()
    options = extras.options(parent.domain)
    environment = preview_env.compose(parent, settings, options)
    database: dict[str, Any] | None = None
    owned: tuple[str, ...] = ()
    databases: tuple[str, ...] = ()
    if options.data_mode != DATA_SHARED:
        wish = preview_env.database_wish(parent, _sequence(record.number))
        if wish is not None:
            database = {
                "engine": wish.engine,
                "name": wish.name,
                "env_var": wish.env_var,
                "extra_vars": wish.extra_vars,
                "instance": wish.instance,
            }
            owned = (wish.env_var, *(EXTRA_VARIABLES if wish.extra_vars else ()))
            databases = (f"{wish.engine}/{wish.name}",)
    origin = extras.origin(record.domain)
    extras.save_origin(
        replace(origin, given_env=tuple(environment.given), owned_env=owned, databases=databases)
    )
    port = _reserve_port()
    stack = _is_stack(parent)
    compose_file: str | None = None
    if stack:
        # The file the stack runs from is recorded in its unit, not in the store.
        from noust.deployers.docker_compose import stack_deployer

        compose_file = stack_deployer(parent).compose_file
    try:
        return deploy_app_job(
            domain=record.domain,
            source=parent.source,
            app_type=parent.app_type or "auto",
            port=port,
            branch=record.branch,
            env_vars=environment.values,
            webserver=parent.webserver,
            ssl=True,
            # A stack has no release layout, and its containers are not in its unit's cgroup:
            # limits are set in its compose file, and the deployer refuses them here.
            layout=None if stack else "releases",
            compose_file=compose_file,
            memory_max_mb=None if stack else PREVIEW_MEMORY_MB,
            cpu_quota_percent=None if stack else PREVIEW_CPU_PERCENT,
            trigger=DeploymentTrigger.WEBHOOK.value,
            github_installation_id=parent.github_installation_id,
            preview_parent=parent.domain,
            env_secret_marks=environment.marks,
            env_audience=environment.audience,
            database=database,
            job_context=context,
        )
    finally:
        _release_port(port)


def _update(domain: str, commit: str | None, context: JobContext) -> dict[str, Any]:
    """
    Update a preview to a commit, the way every application is updated.

    Args:
        domain: The preview's domain.
        commit: The pull request's head, or None for its branch's head.
        context: The running job's context.

    Returns:
        What the update returned.
    """
    from noust.web.jobs import run_update

    return run_update(
        domain, trigger=DeploymentTrigger.WEBHOOK.value, job_context=context, commit=commit
    )


def _delete(domain: str, on_phase: PhaseReporter | None, logger: Logger | None) -> tuple[str, ...]:
    """
    Remove a preview's application, the way every application is removed.

    Args:
        domain: The preview's domain.
        on_phase: Progress callback.
        logger: Where the details go.

    Returns:
        What could not be removed, as warnings.
    """
    from noust.deployers.lifecycle import delete_app

    outcome = delete_app(
        domain,
        remove_files=True,
        remove_certificate=True,
        # A stack's named volumes are the preview's own, made for it: they go with it.
        remove_volumes=True,
        on_phase=on_phase,
        logger=logger,
    )
    return tuple(outcome.warnings)


def _jobs() -> Any:
    """
    Return the console's job manager.

    Imported here because the CLI runs without the web layer's dependencies;
    only the webhook paths, which run inside the console, queue jobs.

    Returns:
        The job manager.
    """
    from noust.web.jobs import get_job_manager

    return get_job_manager()


def _job_type(name: str) -> Any:
    """
    Name a job type without importing the web layer at module import.

    Args:
        name: ``DEPLOY``, ``UPDATE`` or ``DELETE``.

    Returns:
        The :class:`noust.web.jobs.JobType` member.
    """
    from noust.web.jobs import JobType

    return JobType[name]


# ---------------------------------------------------------------------------
# Pull request events
# ---------------------------------------------------------------------------


def _expiry(ttl_hours: int) -> str:
    """
    Say when a preview pushed to now expires.

    Args:
        ttl_hours: The application's time-to-live.

    Returns:
        UTC ISO 8601, as the store compares it.
    """
    return _iso(_now() + timedelta(hours=ttl_hours))


def _set_status(
    record: PreviewRecord,
    status: str,
    error: str | None = None,
    *,
    expect: dict[str, Any] | None = None,
) -> PreviewRecord | None:
    """
    Record a preview's new status, and nothing else of the record.

    Only the status and the error are written: ``record`` may have been read
    before a push recorded a newer commit, which must not be put back.

    Args:
        record: The preview.
        status: One of the statuses above.
        error: Why it failed, for ``failed``.
        expect: Write only while the stored preview still has these values
            (see :meth:`noust.core.store.NoustStore.update_preview`).

    Returns:
        The preview as stored, or None when it is gone or no longer matches
        ``expect``.
    """
    return get_store().update_preview(
        record.parent_domain,
        record.number,
        {"status": status, "error": error},
        expect=expect,
    )


def handle_pull_request(event: PullRequestEvent, *, app_domain: str | None = None) -> list[str]:
    """
    Act on a pull request event for every application that previews it.

    Opened and updated pull requests get a preview built or rebuilt (a job
    each, in the console's job list, queued by ``webhook``); closed ones get
    theirs removed. A preview runs with the parent's production secrets (its
    build is sandboxed, but the running preview has them), so it is only
    built for people trusted with the repository: a pull request from a fork is refused, on GitHub so is one
    whose author is not an owner, member or collaborator, and one opened or
    pushed to by a bot unless the settings allow bots.

    Args:
        event: The pull request event.
        app_domain: Only this application (a per-application webhook knows
            which application it is for); None to find every application
            whose source is ``event.repository`` and has previews on.

    Returns:
        The ids of the jobs queued (one per preview built, rebuilt or
        removed); empty when nothing previews this repository.
    """
    targets = previewing_apps(event, app_domain=app_domain)
    if not targets:
        _log.info(
            "Pull request %s#%d: no application previews this repository",
            event.repository,
            event.number,
        )
        return []

    queued: list[str] = []
    for parent, settings in targets:
        refusal = None if event.action is PullRequestAction.CLOSED else _refusal(event, settings)
        if event.action is PullRequestAction.CLOSED:
            job_id = _queue_removal(parent, event)
        elif refusal is not None:
            _refuse(parent, event, *refusal)
            job_id = None
        else:
            job_id = _queue_build(parent, settings, event)
        if job_id is not None:
            queued.append(job_id)
    return queued


def _refusal(event: PullRequestEvent, settings: PreviewSettings) -> tuple[str, str] | None:
    """
    Decide whether a pull request is one a preview may be built for.

    Args:
        event: An opened or updated pull request.
        settings: The previewed application's settings.

    Returns:
        None when it may; otherwise why not, once for the log and once for
        the pull request (Markdown, the rest of the sentence "No preview for
        this pull request: ...").
    """
    if event.from_fork:
        return ("its branch lives in a fork", "its branch lives in a fork.")
    if event.bot:
        if settings.allow_bots:
            return None
        who = event.author if event.sender in ("", event.author) else event.sender
        return (
            f"{who or 'its author'} is a bot account and previews do not allow bots",
            f"it comes from a bot account (`{who}`). Bot pull requests get a preview "
            f"only when previews allow bots (`noust preview enable {settings.app_domain} "
            "--allow-bots`).",
        )
    if event.forge is Forge.GITHUB and event.author_association not in TRUSTED_ASSOCIATIONS:
        association = (event.author_association or "unknown").lower().replace("_", " ")
        return (
            f"its author {event.author or '(unknown)'} is {association}, not an owner, "
            "member or collaborator of the repository",
            f"its author (`{event.author or 'unknown'}`) is not an owner, member or "
            "collaborator of the repository.",
        )
    return None


def _refuse(parent: App, event: PullRequestEvent, reason: str, explanation: str) -> None:
    """
    Refuse to preview a pull request, and say so.

    Args:
        parent: The application that would have been previewed.
        event: The pull request.
        reason: Why, for the log.
        explanation: Why, for the pull request.
    """
    _log.warning(
        "Refused a preview of %s for %s#%d: %s. A preview runs with %s's production secrets",
        parent.domain,
        event.repository,
        event.number,
        reason,
        parent.domain,
    )
    # Said once, when it is opened: every push would otherwise add a comment.
    if event.action is PullRequestAction.OPENED:
        _upsert_comment(
            event.forge.value,
            event.repository,
            event.number,
            f"**Noust preview** of `{parent.domain}`\n\n"
            f"No preview for this pull request: {explanation} A preview runs on the "
            f"server with a copy of `{parent.domain}`'s environment variables, production "
            "secrets included, so only pull requests from people trusted with this "
            "repository get one.",
        )


def _queue_build(parent: App, settings: PreviewSettings, event: PullRequestEvent) -> str | None:
    """
    Record a preview as pending and queue the job that builds it.

    Args:
        parent: The application previewed.
        settings: Its preview settings.
        event: An opened or updated pull request.

    Returns:
        The job id, or None when the preview was refused (quota, a name
        collision).
    """
    # Counting and recording under one lock: two pull requests opened at once
    # must not both take the last place.
    with _quota_guard:
        return _record_and_queue(parent, settings, event)


def _record_and_queue(
    parent: App, settings: PreviewSettings, event: PullRequestEvent
) -> str | None:
    """
    The body of :func:`_queue_build`, run under :data:`_quota_guard`.

    Args:
        parent: The application previewed.
        settings: Its preview settings.
        event: An opened or updated pull request.

    Returns:
        The job id, or None when the preview was refused.
    """
    store = get_store()
    existing = store.get_preview(parent.domain, event.number)
    if (
        existing is not None
        and event.forge is Forge.BITBUCKET
        and event.action is PullRequestAction.UPDATED
        and event.head_sha
        and existing.head_sha == event.head_sha
        and existing.status in (PENDING, DEPLOYING, READY)
    ):
        # An update that moved nothing. Bitbucket sends pullrequest:updated for
        # a new title or description as well as for new commits, and says
        # nothing of which it was (GitLab says so with ``oldrev``, which is
        # read where its event is translated), so a preview that is built, or
        # being built, from this very commit has nothing to rebuild. A failed
        # one is not skipped: the same commit again is how an operator retries.
        _log.info(
            "Pull request %s#%d is already at %s: nothing to rebuild",
            event.repository,
            event.number,
            event.head_sha[:12],
        )
        return None
    if existing is None:
        others = [p for p in store.list_previews(parent.domain) if p.number != event.number]
        if len(others) >= settings.max_previews:
            _log.warning(
                "No preview of %s for %s#%d: it has %d of %d already",
                parent.domain,
                event.repository,
                event.number,
                len(others),
                settings.max_previews,
            )
            if event.action is PullRequestAction.OPENED:
                _upsert_comment(
                    event.forge.value,
                    event.repository,
                    event.number,
                    f"**Noust preview** of `{parent.domain}`\n\n"
                    f"No preview for this pull request: the limit of {settings.max_previews} "
                    "previews at once is reached. Close another pull request, or raise the "
                    f"limit with `noust preview enable {parent.domain} --max N`, and push again.",
                )
            return None
        try:
            domain = preview_domain_for(parent.domain, event.number, settings.base_domain)
        except ValidationError as exc:
            _log.error("No preview of %s for #%d: %s", parent.domain, event.number, exc)
            return None
    else:
        domain = existing.domain

    occupant = store.get_app(domain)
    if occupant is not None and occupant.preview_parent != parent.domain:
        _log.error(
            "No preview of %s for #%d: %s is already an application that is not this preview",
            parent.domain,
            event.number,
            domain,
        )
        return None

    return _save_and_queue(
        parent,
        settings,
        number=event.number,
        domain=domain,
        branch=event.branch,
        provider=event.forge.value,
        repository=event.repository,
        head_sha=event.head_sha or None,
        occupied=occupant is not None,
        actor="webhook",
        trigger="webhook",
    )


def _save_and_queue(
    parent: App,
    settings: PreviewSettings,
    *,
    number: int,
    domain: str,
    branch: str,
    provider: str,
    repository: str | None,
    head_sha: str | None,
    occupied: bool,
    actor: str,
    trigger: str,
    queue: bool = True,
) -> str | None:
    """
    Record a preview as pending and queue the job that builds it.

    The one tail of a preview a webhook asks for and one an operator makes by
    hand, so both are recorded and queued the same way.

    Args:
        parent: The application previewed.
        settings: Its preview settings.
        number: The preview's number.
        domain: Its domain.
        branch: The branch (or tag) it deploys.
        provider: ``github``, ``gitlab``, ``gitea``, ``bitbucket`` or ``manual``.
        repository: ``owner/repo``, when known.
        head_sha: The commit to build, when known.
        occupied: Its application already exists (an update, not a deploy).
        actor: Who asked, for the job list.
        trigger: What the history says started it.
        queue: Queue the build. False records the preview and leaves the build
            to the caller, which runs it in its own process (the terminal).

    Returns:
        The job id, or None when nothing was queued: the queue had no room (the
        preview is then recorded as failed, saying so), or ``queue`` is False.
    """
    store = get_store()
    record = store.save_preview(
        PreviewRecord(
            parent_domain=parent.domain,
            domain=domain,
            number=number,
            branch=branch,
            provider=provider,
            expires_at=_expiry(settings.ttl_hours),
            head_sha=head_sha,
            repository=repository,
            # None keeps the stored one: a build may have posted it since.
            comment_ref=None,
            status=PENDING,
            error=None,
        )
    )
    if not queue:
        return None
    first = not occupied
    what = "pull request" if number < MANUAL_BASE else "branch or tag"
    label = f"#{number}" if number < MANUAL_BASE else branch
    try:
        job = _jobs().create_job(
            job_type=_job_type("DEPLOY" if first else "UPDATE"),
            name=f"{'Preview' if first else 'Update preview'} {label} of {parent.domain}",
            description=f"{'Deploying' if first else 'Updating'} the preview of {what} "
            f"{label} ({record.branch}) at {domain}",
            func=preview_deploy_job,
            kwargs={"parent_domain": parent.domain, "number": record.number},
            metadata={
                "domain": domain,
                "parent": parent.domain,
                "preview": record.number,
                "branch": record.branch,
                "trigger": trigger,
                "provider": record.provider,
            },
            actor=actor,
        )
    except QueueFullError as exc:
        # The deploy queue has no room: the preview is not built, and says so
        # instead of staying "pending" for a job that does not exist. The next
        # push to the pull request asks again.
        _log.warning("No preview of %s for %s: %s", parent.domain, label, exc.message)
        _set_status(record, FAILED, exc.message)
        return None
    return str(job.id)


def _queue_removal(parent: App, event: PullRequestEvent) -> str | None:
    """
    Queue the removal of a closed pull request's preview.

    Args:
        parent: The application previewed.
        event: The closed pull request.

    Returns:
        The job id, or None when the pull request had no preview.
    """
    record = get_store().get_preview(parent.domain, event.number)
    if record is None:
        return None
    return str(queue_preview_removal(record, reason="closed", actor="webhook").id)


def queue_preview_removal(record: PreviewRecord, *, reason: str, actor: str | None) -> Job:
    """
    Mark a preview as being removed and queue its removal as a console job.

    Args:
        record: The preview.
        reason: Why, for the pull request comment: ``closed``, ``removed``,
            ``disabled``.
        actor: Who asked, for the job list.

    Returns:
        The queued job.
    """
    _set_status(record, REMOVING)
    job: Job = _jobs().create_job(
        job_type=_job_type("DELETE"),
        name=f"Remove preview {record.domain}",
        description=f"Removing the preview of #{record.number} at {record.domain}",
        func=preview_remove_job,
        kwargs={"domain": record.domain, "reason": reason},
        metadata={"domain": record.domain, "parent": record.parent_domain, "reason": reason},
        actor=actor,
    )
    return job


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def _adopt(domain: str, parent: App) -> None:
    """
    Make sure a freshly created application is marked as a preview of its parent.

    The deployment creates its row already linked (see :func:`_deploy`);
    this is the check afterwards, done whether or not it succeeded: only a
    row marked as a preview is ever removed by this module, and a failed
    deployment must not leave one it cannot remove.

    Args:
        domain: The preview's domain.
        parent: The application previewed.
    """
    store = get_store()
    child = store.get_app(domain)
    if child is None:
        return
    if child.preview_parent is None:
        store.set_preview_parent(domain, parent.domain)
    elif child.preview_parent != parent.domain:
        return
    if parent.github_installation_id is not None and child.github_installation_id is None:
        store.set_github_installation(domain, parent.github_installation_id)


def preview_deploy_job(
    parent_domain: str, number: int, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Build or rebuild the preview of a pull request.

    What to build is read when the job runs, not when it was queued. A push
    while a build of the same preview runs does not wait for it in a job
    slot of its own: its job returns at once, and the running build, seeing
    the preview pending again when it finishes, builds once more at the new
    head. So N quick pushes cost one running build and N-1 jobs that end at
    once, not N of the console's job slots.

    Args:
        parent_domain: The application previewed.
        number: The pull request number.
        job_context: Injected by the job manager.

    Returns:
        What was done: the last deploy or update summary, plus the preview;
        ``status`` is ``skipped`` when there was nothing to build and
        ``coalesced`` when the running build takes this push over.

    Raises:
        DeploymentError: The parent is gone.
        NoustError: The last build failed; the preview is marked failed.
    """
    if job_context is None:
        raise ValueError("job_context is required; job functions run under the job manager")
    context = job_context
    store = get_store()
    summary = {"parent": parent_domain, "number": number}
    with _build_guard:
        record = store.get_preview(parent_domain, number)
        if record is None:
            context.log(f"The preview of #{number} was removed before its build started")
            return {"status": "skipped", **summary}
        if record.status in (READY, FAILED, REMOVING):
            # READY or FAILED: a build that ran when this was pushed built it.
            context.log(f"Nothing to build: the preview of #{number} is {record.status}")
            return {"status": "skipped", "preview": record.domain, **summary}
        if record.domain in _building:
            context.log(
                f"A build of {record.domain} is running; it builds "
                f"{(record.head_sha or record.branch)[:12]} next"
            )
            return {"status": "coalesced", "preview": record.domain, **summary}
        _building.add(record.domain)
    domain = record.domain
    released = False
    try:
        while True:
            failure: NoustError | None = None
            result: dict[str, Any] = {}
            attempt = _Attempt()
            try:
                result = _build_once(parent_domain, number, context, attempt)
            except NoustError as exc:
                failure = exc
            with _build_guard:
                latest = store.get_preview(parent_domain, number)
                # Only after a build was claimed: a preview left pending by a
                # refusal before it (the parent gone, previews off) would
                # otherwise be tried forever.
                if attempt.claimed and latest is not None and latest.status == PENDING:
                    # Pushed to while this built: build the new head too.
                    context.log(
                        f"{domain} was pushed to while it built; building "
                        f"{(latest.head_sha or latest.branch)[:12]}"
                    )
                    continue
                _building.discard(domain)
                released = True
            if failure is not None:
                raise failure
            return {**result, "preview": domain, **summary}
    finally:
        if not released:
            with _build_guard:
                _building.discard(domain)


class _Attempt:
    """
    What one pass of a build job got to.

    Attributes:
        claimed: The pass tried to claim the preview for its build, so any
            change to the record since is someone else's: a push to build
            next, or a close.
    """

    def __init__(self) -> None:
        self.claimed = False


def _build_once(
    parent_domain: str, number: int, context: JobContext, attempt: _Attempt | None = None
) -> dict[str, Any]:
    """
    Build a preview once, at the head its record names now.

    Args:
        parent_domain: The application previewed.
        number: The pull request number.
        context: The running job's context.
        attempt: Told whether the pass got as far as claiming the preview.

    Returns:
        The deploy or update summary; ``status`` is ``skipped`` when the
        preview was removed, is being removed, changed before the build
        could claim it, or previews were turned off.

    Raises:
        DeploymentError: The parent is gone, or the domain is a real
            application.
        NoustError: The deploy or update failed; the preview is marked
            failed unless it changed meanwhile.
    """
    store = get_store()
    record = store.get_preview(parent_domain, number)
    if record is None:
        return {"status": "skipped"}
    with _preview_lock(record.domain):
        record = store.get_preview(parent_domain, number)
        if record is None or record.status == REMOVING:
            context.log(f"The preview of #{number} is being removed; nothing to build")
            return {"status": "skipped"}
        parent = store.get_app(parent_domain)
        if parent is None:
            raise DeploymentError(
                f"The application {parent_domain} is gone",
                details="Its previews are removed with it; nothing to build.",
            )
        settings = store.get_preview_settings(parent_domain)
        if settings is None:
            context.log(f"Previews are off for {parent_domain}; nothing to build")
            return {"status": "skipped"}

        child = store.get_app(record.domain)
        existed = child is not None
        if child is not None and child.preview_parent != parent_domain:
            _set_status(
                record, FAILED, f"{record.domain} is an application that is not this preview"
            )
            raise DeploymentError(
                f"{record.domain} is already an application that is not a preview of "
                f"{parent_domain}",
                details="It was left alone. Remove the preview record with "
                f"'noust preview remove {record.domain}'.",
            )
        built = record.head_sha
        if attempt is not None:
            attempt.claimed = True
        claimed = _set_status(
            record, DEPLOYING, expect={"status": record.status, "head_sha": built}
        )
        if claimed is None:
            # Pushed to or closed since it was read: the caller looks again.
            return {"status": "skipped"}
        record = _refresh_comment(claimed)
        context.log(
            f"{'Updating' if existed else 'Creating'} {record.domain} from {record.branch}"
            + (f" at {record.head_sha[:12]}" if record.head_sha else "")
        )
        succeeded = False
        error: str | None = None
        try:
            if existed:
                _sync_from_parent(record.domain, parent, settings)
                result = _update(record.domain, record.head_sha, context)
            else:
                result = _deploy(parent, record, context, settings)
            succeeded = True
        except NoustError as exc:
            error = exc.message
            raise
        finally:
            if not existed:
                _adopt(record.domain, parent)
            # Only over the build this claimed: a push since (pending again,
            # at a newer head) is built next, and a close since (removing)
            # is not undone.
            if succeeded:
                final = _set_status(record, READY, expect={"status": DEPLOYING, "head_sha": built})
            else:
                final = _set_status(
                    record,
                    FAILED,
                    error or "The build failed; see the job log",
                    expect={"status": DEPLOYING, "head_sha": built},
                )
            if final is not None:
                _refresh_comment(final)
    return result


def preview_remove_job(
    domain: str, reason: str = "removed", job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Remove one preview, as a console job.

    Args:
        domain: The preview's domain.
        reason: Why, for the pull request comment.
        job_context: Injected by the job manager.

    Returns:
        What was removed.

    Raises:
        NoustError: It is not a preview, or its application could not be
            removed.
    """
    if job_context is None:
        raise ValueError("job_context is required; job functions run under the job manager")
    context = job_context
    context.set_metadata("domain", domain)
    removed, warnings = _remove(
        validate_domain(domain),
        reason=reason,
        on_phase=lambda index, total, message: context.update(message, 100 * (index - 1) // total),
        logger=None,
        only_if_removing=True,
    )
    for warning in warnings:
        context.log(warning, "warning")
    if not removed:
        context.update("Preview kept", 100)
        return {"domain": domain, "status": "kept", "reason": reason}
    context.update("Preview removed", 100)
    return {"domain": domain, "status": "removed", "reason": reason}


def remove_previews_job(
    parent_domain: str, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Remove every preview of an application, as a console job.

    Args:
        parent_domain: The application previewed.
        job_context: Injected by the job manager.

    Returns:
        The domains removed.

    Raises:
        DeploymentError: Some could not be removed; the rest were.
    """
    if job_context is None:
        raise ValueError("job_context is required; job functions run under the job manager")
    job_context.set_metadata("domain", parent_domain)
    job_context.update("Removing previews", 10)
    removed = remove_previews_of(parent_domain)
    job_context.update("Previews removed", 100)
    return {"domain": parent_domain, "removed": removed}


# ---------------------------------------------------------------------------
# Made by hand, and managed one by one
# ---------------------------------------------------------------------------


def build_in_process(parent_domain: str, number: int, logger: Logger) -> dict[str, Any]:
    """
    Build a preview here, in this process, instead of as a console job.

    What the terminal does after recording a preview: it has no console to queue
    the build in, and waits for it.

    Args:
        parent_domain: The application previewed.
        number: The preview's number.
        logger: Where progress is printed.

    Returns:
        What the build job returned.

    Raises:
        NoustError: The build failed; the preview is marked failed.
    """
    # The job function only needs something to tell progress to.
    context = cast("JobContext", TerminalContext(logger))
    return preview_deploy_job(parent_domain, number, job_context=context)


@dataclass(frozen=True)
class CreatedPreview:
    """
    A preview made by hand.

    Attributes:
        record: The preview, pending.
        job_id: The job building it; None when the caller builds it itself.
        kind: ``pr``, ``branch`` or ``tag``.
    """

    record: PreviewRecord
    job_id: str | None
    kind: str


def list_pull_requests(parent_domain: str) -> list[dict[str, Any]]:
    """
    List the open pull requests of an application's repository, with their previews.

    Args:
        parent_domain: The application.

    Returns:
        One entry per open pull request: its number, title, branch, head
        commit, author and whether it comes from a fork, a draft or a bot,
        the preview it already has (its status) and whether Noust would build
        one for it on its own.

    Raises:
        ValidationError: The application cannot have previews.
        IntegrationError: There is no GitHub App that reaches the repository,
            or GitHub could not be asked.
    """
    parent = _require_previewable(parent_domain)
    key = repository_key(parent.source)
    if key is None:
        return []
    existing = {record.number: record for record in get_store().list_previews(parent.domain)}
    out = []
    for pull in pulls.list_open(key[1], parent.github_installation_id):
        record = existing.get(pull.number)
        out.append(
            {
                "number": pull.number,
                "title": pull.title,
                "branch": pull.branch,
                "base_branch": pull.base_branch,
                "head_sha": pull.head_sha,
                "author": pull.author,
                "draft": pull.draft,
                "from_fork": pull.from_fork,
                "trusted": pull.author_association in TRUSTED_ASSOCIATIONS and not pull.from_fork,
                "url": pull.url,
                "preview": record.domain if record is not None else None,
                "status": record.status if record is not None else None,
            }
        )
    return out


def create_preview(
    parent_domain: str,
    *,
    pull_request: int | None = None,
    branch: str | None = None,
    tag: str | None = None,
    allow_fork: bool = False,
    actor: str = "cli",
    queue: bool = True,
) -> CreatedPreview:
    """
    Make a preview by hand, from an open pull request, a branch or a tag.

    What a webhook does for a pull request, asked for: the same record, the same
    limits (at most the application's number of previews, one domain each) and the
    same build. A preview of a pull request needs the GitHub App to read the
    branch it comes from; one of a branch or a tag needs nothing but the
    repository. A pull request from a fork is refused unless the operator says
    ``allow_fork``: its code runs on this server.

    Args:
        parent_domain: The application previewed.
        pull_request: The number of an open pull request.
        branch: A branch.
        tag: A tag.
        allow_fork: Preview a pull request whose branch lives in a fork.
        actor: Who asked.
        queue: Queue the build as a console job. False leaves it to the caller.

    Returns:
        The preview, recorded as pending, and the job building it.

    Raises:
        ValidationError: Previews are off; not exactly one of a pull request, a
            branch and a tag; a pull request that is not open, or comes from a
            fork; a ref git would refuse; or the limit of previews is reached.
        IntegrationError: The pull request cannot be read.
    """
    require_server_role("Preview environments")
    parent = _require_previewable(parent_domain)
    store = get_store()
    settings = store.get_preview_settings(parent.domain)
    if settings is None:
        raise ValidationError(
            f"Previews are off for {parent.domain}",
            details=f"Turn them on first: noust preview enable {parent.domain} --domain BASE",
        )
    asked = [value for value in (pull_request, branch, tag) if value is not None]
    if len(asked) != 1:
        raise ValidationError(
            "Name exactly one of a pull request, a branch or a tag",
            details="For example: --pr 42, --branch feature/login or --tag v1.4.0.",
        )
    extras = PreviewStore(store)
    provider = "manual"
    repository: str | None = None
    head_sha: str | None = None
    if pull_request is not None:
        key = repository_key(parent.source)
        if key is None or pull_request < 1 or pull_request >= MANUAL_BASE:
            raise ValidationError(
                f"Not a pull request number: {pull_request}", field="pull_request"
            )
        pull = pulls.get(key[1], pull_request, parent.github_installation_id)
        if pull is None:
            raise ValidationError(
                f"Pull request #{pull_request} is not open",
                details="A closed or merged pull request has no preview to make.",
                field="pull_request",
            )
        if pull.from_fork and not allow_fork:
            raise ValidationError(
                f"Pull request #{pull_request} comes from a fork",
                details="Its code would run on this server. Preview it from a branch you "
                "trust, or say so with --allow-fork.",
                field="pull_request",
            )
        number, kind, ref = pull_request, KIND_PR, pull.branch
        provider, repository, head_sha = "github", key[1], pull.head_sha
    else:
        ref = validate_git_ref(branch or tag or "")
        kind = KIND_BRANCH if branch is not None else KIND_TAG
        number = 0
    with _quota_guard:
        if number == 0:
            number = extras.next_manual_number(parent.domain)
        existing = store.get_preview(parent.domain, number)
        if existing is None:
            held = [p for p in store.list_previews(parent.domain) if p.number != number]
            if len(held) >= settings.max_previews:
                raise ValidationError(
                    f"{parent.domain} has {len(held)} of {settings.max_previews} previews",
                    details="Remove one (noust preview remove DOMAIN), or raise the limit: "
                    f"noust preview enable {parent.domain} --max N",
                )
            domain = preview_domain_for(parent.domain, number, settings.base_domain)
        else:
            domain = existing.domain
        occupant = store.get_app(domain)
        if occupant is not None and occupant.preview_parent != parent.domain:
            raise ValidationError(
                f"{domain} is already an application that is not a preview of {parent.domain}",
                details="Choose another base domain for previews.",
            )
        old = extras.origin(domain)
        extras.save_origin(
            replace(
                old,
                kind=kind,
                ref=None if kind == KIND_PR else ref,
                created_by=actor,
            )
        )
        job_id = _save_and_queue(
            parent,
            settings,
            number=number,
            domain=domain,
            branch=ref,
            provider=provider,
            repository=repository,
            head_sha=head_sha,
            occupied=occupant is not None,
            actor=actor,
            trigger="manual",
            queue=queue,
        )
    record = store.get_preview(parent.domain, number)
    if record is None:
        raise DeploymentError(f"The preview of {ref} was not recorded")
    return CreatedPreview(record, job_id, kind)


def _preview_of(domain: str) -> tuple[PreviewRecord, App, PreviewSettings]:
    """
    Find a preview, its application and that application's settings.

    Args:
        domain: The preview's domain.

    Returns:
        The three.

    Raises:
        ValidationError: Nothing at that domain is a preview, its application is
            gone, or previews are off.
    """
    store = get_store()
    record = store.get_preview_by_domain(validate_domain(domain))
    if record is None:
        raise ValidationError(
            f"{domain} is not a preview",
            details="List previews with 'noust preview list'.",
        )
    parent = store.get_app(record.parent_domain)
    settings = store.get_preview_settings(record.parent_domain)
    if parent is None or settings is None:
        raise ValidationError(
            f"Previews are off for {record.parent_domain}",
            details="Turn them on again, or remove this preview.",
        )
    return record, parent, settings


def redeploy_preview(domain: str, *, actor: str = "cli", queue: bool = True) -> CreatedPreview:
    """
    Build a preview again, from the head of its branch as it is now.

    Counts as activity: the preview's time-to-live starts again.

    Args:
        domain: The preview's domain.
        actor: Who asked.
        queue: Queue the build as a console job. False leaves it to the caller.

    Returns:
        The preview, pending, and the job building it.

    Raises:
        ValidationError: Not a preview, or it is being built or removed.
    """
    require_server_role("Preview environments")
    record, parent, settings = _preview_of(domain)
    if record.status in (DEPLOYING, REMOVING):
        raise ValidationError(
            f"{record.domain} is {'being built' if record.status == DEPLOYING else 'being removed'}",
            details="Wait for it to finish.",
        )
    head = record.head_sha
    origin = PreviewStore().origin(record.domain)
    if origin.kind == KIND_PR and record.repository:
        try:
            pull = pulls.get(record.repository, record.number, parent.github_installation_id)
        except IntegrationError:
            pull = None
        if pull is not None:
            head = pull.head_sha
    with _quota_guard:
        job_id = _save_and_queue(
            parent,
            settings,
            number=record.number,
            domain=record.domain,
            branch=record.branch,
            provider=record.provider,
            repository=record.repository,
            head_sha=head,
            occupied=get_store().get_app(record.domain) is not None,
            actor=actor,
            trigger="manual",
            queue=queue,
        )
    fresh = get_store().get_preview(record.parent_domain, record.number)
    if fresh is None:
        raise DeploymentError(f"The preview {record.domain} vanished while it was queued")
    return CreatedPreview(fresh, job_id, origin.kind)


def _control(domain: str, action: str) -> None:
    """
    Stop or start the process a preview runs as.

    Args:
        domain: The preview's domain.
        action: ``stop`` or ``start``.

    Raises:
        ValidationError: Nothing runs there to control.
        ServiceError: systemd refuses.
    """
    from noust.managers.service_manager import ServiceManager

    manager = ServiceManager(verbose=False)
    name = domain_to_app_name(domain)
    if not manager.service_exists(name):
        raise ValidationError(
            f"{domain} has no service to {action}",
            details="It was never built, or its build failed before a unit was made.",
        )
    getattr(manager, action)(name)


def stop_preview(domain: str) -> PreviewRecord:
    """
    Stop a preview without removing it: its code, database and certificate stay.

    It expires on its own time as any preview does; a push or a redeploy starts it again.

    Args:
        domain: The preview's domain.

    Returns:
        The preview, stopped.

    Raises:
        ValidationError: Not a preview, or nothing runs.
        ServiceError: systemd refuses.
    """
    record, _parent, _settings = _preview_of(domain)
    if record.status in (DEPLOYING, REMOVING):
        raise ValidationError(f"{record.domain} is {record.status}: wait for it to finish")
    _control(record.domain, "stop")
    stopped = _set_status(record, STOPPED) or record
    return _refresh_comment(stopped)


def start_preview(domain: str) -> PreviewRecord:
    """
    Start a preview that was stopped.

    Args:
        domain: The preview's domain.

    Returns:
        The preview, ready.

    Raises:
        ValidationError: Not a preview, or nothing runs.
        ServiceError: systemd refuses.
    """
    record, _parent, _settings = _preview_of(domain)
    if record.status in (DEPLOYING, REMOVING):
        raise ValidationError(f"{record.domain} is {record.status}: wait for it to finish")
    _control(record.domain, "start")
    started = _set_status(record, READY) or record
    return _refresh_comment(started)


def last_build_log(domain: str, tail: int = 20_000) -> str | None:
    """
    Read the captured log of a preview's newest build.

    Args:
        domain: The preview's domain.
        tail: Bytes to return, from the end.

    Returns:
        The log, scrubbed of secrets when it was captured; None when the preview
        was never built or its log is gone.
    """
    from noust.deployers.logs import read_deployment_log

    recent = get_store().list_deployments(domain, limit=1)
    if not recent:
        return None
    result = read_deployment_log(recent[0], tail=tail)
    return result.content or None


def _drop_databases(databases: Collection[str]) -> tuple[str, ...]:
    """
    Drop the databases that were made for a preview.

    Args:
        databases: ``engine/name`` of each.

    Returns:
        What could not be dropped, as warnings; the rest were.
    """
    if not databases:
        return ()
    from noust.managers.database.service import DatabaseService

    service = DatabaseService()
    warnings: list[str] = []
    for entry in databases:
        engine, _, name = entry.partition("/")
        try:
            service.drop(engine, name, force=True, keep_backup=False, unlink=True)
        except NoustError as exc:
            warnings.append(f"The database {entry} of the preview was not dropped: {exc.message}")
    return tuple(warnings)


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


def remove_preview(
    domain: str,
    *,
    reason: str = "removed",
    on_phase: PhaseReporter | None = None,
    logger: Logger | None = None,
) -> tuple[str, ...]:
    """
    Remove a preview: its application (files, unit, site, certificate) and its record.

    An application is only ever deleted here when the store says it is the
    preview of the application the record names: a domain someone deployed a
    real application at is never taken down because a record points at it.

    Args:
        domain: The preview's domain.
        reason: Why, for the pull request comment.
        on_phase: Progress callback for the deletion.
        logger: Where the details go.

    Returns:
        What could not be removed, as warnings; the rest was.

    Raises:
        ValidationError: Nothing at that domain is a preview.
        AppBusyError: Another operation is running on the preview. Its record
            stays ``removing``, and the sweep tries again.
    """
    return _remove(validate_domain(domain), reason=reason, on_phase=on_phase, logger=logger)[1]


def _remove(
    domain: str,
    *,
    reason: str,
    on_phase: PhaseReporter | None,
    logger: Logger | None,
    only_if_removing: bool = False,
) -> tuple[bool, tuple[str, ...]]:
    """
    Remove a preview, holding its lock; see :func:`remove_preview`.

    Args:
        domain: The preview's domain, validated.
        reason: Why, for the pull request comment.
        on_phase: Progress callback for the deletion.
        logger: Where the details go.
        only_if_removing: Remove it only if its record is still
            ``removing``. A queued removal passes this: a pull request closed
            and reopened before the removal ran has a pending preview again,
            which must not be lost.

    Returns:
        Whether it was removed, and the warnings.

    Raises:
        ValidationError: Nothing at that domain is a preview.
        AppBusyError: Another operation is running on the preview.
    """
    store = get_store()
    with _preview_lock(domain):
        record = store.get_preview_by_domain(domain)
        app = store.get_app(domain)
        if only_if_removing and (record is None or record.status != REMOVING):
            if record is None:
                return False, (f"{domain} was already removed",)
            return False, (
                f"{domain} was not removed: its pull request was pushed to or reopened "
                "after the removal was asked for",
            )
        if record is None and (app is None or not app.preview_parent):
            raise ValidationError(
                f"{domain} is not a preview",
                details="List previews with 'noust preview list'. Remove an application "
                "with 'noust delete'.",
            )
        warnings: tuple[str, ...] = ()
        extras = PreviewStore(store)
        removed_app = app is None
        if record is not None and record.status != REMOVING:
            record = _set_status(record, REMOVING) or record
        if app is not None:
            owner = record.parent_domain if record is not None else app.preview_parent
            if app.preview_parent and app.preview_parent == owner:
                warnings = _delete(domain, on_phase, logger)
                removed_app = True
            else:
                warnings = (
                    f"{domain} is an application that is not a preview of {owner}; "
                    "it was left alone and only the preview record was removed",
                )
        if removed_app:
            # The databases made for the preview go with it, once nothing uses them.
            warnings = (*warnings, *_drop_databases(extras.origin(domain).databases))
            extras.forget_origin(domain)
        if record is None:
            return True, warnings
        # Only a record still being removed: one reopened meanwhile is pending
        # again, and its build deploys it afresh.
        if not store.delete_preview(domain, status=REMOVING):
            return True, (
                *warnings,
                f"{domain} was reopened while it was removed; it is built again",
            )
        _refresh_comment(record, removed_because=reason)
    return True, warnings


def remove_previews_of(parent_domain: str, *, logger: Logger | None = None) -> list[str]:
    """
    Remove every preview of an application and turn its previews off.

    What deleting the application, or ``noust preview disable``, calls, so no
    preview outlives the application it copies.

    Args:
        parent_domain: The application previewed.
        logger: Where progress is reported.

    Returns:
        The domains of the previews removed.

    Raises:
        DeploymentError: Some previews could not be removed; every other one
            was, and previews are off either way.
    """
    store = get_store()
    store.delete_preview_settings(parent_domain)
    PreviewStore(store).forget_options(parent_domain)
    domains = [record.domain for record in store.list_previews(parent_domain)]
    # A preview whose record was lost is still this application's copy.
    domains += [
        app.domain
        for app in store.list_apps()
        if app.preview_parent == parent_domain and app.domain not in domains
    ]
    removed: list[str] = []
    failures: list[str] = []
    for domain in domains:
        try:
            for warning in remove_preview(domain, reason="disabled", logger=logger):
                _report(logger, warning)
        except NoustError as exc:
            failures.append(f"{domain}: {exc}")
            continue
        removed.append(domain)
    refresh_sweep_timer(logger)
    if failures:
        raise DeploymentError(
            f"Could not remove {len(failures)} preview(s) of {parent_domain}",
            details="; ".join(failures) + ". Remove them with 'noust preview remove DOMAIN'.",
        )
    return removed


def sweep(*, logger: Logger | None = None) -> list[str]:
    """
    Remove the previews whose time is up, those whose parent is gone, and orphans.

    What ``noust preview sweep`` does, hourly, from ``noust-previews.timer``. A
    preview that cannot be removed now (it is being built) is left for the
    next run, and so is retried one whose removal was asked for but did not
    finish (another operation held it, or the console stopped).

    Args:
        logger: Where progress is reported.

    Returns:
        The domains of the previews removed.
    """
    store = get_store()
    now = _iso(_now())
    due: dict[str, str] = {record.domain: "expired" for record in store.list_expired_previews(now)}
    records = store.list_previews()
    stuck = _iso(_now() - _REMOVING_GRACE)
    for record in records:
        if record.domain in due:
            continue
        if store.get_app(record.parent_domain) is None:
            due[record.domain] = "removed"
        elif record.status == REMOVING and (record.updated_at or "") <= stuck:
            due[record.domain] = "retried"
    # A preview application whose record is gone (its pull request was closed
    # by another process while it was being created) has nothing left to
    # expire it but this.
    recorded = {record.domain for record in records}
    for app in store.list_apps():
        if app.preview_parent and app.domain not in recorded:
            due.setdefault(app.domain, "removed")
    removed: list[str] = []
    for domain, reason in due.items():
        try:
            for warning in remove_preview(domain, reason=reason, logger=logger):
                _report(logger, warning)
        except NoustError as exc:
            _report(logger, f"Could not remove {domain} now: {exc}")
            continue
        removed.append(domain)
    refresh_sweep_timer(logger)
    return removed


def _report(logger: Logger | None, message: str) -> None:
    """
    Report a warning to the operator when one is watching, and to the log always.

    Args:
        logger: The CLI's logger, if any.
        message: What to say.
    """
    _log.warning("%s", message)
    if logger is not None:
        logger.warning(message)


def refresh_sweep_timer(logger: Logger | None = None) -> None:
    """
    Bring the sweep timer in line with what exists, reporting a failure instead of raising it.

    Called after a removal, which has already happened and must be reported
    as done; a timer left installed with nothing to sweep only costs an
    hourly no-op.

    Args:
        logger: Where a failure is reported.
    """
    try:
        sync_sweep_timer()
    except NoustError as exc:
        _report(logger, f"Could not update {SWEEP_UNIT}.timer: {exc}")


# ---------------------------------------------------------------------------
# The sweep timer
# ---------------------------------------------------------------------------


def _templates() -> Environment:
    """
    Load the systemd templates.

    Returns:
        A Jinja environment over ``templates/systemd``.
    """
    return Environment(
        loader=PackageLoader("noust", "templates/systemd"),
        trim_blocks=True,
        lstrip_blocks=True,
        # Systemd units are not markup: HTML escaping would corrupt them.
        # What they interpolate goes through _escape.j2.
        autoescape=False,  # noqa: S701 - systemd unit files, not markup
    )


def _render(name: str) -> str:
    """
    Render one of the sweep's unit templates.

    The service runs this machine's ``noust``, wherever it is installed: a
    unit pointing at an executable that is not there would never sweep, and
    previews holding production secrets would never expire.

    Args:
        name: Template file name.

    Returns:
        The unit file.

    Raises:
        ServiceError: The template is missing or broken.
    """
    try:
        return (
            _templates().get_template(name).render(noust=find_noust_executable() or _DEFAULT_NOUST)
        )
    except TemplateError as exc:
        raise ServiceError(
            f"Failed to render systemd template: {name}",
            details=f"{exc}. The Noust installation may be incomplete.",
        ) from exc


def _unit_files() -> dict[Path, str]:
    """
    Say which files make up the sweep and what they hold.

    Returns:
        Each unit file and its rendered content.
    """
    return {
        resolve_within(SYSTEMD_DIR, f"{SWEEP_UNIT}.service"): _render("preview-sweep.service.j2"),
        resolve_within(SYSTEMD_DIR, f"{SWEEP_UNIT}.timer"): _render("preview-sweep.timer.j2"),
    }


def _systemctl(runner: CommandRunner, *args: str) -> Any:
    """
    Run a systemctl verb through the runner.

    Args:
        runner: The runner.
        args: What follows ``systemctl``.

    Returns:
        The command outcome.
    """
    return runner.run(["systemctl", *args], timeout=_SYSTEMCTL_TIMEOUT)


def install_sweep_timer(
    *, runner: CommandRunner | None = None, fs: FileSystem | None = None
) -> bool:
    """
    Write and start ``noust-previews.timer``, which runs ``noust preview sweep`` hourly.

    Args:
        runner: Runner for systemctl; the process-wide one by default.
        fs: Filesystem for the unit files; the process-wide one by default.

    Returns:
        True when a unit file was written, False when they were already
        exactly this.

    Raises:
        ServiceError: A file of that name is not Noust's, cannot be written,
            or the timer does not start.
    """
    runner = runner or get_runner()
    fs = fs or get_fs()
    wanted = _unit_files()
    changed = False
    for path, content in wanted.items():
        if path.exists():
            try:
                current = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ServiceError(f"Cannot read {path}", details=str(exc)) from exc
            if not paths.carries_unit_marker(current):
                raise ServiceError(
                    f"Refusing to overwrite {path}: Noust did not write it",
                    details="Remove or rename that unit, then turn previews on again.",
                )
            if current == content:
                continue
        try:
            fs.write_text(path, content, mode=_UNIT_MODE)
        except OSError as exc:
            raise ServiceError(
                f"Failed to write unit file: {path}",
                details=f"{exc}. Noust must run as root to manage systemd units.",
            ) from exc
        changed = True
    if changed:
        _systemctl(runner, "daemon-reload")
    result = _systemctl(runner, "enable", "--now", f"{SWEEP_UNIT}.timer")
    if not result.success:
        raise ServiceError(
            f"Failed to enable {SWEEP_UNIT}.timer",
            details=result.stderr.strip() or f"Check 'systemctl status {SWEEP_UNIT}.timer'.",
        )
    return changed


def remove_sweep_timer(
    *, runner: CommandRunner | None = None, fs: FileSystem | None = None
) -> bool:
    """
    Stop and remove ``noust-previews.timer`` and its service.

    Args:
        runner: Runner for systemctl; the process-wide one by default.
        fs: Filesystem for the unit files; the process-wide one by default.

    Returns:
        True when there was something to remove.

    Raises:
        ServiceError: A file of that name is not Noust's, or cannot be removed.
    """
    runner = runner or get_runner()
    fs = fs or get_fs()
    unit_files = [
        resolve_within(SYSTEMD_DIR, f"{SWEEP_UNIT}.timer"),
        resolve_within(SYSTEMD_DIR, f"{SWEEP_UNIT}.service"),
    ]
    present = [path for path in unit_files if path.exists()]
    if not present:
        return False
    for path in present:
        try:
            ours = paths.carries_unit_marker(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ServiceError(f"Cannot read {path}", details=str(exc)) from exc
        if not ours:
            raise ServiceError(
                f"Refusing to remove {path}: Noust did not write it",
                details="It is not the previews sweep; leave it or remove it by hand.",
            )
    _systemctl(runner, "disable", "--now", f"{SWEEP_UNIT}.timer")
    for path in present:
        try:
            fs.remove(path)
        except OSError as exc:
            raise ServiceError(f"Failed to remove {path}", details=str(exc)) from exc
    _systemctl(runner, "daemon-reload")
    return True


def sync_sweep_timer(
    *,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
    needed: Callable[[], bool] = previews_in_use,
) -> bool:
    """
    Install the sweep timer while anything uses previews, and remove it after.

    Args:
        runner: Runner for systemctl.
        fs: Filesystem for the unit files.
        needed: Says whether the timer is needed; :func:`previews_in_use`.

    Returns:
        True when the timer is installed afterwards.

    Raises:
        ServiceError: See :func:`install_sweep_timer`, :func:`remove_sweep_timer`.
    """
    if needed():
        install_sweep_timer(runner=runner, fs=fs)
        return True
    remove_sweep_timer(runner=runner, fs=fs)
    return False
