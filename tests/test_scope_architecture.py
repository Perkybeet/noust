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
  :class:`~noust.core.teams.scoped.ScopedStore`, which filters in SQL;
- every table another area of the release adds for something an application or
  a database owns (a build block, a deploy key, a database open to the network,
  a volume backup policy) says how a team's scope reaches it, and the routes it
  names are classified as it says.

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
from noust.core import schema_v14
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
        names_a_resource = re.compile(
            r"\{(domain|engine|name|database|deployment_id|job_id|team)\}"
        )
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
        "/api/recipes/{name}/requirements",
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
            "POST /api/workspaces/inspect",
            "GET /api/sites",
            "POST /api/sites",
            "GET /api/certs",
            "GET /api/databases/databases",
            "POST /api/databases/databases",
            "GET /api/databases/public",
            "GET /api/deploy-keys",
            "GET /api/backup-schedules",
            "GET /api/deployments",
            "GET /api/jobs",
            "GET /api/terminal/sessions",
            "DELETE /api/terminal/sessions/{session_id}",
            "GET /api/jobs/active",
            "GET /api/jobs/queue",
            "POST /api/jobs/update-many",
            "GET /api/recipes/installed",
            "GET /api/tags",
            "POST /api/tags/{tag}/deploy",
            "GET /api/teams",
            "GET /api/projects",
            "GET /api/environments",
            "GET /events",
        }


#: Columns that say a row belongs to an application, a database or a repository built by one.
OWNER_COLUMNS = frozenset({"app_id", "app_domain", "domain", "db_name", "database", "repository"})

#: The fragment of the teams themselves: its tables are the model, not something it owns.
TEAMS_FRAGMENT = "TEAMS"

#: How a team's scope reaches what each table of the 3.4 fragments holds for an application or a
#: database: ``(method, route, how the route is classified)``. Each route must be served and must
#: be classified as said, so a route that drifts to "open" or loses its resource fails here.
OWNED_TABLES: dict[str, tuple[tuple[str, str, str], ...]] = {
    # The text a site's file held: reached through the site, which names its application.
    "site_config_versions": (
        ("GET", "/api/sites/{domain}/config/versions", RESOURCE),
        ("GET", "/api/sites/{domain}/config/versions/{version}", RESOURCE),
        ("POST", "/api/sites/{domain}/config/versions/{version}/restore", RESOURCE),
    ),
    # The certificates signed for an application's clients: reached through the application.
    "client_certificates": (
        ("GET", "/api/apps/{domain}/client-certs", RESOURCE),
        ("POST", "/api/apps/{domain}/client-certs", RESOURCE),
        ("DELETE", "/api/apps/{domain}/client-certs/{serial}", RESOURCE),
        ("GET", "/api/apps/{domain}/client-certs/ca", RESOURCE),
    ),
    # A rule may name an application to watch; the rules are the server's, like the monitor.
    "alert_rules": (
        ("GET", "/api/alerts/rules", INSTANCE),
        ("PUT", "/api/alerts/rules/{name}", INSTANCE),
        ("DELETE", "/api/alerts/rules/{name}", INSTANCE),
    ),
    # An application's own routes: another team's reads as absent before the handler runs.
    "app_build": (
        ("GET", "/api/apps/{domain}/build", RESOURCE),
        ("PUT", "/api/apps/{domain}/build", RESOURCE),
        ("DELETE", "/api/apps/{domain}/build", RESOURCE),
        ("GET", "/api/apps/{domain}/build-cache", RESOURCE),
        ("DELETE", "/api/apps/{domain}/build-cache", RESOURCE),
    ),
    # Which accounts an application uses: seen through the application, set by the server.
    "app_registry_logins": (
        ("GET", "/api/apps/{domain}/registry-logins", RESOURCE),
        ("PUT", "/api/apps/{domain}/registry-logins", INSTANCE),
    ),
    "app_compose": (
        ("GET", "/api/apps/{domain}/compose", RESOURCE),
        ("PUT", "/api/apps/{domain}/compose", RESOURCE),
        ("DELETE", "/api/apps/{domain}/compose", RESOURCE),
    ),
    "app_source": (
        ("GET", "/api/apps/{domain}/source-options", RESOURCE),
        ("PUT", "/api/apps/{domain}/source-options", RESOURCE),
        ("DELETE", "/api/apps/{domain}/source-options", RESOURCE),
    ),
    "app_container": (
        ("GET", "/api/apps/{domain}/container", RESOURCE),
        ("PUT", "/api/apps/{domain}/container", RESOURCE),
    ),
    # The mode a link asks of the engine's certificate: read and written with the link.
    "database_links": (
        ("GET", "/api/apps/{domain}/databases", RESOURCE),
        ("POST", "/api/apps/{domain}/databases/link", RESOURCE),
    ),
    "restore_drills": (
        ("GET", "/api/apps/{domain}/restore-drills", RESOURCE),
        ("POST", "/api/apps/{domain}/restore-drills", RESOURCE),
    ),
    "app_update_policy": (
        ("GET", "/api/apps/{domain}/backup-required", RESOURCE),
        ("PUT", "/api/apps/{domain}/backup-required", RESOURCE),
    ),
    # The health check and the unit's sandbox: an application's own, whoever's team it is in.
    "app_health": (
        ("GET", "/api/apps/{domain}/health", RESOURCE),
        ("PATCH", "/api/apps/{domain}/health", RESOURCE),
    ),
    "app_processes": (
        ("GET", "/api/apps/{domain}/processes", RESOURCE),
        ("PUT", "/api/apps/{domain}/processes/{name}", RESOURCE),
        ("POST", "/api/apps/{domain}/processes/{name}/scale", RESOURCE),
        ("DELETE", "/api/apps/{domain}/processes/{name}", RESOURCE),
        ("GET", "/api/apps/{domain}/processes/{name}/logs", RESOURCE),
    ),
    "app_mounts": (
        ("GET", "/api/apps/{domain}/mounts", RESOURCE),
        ("GET", "/api/apps/{domain}/mounts/content", RESOURCE),
        ("PUT", "/api/apps/{domain}/mounts", RESOURCE),
        ("DELETE", "/api/apps/{domain}/mounts", RESOURCE),
    ),
    "app_applied_config": (("GET", "/api/apps/{domain}/pending", RESOURCE),),
    # The images a stack kept for each release it served: reached through its releases.
    "compose_release_images": (
        ("GET", "/api/apps/{domain}/releases", RESOURCE),
        ("POST", "/api/apps/{domain}/releases/{release_id}/activate", RESOURCE),
        ("PATCH", "/api/apps/{domain}/releases/retention", RESOURCE),
    ),
    "app_replicas": (
        ("GET", "/api/apps/{domain}/replicas", RESOURCE),
        ("PUT", "/api/apps/{domain}/replicas", RESOURCE),
    ),
    "app_hardening": (
        ("GET", "/api/apps/{domain}/hardening", RESOURCE),
        ("PUT", "/api/apps/{domain}/hardening", RESOURCE),
    ),
    "app_recipes": (
        ("GET", "/api/recipes/installed", SCOPED),
        ("GET", "/api/recipes/installed/{domain}", RESOURCE),
        ("POST", "/api/recipes/installed/{domain}/update", RESOURCE),
    ),
    # Asked to stop through the job, which is the application's.
    "delivery_cancel_requests": (("POST", "/api/jobs/{job_id}/cancel", RESOURCE),),
    "deployments": (
        ("GET", "/api/deployments", SCOPED),
        ("GET", "/api/deployments/{deployment_id}", RESOURCE),
    ),
    # A key is found by repository: listed by the repositories the caller's applications are built
    # from, and changed through the application that is.
    "deploy_keys": (
        ("GET", "/api/deploy-keys", SCOPED),
        ("GET", "/api/apps/{domain}/deploy-key", RESOURCE),
        ("POST", "/api/apps/{domain}/deploy-key", RESOURCE),
        ("POST", "/api/apps/{domain}/deploy-key/rotate", RESOURCE),
        ("GET", "/api/apps/{domain}/git-credential", RESOURCE),
        ("PUT", "/api/apps/{domain}/git-credential", RESOURCE),
    ),
    "deploy_key_repositories": (
        ("GET", "/api/deploy-keys", SCOPED),
        ("DELETE", "/api/apps/{domain}/deploy-key", RESOURCE),
    ),
    # A port, an account on the engine and a firewall rule: opening is the server's; seeing,
    # reading and closing one are its database's owner's.
    "database_exposures": (
        ("GET", "/api/databases/public", SCOPED),
        ("POST", "/api/databases/public", INSTANCE),
        ("POST", "/api/databases/public/plan", INSTANCE),
        ("GET", "/api/databases/public/{engine}/{database}", RESOURCE),
        ("POST", "/api/databases/public/{engine}/{database}/reveal", RESOURCE),
        ("DELETE", "/api/databases/public/{engine}/{database}", RESOURCE),
    ),
    # Each writes a root timer and names a destination the server owns: the server's.
    "volume_backup_policies": (
        ("GET", "/api/volume-backups/policies", INSTANCE),
        ("GET", "/api/volume-backups/policies/{domain}", INSTANCE),
        ("PUT", "/api/volume-backups/policies/{domain}", INSTANCE),
        ("DELETE", "/api/volume-backups/policies/{domain}", INSTANCE),
        ("POST", "/api/volume-backups/policies/{domain}/run", INSTANCE),
    ),
    "database_backup_policies": (
        ("GET", "/api/databases/backup-policies", INSTANCE),
        ("PUT", "/api/databases/backup-policies/{engine}/{database}", INSTANCE),
        ("POST", "/api/databases/backup-policies/{engine}/{database}/run", INSTANCE),
    ),
    "self_backup_schedule": (
        ("GET", "/api/self-backup", INSTANCE),
        ("PUT", "/api/self-backup/schedule", INSTANCE),
    ),
    # What an application's variables are made of is read and written through the application;
    # a variable shared by a team, a project or an environment is that team's, reached through
    # its path, and the server's own variables are the server's. The set previews are given is
    # the application's.
    "app_env_variables": (
        ("GET", "/api/apps/{domain}/env", RESOURCE),
        ("PUT", "/api/apps/{domain}/env", RESOURCE),
        ("POST", "/api/apps/{domain}/env/resolve", RESOURCE),
    ),
    "shared_variables": (
        ("GET", "/api/teams/{team}/variables", RESOURCE),
        ("GET", "/api/teams/{team}/projects/{project}/variables", RESOURCE),
        (
            "GET",
            "/api/teams/{team}/projects/{project}/environments/{environment}/variables",
            RESOURCE,
        ),
        ("GET", "/api/variables/server", INSTANCE),
        ("GET", "/api/apps/{domain}/previews/variables", RESOURCE),
        ("PUT", "/api/apps/{domain}/previews/variables/{name}", RESOURCE),
    ),
    # Tags belong to the applications that carry them: listed and deployed within what the
    # caller sees (the handler asks the scoped store), set through the application.
    "app_tags": (
        ("GET", "/api/apps/{domain}/tags", RESOURCE),
        ("PUT", "/api/apps/{domain}/tags", RESOURCE),
        ("GET", "/api/tags", SCOPED),
        ("POST", "/api/tags/{tag}/deploy", SCOPED),
    ),
    # How the previews of an application are made, and what each was made from: the application's.
    "preview_options": (
        ("GET", "/api/apps/{domain}/previews", RESOURCE),
        ("PUT", "/api/apps/{domain}/previews/settings", RESOURCE),
    ),
    "preview_origins": (
        ("GET", "/api/apps/{domain}/previews", RESOURCE),
        ("POST", "/api/apps/{domain}/previews", RESOURCE),
        ("GET", "/api/apps/{domain}/previews/{number}", RESOURCE),
        ("POST", "/api/apps/{domain}/previews/{number}/redeploy", RESOURCE),
    ),
    # Networking: what an application's site does and answers on. Each is the application's
    # own, under its path; the ranges of Cloudflare they may trust are the server's.
    "site_policies": (
        ("GET", "/api/apps/{domain}/policy", RESOURCE),
        ("PUT", "/api/apps/{domain}/policy", RESOURCE),
        ("DELETE", "/api/apps/{domain}/policy", RESOURCE),
        ("POST", "/api/policy/cloudflare-ranges/refresh", INSTANCE),
    ),
    "site_auth_users": (
        ("PUT", "/api/apps/{domain}/policy/users/{username}", RESOURCE),
        ("DELETE", "/api/apps/{domain}/policy/users/{username}", RESOURCE),
    ),
    "domains": (
        ("GET", "/api/apps/{domain}/domains", RESOURCE),
        ("POST", "/api/apps/{domain}/domains", RESOURCE),
        ("PUT", "/api/apps/{domain}/domains/canonical", RESOURCE),
        ("PUT", "/api/apps/{domain}/domains/{name}/port", RESOURCE),
        ("DELETE", "/api/apps/{domain}/domains/{name}", RESOURCE),
    ),
    # How an application's certificate comes to be is its own; the authority it orders from
    # and the certificates uploaded are the server's.
    "certificate_settings": (
        ("GET", "/api/apps/{domain}/certificate", RESOURCE),
        ("PUT", "/api/apps/{domain}/certificate", RESOURCE),
        ("GET", "/api/certs/uploaded", INSTANCE),
    ),
    # A record an application uses is read and made through the application; the managed
    # records as a whole, the accounts they are at and their sweep are the server's.
    "dns_record_refs": (
        ("GET", "/api/apps/{domain}/dns", RESOURCE),
        ("POST", "/api/apps/{domain}/dns", RESOURCE),
        ("GET", "/api/dns/records", INSTANCE),
        ("POST", "/api/dns/sweep", INSTANCE),
    ),
    # A published port may be for an application, but it opens the firewall and changes nginx:
    # it is the server's, however it is named.
    "stream_exposures": (
        ("GET", "/api/streams", INSTANCE),
        ("POST", "/api/streams/plan", INSTANCE),
        ("POST", "/api/streams", INSTANCE),
        ("DELETE", "/api/streams/{name}", INSTANCE),
    ),
    # A tunnel carries names for applications, but it is a service with a token that edits DNS:
    # the server's, however a route is named.
    "tunnel_hostnames": (
        ("GET", "/api/tunnels", INSTANCE),
        ("POST", "/api/tunnels", INSTANCE),
        ("DELETE", "/api/tunnels/{name}", INSTANCE),
        ("POST", "/api/tunnels/{name}/routes", INSTANCE),
        ("DELETE", "/api/tunnels/{name}/routes/{hostname}", INSTANCE),
    ),
}

#: Tables with a column that looks like an owner and is not one, and why.
NOT_OWNED: dict[str, str] = {
    "secret_providers": "the secret managers a deploy can fetch from: the server's, which decides "
    "what any team's deploy can reach (the optional team only narrows who may name one)",
    "console_exposure": "the name the console itself is published on: the server's, not an application's",
    "db_instances": "an engine Noust runs in a container beside the host's: the server's, like the "
    "host's engines. A database on it is a team's, and is reached as any database is",
}

#: Prefixes whose routes belong to what the tables above hold. None of them may be "open": a route
#: under one that forgot to say who it is about would answer every team about every other.
OWNED_PREFIXES = (
    "/api/alerts",
    "/api/notifications",
    "/api/deploy-keys",
    "/api/databases/public",
    "/api/databases/backup-policies",
    "/api/databases/backups",
    "/api/databases/imports",
    "/api/databases/instances",
    "/api/volume-backups",
    "/api/self-backup",
    "/api/backups",
    "/api/backup-destinations",
    "/api/recipes/installed",
    "/api/tags",
    "/api/variables",
    "/api/secret-providers",
    "/api/dns",
    "/api/streams",
    "/api/tunnels",
)


def fragment_tables() -> dict[str, set[str]]:
    """
    Read the tables the 3.4 fragments create, and the columns each declares.

    Returns:
        ``{table: columns}`` for every ``CREATE TABLE`` of a fragment other
        than the teams' own.
    """
    found: dict[str, set[str]] = {}
    for name in vars(schema_v14):
        if not name.endswith("_SQL") or name.removesuffix("_SQL") == TEAMS_FRAGMENT:
            continue
        for table, body in re.findall(
            r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", getattr(schema_v14, name), re.S
        ):
            found[table] = {
                line.split()[0]
                for line in body.splitlines()
                if line.startswith("    ") and not line.startswith("     ")
            }
    return found


def fragment_altered_tables() -> set[str]:
    """
    Returns:
        The tables a fragment other than the teams' own adds columns to.
    """
    return {
        table
        for name in vars(schema_v14)
        if name.endswith("_COLUMNS") and name.removesuffix("_COLUMNS") != TEAMS_FRAGMENT
        for table, _column, _definition in getattr(schema_v14, name)
    }


class TestEveryResourceAnotherAreaAddedSaysHowATeamReachesIt:
    def test_the_scanner_finds_the_tables_it_is_about(self) -> None:
        tables = fragment_tables()

        assert tables["app_build"] == {"app_id", "document", "applied", "updated_by", "updated_at"}
        assert "db_name" in tables["database_exposures"]
        assert "app_domain" in tables["volume_backup_policies"]
        assert "teams" not in tables
        assert {"deployments", "database_backup_policies"} <= fragment_altered_tables()

    def test_a_table_that_names_an_application_or_a_database_is_accounted_for(self) -> None:
        owned = {t for t, columns in fragment_tables().items() if columns & OWNER_COLUMNS}
        owned |= fragment_altered_tables()

        unaccounted = sorted(owned - OWNED_TABLES.keys() - NOT_OWNED.keys())

        assert not unaccounted, (
            "These tables hold rows that belong to an application or a database and no entry of "
            "OWNED_TABLES says how a team's scope reaches them. Reach them through a route "
            "whose rule in noust.web.permissions.scopes names the resource (a path with "
            "{domain}), through a list the handler filters, or make the route the server's, "
            "then list them here with those routes:\n" + "\n".join(unaccounted)
        )

    def test_the_lists_hold_no_table_that_is_gone(self) -> None:
        known = fragment_tables().keys() | fragment_altered_tables()

        gone = sorted((OWNED_TABLES.keys() | NOT_OWNED.keys()) - known)

        assert not gone, f"No fragment creates or alters these any more; remove them: {gone}"

    def test_a_table_is_not_owned_and_not_owned_at_once(self) -> None:
        assert not OWNED_TABLES.keys() & NOT_OWNED.keys()

    def test_every_way_in_is_served_and_classified_as_declared(self, served: Any) -> None:
        classified = {
            (method, template): rule
            for method, template, _route in served_routes(served)
            if (rule := rule_for(method, template)) is not None
        }
        wrong = []
        for table, ways in OWNED_TABLES.items():
            for method, template, scoping in ways:
                rule = classified.get((method, template))
                if rule is None:
                    wrong.append(f"{table}: {method} {template} is not a route of the console")
                elif rule.scoping != scoping:
                    wrong.append(f"{table}: {method} {template} is {rule.scoping}, not {scoping}")
        assert not wrong, "\n".join(wrong)

    def test_nothing_under_what_these_tables_hold_is_open(self, served: Any) -> None:
        open_routes = sorted(
            f"{method} {template}"
            for method, template, _route in served_routes(served)
            if any(template == p or template.startswith(p + "/") for p in OWNED_PREFIXES)
            and (rule := rule_for(method, template)) is not None
            and rule.scoping == OPEN
        )
        assert not open_routes, (
            "These routes sit under the resources of an application or a database and say "
            "nothing about whose they are:\n" + "\n".join(open_routes)
        )

    def test_the_prefixes_still_have_routes(self, served: Any) -> None:
        templates = [template for _method, template, _route in served_routes(served)]
        empty = [
            prefix
            for prefix in OWNED_PREFIXES
            if not any(t == prefix or t.startswith(prefix + "/") for t in templates)
        ]
        assert not empty, f"No route is served under these any more; remove them: {empty}"
