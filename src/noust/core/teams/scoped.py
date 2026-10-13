# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ownership filter of the data layer: the one way the API reads resources.

:class:`ScopedStore` answers the questions the API asks the store about
applications, databases, sites, backup schedules and their history, and only
about what a :class:`~noust.core.teams.scope.Scope` sees. The filter is in the
SQL (:func:`~noust.core.teams.scope.visible_clause`), not applied to what came
back, so a list is never short of rows a hidden one displaced, and a count
never includes what the principal may not know exists.

What is outside the scope reads as absent (I-04): a lookup answers None, a
``require_*`` raises :class:`~noust.core.teams.model.ResourceNotFoundError`
(404), and nothing in between reveals that the resource is there. There is
never a 403 for a resource, because a 403 says "it exists".

The store's own readers (``NoustStore.get_app`` and the rest) remain, for what
runs as the instance: jobs, the monitor, the CLI as root. A router must not
call them for these resources - ``tests/test_scope_architecture.py`` fails when
one does - so a route that forgot to ask "whose is this" cannot be written.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, TypeVar

from noust.core.teams.model import ResourceNotFoundError, Target
from noust.core.teams.scope import Scope, current_scope, visible_clause

if TYPE_CHECKING:
    from noust.core.store import (
        App,
        BackupScheduleRecord,
        Database,
        DeploymentRecord,
        DomainRecord,
        JobRecord,
        NoustStore,
        Site,
    )

_T = TypeVar("_T")


class ScopedStore:
    """
    Read applications, databases, sites and backup schedules as a principal.

    Args:
        scope: Whom the reads are for; the ambient request scope by default,
            which outside a request is the instance's.
        store: The store; the process-wide one by default.
    """

    def __init__(self, scope: Scope | None = None, store: NoustStore | None = None) -> None:
        self._scope = scope
        self._store = store

    @property
    def scope(self) -> Scope:
        """Whom the reads are for."""
        return self._scope if self._scope is not None else current_scope()

    @property
    def store(self) -> NoustStore:
        """The store underneath."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    # ----------------------------------------------------------------- plumbing

    def _clause(self, column: str = "project_id") -> tuple[str, list[int]]:
        return visible_clause(self.scope, column)

    def _select(
        self,
        table: str,
        build: Callable[[Any], _T],
        where: str = "1 = 1",
        params: Iterable[Any] = (),
        *,
        order: str = "",
        limit: int | None = None,
    ) -> list[_T]:
        """
        Read the visible rows of a table.

        Args:
            table: An owned table: ``apps``, ``sites``, ``databases`` or
                ``backup_schedules``.
            build: Turns a row into a record.
            where: The caller's own condition, over fixed text.
            params: Its parameters.
            order: An ``ORDER BY`` clause body, or empty.
            limit: At most this many rows.

        Returns:
            The records.
        """
        clause, scope_params = self._clause()
        sql = f"SELECT * FROM {table} WHERE ({where}) AND {clause}"  # noqa: S608 - fixed names
        values: list[Any] = [*params, *scope_params]
        if order:
            sql += f" ORDER BY {order}"
        if limit is not None:
            sql += " LIMIT ?"
            values.append(limit)
        with self.store._transaction() as cursor:
            cursor.execute(sql, values)
            return [build(row) for row in cursor.fetchall()]

    @staticmethod
    def _owner_filters(
        project: str | None, environment: str | None, team: str | None
    ) -> tuple[str, list[str]]:
        """
        Turn the ``project``, ``environment`` and ``team`` filters of a list
        into SQL over the owner columns.

        Args:
            project: A project slug.
            environment: An environment slug.
            team: A team slug.

        Returns:
            A condition (``1 = 1`` for none) and its parameters.
        """
        parts: list[str] = []
        params: list[str] = []
        if team is not None:
            parts.append(
                "project_id IN (SELECT p.id FROM projects p JOIN teams t "
                "ON t.id = p.team_id WHERE t.slug = ?)"
            )
            params.append(team.strip().lower())
        if project is not None:
            parts.append("project_id IN (SELECT id FROM projects WHERE slug = ?)")
            params.append(project.strip().lower())
        if environment is not None:
            parts.append("environment_id IN (SELECT id FROM environments WHERE slug = ?)")
            params.append(environment.strip().lower())
        return (" AND ".join(parts) or "1 = 1"), params

    # ------------------------------------------------------------- applications

    def get_app(self, domain: str) -> App | None:
        """
        Args:
            domain: An application's domain.

        Returns:
            The application, or None when there is none or it is not visible.
        """
        from noust.core.store import App

        rows = self._select("apps", App.from_row, "domain = ?", (domain,))
        return rows[0] if rows else None

    def get_app_by_id(self, app_id: int) -> App | None:
        """
        Args:
            app_id: An application's id.

        Returns:
            The application, or None when there is none or it is not visible.
        """
        from noust.core.store import App

        rows = self._select("apps", App.from_row, "id = ?", (app_id,))
        return rows[0] if rows else None

    def domain_is_taken(self, domain: str) -> bool:
        """
        Whether an application, of any team, is deployed at a domain.

        Deliberately not filtered: a domain is one per server, so a name that
        another team holds is a conflict for everyone, and answering "free"
        would only move the failure to the job that tries to create it. What it
        reveals is that the name is taken, which the conflict does either way.

        Args:
            domain: A domain.

        Returns:
            True when an application has it.
        """
        return self.store.get_app(domain) is not None

    def require_app(self, domain: str) -> App:
        """
        Args:
            domain: An application's domain.

        Returns:
            The application.

        Raises:
            ResourceNotFoundError: When there is none or it is not visible.
        """
        app = self.get_app(domain)
        if app is None:
            raise ResourceNotFoundError(f"Application not found: {domain}")
        return app

    def app_exists(self, domain: str) -> bool:
        """
        Args:
            domain: An application's domain.

        Returns:
            Whether a visible application has it.
        """
        return self.get_app(domain) is not None

    def list_apps(
        self,
        status: str | None = None,
        app_type: str | None = None,
        *,
        project: str | None = None,
        environment: str | None = None,
        team: str | None = None,
    ) -> list[App]:
        """
        List the visible applications, newest first.

        Args:
            status: Only this status.
            app_type: Only this type.
            project: Only the projects with this slug.
            environment: Only the environments with this slug.
            team: Only this team's.

        Returns:
            The applications.
        """
        from noust.core.store import App

        owner, owner_params = self._owner_filters(project, environment, team)
        where = f"({owner})"
        params: list[Any] = [*owner_params]
        if status:
            where += " AND status = ?"
            params.append(status)
        if app_type:
            where += " AND app_type = ?"
            params.append(app_type)
        return self._select("apps", App.from_row, where, params, order="created_at DESC")

    def list_domains(self, app_domain: str) -> list[DomainRecord]:
        """
        Args:
            app_domain: An application's domain.

        Returns:
            The names the application answers on.

        Raises:
            ResourceNotFoundError: When the application is not visible.
        """
        self.require_app(app_domain)
        return self.store.list_domains(app_domain)

    # -------------------------------------------------------------------- sites

    def get_site(self, domain: str) -> Site | None:
        """
        Args:
            domain: A site's domain.

        Returns:
            The site, or None when there is none or it is not visible.
        """
        from noust.core.store import Site

        rows = self._select("sites", Site.from_row, "domain = ?", (domain,))
        return rows[0] if rows else None

    def get_site_by_app_id(self, app_id: int) -> Site | None:
        """
        Args:
            app_id: An application's id.

        Returns:
            Its site, or None when there is none or it is not visible.
        """
        from noust.core.store import Site

        rows = self._select("sites", Site.from_row, "app_id = ?", (app_id,))
        return rows[0] if rows else None

    def list_sites(
        self,
        webserver: str | None = None,
        enabled: bool | None = None,
        *,
        project: str | None = None,
        environment: str | None = None,
        team: str | None = None,
    ) -> list[Site]:
        """
        List the visible sites, newest first.

        Args:
            webserver: Only this web server's.
            enabled: Only enabled or only disabled ones.
            project: Only the projects with this slug.
            environment: Only the environments with this slug.
            team: Only this team's.

        Returns:
            The sites.
        """
        from noust.core.store import Site

        owner, owner_params = self._owner_filters(project, environment, team)
        where = f"({owner})"
        params: list[Any] = [*owner_params]
        if webserver:
            where += " AND webserver = ?"
            params.append(webserver)
        if enabled is not None:
            where += " AND enabled = ?"
            params.append(1 if enabled else 0)
        return self._select("sites", Site.from_row, where, params, order="created_at DESC")

    # ---------------------------------------------------------------- databases

    def get_database(self, name: str, engine: str) -> Database | None:
        """
        Args:
            name: The database's name.
            engine: Its engine.

        Returns:
            The database, or None when there is none or it is not visible.
        """
        from noust.core.store import Database

        rows = self._select(
            "databases", Database.from_row, "name = ? AND engine = ?", (name, engine)
        )
        return rows[0] if rows else None

    def list_databases(
        self,
        engine: str | None = None,
        app_id: int | None = None,
        *,
        project: str | None = None,
        environment: str | None = None,
        team: str | None = None,
    ) -> list[Database]:
        """
        List the visible databases the store records, newest first.

        Args:
            engine: Only this engine's.
            app_id: Only those linked to this application.
            project: Only the projects with this slug.
            environment: Only the environments with this slug.
            team: Only this team's.

        Returns:
            The databases.
        """
        from noust.core.store import Database

        owner, owner_params = self._owner_filters(project, environment, team)
        where = f"({owner})"
        params: list[Any] = [*owner_params]
        if engine:
            where += " AND engine = ?"
            params.append(engine)
        if app_id is not None:
            where += " AND app_id = ?"
            params.append(app_id)
        return self._select("databases", Database.from_row, where, params, order="created_at DESC")

    def visible_database_keys(self) -> set[tuple[str, str]] | None:
        """
        Returns:
            The ``(name, engine)`` of every visible database the store
            records, or None when nothing is filtered out. A database an
            engine holds that the store never recorded has no owner, and is
            visible only to a principal that sees everything.
        """
        if self.scope.sees_all:
            return None
        return {(record.name, record.engine) for record in self.list_databases()}

    def sees_database(self, name: str, engine: str) -> bool:
        """
        Args:
            name: The database's name.
            engine: Its engine.

        Returns:
            Whether the principal may see it.
        """
        return self.scope.sees_all or self.get_database(name, engine) is not None

    def require_database(self, name: str, engine: str) -> Database | None:
        """
        Refuse a database the principal does not see.

        Args:
            name: The database's name.
            engine: Its engine.

        Returns:
            The recorded database, or None for one the store never recorded
            when the principal sees everything.

        Raises:
            ResourceNotFoundError: When it is not visible.
        """
        if self.scope.sees_all:
            return self.store.get_database(name, engine)
        record = self.get_database(name, engine)
        if record is None:
            raise ResourceNotFoundError(f"Database not found: {name}")
        return record

    # ---------------------------------------------------------- backup schedules

    def get_backup_schedule(self, app_domain: str) -> BackupScheduleRecord | None:
        """
        Args:
            app_domain: The application's domain.

        Returns:
            Its schedule, or None when there is none or it is not visible.
        """
        from noust.core.store import BackupScheduleRecord

        rows = self._select(
            "backup_schedules", BackupScheduleRecord.from_row, "app_domain = ?", (app_domain,)
        )
        return rows[0] if rows else None

    def list_backup_schedules(
        self,
        *,
        project: str | None = None,
        environment: str | None = None,
        team: str | None = None,
    ) -> list[BackupScheduleRecord]:
        """
        List the visible backup schedules by domain.

        Args:
            project: Only the projects with this slug.
            environment: Only the environments with this slug.
            team: Only this team's.

        Returns:
            The schedules.
        """
        from noust.core.store import BackupScheduleRecord

        owner, owner_params = self._owner_filters(project, environment, team)
        return self._select(
            "backup_schedules",
            BackupScheduleRecord.from_row,
            owner,
            owner_params,
            order="app_domain",
        )

    # ------------------------------------------------------- history of an app

    def _domains_clause(self, column: str = "domain") -> tuple[str, list[Any]]:
        """
        Args:
            column: The column that holds a domain.

        Returns:
            A condition over it that keeps the domains of the visible
            applications, their aliases and their sites.
        """
        clause, params = self._clause()
        # The column and the clauses are code; nothing typed is spliced in.
        sql = (
            f"{column} IN (SELECT domain FROM apps WHERE {clause} "  # noqa: S608
            f"UNION SELECT domain FROM domains WHERE app_id IN "
            f"(SELECT id FROM apps WHERE {clause}) "
            f"UNION SELECT domain FROM sites WHERE {clause})"
        )
        return sql, [*params, *params, *params]

    def list_deployments(
        self, domain: str | None = None, limit: int = 50
    ) -> list[DeploymentRecord]:
        """
        List the history of the visible applications, newest first.

        Args:
            domain: Only this domain's history.
            limit: At most this many rows.

        Returns:
            The records. A deleted application's history outlives it and
            belongs to no project, so only a principal that sees everything
            reads it.
        """
        from noust.core.store import DeploymentRecord

        if self.scope.sees_all:
            return self.store.list_deployments(domain=domain, limit=limit)
        clause, params = self._domains_clause()
        where = clause
        if domain:
            where += " AND domain = ?"
            params = [*params, domain]
        sql = f"SELECT * FROM deployments WHERE {where} ORDER BY started_at DESC, id DESC LIMIT ?"  # noqa: S608 - fixed text
        with self.store._transaction() as cursor:
            cursor.execute(sql, [*params, limit])
            return [DeploymentRecord.from_row(row) for row in cursor.fetchall()]

    def list_jobs(
        self, limit: int = 50, status: str | None = None, domain: str | None = None
    ) -> list[JobRecord]:
        """
        List the jobs of the visible applications, newest first.

        Args:
            limit: At most this many rows.
            status: Only jobs in this status.
            domain: Only jobs acting on this domain.

        Returns:
            The records. A job that acts on no domain (an update of the
            server itself) belongs to nobody, and only a principal that sees
            everything reads it.
        """
        from noust.core.store import JobRecord

        if self.scope.sees_all:
            return self.store.list_jobs(limit=limit, status=status, domain=domain)
        where, params = self._domains_clause()
        if status:
            where += " AND status = ?"
            params = [*params, status]
        if domain:
            where += " AND domain = ?"
            params = [*params, domain]
        sql = f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ?"  # noqa: S608 - fixed text
        with self.store._transaction() as cursor:
            cursor.execute(sql, [*params, limit])
            return [JobRecord.from_row(row) for row in cursor.fetchall()]

    def get_job(self, job_id: str) -> JobRecord | None:
        """
        Args:
            job_id: A job's id.

        Returns:
            The record, or None when there is none or the job acts on nothing
            the principal sees.
        """
        record = self.store.get_job(job_id)
        if record is None:
            return None
        return record if self.scope.sees_all or self.sees_domain(record.domain) else None

    def get_deployment(self, deployment_id: int) -> DeploymentRecord | None:
        """
        Args:
            deployment_id: A deployment's id.

        Returns:
            The record, or None when there is none or its application is not
            visible.
        """
        record = self.store.get_deployment(deployment_id)
        if record is None:
            return None
        return record if self.sees_domain(record.domain) else None

    def get_latest_deployments(self, domains: Iterable[str]) -> dict[str, DeploymentRecord]:
        """
        Args:
            domains: Domains to look up.

        Returns:
            The newest deployment of each visible one.
        """
        return self.store.get_latest_deployments([d for d in domains if self.sees_domain(d)])

    # --------------------------------------------------------- whose it is

    def team_of_project(self, project_id: int | None) -> int | None:
        """
        Args:
            project_id: A project's id, or None.

        Returns:
            The team the project belongs to, or None for none.
        """
        if project_id is None:
            return None
        with self.store._transaction() as cursor:
            cursor.execute("SELECT team_id FROM projects WHERE id = ?", (project_id,))
            row = cursor.fetchone()
        return int(row[0]) if row is not None else None

    def placement_of_domain(self, domain: str | None) -> Target | None:
        """
        Where the visible resource a domain names lives.

        Args:
            domain: An application's domain, one of its aliases, or a site's.

        Returns:
            The team, project and environment of the visible application, alias
            or site that has it, or None when there is none or the principal
            does not see it. An environment is 0 for a row that has none yet.
        """
        if not domain:
            return None
        apps_clause, params = self._clause("a.project_id")
        sites_clause, _ = self._clause("s.project_id")
        sql = (
            "SELECT p.team_id, a.project_id, COALESCE(a.environment_id, 0) FROM apps a "  # noqa: S608 - fixed text
            f"JOIN projects p ON p.id = a.project_id WHERE a.domain = ? AND {apps_clause} "
            "UNION SELECT p.team_id, a.project_id, COALESCE(a.environment_id, 0) FROM domains d "
            "JOIN apps a ON a.id = d.app_id "
            f"JOIN projects p ON p.id = a.project_id WHERE d.domain = ? AND {apps_clause} "
            "UNION SELECT p.team_id, s.project_id, COALESCE(s.environment_id, 0) FROM sites s "
            f"JOIN projects p ON p.id = s.project_id WHERE s.domain = ? AND {sites_clause}"
        )
        values = [domain, *params, domain, *params, domain, *params]
        with self.store._transaction() as cursor:
            cursor.execute(sql, values)
            row = cursor.fetchone()
        return Target(int(row[0]), int(row[1]), int(row[2])) if row is not None else None

    def team_of_domain(self, domain: str | None) -> int | None:
        """
        The team of the visible resource a domain names.

        Args:
            domain: An application's domain, one of its aliases, or a site's.

        Returns:
            The team of the visible application, alias or site that has it,
            or None when there is none or the principal does not see it.
        """
        placement = self.placement_of_domain(domain)
        return placement.team_id if placement is not None else None

    def placement_of_project(
        self, project_id: int | None, environment_id: int | None = None
    ) -> Target | None:
        """
        Args:
            project_id: A project's id, or None.
            environment_id: The environment the resource is in, when it has one.

        Returns:
            The resource's team, project and environment, or None when it has
            no project.
        """
        team_id = self.team_of_project(project_id)
        if project_id is None or team_id is None:
            return None
        return Target(team_id, project_id, environment_id or 0)

    # -------------------------------------------------- lists from somewhere else

    def sees_domain(self, domain: str | None) -> bool:
        """
        Whether a domain belongs to something the principal sees.

        A domain is an application's primary, one of its aliases, or a site's.
        Jobs, certificates, events and the timeline name resources by domain,
        and this is how they are held to the scope.

        Args:
            domain: A domain, or None for something that names none.

        Returns:
            True when nothing is filtered out; otherwise whether a visible
            application, one of its aliases or a visible site has the domain.
        """
        if self.scope.sees_all:
            return True
        if not domain:
            return False
        clause, params = self._clause()
        sql = (
            f"SELECT 1 FROM apps WHERE domain = ? AND {clause} "  # noqa: S608 - fixed text
            f"UNION SELECT 1 FROM domains WHERE domain = ? AND app_id IN "
            f"(SELECT id FROM apps WHERE {clause}) "
            f"UNION SELECT 1 FROM sites WHERE domain = ? AND {clause}"
        )
        values = [domain, *params, domain, *params, domain, *params]
        with self.store._transaction() as cursor:
            cursor.execute(sql, values)
            return cursor.fetchone() is not None

    def visible_domains(self) -> set[str] | None:
        """
        Returns:
            Every domain of the visible applications, their aliases and their
            sites, or None when nothing is filtered out.
        """
        if self.scope.sees_all:
            return None
        clause, params = self._clause()
        sql = (
            f"SELECT domain FROM apps WHERE {clause} "  # noqa: S608 - fixed text
            f"UNION SELECT domain FROM domains WHERE app_id IN "
            f"(SELECT id FROM apps WHERE {clause}) "
            f"UNION SELECT domain FROM sites WHERE {clause}"
        )
        with self.store._transaction() as cursor:
            cursor.execute(sql, [*params, *params, *params])
            return {str(row[0]) for row in cursor.fetchall()}

    def filter_domains(
        self, items: Iterable[_T], domain_of: Callable[[_T], str | None]
    ) -> list[_T]:
        """
        Keep the items that name a visible domain.

        Args:
            items: Anything that names a domain: a job, a certificate, an event.
            domain_of: Reads the domain out of an item.

        Returns:
            The items, in their order, without those of other teams. An item
            that names no domain (a job that updates the server) is kept only
            for a principal that sees everything.
        """
        if self.scope.sees_all:
            return list(items)
        visible = self.visible_domains() or set()
        return [item for item in items if (domain_of(item) or "") in visible]


def scoped_store() -> ScopedStore:
    """
    Returns:
        A scoped store for the request being served, or for the instance
        outside one.
    """
    return ScopedStore()
