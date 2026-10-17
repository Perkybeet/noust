# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Signing in against a directory (checklist I-22): configuring it, and what a sign-in does.

The directory is a real LDAP server in-process (``tests/ldap_support.py``): what these tests say
about the passwords and the filters it was sent is what a real one would have seen.
"""

# ruff: noqa: F811 - the fixtures imported from the manager tests are used by name

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import AccountManager
from noust.core.accounts.directory import DirectoryManager
from noust.core.accounts.local_login import LocalLoginRefused
from noust.core.accounts.model import STATUS_ACTIVE
from noust.core.accounts.oidc_model import OidcError, OidcRefused
from noust.core.exceptions import ValidationError
from noust.core.teams import TeamStore
from noust.web.permissions.access import AccessManager
from tests.ldap_support import FakeDirectory, entry
from tests.test_oidc_manager import (  # noqa: F401 - the fixtures of the manager tests
    accounts,
    admin,
    cheap_hashes,
    clock,
    secure,
    store,
)

IP = "203.0.113.5"
SERVICE = "cn=svc,ou=system,dc=acme,dc=test"
USER_FILTER = "(&(objectClass=person)(uid={username}))"


def people() -> list[dict[str, Any]]:
    return [
        entry(
            "uid=maria,ou=people,dc=acme,dc=test",
            uid="maria",
            mail="Maria@Acme.Test",
            displayName="Maria Lopez",
            userPassword="correct horse",
            entryUUID="7f3a-uuid-maria",
            memberOf=["cn=ops,ou=groups,dc=acme,dc=test", "cn=devs,ou=groups,dc=acme,dc=test"],
            objectClass=["inetOrgPerson", "person"],
        ),
        entry(
            "uid=olga,ou=people,dc=acme,dc=test",
            uid="olga",
            mail="olga@acme.test",
            userPassword="other secret",
            entryUUID="9c1d-uuid-olga",
            objectClass=["inetOrgPerson", "person"],
        ),
        entry(
            "uid=twin1,ou=people,dc=acme,dc=test",
            uid="twin",
            userPassword="x1",
            entryUUID="t1",
            objectClass="person",
        ),
        entry(
            "uid=twin2,ou=people,dc=acme,dc=test",
            uid="twin",
            userPassword="x2",
            entryUUID="t2",
            objectClass="person",
        ),
        entry(
            "uid=ghost,ou=people,dc=acme,dc=test",
            uid="ghost",
            userPassword="boo",
            objectClass="person",
        ),
        entry(
            SERVICE,
            cn="svc",
            userPassword="service password",
            objectClass="applicationProcess",
        ),
        entry(
            "cn=ops,ou=groups,dc=acme,dc=test",
            cn="ops",
            member="uid=olga,ou=people,dc=acme,dc=test",
            objectClass="groupOfNames",
        ),
    ]


@pytest.fixture
def server() -> Iterator[FakeDirectory]:
    with FakeDirectory(people()) as directory:
        yield directory


def local_guard(_host: str, _addresses: tuple[str, ...]) -> None:
    """The directories of these tests are on this machine."""


@pytest.fixture
def directories(store: Any, accounts: AccountManager, clock: Any) -> DirectoryManager:
    return DirectoryManager(
        store,
        accounts=accounts,
        clock=clock,
        guard=local_guard,
        resolver=lambda _name: ("127.0.0.1",),
    )


def settings(server: FakeDirectory, **more: Any) -> dict[str, Any]:
    return {
        "host": "localhost",
        "port": server.port,
        "tls": "none",
        "user_base": "ou=people,dc=acme,dc=test",
        "user_filter": USER_FILTER,
        "label": "Acme directory",
        **more,
    }


def add(directories: DirectoryManager, server: FakeDirectory, **more: Any) -> Any:
    return directories.configure(
        "corp",
        settings(server, bind_dn=SERVICE, **more),
        bind_password="service password",
    )


def sign_in(
    directories: DirectoryManager, username: str = "maria", typed: str = "correct horse"
) -> Any:
    return directories.authenticate("corp", username, typed, IP)


class TestConfiguration:
    def test_a_directory_is_added_with_the_defaults_of_an_openldap(
        self, directories: DirectoryManager, server: FakeDirectory
    ) -> None:
        directory = add(directories, server)

        assert (directory.subject_attribute, directory.email_attribute) == ("entryUUID", "mail")
        assert directory.issuer == "ldap:corp" and directory.protocol == "ldap"
        assert directories.public_directories() == [{"name": "corp", "label": "Acme directory"}]

    def test_the_service_accounts_password_is_kept_apart_and_never_shown(
        self, directories: DirectoryManager, server: FakeDirectory, store: Any
    ) -> None:
        directory = add(directories, server)

        assert directories.data.read_secret("corp") == "service password"
        assert "service password" not in str(directory.to_dict())
        stored = store._get_connection().execute("SELECT * FROM ldap_directories").fetchall()
        assert "service password" not in str(tuple(map(tuple, stored)))
        path = directories.data.secrets.path("ldap/corp")
        assert oct(path.stat().st_mode & 0o777) == "0o600"

    def test_a_service_account_without_a_password_is_refused(
        self, directories: DirectoryManager, server: FakeDirectory
    ) -> None:
        with pytest.raises(ValidationError, match="needs its password"):
            directories.configure("corp", settings(server, bind_dn=SERVICE))

        assert directories.directories() == []

    def test_a_directory_searched_anonymously_needs_none(
        self, directories: DirectoryManager, server: FakeDirectory
    ) -> None:
        directories.configure("corp", settings(server))

        assert not directories.data.has_secret("corp")

    def test_clearing_the_password_of_an_anonymous_directory_deletes_it(
        self, directories: DirectoryManager, server: FakeDirectory
    ) -> None:
        add(directories, server)

        directories.configure("corp", {"bind_dn": ""}, clear_bind_password=True)

        assert not directories.data.has_secret("corp")

    def test_a_new_directory_needs_a_host_and_a_base(self, directories: DirectoryManager) -> None:
        with pytest.raises(ValidationError, match="host and a user base"):
            directories.configure("corp", {"host": "ldap.acme.test"})

    @pytest.mark.parametrize(
        ("setting", "value", "message"),
        [
            ("host", "ldap://ldap.acme.test", "Not a host name"),
            ("host", "ldap.acme.test:636", "Not a host name"),
            ("host", "a b", "Not a host name"),
            ("port", "0", "between 1 and 65535"),
            ("port", "http", "number"),
            ("tls", "tls1", "Unknown TLS mode"),
            ("user_filter", "(uid=maria)", "must use {username}"),
            ("user_filter", "(uid={username})({nope})", "may use"),
            ("user_filter", "(uid={username}", "not a search filter"),
            ("user_base", "", "host and a user base"),
            ("subject_attribute", "bad attribute", "Not an attribute name"),
            ("timeout_seconds", "99", "between 1 and 30"),
            ("ca_file", "/nonexistent/ca.pem", "certificate bundle"),
            ("provisioning", "everyone", "provisioning"),
            ("default_role", "security", "cannot start"),
        ],
    )
    def test_what_is_wrong_is_refused_beside_its_setting(
        self,
        directories: DirectoryManager,
        server: FakeDirectory,
        setting: str,
        value: str,
        message: str,
    ) -> None:
        with pytest.raises(ValidationError, match=message):
            directories.configure("corp", settings(server, **{setting: value}))

        assert directories.directories() == []

    def test_plain_text_to_a_directory_over_the_network_cannot_be_configured(
        self, directories: DirectoryManager
    ) -> None:
        with pytest.raises(ValidationError, match="must use TLS"):
            directories.configure(
                "corp",
                {
                    "host": "ldap.acme.test",
                    "tls": "none",
                    "user_base": "dc=acme",
                    "user_filter": USER_FILTER,
                },
            )

    def test_the_default_port_follows_the_mode(self, directories: DirectoryManager) -> None:
        base = {"host": "ldap.acme.test", "user_base": "dc=acme", "user_filter": USER_FILTER}

        ldaps = directories.configure("corp", base)
        starttls = directories.configure("corp", {"tls": "starttls"})

        assert (ldaps.port, starttls.port) == (636, 389)

    def test_a_group_search_needs_both_halves(self, directories: DirectoryManager) -> None:
        base = {"host": "ldap.acme.test", "user_base": "dc=acme", "user_filter": USER_FILTER}

        with pytest.raises(ValidationError, match="group filter"):
            directories.configure("corp", {**base, "group_base": "ou=groups,dc=acme"})
        with pytest.raises(ValidationError, match="group base"):
            directories.configure(
                "corp", {**base, "group_filter": "(&(objectClass=groupOfNames)(member={dn}))"}
            )

    def test_a_name_another_way_of_signing_in_has_is_refused(
        self,
        directories: DirectoryManager,
        server: FakeDirectory,
        store: Any,
        accounts: AccountManager,
    ) -> None:
        from noust.core.accounts.oidc import OidcManager

        OidcManager(store, accounts=accounts).configure(
            "corp", {"issuer": "https://sso.acme.test", "client_id": "x"}
        )

        with pytest.raises(ValidationError, match="already a sign-in provider"):
            directories.configure("corp", settings(server))
        with pytest.raises(ValidationError, match="already a sign-in provider"):
            OidcManager(store, accounts=accounts).configure(
                "other", {"issuer": "https://sso.acme.test", "client_id": "x"}
            )
            directories.configure("other", settings(server))

    def test_a_team_that_does_not_exist_is_refused(
        self, directories: DirectoryManager, server: FakeDirectory
    ) -> None:
        with pytest.raises(ValidationError):
            directories.configure("corp", settings(server, default_team="ghost"))

    def test_a_setting_that_does_not_exist_is_refused(
        self, directories: DirectoryManager, server: FakeDirectory
    ) -> None:
        with pytest.raises(ValidationError, match="Unknown setting"):
            directories.configure("corp", settings(server, redirect_uri="x"))


class TestSigningIn:
    @pytest.fixture(autouse=True)
    def ready(self, directories: DirectoryManager, server: FakeDirectory, admin: Any) -> None:
        self.directories = directories
        self.server = server

    def test_a_person_with_the_right_password_is_admitted_and_their_account_made(
        self, accounts: AccountManager
    ) -> None:
        add(self.directories, self.server, provisioning="auto", default_role="operator")

        result = sign_in(self.directories)

        account = accounts.require("maria")
        assert result.account.id == account.id
        assert (account.role, account.status, account.person_ref) == (
            "operator",
            STATUS_ACTIVE,
            "maria@acme.test",
        )
        assert account.display_name == "Maria Lopez"
        identity = self.directories.admission.data.find_identity(
            "ldap:corp", "7f3a-uuid-maria", "ldap"
        )
        assert identity is not None and identity.provider == "corp"
        assert any("created the account maria" in note for note in result.notes)

    def test_the_directory_is_asked_for_the_password_not_told_it(self) -> None:
        add(self.directories, self.server, provisioning="auto")

        sign_in(self.directories)

        assert self.server.seen.binds == [
            (SERVICE, "service password"),
            ("uid=maria,ou=people,dc=acme,dc=test", "correct horse"),
        ]

    def test_an_anonymous_directory_is_searched_without_a_service_account(self) -> None:
        self.directories.configure("corp", settings(self.server, provisioning="auto"))

        sign_in(self.directories)

        assert self.server.seen.binds[0] == ("", "")

    def test_a_wrong_password_and_an_unknown_name_are_the_same_answer(self) -> None:
        add(self.directories, self.server, provisioning="auto")

        with pytest.raises(OidcError) as wrong:
            sign_in(self.directories, "maria", "not the password")
        with pytest.raises(OidcError) as unknown:
            sign_in(self.directories, "nobody", "whatever")

        assert (wrong.value.reason, wrong.value.public) == ("bad_credentials", "failed")
        assert (unknown.value.reason, unknown.value.public) == ("bad_credentials", "failed")
        assert wrong.value.message == unknown.value.message

    def test_an_empty_password_never_reaches_the_directory(self) -> None:
        add(self.directories, self.server, provisioning="auto")

        for password in ("", "   ".strip(), "a\x00b"):
            with pytest.raises(OidcError):
                sign_in(self.directories, "maria", password)

        assert self.server.seen.binds == []

    def test_what_is_typed_is_one_value_in_the_search(self) -> None:
        add(self.directories, self.server, provisioning="auto")

        with pytest.raises(OidcError) as caught:
            sign_in(self.directories, "*)(uid=*", "x")

        assert caught.value.reason == "bad_credentials"
        sent = self.server.seen.searches[-1][1]
        assert sent.count("a3") == 2 and "a1" not in sent

    def test_two_entries_for_one_name_refuse_instead_of_choosing(self) -> None:
        add(self.directories, self.server, provisioning="auto")

        with pytest.raises(OidcRefused) as caught:
            sign_in(self.directories, "twin", "x1")

        assert caught.value.reason == "ambiguous_user"
        assert all(dn == SERVICE for dn, _ in self.server.seen.binds)

    def test_the_directorys_own_identifier_is_who_they_are_not_their_name_or_mail(
        self, accounts: AccountManager
    ) -> None:
        add(self.directories, self.server, provisioning="auto")
        first = sign_in(self.directories)
        # The mail and the login are changed in the directory; the person is the same.
        for candidate in self.server.entries:
            if candidate["dn"].startswith("uid=maria"):
                candidate["attrs"]["mail"] = [b"someone.else@acme.test"]

        again = sign_in(self.directories)

        assert again.account.id == first.account.id
        assert accounts.require("maria").person_ref == "maria@acme.test"

    def test_a_directory_that_does_not_name_the_person_stably_is_refused(self) -> None:
        add(self.directories, self.server, provisioning="auto")

        with pytest.raises(OidcRefused) as caught:
            sign_in(self.directories, "ghost", "boo")

        assert caught.value.reason == "no_subject"
        assert "entryUUID" in caught.value.details and "objectGUID" in caught.value.details

    def test_nobody_comes_in_unlinked_while_provisioning_is_off(self) -> None:
        add(self.directories, self.server)

        with pytest.raises(OidcRefused) as caught:
            sign_in(self.directories)

        assert caught.value.reason == "no_account"
        assert "noust auth ldap link" in caught.value.details

    def test_an_administrator_links_a_person_by_hand(self, accounts: AccountManager) -> None:
        add(self.directories, self.server)
        secure(accounts, "maria", "viewer")
        self.directories.link("corp", "maria", "7f3a-uuid-maria", actor="cli:root")

        result = sign_in(self.directories)

        assert result.account.username == "maria"

    def test_the_account_of_the_same_mail_is_linked_only_with_a_second_factor(
        self, accounts: AccountManager
    ) -> None:
        add(self.directories, self.server, link_by_email=True)
        accounts.create(
            "maria", "viewer", password="correct horse battery staple", person_ref="maria@acme.test"
        )

        with pytest.raises(OidcRefused) as caught:
            sign_in(self.directories)

        assert caught.value.reason == "link_needs_second_factor"

    def test_an_invited_account_is_activated_by_the_sign_in(self, accounts: AccountManager) -> None:
        add(self.directories, self.server, provisioning="invite")
        accounts.provision_external("maria", "operator", person_ref="maria@acme.test")

        result = sign_in(self.directories)

        assert result.account.status == STATUS_ACTIVE and result.account.role == "operator"

    def test_a_domain_that_is_not_allowed_is_refused(self) -> None:
        add(self.directories, self.server, provisioning="auto", allowed_domains=["other.test"])

        with pytest.raises(OidcRefused) as caught:
            sign_in(self.directories)

        assert caught.value.reason == "domain_not_allowed"

    def test_a_disabled_directory_is_not_there(self, accounts: AccountManager) -> None:
        add(self.directories, self.server, provisioning="auto")
        accounts.create("root2", "admin", password="correct horse battery staple")
        self.directories.configure("corp", {"enabled": False})

        with pytest.raises(OidcError) as caught:
            sign_in(self.directories)

        assert (caught.value.reason, caught.value.public) == ("unknown_directory", "unavailable")
        assert self.directories.public_directories() == []

    def test_a_directory_that_cannot_be_reached_is_unavailable_not_a_wrong_password(self) -> None:
        add(self.directories, self.server, provisioning="auto", port=1)

        with pytest.raises(OidcError) as caught:
            sign_in(self.directories)

        assert (caught.value.reason, caught.value.public) == (
            "directory_unavailable",
            "unavailable",
        )

    def test_nobody_comes_in_while_there_is_no_account_at_all(
        self, store: Any, clock: Any, server: FakeDirectory, tmp_path: Path
    ) -> None:
        fresh = AccountManager(store, clock=clock)
        for found in fresh.list_all():
            fresh.remove(found.username)
        manager = DirectoryManager(
            store,
            accounts=fresh,
            clock=clock,
            guard=local_guard,
            resolver=lambda _n: ("127.0.0.1",),
        )
        manager.configure("corp", settings(server, provisioning="auto"))

        with pytest.raises(OidcRefused) as caught:
            manager.authenticate("corp", "maria", "correct horse", IP)

        assert caught.value.reason == "no_accounts"


class TestGroups:
    @pytest.fixture(autouse=True)
    def ready(
        self,
        directories: DirectoryManager,
        server: FakeDirectory,
        admin: Any,
        accounts: AccountManager,
    ) -> None:
        teams = TeamStore(accounts.store)
        teams.create_team("Acme", slug="acme")
        teams.create_project("acme", "Web", slug="web")
        AccessManager(accounts.store).create_role("release-manager", ["apps.read", "apps.deploy"])
        self.directories = directories
        self.server = server
        self.accounts = accounts
        self.teams = teams
        add(
            directories,
            server,
            provisioning="auto",
            group_sync=True,
            group_mappings=[
                {"group": "cn=ops,ou=groups,dc=acme,dc=test", "role": "operator"},
                {"group": "devs", "team": "acme", "team_role": "operator"},
                {"group": "release", "bind": "release-manager", "team": "acme", "project": "web"},
            ],
        )

    def test_groups_the_entry_lists_give_roles_by_distinguished_name_and_by_name(self) -> None:
        sign_in(self.directories)

        account = self.accounts.require("maria")
        assert account.role == "operator"
        memberships = {t.slug: role for t, role in self.teams.teams_of(account.id)}
        assert memberships["acme"] == "operator"

    def test_groups_a_search_finds_give_roles_too(self) -> None:
        self.directories.configure(
            "corp",
            {
                "group_base": "ou=groups,dc=acme,dc=test",
                "group_filter": "(&(objectClass=groupOfNames)(member={dn}))",
                "group_mappings": [{"group": "ops", "role": "operator"}],
            },
        )

        sign_in(self.directories, "olga", "other secret")

        assert self.accounts.require("olga").role == "operator"

    def test_leaving_a_group_in_the_directory_takes_it_back_at_the_next_sign_in(self) -> None:
        sign_in(self.directories)
        assert self.accounts.require("maria").role == "operator"
        for candidate in self.server.entries:
            if candidate["dn"].startswith("uid=maria"):
                candidate["attrs"]["memberof"] = []

        sign_in(self.directories)

        assert self.accounts.require("maria").role == "viewer"

    def test_a_role_at_a_project_follows_the_group_too(self) -> None:
        for candidate in self.server.entries:
            if candidate["dn"].startswith("uid=maria"):
                candidate["attrs"]["memberof"] = [b"cn=release,ou=groups,dc=acme,dc=test"]

        sign_in(self.directories)

        bindings = AccessManager(self.accounts.store).list_bindings("maria")
        assert [(b.scope, b.role) for b in bindings] == [("acme/web", "release-manager")]


class TestTesting:
    def test_every_step_is_checked_and_passes(
        self, directories: DirectoryManager, server: FakeDirectory, admin: Any
    ) -> None:
        add(directories, server)

        checks = directories.test("corp")

        assert [c.name for c in checks] == ["connection", "bind", "users"]
        assert all(c.ok for c in checks)

    def test_a_wrong_service_password_fails_at_the_bind(
        self, directories: DirectoryManager, server: FakeDirectory
    ) -> None:
        directories.configure(
            "corp", settings(server, bind_dn=SERVICE), bind_password="not the password"
        )

        checks = directories.test("corp")

        assert [(c.name, c.ok) for c in checks] == [("connection", True), ("bind", False)]
        assert (
            "refused the bind" in checks[-1].detail and "invalid credentials" in checks[-1].detail
        )

    def test_a_directory_that_is_down_fails_at_the_connection(
        self, directories: DirectoryManager, server: FakeDirectory
    ) -> None:
        directories.configure("corp", settings(server, port=1))

        checks = directories.test("corp")

        assert [(c.name, c.ok) for c in checks] == [("connection", False)]

    def test_testing_a_directory_that_does_not_exist_is_said(
        self, directories: DirectoryManager
    ) -> None:
        with pytest.raises(ValidationError, match="No directory named"):
            directories.test("nowhere")


class TestTheLastWayIn:
    def test_a_directory_counts_as_a_way_in_and_the_last_one_cannot_be_removed(
        self,
        directories: DirectoryManager,
        server: FakeDirectory,
        admin: Any,
        accounts: AccountManager,
        real_config: Any,
    ) -> None:
        add(directories, server)
        directories.link("corp", "root-admin", "some-subject")
        real_config.set("auth.local_login.enabled", False)
        real_config.set("auth.local_login.exempt", [])
        from noust.core.accounts.local_login import survivors

        assert survivors(accounts.store) == ["root-admin"]

        with pytest.raises(LocalLoginRefused):
            directories.remove("corp")
        with pytest.raises(LocalLoginRefused):
            directories.configure("corp", {"enabled": False})

        real_config.set("auth.local_login.enabled", True)
        directories.remove("corp")
        assert directories.directories() == [] and not directories.data.has_secret("corp")
