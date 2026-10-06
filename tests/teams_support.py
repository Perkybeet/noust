# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server with two teams in it, for the tests of teams and of the scope filter.

``acme`` and ``globex`` each own an application with a site, a database and a
backup schedule, a deployment and a job, and each has people in it. Everything
is in the store the console serves, so what a test asks over HTTP is what a
real principal would be answered.

The people are accounts with API tokens (``Authorization: Bearer``): a token is
the credential a script holds, carries its owner's teams, and needs no second
factor to be created, which keeps the tests about scope and not about sign-in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from noust.core.accounts import AuthPolicy, passwords
from noust.core.store import (
    App,
    BackupScheduleRecord,
    Database,
    JobRecord,
    NoustStore,
    Site,
)
from noust.core.teams import Scope, Target, TeamStore
from noust.web.auth import SecurityConfig
from noust.web.server import create_app, get_token_manager

PASSWORD = "correct horse battery staple"


@dataclass
class Person:
    """
    One account and the credential it holds.

    Attributes:
        username: The account.
        token: Its API token, in clear.
        headers: What a request carries to act as it.
    """

    username: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        """The Authorization header."""
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class World:
    """
    Everything a two-team test needs.

    Attributes:
        client: The console, as it is served.
        store: Its store.
        teams: The team store over it.
        people: Accounts by name, with their tokens.
        targets: Where each team's default project is, by team slug.
        master: The master token's header.
    """

    client: TestClient
    store: NoustStore
    teams: TeamStore
    people: dict[str, Person] = field(default_factory=dict)
    targets: dict[str, Target] = field(default_factory=dict)
    master: dict[str, str] = field(default_factory=dict)
    deployments: dict[str, int] = field(default_factory=dict)

    def as_(self, name: str) -> dict[str, str]:
        """
        Args:
            name: One of the people.

        Returns:
            The headers that act as it.
        """
        return self.people[name].headers


def make_person(
    world_teams: TeamStore,
    username: str,
    role: str,
    *,
    team: str | None = None,
    team_role: str | None = None,
    permissions: list[str] | None = None,
) -> Person:
    """
    Create an account, put it in one team, and give it a token.

    Args:
        world_teams: The team store.
        username: The account's name.
        role: Its instance role.
        team: The team it belongs to, instead of the default one.
        team_role: Its role there.
        permissions: What its token holds; everything its owner holds when omitted.

    Returns:
        The person.
    """
    manager = get_token_manager().accounts
    account = manager.create(username, role, password=PASSWORD, join_default_team=team is None)
    if team is not None:
        world_teams.add_member(team, username, team_role or "viewer")
    issued = get_token_manager().create_api_token(
        f"{username}-token",
        "admin",
        None,
        owner=account,
        allow_elevated=True,
        permissions=permissions,
    )
    return Person(username, issued["token"])


def make_app(
    store: NoustStore,
    domain: str,
    target: Target,
    tmp_path: Path,
    *,
    with_schedule: bool = True,
    deployments: dict[str, int] | None = None,
) -> App:
    """
    Record an application and what belongs to it, in a project.

    Args:
        store: The store.
        domain: The application's domain.
        target: The project and environment it lives in.
        tmp_path: A directory to name as its home.
        with_schedule: Also record a backup schedule for it.

    Returns:
        The application.
    """
    app = store.create_app(
        App(
            domain=domain,
            app_type="nodejs",
            app_path=str(tmp_path / domain),
            port=3000,
            status="running",
            project_id=target.project_id,
            environment_id=target.environment_id,
        )
    )
    store.create_site(
        Site(app_id=app.id, domain=domain, config_path=f"/etc/nginx/sites-available/{domain}")
    )
    store.create_database(
        Database(app_id=app.id, name=domain.split(".")[0] + "_db", engine="postgresql")
    )
    if with_schedule:
        store.save_backup_schedule(BackupScheduleRecord(app_domain=domain, schedule="daily"))
    deployment = store.record_deployment_start(domain, "panel", git_commit="abc1234")
    if deployments is not None:
        deployments[domain] = deployment
    store.create_job(
        JobRecord(
            id=f"job-{domain.split('.')[0]}",
            type="deploy",
            name=f"Deploy {domain}",
            status="completed",
            domain=domain,
        )
    )
    return app


def build_world(tmp_path: Path, monkeypatch: Any) -> World:
    """
    Build the console with two teams, their resources and their people.

    Args:
        tmp_path: Per-test directory.
        monkeypatch: Patching helper, to keep scrypt cheap.

    Returns:
        The world.
    """
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    teams = TeamStore(store)
    config = SecurityConfig(
        state_dir=tmp_path / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
    )
    client = TestClient(create_app(config), client=("testclient", 50000))
    world = World(client=client, store=store, teams=teams)

    for slug in ("acme", "globex"):
        teams.create_team(slug.title(), slug=slug)
        world.targets[slug] = teams.resolve_target(Scope.everything(), team=slug)

    make_app(
        store, "shop.acme.example", world.targets["acme"], tmp_path, deployments=world.deployments
    )
    make_app(
        store,
        "blog.globex.example",
        world.targets["globex"],
        tmp_path,
        deployments=world.deployments,
    )

    world.people["admin"] = make_person(teams, "root-admin", "admin")
    world.people["alice"] = make_person(teams, "alice", "viewer", team="acme", team_role="admin")
    world.people["bob"] = make_person(teams, "bob", "viewer", team="globex", team_role="admin")
    world.people["dana"] = make_person(teams, "dana", "viewer", team="acme", team_role="viewer")
    world.people["omar"] = make_person(teams, "omar", "operator", team="acme", team_role="operator")
    world.people["sec"] = make_person(
        teams, "sofia", "security", permissions=["accounts.manage", "accounts.read", "apps.read"]
    )
    world.people["aud"] = make_person(teams, "aurora", "auditor")
    world.master = {"Authorization": f"Bearer {get_token_manager().generate_master_token()}"}
    return world
