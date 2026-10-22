# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server of the fleet assigned to a team: the data layer (checklist F, I-05).

A node belongs to one team or to nobody. The scoped store shows a principal the
nodes of the teams it belongs to, and reads every other one as absent: the same
answer as for a node that is not registered (rule 4 of the teams).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.store import NodeRecord, NoustStore
from noust.core.teams import (
    ResourceNotFoundError,
    Scope,
    ScopedStore,
    TeamConflictError,
    TeamNotFoundError,
    TeamStore,
)
from noust.core.teams.nodes import NodeAssignments


def _node(name: str) -> NodeRecord:
    return NodeRecord(
        name=name,
        ssh_host=f"{name}.example.com",
        ssh_port=22,
        ssh_user="noust-tunnel",
        host_key=f"{name}.example.com ssh-ed25519 AAAA",
        console_port=8080,
    )


@pytest.fixture
def store(tmp_path: Path) -> NoustStore:
    NoustStore.reset_instance()
    found = NoustStore(tmp_path / "noust.db")
    teams = TeamStore(found)
    teams.create_team("Acme", slug="acme")
    teams.create_team("Globex", slug="globex")
    for name in ("web-1", "web-2", "db-1"):
        found.save_node(_node(name))
    return found


@pytest.fixture
def assignments(store: NoustStore) -> NodeAssignments:
    return NodeAssignments(store)


def _scope_of(store: NoustStore, *slugs: str) -> Scope:
    teams = TeamStore(store)
    return Scope(team_roles={teams.require_team(slug).id: "viewer" for slug in slugs})


class TestAssigning:
    def test_a_node_is_assigned_to_a_team(self, assignments: NodeAssignments) -> None:
        record = assignments.assign("web-1", "acme", actor="root")

        assert (record.node, record.team) == ("web-1", "acme")
        assert record.assigned_by == "root"
        assert assignments.team_of("web-1") == "acme"

    def test_assigning_again_moves_it_to_the_other_team(self, assignments: NodeAssignments) -> None:
        assignments.assign("web-1", "acme")
        assignments.assign("web-1", "globex")

        assert assignments.team_of("web-1") == "globex"
        assert [row.node for row in assignments.list()] == ["web-1"]

    def test_a_node_nobody_took_has_no_team(self, assignments: NodeAssignments) -> None:
        assert assignments.team_of("web-2") is None

    def test_a_node_that_is_not_registered_cannot_be_assigned(
        self, assignments: NodeAssignments
    ) -> None:
        with pytest.raises(TeamNotFoundError, match="ghost"):
            assignments.assign("ghost", "acme")

    def test_a_team_that_does_not_exist_cannot_take_a_node(
        self, assignments: NodeAssignments
    ) -> None:
        with pytest.raises(TeamNotFoundError):
            assignments.assign("web-1", "nope")

        assert assignments.team_of("web-1") is None

    def test_unassigning_gives_it_back_to_the_instance(self, assignments: NodeAssignments) -> None:
        assignments.assign("web-1", "acme")

        assert assignments.unassign("web-1") is True
        assert assignments.team_of("web-1") is None
        assert assignments.unassign("web-1") is False

    def test_the_list_can_be_narrowed_to_a_team(self, assignments: NodeAssignments) -> None:
        assignments.assign("web-1", "acme")
        assignments.assign("web-2", "acme")
        assignments.assign("db-1", "globex")

        assert [row.node for row in assignments.list(team="acme")] == ["web-1", "web-2"]
        assert [row.node for row in assignments.list(team="globex")] == ["db-1"]
        assert len(assignments.list()) == 3

    def test_a_row_goes_with_its_node(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        assignments.assign("web-1", "acme")

        store.delete_node("web-1")

        assert assignments.list() == []

    def test_a_registry_write_never_takes_the_team_back(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        # save_node runs every time a node answers: it must leave the owner alone.
        assignments.assign("web-1", "acme")

        store.save_node(_node("web-1"))
        store.set_node_status("web-1", "reachable", version="3.4.0")

        assert assignments.team_of("web-1") == "acme"

    def test_a_team_that_still_has_a_server_cannot_be_deleted(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        assignments.assign("web-1", "acme")

        with pytest.raises(TeamConflictError, match="server"):
            TeamStore(store).delete_team("acme")

        assignments.unassign("web-1")
        assert TeamStore(store).delete_team("acme").slug == "acme"


class TestWhatAPrincipalSees:
    def test_the_instance_sees_every_node_assigned_or_not(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        assignments.assign("web-1", "acme")
        scoped = ScopedStore(Scope.everything(), store)

        assert [node.name for node in scoped.list_nodes()] == ["db-1", "web-1", "web-2"]

    def test_a_member_sees_the_nodes_of_its_team_only(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        assignments.assign("web-1", "acme")
        assignments.assign("web-2", "globex")
        scoped = ScopedStore(_scope_of(store, "acme"), store)

        assert [node.name for node in scoped.list_nodes()] == ["web-1"]
        assert scoped.get_node("web-1") is not None
        assert scoped.get_node("web-2") is None

    def test_a_node_nobody_took_is_the_instances_alone(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        assignments.assign("web-1", "acme")
        scoped = ScopedStore(_scope_of(store, "acme"), store)

        assert scoped.get_node("db-1") is None
        assert scoped.sees_node("db-1") is False

    def test_another_teams_node_reads_as_one_that_does_not_exist(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        assignments.assign("web-2", "globex")
        scoped = ScopedStore(_scope_of(store, "acme"), store)

        with pytest.raises(ResourceNotFoundError) as other:
            scoped.require_node("web-2")
        with pytest.raises(ResourceNotFoundError) as missing:
            scoped.require_node("ghost")

        assert str(other.value).replace("web-2", "X") == str(missing.value).replace("ghost", "X")

    def test_a_member_of_every_team_sees_every_node(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        # Such a principal is built with sees_all: nothing is filtered out for it.
        assignments.assign("web-1", "acme")
        scoped = ScopedStore(Scope(sees_all=True), store)

        assert {node.name for node in scoped.list_nodes()} == {"web-1", "web-2", "db-1"}

    def test_a_principal_with_no_team_sees_no_node(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        assignments.assign("web-1", "acme")

        assert ScopedStore(Scope.nothing(), store).list_nodes() == []

    def test_a_project_given_alone_does_not_show_the_teams_servers(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        # A role on one project is not a role on the team: the machines are the team's.
        assignments.assign("web-1", "acme")
        teams = TeamStore(store)
        project = teams.list_projects(team="acme")[0]
        scope = Scope(project_roles={project.id: "admin"})

        assert ScopedStore(scope, store).list_nodes() == []

    def test_the_list_can_ask_for_one_team(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        assignments.assign("web-1", "acme")
        assignments.assign("web-2", "globex")
        scoped = ScopedStore(Scope.everything(), store)

        assert [node.name for node in scoped.list_nodes(team="globex")] == ["web-2"]

    def test_the_placement_of_a_node_is_its_team(
        self, store: NoustStore, assignments: NodeAssignments
    ) -> None:
        assignments.assign("web-1", "acme")
        acme = TeamStore(store).require_team("acme")
        scoped = ScopedStore(_scope_of(store, "acme"), store)

        placed = scoped.placement_of_node("web-1")

        assert placed is not None
        assert (placed.team_id, placed.project_id, placed.environment_id) == (acme.id, 0, 0)
        assert scoped.placement_of_node("db-1") is None
