# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How each route meets the teams: which resource it addresses, or why it needs none.

``routes_<area>.py`` says what permission a route needs. This module says what
the route is *about*, because with teams (I-01) a permission is not enough:
``apps.restart`` on another team's application is not a permission the caller
lacks, it is an application the caller does not see. Every route is one of:

- **resource**: addressed to one resource (``/api/apps/{domain}/restart``), in
  the path, the query or the body. A principal that does not see it is answered
  404 before the handler runs, as if it did not exist (I-04), and one that sees
  it must hold the route's permission *in the resource's team*.
- **scoped**: a list, a creation or an aggregate. The handler asks the
  :class:`~noust.core.teams.scoped.ScopedStore`, which filters in SQL, and
  creations name the project they go to.
- **open**: server-wide and about nobody's resources (the configuration, the
  recipe catalogue, the OpenAPI document, one's own session). Held to the
  permissions the principal's *instance* role holds, as before teams.
- **instance**: it shows or changes something that belongs to the whole server,
  or to resources this release does not yet filter by team (the services and
  their journals, cron jobs, the host's processes, backups, nodes). Only a
  principal that sees every team may call it.

A principal "sees every team" when it is an instance principal (the master
token, a central's fleet token), an ``admin``, ``security`` or ``auditor``
account, or a member of every team there is: on a server with one team nothing
is hidden from anyone, so a server nobody organised in teams behaves as 3.3 did.

The rules are ordered, first match wins, and a route no rule names is refused
to a principal that does not see every team and fails
``tests/test_scope_routes.py``: a new route is classified before it is
served, exactly as with permissions.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The kinds of route, as described in the module docstring.
RESOURCE = "resource"
SCOPED = "scoped"
OPEN = "open"
INSTANCE = "instance"

#: Where a resource's name is read from.
PATH = "path"
QUERY = "query"
BODY = "body"

#: What a route can be addressed to.
APP = "app"
DOMAIN = "domain"
DATABASE = "database"
DEPLOYMENT = "deployment"
JOB = "job"
TEAM = "team"


@dataclass(frozen=True)
class Ref:
    """
    One resource a route is addressed to.

    Attributes:
        kind: :data:`APP`, :data:`DOMAIN` (an application's name, an alias or a
            site), :data:`DATABASE` (needs ``engine`` and ``name``),
            :data:`DEPLOYMENT`, :data:`JOB` or :data:`TEAM`.
        where: :data:`PATH`, :data:`QUERY` or :data:`BODY`.
        names: The parameters that name it, in the order the kind reads them:
            ``("domain",)``, or ``("engine", "name")`` for a database.
    """

    kind: str
    where: str = PATH
    names: tuple[str, ...] = ("domain",)


@dataclass(frozen=True)
class Rule:
    """
    One line of the table.

    Attributes:
        pattern: A route template, exactly as FastAPI reports it. A trailing
            ``/**`` matches the template itself and everything below it.
        scoping: :data:`RESOURCE`, :data:`SCOPED`, :data:`OPEN` or :data:`INSTANCE`.
        refs: The resources a :data:`RESOURCE` route is addressed to; every one
            must be visible, and the route's permission held in each one's team.
        methods: Only these methods, or every method when empty.
    """

    pattern: str
    scoping: str
    refs: tuple[Ref, ...] = ()
    methods: frozenset[str] = frozenset()

    def matches(self, method: str, template: str) -> bool:
        """
        Args:
            method: The HTTP method.
            template: The route's template.

        Returns:
            Whether this rule governs the route.
        """
        if self.methods and method.upper() not in self.methods:
            return False
        if self.pattern.endswith("/**"):
            base = self.pattern[:-3]
            return template == base or template.startswith(base + "/")
        return template == self.pattern


def _rule(pattern: str, scoping: str, *refs: Ref, methods: tuple[str, ...] = ()) -> Rule:
    return Rule(pattern, scoping, refs, frozenset(methods))


_APP = Ref(APP)
_DOMAIN = Ref(DOMAIN)
_DATABASE = Ref(DATABASE, PATH, ("engine", "name"))
_BODY_DATABASE = Ref(DATABASE, BODY, ("engine", "database"))
#: ``/api/databases/public/{engine}/{database}``: the same resource, whose path names it so.
_PUBLIC_DATABASE = Ref(DATABASE, PATH, ("engine", "database"))
_BODY_DOMAIN = Ref(APP, BODY, ("domain",))

#: The table, most specific first.
RULES: tuple[Rule, ...] = (
    # --- applications ---------------------------------------------------------
    _rule("/api/apps", SCOPED),
    _rule("/api/apps/types", OPEN),
    _rule("/api/apps/inspect", SCOPED),
    # An export document may carry cron jobs and previews, which run as root or reach
    # other applications: a team member does not get to apply one.
    _rule("/api/apps/import", INSTANCE),
    # Adopting a stack that already runs reaches containers nobody here owns.
    _rule("/api/apps/adopt", INSTANCE),
    # A database linked to an application changes owner with it, so it must be visible too.
    _rule("/api/apps/{domain}/databases/link", RESOURCE, _APP, _BODY_DATABASE),
    _rule("/api/apps/{domain}/databases/{engine}/{name}/**", RESOURCE, _APP, _DATABASE),
    _rule("/api/apps/{domain}/**", RESOURCE, _APP),
    # --- the terminal ---------------------------------------------------------
    # What is open is listed and ended per application (the handler filters); what a
    # session left is the audit's, which is the server's.
    _rule("/api/terminal/status", OPEN),
    _rule("/api/terminal/sessions", SCOPED),
    _rule("/api/terminal/sessions/{session_id}", SCOPED),
    _rule("/api/terminal/recordings/**", INSTANCE),
    # --- sites, domains, certificates ----------------------------------------
    _rule("/api/sites", SCOPED),
    _rule("/api/sites/templates", OPEN),
    # Reloading the web server touches every site it serves.
    _rule("/api/sites/reload", INSTANCE),
    _rule("/api/sites/{domain}/**", RESOURCE, _DOMAIN),
    _rule("/api/domains/dns", OPEN),
    _rule("/api/certs", SCOPED),
    _rule("/api/certs/renew-all", INSTANCE),
    _rule("/api/certs/{domain}/**", RESOURCE, _DOMAIN),
    # --- databases ------------------------------------------------------------
    _rule("/api/databases/databases", SCOPED),
    # Adopting records every database an engine holds, other teams' included.
    _rule("/api/databases/databases/adopt", INSTANCE),
    _rule("/api/databases/databases/{engine}/{name}/**", RESOURCE, _DATABASE),
    _rule("/api/databases/provisioning/plan", RESOURCE, Ref(APP, QUERY, ("domain",))),
    # The engines and versions this server can offer: a catalogue, like the engines'.
    _rule("/api/databases/provisioning/choices", OPEN),
    _rule("/api/databases/query", RESOURCE, _BODY_DATABASE),
    _rule("/api/databases/query/explain", RESOURCE, _BODY_DATABASE),
    _rule("/api/databases/query/export", RESOURCE, _BODY_DATABASE),
    # Formats what the caller typed; nothing of the server's.
    _rule("/api/databases/connection-string", OPEN),
    # Per operator: history and saved queries are filtered by whoever asks.
    _rule("/api/databases/console/**", OPEN),
    _rule("/api/databases/engines", OPEN, methods=("GET",)),
    _rule("/api/databases/engines/catalog", OPEN, methods=("GET",)),
    _rule("/api/databases/engines/{engine}/status", OPEN, methods=("GET",)),
    _rule("/api/databases/engines/**", INSTANCE),
    # An engine Noust runs itself, in a container, is the server's like the host's: a database
    # on it is a team's, the engine is nobody's. Making, upgrading and removing one is the
    # server's; a team member picks one for its application's database through the choices.
    _rule("/api/databases/instances/**", INSTANCE),
    _rule("/api/databases/exposure", INSTANCE),
    # Making a database reachable from the network opens a port, an account on the engine and
    # a firewall rule: the host's, whoever's database it is. A team member sees which of its
    # databases are open, can read how to connect and closes one; opening is the server's.
    _rule("/api/databases/public", SCOPED, methods=("GET",)),
    _rule("/api/databases/public", INSTANCE, methods=("POST",)),
    _rule("/api/databases/public/plan", INSTANCE),
    _rule("/api/databases/public/{engine}/{database}/**", RESOURCE, _PUBLIC_DATABASE),
    _rule("/api/databases/users/**", INSTANCE),
    _rule("/api/databases/backups/**", INSTANCE),
    # Loading a dump into an engine creates databases and may replace one: the server's.
    _rule("/api/databases/imports/**", INSTANCE),
    _rule("/api/databases/backup-policies/**", INSTANCE),
    # --- backups --------------------------------------------------------------
    _rule("/api/backup-schedules", SCOPED, methods=("GET",)),
    _rule("/api/backup-schedules", RESOURCE, _BODY_DOMAIN, methods=("POST",)),
    _rule("/api/backup-schedules/{domain}", RESOURCE, _APP),
    _rule("/api/backups/**", INSTANCE),
    _rule("/api/backup-destinations/**", INSTANCE),
    # Where the logs of applications leave the server for: the server's.
    _rule("/api/log-drains/**", INSTANCE),
    # Volumes, and Noust's own backup, are copies of the machine's disks and of its state.
    _rule("/api/volume-backups/**", INSTANCE),
    _rule("/api/self-backup/**", INSTANCE),
    # --- deploy keys -----------------------------------------------------------
    # A key is found by repository, so one may reach the applications of several teams: the
    # list shows each team the keys of the repositories its applications are built from.
    _rule("/api/deploy-keys", SCOPED),
    # What the server's known_hosts has pinned is the server's own trust in the network.
    _rule("/api/deploy-keys/hosts/**", INSTANCE),
    # --- history of an application -------------------------------------------
    _rule("/api/deployments", SCOPED),
    _rule(
        "/api/deployments/{deployment_id}/**", RESOURCE, Ref(DEPLOYMENT, PATH, ("deployment_id",))
    ),
    _rule("/api/jobs", SCOPED),
    _rule("/api/jobs/active", SCOPED),
    _rule("/api/jobs/queue", SCOPED),
    _rule("/api/jobs/cleanup", INSTANCE),
    _rule("/api/jobs/update", RESOURCE, _BODY_DOMAIN),
    _rule("/api/jobs/delete", RESOURCE, _BODY_DOMAIN),
    _rule("/api/jobs/backup", RESOURCE, _BODY_DOMAIN),
    _rule("/api/jobs/rollback", RESOURCE, _BODY_DOMAIN),
    _rule("/api/jobs/cert", RESOURCE, Ref(DOMAIN, BODY, ("domain",))),
    _rule("/api/jobs/{job_id}/**", RESOURCE, Ref(JOB, PATH, ("job_id",))),
    # --- teams ----------------------------------------------------------------
    # Creating a team is the administrator's: a team is handed to its owners afterwards.
    _rule("/api/teams", INSTANCE, methods=("POST",)),
    _rule("/api/teams", SCOPED),
    _rule("/api/teams/mine", OPEN),
    _rule("/api/teams/roles", OPEN),
    _rule("/api/teams/active", OPEN),
    _rule("/api/teams/{team}/**", RESOURCE, Ref(TEAM, PATH, ("team",))),
    _rule("/api/projects", SCOPED),
    _rule("/api/environments", SCOPED),
    # --- the rest is about the server, or about nobody ------------------------
    _rule("/api/auth/**", OPEN),
    _rule("/api/approvals/**", OPEN),
    _rule("/api/audit/**", OPEN),
    _rule("/api/openapi.json", OPEN),
    _rule("/api/config/**", OPEN),
    # The catalogue is nobody's; what was installed from it is an application's.
    _rule("/api/recipes/installed", SCOPED),
    _rule("/api/recipes/installed/{domain}/**", RESOURCE, _APP),
    # Installing creates an application, which a team member does through POST /api/apps
    # with a recipe, naming its project; this route does not name one yet.
    _rule("/api/recipes/{name}/install", INSTANCE, methods=("POST",)),
    _rule("/api/recipes/**", OPEN),
    _rule("/api/central/**", OPEN),
    _rule("/api/ens/inventory/**", INSTANCE),
    _rule("/api/ens/**", OPEN),
    _rule("/api/system/version", OPEN),
    _rule("/api/system/cpu", OPEN),
    _rule("/api/system/memory", OPEN),
    _rule("/api/system/disks", OPEN),
    _rule("/api/system/network", OPEN),
    _rule("/api/system/machine", OPEN),
    _rule("/api/system", OPEN),
    _rule("/api/system/**", INSTANCE),
    _rule("/api/server/**", INSTANCE),
    _rule("/api/services/**", INSTANCE),
    _rule("/api/cron/**", INSTANCE),
    _rule("/api/monitor/**", INSTANCE),
    _rule("/api/metrics/**", INSTANCE),
    _rule("/api/overview", INSTANCE),
    _rule("/api/timeline", INSTANCE),
    _rule("/api/integrations/**", INSTANCE),
    # How the console itself is exposed: the server's.
    _rule("/api/console/**", INSTANCE),
    _rule("/api/fleet/**", INSTANCE),
    _rule("/api/nodes/**", INSTANCE),
    _rule("/events", SCOPED),
)


def rule_for(method: str, template: str) -> Rule | None:
    """
    Find the rule that governs a route.

    Args:
        method: The HTTP method.
        template: The route's template, such as ``/api/apps/{domain}/restart``.

    Returns:
        The first rule that matches, or None when no rule names the route.
    """
    for rule in RULES:
        if rule.matches(method, template):
            return rule
    return None
