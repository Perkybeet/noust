# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SCIM provisioning: what an identity provider's calls do to accounts, groups and access (I-21).

The service is tested without HTTP, with the payloads Microsoft Entra and Okta send, which
differ in the ways that matter (a flag as the text "False", a PATCH without a path).
"""

# ruff: noqa: F811 - the fixtures imported from the manager tests are used by name

from __future__ import annotations

from typing import Any

import pytest

from noust.core.accounts import AccountManager
from noust.core.accounts.model import STATUS_ACTIVE, STATUS_DISABLED, STATUS_INVITED
from noust.core.accounts.oidc import OidcManager
from noust.core.accounts.scim import (
    SCHEMA_GROUP,
    SCHEMA_PATCH,
    SCHEMA_USER,
    TOKEN_PREFIX,
    ScimError,
    ScimManager,
    ScimService,
    hash_token,
    parse_filter,
)
from noust.core.audit import get_log
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.core.teams import TeamStore
from noust.web.permissions.access import AccessManager
from tests.oidc_support import FakeIdP
from tests.test_oidc_manager import (  # noqa: F401 - the fixtures of the manager tests
    accounts,
    admin,
    cheap_hashes,
    clock,
    configure,
    idp,
    manager,
    refused,
    run,
    secure,
    store,
)


@pytest.fixture
def scim(store: NoustStore, accounts: AccountManager, clock: Any) -> ScimManager:
    return ScimManager(store, accounts=accounts, clock=clock)


def user_doc(name: str = "maria@acme.test", **extra: Any) -> dict[str, Any]:
    """A user the way Microsoft Entra describes one."""
    return {
        "schemas": [SCHEMA_USER],
        "userName": name,
        "externalId": f"ext-{name}",
        "displayName": "Maria Lopez",
        "emails": [{"value": name, "primary": True, "type": "work"}],
        "active": True,
        **extra,
    }


def patch_doc(*operations: dict[str, Any]) -> dict[str, Any]:
    return {"schemas": [SCHEMA_PATCH], "Operations": list(operations)}


class Source:
    """A configured source and the service its calls are answered by."""

    def __init__(self, scim: ScimManager, name: str = "entra", **changes: Any) -> None:
        self.manager = scim
        self.source, self.token = scim.configure(name, changes, actor="cli:root")
        self.service = self.again()

    def again(self) -> ScimService:
        return self.manager.service(
            self.manager.require_source(self.source.name),
            base_url="https://noust.test/api/scim/v2",
            client_ip="198.51.100.9",
        )


@pytest.fixture
def entra(scim: ScimManager, admin: Any) -> Source:
    return Source(scim, "entra", label="Entra ID")


def events(action: str) -> list[dict[str, Any]]:
    return [e for e in get_log().read(limit=500) if e["action"] == action]


class TestTheSourceAndItsToken:
    def test_a_new_source_gets_a_token_shown_once_and_kept_as_a_hash(
        self, scim: ScimManager, store: NoustStore
    ) -> None:
        source, token = scim.configure("entra", {"label": "Entra ID"})

        assert token is not None and token.startswith(TOKEN_PREFIX)
        assert source.token_hash == hash_token(token) and token not in str(source.to_dict())
        stored = store._get_connection().execute("SELECT * FROM scim_sources").fetchall()
        assert token not in str(tuple(map(tuple, stored)))
        assert scim.authenticate(token) == source

    def test_changing_a_setting_keeps_the_token_and_rotating_replaces_it(
        self, scim: ScimManager
    ) -> None:
        source, token = scim.configure("entra", {})
        assert token is not None

        again, none = scim.configure("entra", {"label": "Acme"})
        assert none is None and again.token_hash == source.token_hash and scim.authenticate(token)

        rotated, fresh = scim.configure("entra", {}, rotate_token=True)
        assert fresh is not None and fresh != token
        assert scim.authenticate(token) is None and scim.authenticate(fresh) == rotated

    @pytest.mark.parametrize(
        "token",
        [None, "", "noust_tok_whatever", "noust_scim_", "noust_scim_" + "x" * 300, "Bearer"],
    )
    def test_anything_else_authenticates_nobody(self, scim: ScimManager, token: str | None) -> None:
        scim.configure("entra", {})

        assert scim.authenticate(token) is None

    def test_a_source_that_is_switched_off_authenticates_nobody(self, scim: ScimManager) -> None:
        _, token = scim.configure("entra", {})
        scim.configure("entra", {"enabled": False})

        assert scim.authenticate(token) is None

    def test_a_setting_that_does_not_exist_or_a_team_that_does_not_exist_is_refused(
        self, scim: ScimManager
    ) -> None:
        with pytest.raises(ValidationError, match="Unknown setting"):
            scim.configure("entra", {"provisioning": "auto"})
        with pytest.raises(ValidationError):
            scim.configure("entra", {"default_team": "ghost"})
        assert scim.sources() == []

    def test_a_source_cannot_hand_out_security_or_auditor(self, scim: ScimManager) -> None:
        for role in ("security", "auditor", "owner"):
            with pytest.raises(ValidationError):
                scim.configure("entra", {"default_role": role})

    def test_removing_a_source_keeps_the_accounts_it_made(
        self, scim: ScimManager, entra: Source, accounts: AccountManager
    ) -> None:
        entra.service.create_user(user_doc())

        scim.remove("entra")

        assert scim.sources() == [] and accounts.find("maria") is not None


class TestProvisioning:
    def test_a_person_gets_an_invited_account_with_no_credential(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        resource = entra.service.create_user(user_doc())

        account = accounts.find("maria")
        assert account is not None
        assert (account.status, account.role, account.person_ref) == (
            STATUS_INVITED,
            "viewer",
            "maria@acme.test",
        )
        assert not account.has_password and not account.totp_enabled
        assert resource["userName"] == "maria@acme.test" and resource["active"] is True
        assert resource["emails"] == [{"value": "maria@acme.test", "primary": True, "type": "work"}]
        assert resource["meta"]["location"].endswith(f"/Users/{resource['id']}")
        assert [e["resource"] for e in events("auth.scim.provision")] == ["account:maria"]

    def test_it_follows_the_source_default_role_and_team(
        self, scim: ScimManager, admin: Any, accounts: AccountManager
    ) -> None:
        teams = TeamStore(accounts.store)
        teams.create_team("Acme", slug="acme")
        entra = Source(
            scim,
            "entra",
            default_role="operator",
            default_team="acme",
            default_team_role="operator",
        )

        entra.service.create_user(user_doc())

        account = accounts.require("maria")
        assert account.role == "operator"
        assert {t.slug: role for t, role in teams.teams_of(account.id)} == {"acme": "operator"}

    def test_the_actor_in_the_trail_is_the_source_and_the_address_its_own(
        self, entra: Source
    ) -> None:
        entra.service.create_user(user_doc())

        [event] = events("auth.scim.provision")
        assert event["who"]["name"] == "scim:entra" and event["who"]["via"] == "scim"

    def test_a_user_without_a_user_name_is_refused(self, entra: Source) -> None:
        with pytest.raises(ScimError) as no_name:
            entra.service.create_user({"emails": [{"value": "a@acme.test"}]})

        assert no_name.value.status == 400

    def test_a_user_without_an_e_mail_is_made_but_nobody_can_sign_in_as_them(
        self, entra: Source, accounts: AccountManager, idp: FakeIdP, manager: OidcManager
    ) -> None:
        created = entra.service.create_user({"schemas": [SCHEMA_USER], "userName": "abc-123"})

        account = accounts.require("abc-123")
        assert account.status == STATUS_INVITED and account.person_ref is None
        assert "emails" not in created
        # The e-mail is what ties a sign-in to an account: nothing a provider says without one
        # can be the way in.
        configure(manager, idp, provisioning="invite")
        assert refused(manager, idp).reason == "not_invited"

    def test_a_source_limited_to_some_domains_needs_an_e_mail(
        self, scim: ScimManager, admin: Any
    ) -> None:
        source = Source(scim, "entra", allowed_domains=["acme.test"])

        with pytest.raises(ScimError, match="needs an e-mail"):
            source.service.create_user({"userName": "abc-123"})

    def test_the_provider_gives_the_first_e_mail_once_and_not_a_second(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        created = entra.service.create_user({"userName": "abc-123"})

        entra.service.patch_user(
            created["id"],
            patch_doc({"op": "add", "path": "emails", "value": [{"value": "Maria@Acme.test"}]}),
        )
        assert accounts.require("abc-123").person_ref == "maria@acme.test"
        entra.service.patch_user(
            created["id"],
            patch_doc(
                {"op": "replace", "path": 'emails[type eq "work"].value', "value": "x@acme.test"}
            ),
        )

        assert accounts.require("abc-123").person_ref == "maria@acme.test"

    def test_the_first_e_mail_cannot_be_one_another_account_has(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        entra.service.create_user(user_doc())
        created = entra.service.create_user({"userName": "abc-123"})

        with pytest.raises(ScimError) as caught:
            entra.service.patch_user(
                created["id"],
                patch_doc({"op": "add", "path": "emails", "value": [{"value": "maria@acme.test"}]}),
            )

        assert caught.value.status == 409 and caught.value.scim_type == "uniqueness"
        assert accounts.require("abc-123").person_ref is None

    def test_removing_the_name_removes_how_the_person_is_greeted(self, entra: Source) -> None:
        created = entra.service.create_user(user_doc())
        assert created["displayName"] == "Maria Lopez" and "name" in created

        changed = entra.service.patch_user(
            created["id"], patch_doc({"op": "remove", "path": "name"})
        )

        assert "displayName" not in changed and "name" not in changed

    def test_the_user_name_may_be_the_e_mail(self, entra: Source, accounts: AccountManager) -> None:
        entra.service.create_user({"userName": "Maria@Acme.test"})

        assert accounts.require("maria").person_ref == "maria@acme.test"

    def test_a_domain_the_source_is_not_allowed_is_refused(
        self, scim: ScimManager, admin: Any
    ) -> None:
        source = Source(scim, "entra", allowed_domains=["acme.test"])

        with pytest.raises(ScimError, match="domain"):
            source.service.create_user(user_doc("mallory@evil.test"))
        source.service.create_user(user_doc())

    def test_the_same_user_twice_is_a_conflict_the_protocol_names(self, entra: Source) -> None:
        entra.service.create_user(user_doc())

        with pytest.raises(ScimError) as caught:
            entra.service.create_user(user_doc())

        assert (caught.value.status, caught.value.scim_type) == (409, "uniqueness")
        assert caught.value.to_dict()["scimType"] == "uniqueness"

    def test_an_account_that_has_the_e_mail_is_never_taken_over_without_link_by_email(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        secure(accounts, "maria", "admin", person_ref="maria@acme.test")

        with pytest.raises(ScimError) as caught:
            entra.service.create_user(user_doc())

        assert caught.value.status == 409
        assert accounts.require("maria").role == "admin"

    def test_with_link_by_email_the_account_is_attached_and_nothing_else_changes(
        self, scim: ScimManager, admin: Any, accounts: AccountManager
    ) -> None:
        secure(accounts, "maria", "admin", person_ref="maria@acme.test")
        source = Source(scim, "entra", link_by_email=True)

        resource = source.service.create_user(user_doc())

        account = accounts.require("maria")
        assert (account.status, account.role) == (STATUS_ACTIVE, "admin")
        assert resource["active"] is True
        assert [e["resource"] for e in events("auth.scim.link")] == ["account:maria"]

    def test_two_accounts_with_the_e_mail_are_not_guessed_between(
        self, scim: ScimManager, admin: Any, accounts: AccountManager
    ) -> None:
        secure(accounts, "maria", "viewer", person_ref="maria@acme.test")
        secure(accounts, "maria2", "viewer", person_ref="maria@acme.test")
        source = Source(scim, "entra", link_by_email=True)

        with pytest.raises(ScimError):
            source.service.create_user(user_doc())

    def test_an_account_an_administrator_disabled_is_not_switched_on_by_a_provider(
        self, scim: ScimManager, admin: Any, accounts: AccountManager
    ) -> None:
        secure(accounts, "maria", "viewer", person_ref="maria@acme.test")
        accounts.disable("maria", "left the company")
        source = Source(scim, "entra", link_by_email=True)

        with pytest.raises(ScimError, match="disabled by an administrator"):
            source.service.create_user(user_doc())

        assert accounts.require("maria").status == STATUS_DISABLED

    def test_a_user_created_inactive_has_a_disabled_account(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        resource = entra.service.create_user(user_doc(active=False))

        assert resource["active"] is False
        assert accounts.require("maria").status == STATUS_DISABLED


class TestDeprovisioning:
    def provisioned(self, entra: Source) -> str:
        return entra.service.create_user(user_doc())["id"]

    def test_entra_deactivates_with_the_text_false_and_the_account_is_off(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        user_id = self.provisioned(entra)

        resource = entra.service.patch_user(
            user_id, patch_doc({"op": "Replace", "path": "active", "value": "False"})
        )

        account = accounts.require("maria")
        assert resource["active"] is False and account.status == STATUS_DISABLED
        assert "SCIM source entra" in (account.disabled_reason or "")
        assert entra.service.ended == [account.id]
        assert [e["resource"] for e in events("auth.scim.deprovision")] == ["account:maria"]

    def test_okta_deactivates_with_a_patch_that_has_no_path(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        user_id = self.provisioned(entra)

        entra.service.patch_user(user_id, patch_doc({"op": "replace", "value": {"active": False}}))

        assert accounts.require("maria").status == STATUS_DISABLED

    def test_a_put_with_active_false_deactivates_too(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        user_id = self.provisioned(entra)

        entra.service.replace_user(user_id, user_doc(active=False))

        assert accounts.require("maria").status == STATUS_DISABLED

    def test_activating_again_switches_on_what_the_provider_switched_off(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        user_id = self.provisioned(entra)
        entra.service.patch_user(
            user_id, patch_doc({"op": "replace", "path": "active", "value": False})
        )

        resource = entra.service.patch_user(
            user_id, patch_doc({"op": "replace", "path": "active", "value": "True"})
        )

        assert resource["active"] is True
        assert accounts.require("maria").status == STATUS_INVITED

    def test_an_administrators_decision_is_not_overridden_by_the_provider(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        user_id = self.provisioned(entra)
        accounts.disable("maria", "investigation")

        resource = entra.service.patch_user(
            user_id, patch_doc({"op": "replace", "path": "active", "value": True})
        )

        assert resource["active"] is False
        assert accounts.require("maria").status == STATUS_DISABLED

    def test_deleting_disables_the_account_and_forgets_the_user_but_keeps_the_account(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        user_id = self.provisioned(entra)

        entra.service.delete_user(user_id)

        assert accounts.require("maria").status == STATUS_DISABLED
        assert entra.service.ended
        with pytest.raises(ScimError) as caught:
            entra.service.get_user(user_id)
        assert caught.value.status == 404

    def test_a_person_who_comes_back_is_attached_again_only_with_link_by_email(
        self, scim: ScimManager, admin: Any, accounts: AccountManager
    ) -> None:
        source = Source(scim, "entra", link_by_email=True)
        source.service.delete_user(source.service.create_user(user_doc())["id"])

        again = source.again().create_user(user_doc())

        assert again["active"] is True and accounts.require("maria").status == STATUS_INVITED

    def test_a_person_who_comes_back_without_link_by_email_is_a_conflict(
        self, entra: Source
    ) -> None:
        entra.service.delete_user(entra.service.create_user(user_doc())["id"])

        with pytest.raises(ScimError) as caught:
            entra.again().create_user(user_doc())

        assert caught.value.status == 409


class TestUpdating:
    def test_the_display_name_and_user_name_follow_the_provider_and_the_e_mail_does_not(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        user_id = entra.service.create_user(user_doc())["id"]

        resource = entra.service.replace_user(
            user_id,
            user_doc(
                "maria.lopez@acme.test",
                displayName="Maria L. Prado",
                emails=[{"value": "someone.else@acme.test", "primary": True}],
            ),
        )

        account = accounts.require("maria")
        assert account.display_name == "Maria L. Prado"
        assert resource["userName"] == "maria.lopez@acme.test"
        # The reference separation of duties judges by is never moved by a provider.
        assert account.person_ref == "maria@acme.test"
        assert resource["emails"][0]["value"] == "maria@acme.test"
        assert any("e-mail" in str(e["details"]) for e in events("auth.scim.update"))

    def test_entra_patches_name_parts_and_work_e_mail_by_path(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        user_id = entra.service.create_user(user_doc())["id"]

        entra.service.patch_user(
            user_id,
            patch_doc(
                {"op": "Replace", "path": "name.givenName", "value": "Maria"},
                {"op": "Replace", "path": "name.familyName", "value": "Prado"},
                {"op": "Replace", "path": 'emails[type eq "work"].value', "value": "m@acme.test"},
                {
                    "op": "Add",
                    "path": "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:manager",
                    "value": "someone",
                },
            ),
        )

        assert accounts.require("maria").display_name == "Maria Prado"

    def test_a_user_name_taken_by_another_user_is_a_conflict(self, entra: Source) -> None:
        entra.service.create_user(user_doc())
        other = entra.service.create_user(user_doc("olga@acme.test"))["id"]

        with pytest.raises(ScimError) as caught:
            entra.service.patch_user(
                other, patch_doc({"op": "replace", "path": "userName", "value": "maria@acme.test"})
            )

        assert caught.value.scim_type == "uniqueness"

    def test_removing_the_external_id_clears_it(self, entra: Source) -> None:
        user_id = entra.service.create_user(user_doc())["id"]

        resource = entra.service.patch_user(
            user_id, patch_doc({"op": "remove", "path": "externalId"})
        )

        assert "externalId" not in resource

    @pytest.mark.parametrize(
        "body", [{}, {"Operations": []}, {"Operations": [{"op": "move"}]}, {"Operations": ["x"]}]
    )
    def test_a_patch_that_is_not_one_is_refused(self, entra: Source, body: dict[str, Any]) -> None:
        user_id = entra.service.create_user(user_doc())["id"]

        with pytest.raises(ScimError) as caught:
            entra.service.patch_user(user_id, body)

        assert caught.value.status == 400


class TestFindingUsers:
    @pytest.fixture(autouse=True)
    def two(self, entra: Source) -> None:
        self.first = entra.service.create_user(user_doc())
        self.second = entra.service.create_user(user_doc("olga@acme.test"))
        self.service = entra.service

    def test_by_user_name_without_regard_to_case(self) -> None:
        found = self.service.list_users('userName eq "MARIA@acme.test"', None, None)

        assert found["totalResults"] == 1 and found["Resources"][0]["id"] == self.first["id"]

    def test_by_external_id_and_by_id(self) -> None:
        by_ext = self.service.list_users('externalId eq "ext-olga@acme.test"', None, None)
        by_id = self.service.list_users(f'id eq "{self.first["id"]}"', None, None)

        assert by_ext["Resources"][0]["id"] == self.second["id"]
        assert by_id["totalResults"] == 1

    def test_nobody_is_an_empty_list_not_an_error(self) -> None:
        found = self.service.list_users('userName eq "nobody@acme.test"', None, None)

        assert found["totalResults"] == 0 and found["Resources"] == []

    def test_a_filter_that_is_not_understood_is_not_answered_with_everybody(self) -> None:
        with pytest.raises(ScimError) as caught:
            self.service.list_users('userName sw "m"', None, None)

        assert (caught.value.status, caught.value.scim_type) == (400, "invalidFilter")

    def test_pages_are_one_based_and_capped(self) -> None:
        page = self.service.list_users(None, 2, 1)

        assert page["totalResults"] == 2 and page["startIndex"] == 2
        assert [r["userName"] for r in page["Resources"]] == ["olga@acme.test"]
        assert self.service.list_users(None, None, 0)["Resources"] == []
        assert self.service.list_users(None, None, 10**6)["itemsPerPage"] == 2

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('userName eq "a"', ("username", "a")),
            ('  USERNAME   EQ   "a b"  ', ("username", "a b")),
            ('userName eq "say \\"hi\\""', ("username", 'say "hi"')),
            (None, None),
            ("", None),
        ],
    )
    def test_the_filter_grammar(self, text: str | None, expected: Any) -> None:
        assert parse_filter(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "userName eq a",
            'userName eq "a" and active eq "true"',
            "userName",
            'a eq "b" or c eq "d"',
        ],
    )
    def test_anything_else_is_refused(self, text: str) -> None:
        with pytest.raises(ScimError):
            parse_filter(text)


class TestASourceStaysWithinItsOwn:
    def test_another_sources_users_do_not_exist(self, scim: ScimManager, admin: Any) -> None:
        entra = Source(scim, "entra")
        okta = Source(scim, "okta")
        mine = entra.service.create_user(user_doc())["id"]

        for call in (
            lambda: okta.service.get_user(mine),
            lambda: okta.service.delete_user(mine),
            lambda: okta.service.patch_user(
                mine, patch_doc({"op": "replace", "path": "active", "value": False})
            ),
        ):
            with pytest.raises(ScimError) as caught:
                call()
            assert caught.value.status == 404
        assert okta.service.list_users(None, None, None)["totalResults"] == 0

    def test_it_never_reaches_an_account_it_did_not_provision(
        self, entra: Source, accounts: AccountManager
    ) -> None:
        secure(accounts, "olga", "admin", person_ref="olga@acme.test")

        found = entra.service.list_users('userName eq "olga@acme.test"', None, None)

        assert found["totalResults"] == 0
        assert accounts.require("olga").status == STATUS_ACTIVE


class TestGroups:
    @pytest.fixture(autouse=True)
    def ready(self, scim: ScimManager, admin: Any, accounts: AccountManager) -> None:
        teams = TeamStore(accounts.store)
        teams.create_team("Acme", slug="acme")
        teams.create_project("acme", "Web", slug="web")
        AccessManager(accounts.store).create_role("release-manager", ["apps.read", "apps.deploy"])
        self.teams = teams
        self.accounts = accounts
        self.entra = Source(
            scim,
            "entra",
            group_sync=True,
            group_mappings=[
                {"group": "noust-ops", "role": "operator"},
                {"group": "acme-ops", "team": "acme", "team_role": "operator"},
                {
                    "group": "acme-release",
                    "bind": "release-manager",
                    "team": "acme",
                    "project": "web",
                },
            ],
        )
        self.service = self.entra.service
        self.maria = self.service.create_user(user_doc())["id"]
        self.olga = self.service.create_user(user_doc("olga@acme.test"))["id"]

    def group(self, name: str, *members: str) -> dict[str, Any]:
        return self.service.create_group(
            {
                "schemas": [SCHEMA_GROUP],
                "displayName": name,
                "members": [{"value": m} for m in members],
            }
        )

    def role(self, username: str = "maria") -> str:
        return self.accounts.require(username).role

    def bindings(self, username: str = "maria") -> dict[str, str]:
        return {b.scope: b.role for b in AccessManager(self.accounts.store).list_bindings(username)}

    def test_joining_a_group_gives_what_the_mapping_says_as_it_is_pushed(self) -> None:
        self.group("noust-ops", self.maria)
        self.group("acme-ops", self.maria)
        self.group("acme-release", self.maria)

        account = self.accounts.require("maria")
        assert account.role == "operator"
        assert {t.slug: r for t, r in self.teams.teams_of(account.id)}["acme"] == "operator"
        assert self.bindings() == {"acme/web": "release-manager"}
        assert self.role("olga") == "viewer"

    def test_leaving_a_group_takes_it_back_the_moment_the_provider_says_so(self) -> None:
        ops = self.group("noust-ops", self.maria, self.olga)["id"]
        release = self.group("acme-release", self.maria)["id"]
        assert self.role() == "operator" and self.role("olga") == "operator"

        self.service.patch_group(
            ops,
            patch_doc({"op": "Remove", "path": f'members[value eq "{self.maria}"]'}),
        )
        self.service.patch_group(
            release,
            patch_doc({"op": "Remove", "path": "members", "value": [{"value": self.maria}]}),
        )

        assert self.role() == "viewer" and self.role("olga") == "operator"
        assert self.bindings() == {}

    def test_deleting_a_group_takes_back_what_it_gave(self) -> None:
        ops = self.group("noust-ops", self.maria)["id"]

        self.service.delete_group(ops)

        assert self.role() == "viewer"

    def test_replacing_the_members_gives_to_the_new_and_takes_from_the_old(self) -> None:
        ops = self.group("noust-ops", self.maria)["id"]

        self.service.replace_group(
            ops, {"displayName": "noust-ops", "members": [{"value": self.olga}]}
        )

        assert self.role() == "viewer" and self.role("olga") == "operator"

    def test_adding_members_to_an_existing_group(self) -> None:
        ops = self.group("noust-ops")["id"]

        resource = self.service.patch_group(
            ops, patch_doc({"op": "Add", "path": "members", "value": [{"value": self.olga}]})
        )

        assert self.role("olga") == "operator"
        assert [m["value"] for m in resource["members"]] == [self.olga]

    def test_renaming_a_group_applies_the_mapping_of_the_new_name(self) -> None:
        group = self.group("temporary", self.maria)["id"]
        assert self.role() == "viewer"

        self.service.patch_group(
            group, patch_doc({"op": "replace", "path": "displayName", "value": "noust-ops"})
        )

        assert self.role() == "operator"

    def test_a_role_set_by_hand_survives_the_group_being_left(self) -> None:
        ops = self.group("noust-ops", self.maria)["id"]
        self.accounts.set_role("maria", "admin")

        self.service.delete_group(ops)

        assert self.role() == "admin"

    def test_a_member_this_source_did_not_provision_is_refused(self) -> None:
        with pytest.raises(ScimError) as caught:
            self.group("noust-ops", "not-a-user")

        assert caught.value.status == 400

    def test_a_group_name_is_unique_within_the_source(self) -> None:
        self.group("noust-ops")

        with pytest.raises(ScimError) as caught:
            self.group("noust-ops")

        assert caught.value.scim_type == "uniqueness"

    def test_the_groups_a_user_is_in_are_on_the_user(self) -> None:
        self.group("noust-ops", self.maria)

        resource = self.service.get_user(self.maria)

        assert [g["display"] for g in resource["groups"]] == ["noust-ops"]

    def test_nothing_is_given_while_the_sync_is_off(self, scim: ScimManager) -> None:
        scim.configure("entra", {"group_sync": False})

        self.entra.again().create_group(
            {"displayName": "noust-ops", "members": [{"value": self.maria}]}
        )

        assert self.role() == "viewer"

    def test_a_deactivated_person_is_not_given_a_role(self) -> None:
        self.service.patch_user(
            self.maria, patch_doc({"op": "replace", "path": "active", "value": False})
        )

        self.group("noust-ops", self.maria)

        assert self.role() == "viewer"

    def test_a_security_officer_provisioned_by_hand_is_not_touched_by_groups(self) -> None:
        secure(self.accounts, "sofia", "security", person_ref="sofia@acme.test")
        source = Source(
            self.entra.manager,
            "okta",
            group_sync=True,
            group_mappings=[{"group": "noust-ops", "role": "operator"}],
            link_by_email=True,
        )
        sofia = source.service.create_user(user_doc("sofia@acme.test"))["id"]

        source.again().create_group({"displayName": "noust-ops", "members": [{"value": sofia}]})

        assert self.role("sofia") == "security"

    def test_filtering_groups(self) -> None:
        group = self.group("noust-ops")

        found = self.service.list_groups('displayName eq "NOUST-OPS"', None, None)

        assert found["Resources"][0]["id"] == group["id"]
        assert self.service.list_groups('displayName eq "none"', None, None)["totalResults"] == 0
        with pytest.raises(ScimError):
            self.service.list_groups('members eq "x"', None, None)


class TestTheFirstSignInOfAProvisionedPerson:
    def test_the_account_scim_made_is_activated_by_the_sign_in_whose_e_mail_it_has(
        self,
        scim: ScimManager,
        admin: Any,
        accounts: AccountManager,
        manager: OidcManager,
        idp: FakeIdP,
    ) -> None:
        entra = Source(scim, "entra")
        entra.service.create_user(user_doc())
        configure(manager, idp, provisioning="invite")

        answer = run(manager, idp)

        account = accounts.require("maria")
        assert answer.account.id == account.id and account.status == STATUS_ACTIVE
        assert any("activated the invited account maria" in note for note in answer.notes)
        # Only now is there a way in; SCIM alone never was one.
        assert manager.identities("maria")[0].protocol == "oidc"

    def test_a_person_scim_deactivated_cannot_sign_in(
        self,
        scim: ScimManager,
        admin: Any,
        manager: OidcManager,
        idp: FakeIdP,
    ) -> None:
        entra = Source(scim, "entra")
        user_id = entra.service.create_user(user_doc())["id"]
        configure(manager, idp, provisioning="invite")
        run(manager, idp)
        entra.again().patch_user(
            user_id, patch_doc({"op": "replace", "path": "active", "value": False})
        )

        with pytest.raises(Exception, match="disabled"):
            run(manager, idp)
