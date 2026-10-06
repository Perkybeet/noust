# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
No route reaches a team's resources except through the scope filter.

The ownership filter is only worth the day nobody can write a route that forgets
it (CONTRIBUTING rule 4: guards go at the chokepoint). Three things hold that
up, and each is checked here against the code as it is:

- every route of the API carries :func:`noust.web.api.scope.enforce_scope`, so a
  resource named in a path, a query or a body is checked before the handler runs;
- every route that names a resource is classified as one in
  :mod:`noust.web.permissions.scopes`, and every route no rule names fails;
- no router reads applications, databases, sites, backup schedules or their
  history from the store directly: it asks the
  :class:`~noust.core.teams.scoped.ScopedStore`, which filters in SQL.

Each guard carries the places that are allowed to break it, with the reason, and
fails both when a new one appears and when a listed one goes away.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import noust
from noust.core.accounts import AuthPolicy
from noust.core.store import NoustStore
from noust.core.teams import ScopedStore
from noust.web.api.openapi import api_routes
from noust.web.api.scope import enforce_scope
from noust.web.auth import SecurityConfig
from noust.web.permissions import PUBLIC
from noust.web.permissions.registry import permission_for_route
from noust.web.permissions.scopes import (
    APP,
    DATABASE,
    DEPLOYMENT,
    DOMAIN,
    INSTANCE,
    JOB,
    OPEN,
    RESOURCE,
    RULES,
    SCOPED,
    TEAM,
    rule_for,
)
from noust.web.server import create_app

SRC = Path(noust.__file__).resolve().parent
REPO = SRC.parent.parent

#: What the store answers about applications and what belongs to them. A router
#: asks the scoped store for each of these.
GUARDED = frozenset(
    {
        "get_app",
        "get_app_by_id",
        "list_apps",
        "get_site",
        "get_site_by_app_id",
        "list_sites",
        "get_database",
        "list_databases",
        "get_backup_schedule",
        "list_backup_schedules",
        "list_domains",
        "get_deployment",
        "list_deployments",
        "get_latest_deployments",
        "list_jobs",
        "get_job",
    }
)

#: Where a router may read the store unscoped, and why. ``(file, function)``.
UNSCOPED_ALLOWED: dict[tuple[str, str], str] = {
    (
        "src/noust/web/api/hooks.py",
        "webhook_update_job",
    ): "a job: it runs as the instance, queued by a webhook that proved the application's secret",
    (
        "src/noust/web/api/hooks.py",
        "deliver",
    ): "the public deploy webhook: the application's own HMAC secret is its authentication",
    (
        "src/noust/web/api/server/lifecycle.py",
        "reattach_updates",
    ): "startup: the console picks the server's own update jobs back up, as the instance",
}

#: Streams outside the API router that read the store, and why they are held to the scope anyway.
STREAMS_ALLOWED: dict[tuple[str, str], str] = {
    (
        "src/noust/web/websockets/router.py",
        "_resolve_log_units",
    ): "reached only after _scoped() refused every application the principal does not see",
    (
        "src/noust/web/events.py",
        "_domain_owning_unit",
    ): "maps a unit to its application for the hub's frame, which each stream filters by scope",
    (
        "src/noust/web/machine.py",
        "classify_apps",
    ): "the machine snapshot of the `machine` event, which a team member's stream never carries",
}


def relative(path: Path) -> str:
    """
    Args:
        path: A path inside the repository.

    Returns:
        It relative to the repository root.
    """
    return str(path.relative_to(REPO))


def store_names(tree: ast.AST) -> set[str]:
    """
    Find the names a module binds to the store.

    Args:
        tree: A parsed module.

    Returns:
        Every name assigned ``get_store()`` or ``NoustStore(...)`` anywhere in it.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in ("get_store", "NoustStore")
            ):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def unscoped_reads(source: str) -> list[tuple[str, int, str]]:
    """
    Find the reads of a guarded resource made straight on the store.

    Args:
        source: A module's source.

    Returns:
        ``(function, line, method)`` for each call of a guarded method on
        ``get_store()`` or on a name bound to the store.
    """
    tree = ast.parse(source)
    names = store_names(tree)
    found: list[tuple[str, int, str]] = []

    def walk(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            inside = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else function
            )
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                receiver = child.func.value
                direct = (
                    isinstance(receiver, ast.Call)
                    and isinstance(receiver.func, ast.Name)
                    and receiver.func.id == "get_store"
                )
                bound = isinstance(receiver, ast.Name) and receiver.id in names
                if child.func.attr in GUARDED and (direct or bound):
                    found.append((function, child.lineno, child.func.attr))
            walk(child, inside)

    walk(tree, "<module>")
    return found


def python_files(*roots: str) -> Iterator[Path]:
    """
    Args:
        *roots: Paths under the package, files or directories.

    Yields:
        Every Python source file under them.
    """
    for root in roots:
        base = SRC / root
        if base.is_file():
            yield base
        else:
            yield from sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


class TestTheScannerSeesWhatItShould:
    def test_it_finds_a_read_on_get_store_and_on_a_name_bound_to_it(self) -> None:
        source = (
            "def one():\n    return get_store().get_app('x')\n"
            "def two():\n    store = get_store()\n    return store.list_sites()\n"
            "def three(scoped):\n    return scoped.get_app('x')\n"
            "def four(manager):\n    return manager.list_databases()\n"
        )

        assert unscoped_reads(source) == [("one", 2, "get_app"), ("two", 5, "list_sites")]

    def test_every_guarded_name_is_a_method_of_the_scoped_store(self) -> None:
        missing = sorted(name for name in GUARDED if not hasattr(ScopedStore, name))

        assert not missing, f"The scoped store has no answer for: {missing}"


class TestNoRouterReadsAScopedResourceFromTheStore:
    def test_the_api_asks_the_scoped_store(self) -> None:
        offenders = {}
        for path in python_files("web/api"):
            for function, line, method in unscoped_reads(path.read_text(encoding="utf-8")):
                key = (relative(path), function)
                if key not in UNSCOPED_ALLOWED:
                    offenders[f"{key[0]}:{line} {function}() reads the store's {method}"] = key
        assert not offenders, (
            "These routers read a team's resources from the store directly. Ask "
            "noust.core.teams.scoped_store() instead, so what the caller may not see reads as "
            "absent:\n" + "\n".join(sorted(offenders))
        )

    def test_the_streams_ask_the_scoped_store_or_say_why_not(self) -> None:
        offenders = []
        for path in python_files("web/events.py", "web/websockets", "web/machine.py"):
            for function, line, method in unscoped_reads(path.read_text(encoding="utf-8")):
                if (relative(path), function) not in STREAMS_ALLOWED:
                    offenders.append(f"{relative(path)}:{line} {function}() reads {method}")
        assert not offenders, "\n".join(offenders)

    def test_the_allowed_places_still_need_their_allowance(self) -> None:
        stale = []
        for allowed in (UNSCOPED_ALLOWED, STREAMS_ALLOWED):
            for (file, function), _reason in allowed.items():
                reads = unscoped_reads((REPO / file).read_text(encoding="utf-8"))
                if not any(name == function for name, _line, _method in reads):
                    stale.append(f"{file} {function}")
        assert not stale, "No longer reads the store unscoped; remove its allowance:\n" + "\n".join(
            stale
        )


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The console as it is served."""
    root = tmp_path_factory.mktemp("scope-architecture")
    NoustStore.reset_instance()
    NoustStore(root / "noust.db")
    app = create_app(
        SecurityConfig(
            state_dir=root / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
        )
    )
    yield app
    NoustStore.reset_instance()


def served_routes(app: Any) -> Iterator[tuple[str, str, Any]]:
    """
    Args:
        app: The application.

    Yields:
        ``(method, template, route)`` for every route, HEAD left to GET.
    """
    for route in api_routes(app.routes):
        for method in sorted(route.methods or ()):
            if method == "HEAD" and "GET" in (route.methods or ()):
                continue
            yield method, str(route.path_format), route


def dependency_calls(dependant: Any) -> Iterator[Any]:
    """
    Args:
        dependant: A FastAPI dependant.

    Yields:
        Every callable it depends on, however deep.
    """
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from dependency_calls(dependency)


class TestEveryRouteIsHeldToTheScope:
    #: ``GET /events`` is a stream: it filters what it sends by the scope itself.
    SELF_FILTERED = {("GET", "/events")}

    def test_every_route_carries_the_guard(self, served: Any) -> None:
        bare = sorted(
            f"{method} {template}"
            for method, template, route in served_routes(served)
            if permission_for_route(method, template) != PUBLIC
            and (method, template) not in self.SELF_FILTERED
            and not any(call is enforce_scope for call in dependency_calls(route.dependant))
        )
        assert not bare, (
            "These routes do not run noust.web.api.scope.enforce_scope, so a team member "
            "could reach another team's resources through them. Mount their router under "
            "noust.web.api.router, or add the dependency:\n" + "\n".join(bare)
        )

    def test_the_scope_is_decided_before_a_second_person_is_asked(self, served: Any) -> None:
        # A four-eyes request opened for a resource the caller may not know exists would
        # answer 202 where everything else answers 404.
        from noust.web.api.approvals import require_approval
        from noust.web.auth import require_auth

        for method, template, route in served_routes(served):
            if template != "/api/sites/{domain}/config" or method != "PUT":
                continue
            order = [d.call for d in route.dependant.dependencies]
            assert (
                order.index(require_auth)
                < order.index(enforce_scope)
                < order.index(require_approval)
            )
            return
        pytest.fail("the route this checks is gone")

    def test_every_route_is_classified(self, served: Any) -> None:
        unclassified = sorted(
            f"{method} {template}"
            for method, template, _route in served_routes(served)
            if permission_for_route(method, template) != PUBLIC
            and rule_for(method, template) is None
        )
        assert not unclassified, (
            "These routes say nothing about teams. Add a rule for each to "
            "noust.web.permissions.scopes:\n" + "\n".join(unclassified)
        )

    def test_a_route_that_names_a_resource_is_checked_against_it(self, served: Any) -> None:
        # A parameter that identifies a resource must be one the rule checks: a path with
        # {domain} that is "open" would answer any team member about any application.
        names_a_resource = re.compile(r"\{(domain|engine|name|deployment_id|job_id|team)\}")
        loose = []
        for method, template, _route in served_routes(served):
            if permission_for_route(method, template) == PUBLIC:
                continue
            if not names_a_resource.search(template):
                continue
            rule = rule_for(method, template)
            if rule is None or rule.scoping not in (RESOURCE, INSTANCE):
                # A few {name}/{domain} parameters name something that is not a tenant resource.
                if (rule.scoping if rule else None) == OPEN and template in self.NOT_RESOURCES:
                    continue
                loose.append(f"{method} {template} is {rule.scoping if rule else 'unclassified'}")
        assert not loose, "\n".join(loose)

    #: Open routes whose ``{name}``, ``{domain}`` or ``{team}`` is not a team's resource.
    NOT_RESOURCES = {
        "/api/config/notifications/{channel}/test",
        "/api/databases/engines/{engine}/status",
        "/api/recipes/{name}",
        "/api/recipes/{name}/logo",
    }

    def test_every_rule_governs_something(self, served: Any) -> None:
        # A rule nobody matches is a rule for a route that was renamed away.
        matched = {
            id(rule)
            for method, template, _route in served_routes(served)
            if (rule := rule_for(method, template)) is not None
        }
        dead = [
            rule.pattern for rule in RULES if id(rule) not in matched and rule.pattern != "/events"
        ]
        assert not dead, f"No route matches these rules; delete them: {dead}"

    def test_a_resource_rule_names_the_parameters_its_route_has(self, served: Any) -> None:
        kinds = {APP, DOMAIN, DATABASE, DEPLOYMENT, JOB, TEAM}
        wrong = []
        for method, template, _route in served_routes(served):
            rule = rule_for(method, template)
            if rule is None or rule.scoping != RESOURCE:
                continue
            assert rule.refs, f"{method} {template} is a resource route that names no resource"
            for ref in rule.refs:
                assert ref.kind in kinds
                if ref.where == "path":
                    missing = [n for n in ref.names if "{" + n + "}" not in template]
                    if missing:
                        wrong.append(
                            f"{method} {template}: no {{{', '.join(missing)}}} in its path"
                        )
        assert not wrong, "\n".join(wrong)

    def test_the_lists_and_creations_are_the_only_scoped_routes(self, served: Any) -> None:
        scoped = {
            f"{method} {template}"
            for method, template, _route in served_routes(served)
            if (rule := rule_for(method, template)) is not None and rule.scoping == SCOPED
        }
        assert scoped == {
            "GET /api/apps",
            "POST /api/apps",
            "POST /api/apps/inspect",
            "GET /api/sites",
            "POST /api/sites",
            "GET /api/certs",
            "GET /api/databases/databases",
            "POST /api/databases/databases",
            "GET /api/backup-schedules",
            "GET /api/deployments",
            "GET /api/jobs",
            "GET /api/jobs/active",
            "GET /api/jobs/queue",
            "GET /api/recipes/installed",
            "GET /api/teams",
            "GET /api/projects",
            "GET /api/environments",
            "GET /events",
        }
