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

Two things a preview inherits from its parent are deliberate and said out
loud wherever a preview is announced: its environment variables are a copy
of the parent's, production secrets included (minus the ones the settings
exclude), and so it talks to the parent's databases (2.2 does not provision
one per preview). And its build runs as root, like every deployment's. That
is why only people trusted with the repository get a preview: a pull request
whose branch lives in a fork never does, on GitHub its author must be an
owner, member or collaborator, and a bot's (Dependabot, Renovate) only when
the settings allow bots. Building previews as the service user instead of
root is not done in 2.2.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections.abc import Callable, Collection, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, PackageLoader, TemplateError

from noust.central import require_server_role
from noust.core import paths
from noust.core.config import SYSTEMD_DIR as _SYSTEMD_DIR
from noust.core.exceptions import (
    DeploymentError,
    IntegrationError,
    NoustError,
    ServiceError,
    ValidationError,
)
from noust.core.forge_events import Forge, PullRequestAction, PullRequestEvent
from noust.core.fs import FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, DeploymentTrigger, PreviewRecord, PreviewSettings, get_store
from noust.core.utils import domain_to_app_name, find_noust_executable
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

# Git sources, reduced to the host and the repository path they name.
_GITHUB_SHORTHAND = re.compile(r"^github:(?P<path>[\w.-]+/[\w.-]+?)(?:\.git)?/?$", re.IGNORECASE)
_SCP_LIKE = re.compile(r"^[\w.-]+@(?P<host>[\w.-]+):(?!//)(?P<path>[\w./-]+?)(?:\.git)?/?$")
_URL_LIKE = re.compile(
    r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[\w.-]+)(?::\d+)?/(?P<path>[\w./-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def repository_key(source: str | None) -> tuple[str, str] | None:
    """
    Reduce a git source to the repository it names.

    ``https://github.com/Owner/Repo.git``, ``git@github.com:owner/repo``,
    ``ssh://git@github.com/owner/repo`` and ``github:owner/repo`` all name the
    same repository, and a webhook names it as ``owner/repo``; comparing the
    sources as typed would miss every one of those.

    Args:
        source: A source as the store records it.

    Returns:
        ``(host, path)``, both lowercased, the path without ``.git``; None
        when the source is not a git repository (a local path, an archive).
    """
    if not source:
        return None
    text = source.strip().split("#", 1)[0]
    shorthand = _GITHUB_SHORTHAND.match(text)
    if shorthand:
        return ("github.com", shorthand.group("path").lower())
    match = _URL_LIKE.match(text) or _SCP_LIKE.match(text)
    if not match:
        return None
    path = match.group("path").strip("/")
    if "/" not in path:
        return None
    return (match.group("host").lower(), path.lower())


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


def _name_room(base_domain: str, number: int) -> int:
    """
    Say how many characters of the application's name fit in a preview domain.

    The whole domain is bounded, not just its first label: it becomes the
    preview's application name and unit name, dots turned into dashes, and
    those are at most :data:`MAX_APP_NAME_LENGTH` characters.

    Args:
        base_domain: The base domain previews answer under.
        number: The pull request number.

    Returns:
        The room left for the name.
    """
    return MAX_APP_NAME_LENGTH - len(base_domain) - 1 - len(f"pr-{number}-")


def preview_domain_for(parent_domain: str, number: int, base_domain: str) -> str:
    """
    Name the preview of a pull request.

    ``pr-<n>-<app-name>.<base domain>``: a single label under the base domain,
    so one wildcard record (and one wildcard certificate, if the operator has
    one) covers every preview. A name that does not fit is cut and given a
    short hash of the whole name, so two long names that share a beginning
    still give different domains.

    Args:
        parent_domain: The application previewed.
        number: The pull request number.
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
            f"No room for the preview of pull request #{number} under {base_domain}",
            details=f"Preview domains are at most {MAX_APP_NAME_LENGTH} characters. "
            "Use a shorter base domain.",
        )
    if len(name) > room:
        digest = hashlib.sha256(name.encode()).hexdigest()[:_HASH_LENGTH]
        name = f"{name[: room - _HASH_LENGTH - 1].rstrip('-')}-{digest}"
    return validate_domain(f"pr-{number}-{name}.{base_domain}")


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
            deployed from git, or its type cannot use the release layout.
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
    if not _supports_releases(app.app_type):
        raise ValidationError(
            f"{app.app_type or 'unknown'} applications cannot have previews yet",
            details="Previews are deployed on the release layout, which this type does not "
            "support.",
        )
    return app


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


def checked_env_names(names: Iterable[str]) -> list[str]:
    """
    Check the variable names a preview must never be given.

    Args:
        names: Environment variable names.

    Returns:
        The names, without repeats, in the order given.

    Raises:
        ValidationError: A name is not an environment variable name, or
            there are more than :data:`MAX_EXCLUDED_ENV`.
    """
    if isinstance(names, str):
        raise ValidationError("Excluded variables must be a list of names", field="exclude_env")
    checked: list[str] = []
    for name in names:
        text = name.strip() if isinstance(name, str) else name
        if not isinstance(text, str) or not ENV_NAME_PATTERN.match(text):
            raise ValidationError(
                f"Not an environment variable name: {name!r}",
                details="Names are letters, digits and underscores, not starting with a digit.",
                field="exclude_env",
            )
        if text not in checked:
            checked.append(text)
    if len(checked) > MAX_EXCLUDED_ENV:
        raise ValidationError(
            f"At most {MAX_EXCLUDED_ENV} variables can be excluded, not {len(checked)}",
            field="exclude_env",
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
) -> PreviewSettings:
    """
    Turn previews on for an application, or change their settings.

    A lower limit does not remove previews that already exist; it only stops
    new ones. A new time-to-live applies from each preview's next push, and
    so do excluded variables: they are taken out of every existing preview
    when it is next built.

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
        exclude_env: Variables never copied to a preview; None keeps the
            current list (empty when they are being turned on).

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
    before = store.get_preview_settings(parent.domain)
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
    stored = store.save_preview_settings(settings)
    try:
        sync_sweep_timer()
    except NoustError:
        # Without the timer nothing would ever expire: previews stay off.
        if before is None:
            store.delete_preview_settings(parent.domain)
        else:
            store.save_preview_settings(before)
        raise
    return stored


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


def _inherited_env(parent: App, exclude: Collection[str] = ()) -> dict[str, str]:
    """
    Read the variables a preview starts with: the parent's, as they are now.

    Args:
        parent: The application previewed.
        exclude: Names the preview settings keep from previews.

    Returns:
        Its ``.env``, without the variables the unit sets itself and the
        excluded ones.
    """
    from noust.deployers.helpers.app_env import read_app_env

    return {
        name: value
        for name, value in read_app_env(parent).items()
        if name not in _MANAGED_ENV_VARS and name not in exclude
    }


def _inherited_marks(parent: App, exclude: Collection[str] = ()) -> dict[str, bool]:
    """
    Read the secret marks a preview carries: the parent's, as they are now.

    A value the operator marked secret on the parent is the same value in
    the preview, and must be hidden and scrubbed there too.

    Args:
        parent: The application previewed.
        exclude: Names the preview settings keep from previews.

    Returns:
        The parent's marks, without the excluded variables'.
    """
    return {name: mark for name, mark in parent.env_secret_marks.items() if name not in exclude}


def _sync_from_parent(domain: str, parent: App, exclude: Collection[str]) -> None:
    """
    Bring an existing preview in line with its parent before it is rebuilt.

    Its secret marks become the parent's again, and a variable excluded
    since it was created is taken out of its ``.env``.

    Args:
        domain: The preview's domain.
        parent: The application previewed.
        exclude: Names the preview settings keep from previews.

    Raises:
        NoustError: The ``.env`` could not be rewritten.
        OSError: The ``.env`` could not be written.
    """
    from noust.deployers.helpers.app_env import read_app_env, write_app_env

    store = get_store()
    store.set_env_secret_marks(domain, _inherited_marks(parent, exclude))
    child = store.get_app(domain)
    if child is None or not exclude:
        return
    current = read_app_env(child)
    kept = {name: value for name, value in current.items() if name not in exclude}
    if kept != current:
        write_app_env(child, kept)


def _deploy(
    parent: App, record: PreviewRecord, context: JobContext, exclude: Collection[str] = ()
) -> dict[str, Any]:
    """
    Create a preview's application, the way the console creates any application.

    Its row is created already linked to the parent and carrying the
    parent's secret marks, so the deployment's own events (GitHub deployment
    statuses, notifications) call it a preview and its log is scrubbed from
    the first line.

    Args:
        parent: The application previewed.
        record: The preview.
        context: The running job's context.
        exclude: Names the preview settings keep from previews.

    Returns:
        What the deploy job returned.
    """
    from noust.web.jobs import deploy_app_job

    port = _reserve_port()
    try:
        return deploy_app_job(
            domain=record.domain,
            source=parent.source,
            app_type=parent.app_type or "auto",
            port=port,
            branch=record.branch,
            env_vars=_inherited_env(parent, exclude),
            webserver=parent.webserver,
            ssl=True,
            layout="releases",
            memory_max_mb=PREVIEW_MEMORY_MB,
            cpu_quota_percent=PREVIEW_CPU_PERCENT,
            trigger=DeploymentTrigger.WEBHOOK.value,
            github_installation_id=parent.github_installation_id,
            preview_parent=parent.domain,
            env_secret_marks=_inherited_marks(parent, exclude),
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
    theirs removed. A preview's build runs as root with the parent's
    production secrets, so it is only built for people trusted with the
    repository: a pull request from a fork is refused, on GitHub so is one
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
        "Refused a preview of %s for %s#%d: %s. A preview is built as root and runs with "
        "%s's production secrets",
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
            f"No preview for this pull request: {explanation} A preview is built on the "
            f"server as root and runs with a copy of `{parent.domain}`'s environment "
            "variables, production secrets included, so only pull requests from people "
            "trusted with this repository get one.",
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

    record = store.save_preview(
        PreviewRecord(
            parent_domain=parent.domain,
            domain=domain,
            number=event.number,
            branch=event.branch,
            provider=event.forge.value,
            expires_at=_expiry(settings.ttl_hours),
            head_sha=event.head_sha or None,
            repository=event.repository,
            # None keeps the stored one: a build may have posted it since.
            comment_ref=None,
            status=PENDING,
            error=None,
        )
    )
    first = occupant is None
    job = _jobs().create_job(
        job_type=_job_type("DEPLOY" if first else "UPDATE"),
        name=f"{'Preview' if first else 'Update preview'} #{record.number} of {parent.domain}",
        description=f"{'Deploying' if first else 'Updating'} the preview of pull request "
        f"#{record.number} ({record.branch}) at {domain}",
        func=preview_deploy_job,
        kwargs={"parent_domain": parent.domain, "number": record.number},
        metadata={
            "domain": domain,
            "parent": parent.domain,
            "preview": record.number,
            "branch": record.branch,
            "trigger": "webhook",
            "provider": record.provider,
        },
        actor="webhook",
    )
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
                _sync_from_parent(record.domain, parent, settings.exclude_env)
                result = _update(record.domain, record.head_sha, context)
            else:
                result = _deploy(parent, record, context, settings.exclude_env)
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
        if record is not None and record.status != REMOVING:
            record = _set_status(record, REMOVING) or record
        if app is not None:
            owner = record.parent_domain if record is not None else app.preview_parent
            if app.preview_parent and app.preview_parent == owner:
                warnings = _delete(domain, on_phase, logger)
            else:
                warnings = (
                    f"{domain} is an application that is not a preview of {owner}; "
                    "it was left alone and only the preview record was removed",
                )
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
