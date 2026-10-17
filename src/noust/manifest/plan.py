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

Nothing is ever deleted. An application on the server that the manifest does not
name is listed under ``unmanaged`` and left alone.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from noust.client.errors import RemoteError
from noust.manifest.schema import AppSpec, BackupSpec, Manifest, ManifestError, Secret

#: The order actions run in. An application exists before its domains are added.
ORDER = (
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
)


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
    """

    kind: str
    target: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict, repr=False)
    elevated: bool = False
    unverified: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The action as a plan shows it: no data, so no value.
        """
        return {
            "kind": self.kind,
            "target": self.target,
            "summary": self.summary,
            "elevated": self.elevated,
            "unverified": self.unverified,
        }


@dataclass
class Plan:
    """
    What a manifest would change on a server.

    Attributes:
        actions: In the order they run.
        warnings: Differences that are not reconciled, and things worth knowing.
        unmanaged: Applications on the server that the manifest does not name.
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
    reader = _Reader(transport)
    result = Plan()
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

    for spec in manifest.projects:
        if need_project(spec.team, spec.project, spec.name):
            for environment in spec.environments:
                need_environment(spec.team, spec.project, environment)

    for app in manifest.applications:
        if app.project and app.team:
            if need_project(app.team, app.project, None) and app.environment:
                need_environment(app.team, app.project, app.environment)
        existing = apps.get(app.domain)
        if existing is None:
            _plan_new(result, app, resolver)
        else:
            _plan_existing(result, app, existing, reader, resolver, compare_env)

    named = {app.domain for app in manifest.applications}
    result.unmanaged = sorted(domain for domain in apps if domain not in named)
    result.actions.sort(key=lambda action: ORDER.index(action.kind))
    return result


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
}
