# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Working out what applying a manifest would change (J-15).

``plan`` reads the server through its API - the same routes the console reads,
as the token that asks - and compares what it finds with what the manifest says.
It changes nothing and prints no value of a variable: a plan is something a
pipeline logs and a pull request shows.

The comparison is by what each route can tell:

- What an application *is* (its source, type and port) cannot be changed after
  it exists, so a difference is a warning, never an action: redeploying under
  another source is a decision, not a reconciliation.
- What it *has* (a branch, limits, health check, domains, databases, backup
  schedule, where it lives) is brought to the manifest.
- Its variables are merged, never replaced: the manifest names the variables it
  owns and the rest stay. Whether they already hold the right values is
  answered by the server (``PATCH .../env`` with ``dry_run``), which compares
  without ever returning a value; that needs sudo mode, and a token without it
  gets the action marked *unverified* instead of a guess.

The cron jobs a manifest names are compared the same way (a command is compared as the
words it splits into, a schedule as the calendar the server normalises it to; a plan never
prints a command, which may hold a secret). On a central the servers of the fleet, where an
application is placed across them, and the applications and cron jobs that live on another
server are planned as well, the latter through the central's proxy to that server.

Nothing is ever deleted. An application or a cron job on the server that the manifest does
not name is listed under ``unmanaged`` and left alone.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from noust.client.errors import RemoteError
from noust.manifest.schema import (
    AppSpec,
    BackupSpec,
    CronSpec,
    Manifest,
    ManifestError,
    PlacementSpec,
    Secret,
)

#: The order actions run in. A server has its team and labels before an application is placed
#: on it; an application exists before its domains are added, and before a cron job of its own.
ORDER = (
    "server.team",
    "server.labels",
    "project.create",
    "environment.create",
    "app.create",
    "app.move",
    "app.branch",
    "app.limits",
    "app.health",
    "app.domain",
    "app.database",
    "app.env",
    "app.backups",
    "cron.set",
    "cron.state",
    "placement.set",
)

#: The statuses with which a central says a server cannot be reached through it: not managed
#: here (404), the token may not use it (403), or it did not answer (502, 503, 504).
_UNREACHABLE = (403, 404, 502, 503, 504)


class Transport(Protocol):
    """What the planner and the applier need of a server: its JSON routes."""

    def get(self, path: str, **query: Any) -> Any:
        """GET a route and return its decoded body."""

    def post(self, path: str, body: Any = None, **query: Any) -> Any:
        """POST a body and return the decoded answer."""

    def put(self, path: str, body: Any) -> Any:
        """PUT a body and return the decoded answer."""

    def patch(self, path: str, body: Any) -> Any:
        """PATCH a body and return the decoded answer."""


class NodeTransport:
    """
    A server of the fleet, reached through its central.

    Every route of the server is also a route of the central under
    ``/nodes/{node}/api``, held to the same role, sudo mode and approvals as the
    caller: a manifest that places an application on another server calls the same
    routes it would call on this one, one level down.

    Args:
        inner: The central's routes.
        node: The server's name there.
    """

    def __init__(self, inner: Transport, node: str) -> None:
        self.inner = inner
        self.node = node

    def _path(self, path: str) -> str:
        return f"/nodes/{self.node}/api{path}"

    def get(self, path: str, **query: Any) -> Any:
        """
        Args:
            path: A route of the server.
            **query: Query parameters.

        Returns:
            Its decoded body.
        """
        return self.inner.get(self._path(path), **query)

    def post(self, path: str, body: Any = None, **query: Any) -> Any:
        """
        Args:
            path: A route of the server.
            body: The request.
            **query: Query parameters.

        Returns:
            The decoded answer.
        """
        return self.inner.post(self._path(path), body, **query)

    def put(self, path: str, body: Any) -> Any:
        """
        Args:
            path: A route of the server.
            body: The request.

        Returns:
            The decoded answer.
        """
        return self.inner.put(self._path(path), body)

    def patch(self, path: str, body: Any) -> Any:
        """
        Args:
            path: A route of the server.
            body: The request.

        Returns:
            The decoded answer.
        """
        return self.inner.patch(self._path(path), body)


class Resolver(Protocol):
    """Where a variable that is not written in the manifest comes from."""

    available: bool

    def resolve(self, secret: Secret) -> str:
        """
        Args:
            secret: A value read where the manifest is applied from.

        Returns:
            The value.

        Raises:
            ManifestError: When it cannot be read.
        """


class RefusingResolver:
    """
    The resolver of a server previewing a manifest it was sent: it reads nothing.

    A server must never read its own environment or files on behalf of whoever
    sent a document, so the variables that come from there are planned as
    *unverified* and a plan never fails because of them.
    """

    available = False

    def resolve(self, secret: Secret) -> str:
        """
        Args:
            secret: A value read where the manifest is applied from.

        Raises:
            ManifestError: Always.
        """
        raise ManifestError(
            f"{secret.kind}: {secret.source} cannot be read here",
            details="It is read where the manifest is applied from (your pipeline or laptop).",
        )


@dataclass
class Action:
    """
    One change a manifest asks for.

    Attributes:
        kind: One of :data:`ORDER`.
        target: What it is done to: ``acme/web``, ``shop.example.com``.
        summary: What it does, in a sentence, with no value of any variable.
        data: What applying it needs (kept out of every printed form).
        elevated: Applying it needs sudo mode (the token must be allowed it).
        unverified: Whether it is needed could not be checked; it is listed so the
            apply decides, and it changes nothing when nothing differs.
        server: The server of the fleet it is done on, through the central; None for the
            server the manifest is applied to.
    """

    kind: str
    target: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict, repr=False)
    elevated: bool = False
    unverified: bool = False
    server: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The action as a plan shows it: no data, so no value.
        """
        shown: dict[str, Any] = {
            "kind": self.kind,
            "target": self.target,
            "summary": self.summary,
            "elevated": self.elevated,
            "unverified": self.unverified,
        }
        if self.server is not None:
            shown["server"] = self.server
        return shown


@dataclass
class Plan:
    """
    What a manifest would change on a server.

    Attributes:
        actions: In the order they run.
        warnings: Differences that are not reconciled, and things worth knowing.
        unmanaged: What is on the server that the manifest does not name: applications by
            domain, cron jobs as ``cron:NAME``, each prefixed ``SERVER:`` when it is on another.
    """

    actions: list[Action] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unmanaged: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """Whether there is nothing to do."""
        return not self.actions

    @property
    def digest(self) -> str:
        """
        What a person agreed to by looking at this plan.

        A hash of the actions in the order they run (kind, target and what
        each says it does), none of which holds the value of a variable. An
        apply that carries the digest of the plan its author saw is refused
        when the plan has changed since, so "apply what I previewed" cannot
        become "apply whatever is there now".
        """
        listed = [[a.kind, a.target, a.summary, a.elevated] for a in self.actions]
        text = json.dumps(listed, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as the API and ``--json`` show it.
        """
        return {
            "empty": self.empty,
            "digest": self.digest,
            "actions": [action.to_dict() for action in self.actions],
            "warnings": list(self.warnings),
            "unmanaged": list(self.unmanaged),
            "counts": {
                kind: sum(1 for a in self.actions if a.kind == kind)
                for kind in ORDER
                if any(a.kind == kind for a in self.actions)
            },
        }


def _canonical_engine(name: str) -> str:
    """
    Args:
        name: A database engine as written.

    Returns:
        Its canonical name, or the name lower-cased when it is not known.
    """
    from noust.managers.database.registry import DatabaseRegistry

    return DatabaseRegistry.canonical(name) or name.lower()


def _where(spec: AppSpec) -> str:
    """
    Args:
        spec: An application.

    Returns:
        Where the manifest puts it, as people write it, or an empty string.
    """
    return "/".join(part for part in (spec.team, spec.project, spec.environment) if part)


class _Reader:
    """Read the server once per question and remember the answer."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self._cache: dict[str, Any] = {}

    def get(self, path: str) -> Any:
        """
        Args:
            path: A route.

        Returns:
            Its body, read once.
        """
        if path not in self._cache:
            self._cache[path] = self.transport.get(path)
        return self._cache[path]

    def optional(self, path: str) -> Any | None:
        """
        Args:
            path: A route the token may not be allowed.

        Returns:
            Its body, or None when the server refused (403) or has none (404).
        """
        try:
            return self.get(path)
        except RemoteError as exc:
            if exc.status in (403, 404):
                return None
            raise


def plan(
    manifest: Manifest,
    transport: Transport,
    resolver: Resolver,
    *,
    compare_env: bool = True,
) -> Plan:
    """
    Compare a manifest with a server.

    Args:
        manifest: What should be.
        transport: The server's routes.
        resolver: Where variables that are not written in the manifest come from.
        compare_env: Ask the server whether the variables are already in place
            (needs sudo mode); off, every variable section is listed unverified.

    Returns:
        The plan. Nothing is changed.

    Raises:
        ManifestError: When a variable cannot be read (and the resolver was
            expected to).
        RemoteError: When the server refuses a read that has no other reading.
    """
    result = Plan()
    _plan_fleet(result, manifest, transport)

    # Each server the manifest puts something on is read and planned on its own: the one the
    # manifest is applied to, and every server of the fleet it names, through the central.
    apps_of: dict[str | None, list[AppSpec]] = defaultdict(list)
    jobs_of: dict[str | None, list[CronSpec]] = defaultdict(list)
    for app in manifest.applications:
        apps_of[app.server].append(app)
    for job in manifest.cron:
        jobs_of[job.server].append(job)
    servers: list[str | None] = [None]
    for server in [*apps_of, *jobs_of]:
        if server not in servers:
            servers.append(server)
    for server in servers:
        group = Plan()
        projects = manifest.projects if server is None else ()
        if server is None:
            _plan_group(
                group,
                transport,
                resolver,
                compare_env,
                projects,
                apps_of.get(None, []),
                jobs_of.get(None, []),
                read_apps=True,
            )
        else:
            try:
                _plan_group(
                    group,
                    NodeTransport(transport, server),
                    resolver,
                    compare_env,
                    projects,
                    apps_of.get(server, []),
                    jobs_of.get(server, []),
                    read_apps=bool(apps_of.get(server)),
                )
            except RemoteError as exc:
                if exc.status not in _UNREACHABLE:
                    raise
                result.warnings.append(
                    f"{server}: this central cannot use that server ({exc.message}); "
                    "what the manifest puts on it is not planned"
                )
                continue
        for action in group.actions:
            if server is not None:
                action.server = server
                action.target = f"{server}:{action.target}"
            result.actions.append(action)
        prefix = f"{server}:" if server else ""
        result.warnings.extend(f"{server}: {w}" if server else w for w in group.warnings)
        result.unmanaged.extend(f"{prefix}{name}" for name in group.unmanaged)
    result.unmanaged = sorted(set(result.unmanaged))
    result.actions.sort(key=lambda action: ORDER.index(action.kind))
    return result


def _plan_group(
    result: Plan,
    transport: Transport,
    resolver: Resolver,
    compare_env: bool,
    manifest_projects: tuple[Any, ...],
    applications: list[AppSpec],
    jobs: list[CronSpec],
    *,
    read_apps: bool = True,
) -> None:
    """
    Plan what one server needs: its projects, its applications and its cron jobs.

    Args:
        result: The plan being built for this server.
        transport: The server's routes (a node's, through the central, or this server's).
        resolver: Where variables that are not written come from.
        compare_env: Ask the server whether the variables are in place.
        manifest_projects: The projects the manifest says (this server's only).
        applications: The applications that should be on it.
        jobs: The cron jobs that should run on it.
        read_apps: Read its applications and projects (always for the server the manifest
            is applied to; for another only when the manifest puts an application on it).

    Raises:
        RemoteError: When the server refuses a read that has no other reading.
    """
    reader = _Reader(transport)
    apps: dict[str, Any] = {}
    projects: dict[tuple[str, str], Any] = {}
    teams: set[str] = set()
    if read_apps:
        apps = {item["domain"]: item for item in reader.get("/apps").get("apps", [])}
        projects = {(p["team"], p["slug"]): p for p in reader.get("/projects").get("projects", [])}
        teams = {t["slug"] for t in reader.get("/teams").get("teams", [])}

    planned_projects: set[tuple[str, str]] = set()
    planned_environments: set[tuple[str, str, str]] = set()

    def need_project(team: str, project: str, name: str | None) -> bool:
        """Plan a project that is missing; False when its team is not there to hold it."""
        key = (team, project)
        if key in projects or key in planned_projects:
            return True
        if team not in teams:
            result.warnings.append(
                f"{team}/{project}: the team {team!r} does not exist, or you do not belong to it "
                "(an administrator creates teams)"
            )
            return False
        planned_projects.add(key)
        planned_environments.add((team, project, "production"))
        result.actions.append(
            Action(
                "project.create",
                f"{team}/{project}",
                f"create the project {team}/{project} (with its production environment)",
                {"team": team, "project": project, "name": name or project},
            )
        )
        return True

    def need_environment(team: str, project: str, environment: str) -> None:
        """Plan an environment that is missing in a project that exists or will."""
        existing = set(projects.get((team, project), {}).get("environments", []))
        if environment in existing or (team, project, environment) in planned_environments:
            return
        planned_environments.add((team, project, environment))
        result.actions.append(
            Action(
                "environment.create",
                f"{team}/{project}/{environment}",
                f"create the environment {environment} in {team}/{project}",
                {"team": team, "project": project, "environment": environment},
            )
        )

    for spec in manifest_projects:
        if need_project(spec.team, spec.project, spec.name):
            for environment in spec.environments:
                need_environment(spec.team, spec.project, environment)

    for app in applications:
        if app.project and app.team:
            if need_project(app.team, app.project, None) and app.environment:
                need_environment(app.team, app.project, app.environment)
        existing = apps.get(app.domain)
        if existing is None:
            _plan_new(result, app, resolver)
        else:
            _plan_existing(result, app, existing, reader, resolver, compare_env)

    if read_apps:
        named = {app.domain for app in applications}
        result.unmanaged.extend(sorted(domain for domain in apps if domain not in named))
    _plan_cron(result, jobs, reader)


def _plan_fleet(result: Plan, manifest: Manifest, transport: Transport) -> None:
    """
    Plan the servers of a fleet and where applications are placed across them.

    Only a central has a fleet: a server that is not one, or a token that may not see it, gets a
    warning and no action, never an error, so one manifest can be applied to either.

    Args:
        result: The plan being built.
        manifest: What should be.
        transport: The central's routes.
    """
    if not manifest.servers and not manifest.placements:
        return
    reader = _Reader(transport)
    listing = reader.optional("/nodes")
    if listing is None:
        result.warnings.append(
            "the fleet cannot be read here (this server is not a central, or the token may not "
            "see it): the servers and placements of the manifest are not planned"
        )
        return
    nodes = {item["name"]: item for item in listing.get("items", [])}
    for spec in manifest.servers:
        node = nodes.get(spec.name)
        if node is None:
            result.warnings.append(
                f"{spec.name}: no server of that name is managed here; register it with "
                "'noust node add' (its join code does not belong in a manifest)"
            )
            continue
        if spec.team is not None and node.get("team") != spec.team:
            result.actions.append(
                Action(
                    "server.team",
                    spec.name,
                    f"give the server to the team {spec.team} (now {node.get('team') or 'nobody'})",
                    {"node": spec.name, "team": spec.team},
                    elevated=True,
                )
            )
        if spec.labels is not None:
            have = dict(node.get("labels") or {})
            merged = {**have, **spec.labels}
            if merged != have:
                added = sorted(k for k in spec.labels if have.get(k) != spec.labels[k])
                result.actions.append(
                    Action(
                        "server.labels",
                        spec.name,
                        "set the labels " + ", ".join(added),
                        {"node": spec.name, "labels": merged},
                    )
                )
    if not manifest.placements:
        return
    placed = reader.optional("/fleet/placements")
    if placed is None:
        result.warnings.append(
            "the placements cannot be read here (the fleet is the administrator's): "
            "those of the manifest are not planned"
        )
        return
    current = {item["domain"]: item for item in placed.get("placements", [])}
    for spec in manifest.placements:
        unknown = [name for name in (spec.nodes or ()) if name not in nodes]
        if unknown:
            result.warnings.append(
                f"{spec.domain}: placed on {', '.join(unknown)}, which this central does not manage"
            )
        found = current.get(spec.domain)
        if found is not None and not _placement_differs(spec, found):
            continue
        result.actions.append(
            Action(
                "placement.set",
                spec.domain,
                ("place" if found is None else "change the placement of")
                + f" {spec.domain}"
                + (f" on {', '.join(spec.nodes)}" if spec.nodes else ""),
                {"domain": spec.domain, "placement": spec},
            )
        )


def _placement_differs(spec: PlacementSpec, current: Mapping[str, Any]) -> bool:
    """
    Args:
        spec: The placement the manifest asks for.
        current: The central's placement of the application.

    Returns:
        Whether the servers it names, its selector or the way an update goes out differ.
    """
    if spec.nodes is not None and sorted(spec.nodes) != sorted(current.get("nodes") or []):
        return True
    if spec.labels is not None and dict(spec.labels) != dict(current.get("labels") or {}):
        return True
    return (
        current.get("serial") != spec.serial
        or current.get("max_failures") != spec.max_failures
        or current.get("canary") != spec.canary
        or bool(current.get("rollback_on_failure", True)) != spec.rollback_on_failure
    )


def _words(command: str) -> list[str]:
    """
    Args:
        command: A command line as written.

    Returns:
        The words the manager splits it into, or the text itself when it cannot be split.
    """
    try:
        return shlex.split(command)
    except ValueError:
        return [command]


def _plan_cron(result: Plan, jobs: list[CronSpec], reader: _Reader) -> None:
    """
    Plan the cron jobs a server should run.

    Args:
        result: The plan being built.
        jobs: The jobs the manifest says for this server.
        reader: Reads (and asks to normalise a calendar of) the server.
    """
    if not jobs:
        return
    listing = reader.optional("/cron")
    if listing is None:
        result.warnings.append(
            "the cron jobs cannot be read here (they belong to the whole server): "
            "those of the manifest are not planned"
        )
        return
    current = {item["name"]: item for item in listing.get("jobs", [])}
    named = {job.name for job in jobs}
    for spec in jobs:
        existing = current.get(spec.name)
        body: dict[str, Any] = {"name": spec.name, "command": spec.command}
        if existing is not None:
            # Writing a job again rewrites it whole, so what the manifest leaves unsaid is carried over.
            for key, found in (
                ("user", existing.get("user")),
                ("working_directory", existing.get("working_directory")),
                ("app_domain", existing.get("app_domain")),
                ("timeout", existing.get("timeout")),
                ("service", existing.get("service")),
            ):
                if found not in (None, ""):
                    body[key] = found
        body["schedule"] = spec.schedule
        for key, wanted in (
            ("user", spec.user),
            ("working_directory", spec.working_directory),
            ("app_domain", spec.app),
            ("timeout", spec.timeout),
            ("service", spec.service),
        ):
            if wanted is not None:
                body[key] = wanted
        if existing is None:
            result.actions.append(
                Action(
                    "cron.set",
                    spec.name,
                    f"create the cron job {spec.name} ({spec.schedule})",
                    {"body": body},
                    elevated=True,
                )
            )
        else:
            differs = _cron_differences(spec, existing, reader)
            if differs:
                result.actions.append(
                    Action(
                        "cron.set",
                        spec.name,
                        f"change the cron job {spec.name} ({', '.join(differs)})",
                        {"body": body},
                        elevated=True,
                    )
                )
        is_on = bool(existing["enabled"]) if existing is not None else True
        if spec.enabled != is_on:
            result.actions.append(
                Action(
                    "cron.state",
                    spec.name,
                    f"switch the cron job {spec.name} {'on' if spec.enabled else 'off'}",
                    {"name": spec.name, "enabled": spec.enabled},
                )
            )
    result.unmanaged.extend(f"cron:{name}" for name in sorted(current) if name not in named)


def _cron_differences(spec: CronSpec, existing: Mapping[str, Any], reader: _Reader) -> list[str]:
    """
    Args:
        spec: The job the manifest asks for.
        existing: The server's account of the job.
        reader: Asks the server to normalise the calendar, as it would when the job is written.

    Returns:
        The names of what differs (never its value: a command may hold a secret).
    """
    differs: list[str] = []
    if _words(spec.command) != _words(str(existing.get("command") or "")):
        differs.append("command")
    calendar = spec.schedule
    try:
        answer = reader.transport.post("/cron/preview", {"schedule": spec.schedule})
        calendar = str(answer.get("calendar") or spec.schedule)
    except RemoteError:
        # The server's own check will say so when the job is written.
        pass
    if calendar != existing.get("on_calendar") and spec.schedule != existing.get("on_calendar"):
        differs.append("schedule")
    for label, wanted, found in (
        ("user", spec.user, existing.get("user")),
        ("working directory", spec.working_directory, existing.get("working_directory")),
        ("application", spec.app, existing.get("app_domain")),
        ("timeout", spec.timeout, existing.get("timeout")),
        ("service", spec.service, existing.get("service")),
    ):
        if wanted is not None and wanted != (found if found not in ("",) else None):
            differs.append(label)
    return differs


def _resolved(spec: AppSpec, resolver: Resolver) -> dict[str, str] | None:
    """
    Args:
        spec: An application.
        resolver: Where variables that are not written come from.

    Returns:
        Its variables with every value in hand, or None when some value is read
        somewhere this resolver cannot reach (a preview on a server).

    Raises:
        ManifestError: When a value is meant to be readable and is not.
    """
    if spec.env is None:
        return {}
    values: dict[str, str] = {}
    for name, value in spec.env.items():
        if isinstance(value, Secret):
            if not resolver.available:
                return None
            values[name] = resolver.resolve(value)
        else:
            values[name] = value
    return values


def _plan_new(result: Plan, spec: AppSpec, resolver: Resolver) -> None:
    """
    Plan an application that does not exist yet.

    Args:
        result: The plan being built.
        spec: The application.
        resolver: Where variables that are not written come from.
    """
    if not spec.source and not spec.recipe:
        result.warnings.append(
            f"{spec.domain}: it does not exist and the manifest gives no source or recipe to create it from"
        )
        return
    values = _resolved(spec, resolver)
    names = sorted(spec.env or {})
    what = (spec.recipe and f"the recipe {spec.recipe}") or spec.source
    result.actions.append(
        Action(
            "app.create",
            spec.domain,
            f"deploy {spec.domain} from {what}"
            + (f" ({spec.branch})" if spec.branch else "")
            + (f" with {len(names)} variables" if names else "")
            + (f" in {_where(spec)}" if _where(spec) else ""),
            {"spec": spec, "env": values},
            unverified=values is None,
        )
    )
    _plan_followers(result, spec, existing=None, reader=None, first_database_made=True)


def _plan_followers(
    result: Plan,
    spec: AppSpec,
    *,
    existing: Mapping[str, Any] | None,
    reader: _Reader | None,
    first_database_made: bool = False,
) -> None:
    """
    Plan what follows creating an application, or what an existing one lacks:
    its domains, its databases and its backup schedule.

    Args:
        result: The plan being built.
        spec: The application.
        existing: The server's account of it, or None when it will be created.
        reader: Reads the server, or None when the application does not exist yet.
        first_database_made: The first database is made with the application
            (a new one), so it is not an action of its own.
    """
    present: set[str] = set()
    if spec.domains and reader is not None:
        listing = reader.optional(f"/apps/{spec.domain}/domains")
        present = {d["domain"] for d in (listing or {}).get("domains", [])}
    for alias in spec.domains:
        if alias not in present:
            result.actions.append(
                Action(
                    "app.domain",
                    spec.domain,
                    f"answer on {alias}",
                    {"domain": spec.domain, "alias": alias},
                )
            )
    have: list[tuple[str, str]] = []
    if spec.databases and reader is not None:
        listing = reader.optional(f"/apps/{spec.domain}/databases")
        have = [
            (_canonical_engine(d["engine"]), d["database"])
            for d in (listing or {}).get("databases", [])
        ]
    for index, database in enumerate(spec.databases):
        if first_database_made and index == 0:
            continue
        engine = _canonical_engine(database.engine)
        if any(e == engine and (database.name is None or n == database.name) for e, n in have):
            continue
        result.actions.append(
            Action(
                "app.database",
                spec.domain,
                f"create a {engine} database for it"
                + (f" named {database.name}" if database.name else ""),
                {"domain": spec.domain, "database": database},
            )
        )
    if spec.backups is not None:
        current = None
        schedules = reader.optional("/backup-schedules") if reader is not None else None
        if schedules is not None:
            current = next(
                (s for s in schedules.get("schedules", []) if s["domain"] == spec.domain), None
            )
        if current is None:
            kind = "create"
        elif _schedule_differs(spec.backups, current):
            kind = "change"
        else:
            return
        result.actions.append(
            Action(
                "app.backups",
                spec.domain,
                f"{kind} its backup schedule ({spec.backups.schedule})",
                {"domain": spec.domain, "backups": spec.backups, "exists": current is not None},
            )
        )


def _schedule_differs(backups: BackupSpec, current: Mapping[str, Any]) -> bool:
    """
    Args:
        backups: The schedule the manifest asks for.
        current: The server's schedule for the application.

    Returns:
        Whether the schedule, its retention or what it dumps differ.
    """
    return (
        current.get("schedule") != backups.schedule
        or current.get("retention_count") != backups.retention_count
        or current.get("retention_days") != backups.retention_days
        or bool(current.get("include_databases", True)) != backups.include_databases
    )


def _plan_existing(
    result: Plan,
    spec: AppSpec,
    existing: Mapping[str, Any],
    reader: _Reader,
    resolver: Resolver,
    compare_env: bool,
) -> None:
    """
    Plan what an application that exists needs to match the manifest.

    Args:
        result: The plan being built.
        spec: The application as it should be.
        existing: The server's account of it.
        reader: Reads the server.
        resolver: Where variables that are not written come from.
        compare_env: Ask the server whether the variables are in place.
    """
    domain = spec.domain
    for label, wanted, found in (
        ("source", spec.source, existing.get("source")),
        ("type", None if spec.app_type == "auto" else spec.app_type, existing.get("app_type")),
        ("port", spec.port, existing.get("port")),
    ):
        if wanted is not None and wanted != found:
            result.warnings.append(
                f"{domain}: the {label} is {found!r} and the manifest says {wanted!r}; "
                "it cannot change once deployed, so it is left as it is"
            )
    current_place = (existing.get("team"), existing.get("project"), existing.get("environment"))
    wanted_place = (
        spec.team or current_place[0],
        spec.project or current_place[1],
        spec.environment or current_place[2],
    )
    if any([spec.team, spec.project, spec.environment]) and wanted_place != current_place:
        result.actions.append(
            Action(
                "app.move",
                domain,
                f"move it from {'/'.join(map(str, current_place))} to {'/'.join(map(str, wanted_place))}",
                {
                    "domain": domain,
                    "team": wanted_place[0],
                    "project": wanted_place[1],
                    "environment": wanted_place[2],
                },
            )
        )
    if spec.branch is not None and spec.branch != existing.get("branch"):
        result.actions.append(
            Action(
                "app.branch",
                domain,
                f"follow the branch {spec.branch} (now {existing.get('branch') or 'none'})",
                {"domain": domain, "branch": spec.branch},
            )
        )
    if spec.limits is not None and any(spec.limits.get(k) != existing.get(k) for k in spec.limits):
        result.actions.append(
            Action(
                "app.limits",
                domain,
                "set its memory, CPU and task limits",
                {"domain": domain, "limits": dict(spec.limits)},
            )
        )
    if spec.health is not None and any(
        spec.health.get(k) != existing.get(f"health_{k}") for k in ("path", "expect", "timeout")
    ):
        result.actions.append(
            Action(
                "app.health",
                domain,
                "set what its health gate asks",
                {"domain": domain, "health": dict(spec.health)},
            )
        )
    if spec.env:
        _plan_env(result, spec, resolver, compare_env, reader)
    _plan_followers(result, spec, existing=existing, reader=reader)


def _plan_env(
    result: Plan, spec: AppSpec, resolver: Resolver, compare_env: bool, reader: _Reader
) -> None:
    """
    Plan the merge of an application's variables.

    Args:
        result: The plan being built.
        spec: The application, with variables.
        resolver: Where variables that are not written come from.
        compare_env: Ask the server whether they are in place.
        reader: Reads (and writes dry runs to) the server.
    """
    values = _resolved(spec, resolver)
    names = sorted(spec.env or {})
    summary_names = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
    if values is None or not compare_env:
        result.actions.append(
            Action(
                "app.env",
                spec.domain,
                f"set the variables {summary_names}",
                {"domain": spec.domain, "env": values, "names": names},
                elevated=True,
                unverified=True,
            )
        )
        return
    try:
        answer = reader.transport.patch(
            f"/apps/{spec.domain}/env", {"set": values, "unset": [], "dry_run": True}
        )
    except RemoteError as exc:
        if exc.code == "elevation_required":
            result.actions.append(
                Action(
                    "app.env",
                    spec.domain,
                    f"set the variables {summary_names}",
                    {"domain": spec.domain, "env": values, "names": names},
                    elevated=True,
                    unverified=True,
                )
            )
            result.warnings.append(
                f"{spec.domain}: the variables could not be compared, which needs a token allowed to "
                "confirm sudo mode; they are listed as unverified"
            )
            return
        raise
    changed = list(answer.get("changed", []))
    if changed:
        result.actions.append(
            Action(
                "app.env",
                spec.domain,
                "set the variables " + ", ".join(changed),
                {"domain": spec.domain, "env": values, "names": changed},
                elevated=True,
            )
        )


#: Used by callers that print a plan: the label of each kind of action.
LABELS: Mapping[str, str] = {
    "project.create": "project",
    "environment.create": "environment",
    "app.create": "deploy",
    "app.move": "move",
    "app.branch": "branch",
    "app.limits": "limits",
    "app.health": "health",
    "app.domain": "domain",
    "app.database": "database",
    "app.env": "variables",
    "app.backups": "backups",
    "cron.set": "cron",
    "cron.state": "cron",
    "server.team": "server",
    "server.labels": "server",
    "placement.set": "placement",
}
