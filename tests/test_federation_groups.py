# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Groups of an identity source give roles at a project or an environment (checklist I-19).

A group already gave an instance role and a team membership. It now also gives a role
held at a scope: a built-in role on a project, a custom role anywhere below the server.
The sign-in recomputes it, takes back what the source gave and nothing else, and a role
a claim may not give (``security``, ``auditor``, ``owner``) is refused when the mapping
is written, not when somebody signs in.
"""

# ruff: noqa: F811 - the fixtures imported from the manager tests are used by name

from __future__ import annotations

from typing import Any

import pytest

from noust.core.accounts import AccountManager
from noust.core.accounts.oidc import OidcManager
from noust.core.accounts.oidc_model import GroupMapping, parse_mappings
from noust.core.audit import get_log
from noust.core.exceptions import ValidationError
from noust.core.teams import TeamStore
from noust.web.permissions.access import AccessManager
from tests.oidc_support import FakeIdP
from tests.test_oidc_manager import (  # noqa: F401 - the fixtures of the manager tests
    IP,
    PASSWORD,
    REDIRECT,
    accounts,
    admin,
    cheap_hashes,
    clock,
    configure,
    idp,
    manager,
    run,
    secure,
    store,
)

RELEASE = {"group": "acme-release", "bind": "release-manager", "team": "acme", "project": "web"}


class TestTheMappingIsChecked:
    def test_a_role_at_a_project_is_a_mapping_of_its_own(self) -> None:
        [mapping] = parse_mappings([RELEASE])

        assert mapping == GroupMapping(
            "acme-release", team="acme", bind="release-manager", project="web"
        )
        assert mapping.to_dict() == {
            "group": "acme-release",
            "bind": "release-manager",
            "team": "acme",
            "project": "web",
        }

    def test_the_three_shapes_live_in_one_list(self) -> None:
        parsed = parse_mappings(
            [
                {"group": "a", "role": "operator"},
                {"group": "b", "team": "acme", "team_role": "viewer"},
                {"group": "c", "bind": "viewer", "team": "acme", "project": "web"},
                {
                    "group": "d",
                    "bind": "release-manager",
                    "team": "acme",
                    "project": "web",
                    "environment": "staging",
                },
            ]
        )

        assert [m.role for m in parsed] == ["operator", None, None, None]
        assert [m.bind for m in parsed] == [None, None, "viewer", "release-manager"]
        assert parsed[3].environment == "staging"

    @pytest.mark.parametrize(
        "entry",
        [
            {"group": "g", "bind": "release-manager"},
            {"group": "g", "bind": "release-manager", "team": "acme", "environment": "staging"},
            {"group": "g", "bind": "viewer", "team": "acme"},
            {"group": "g", "bind": "release-manager", "team": "acme", "team_role": "viewer"},
            {"group": "g", "bind": "release-manager", "role": "admin", "team": "acme"},
            {"group": "g", "bind": "Not A Slug", "team": "acme", "project": "web"},
            {"group": "g", "bind": "release-manager", "team": "acme", "project": "../web"},
            {"group": "g", "bind": "release-manager", "team": "acme", "scope": "instance"},
        ],
    )
    def test_a_mapping_that_does_not_say_where_or_what_is_refused(
        self, entry: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError):
            parse_mappings([entry])

    @pytest.mark.parametrize("role", ["security", "auditor", "owner"])
    def test_a_claim_never_gives_a_role_a_person_gives(self, role: str) -> None:
        with pytest.raises(ValidationError, match="cannot give"):
            parse_mappings([{"group": "g", "bind": role, "team": "acme", "project": "web"}])


class TestTheSignInRecomputesThem:
    @pytest.fixture(autouse=True)
    def ready(
        self, manager: OidcManager, idp: FakeIdP, admin: Any, accounts: AccountManager
    ) -> None:
        teams = TeamStore(accounts.store)
        teams.create_team("Acme", slug="acme")
        teams.create_project("acme", "Web", slug="web")
        teams.create_environment("acme", "web", "Staging", slug="staging")
        access = AccessManager(accounts.store)
        access.create_role("release-manager", ["apps.read", "apps.deploy"])
        self.access = access
        configure(
            manager,
            idp,
            provisioning="auto",
            default_role="viewer",
            group_sync=True,
            group_mappings=[
                RELEASE,
                {"group": "acme-viewers", "bind": "viewer", "team": "acme", "project": "web"},
                {
                    "group": "acme-staging",
                    "bind": "release-manager",
                    "team": "acme",
                    "project": "web",
                    "environment": "staging",
                },
            ],
        )

    def held(self, username: str = "maria") -> dict[str, tuple[str, str | None]]:
        return {b.scope: (b.role, b.created_by) for b in self.access.list_bindings(username)}

    def test_a_group_gives_a_custom_role_at_a_project(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        answer = run(manager, idp, groups=["acme-release"])

        assert self.held() == {"acme/web": ("release-manager", "oidc:corp")}
        assert any("release-manager on acme/web" in note for note in answer.notes)

    def test_a_group_gives_a_role_at_an_environment(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        run(manager, idp, groups=["acme-staging"])

        assert self.held() == {"acme/web/staging": ("release-manager", "oidc:corp")}

    def test_leaving_the_group_takes_back_what_the_group_gave(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        run(manager, idp, groups=["acme-release", "acme-staging"])
        assert set(self.held()) == {"acme/web", "acme/web/staging"}

        again = run(manager, idp, groups=["acme-staging"])

        assert set(self.held()) == {"acme/web/staging"}
        assert any("lost release-manager on acme/web" in note for note in again.notes)

    def test_a_role_given_by_hand_at_the_same_scope_is_never_replaced_or_taken_back(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        run(manager, idp, groups=[])
        self.access.bind("maria", "viewer", team="acme", project="web", actor="cli:root")

        run(manager, idp, groups=["acme-release"])
        assert self.held()["acme/web"] == ("viewer", "cli:root")

        run(manager, idp, groups=[])
        assert self.held()["acme/web"] == ("viewer", "cli:root")

    def test_what_another_scope_holds_is_left_alone(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        run(manager, idp, groups=[])
        self.access.bind("maria", "release-manager", team="acme", actor="cli:root")

        run(manager, idp, groups=["acme-viewers"])
        run(manager, idp, groups=[])

        assert self.held() == {"acme": ("release-manager", "cli:root")}

    def test_a_group_wins_by_the_first_mapping_at_a_scope(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        run(manager, idp, groups=["acme-viewers", "acme-release"])

        assert self.held()["acme/web"][0] == "release-manager"

    def test_the_sync_is_on_the_record_with_the_source_as_the_actor(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        run(manager, idp, groups=["acme-release"])
        run(manager, idp, groups=[])

        events = {
            e["action"]: e for e in get_log().read(limit=500) if e["action"].startswith("access.")
        }
        assert set(events) == {"access.bind", "access.unbind"}
        assert events["access.bind"]["who"]["name"] == "oidc:corp"

    def test_nothing_changes_while_the_sync_is_off(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        manager.configure("corp", {"group_sync": False})

        run(manager, idp, groups=["acme-release"])

        assert self.held() == {}

    def test_a_role_deleted_since_is_a_note_not_a_failed_sign_in(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        self.access.delete_role("release-manager")

        answer = run(manager, idp, groups=["acme-release"])

        assert self.held() == {}
        assert any("release-manager on acme/web not given" in note for note in answer.notes)

    def test_a_security_officer_is_never_given_a_scoped_role_by_a_group(
        self, manager: OidcManager, idp: FakeIdP, accounts: AccountManager
    ) -> None:
        secure(accounts, "maria", "security", person_ref="maria@acme.test")
        manager.link("corp", "maria", "sub-maria")

        run(manager, idp, groups=["acme-release"])

        assert self.held() == {}


class TestTheMappingIsCheckedWhereItIsWritten:
    @pytest.fixture(autouse=True)
    def ready(self, admin: Any, accounts: AccountManager) -> None:
        teams = TeamStore(accounts.store)
        teams.create_team("Acme", slug="acme")
        teams.create_project("acme", "Web", slug="web")
        AccessManager(accounts.store).create_role("release-manager", ["apps.read", "apps.deploy"])

    def test_a_role_that_does_not_exist_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        with pytest.raises(ValidationError, match="does not exist"):
            configure(
                manager,
                idp,
                group_mappings=[{**RELEASE, "bind": "release-manger"}],
            )

    def test_a_project_that_does_not_exist_is_refused(
        self, manager: OidcManager, idp: FakeIdP
    ) -> None:
        with pytest.raises(ValidationError):
            configure(manager, idp, group_mappings=[{**RELEASE, "project": "ghost"}])

    def test_a_good_mapping_is_saved_and_shown(self, manager: OidcManager, idp: FakeIdP) -> None:
        provider = configure(manager, idp, group_mappings=[RELEASE])

        assert provider.to_dict()["group_mappings"] == [RELEASE]
