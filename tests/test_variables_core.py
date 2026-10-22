# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for shared variables, their references and the secret managers they can name
(:mod:`noust.core.variables`), checklist B-44, B-45, B-46 and B-48.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from noust.core.exceptions import ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.core.teams import TeamStore
from noust.core.variables import expressions
from noust.core.variables.model import ENVIRONMENT, PREVIEW, PROJECT, SERVER, TEAM
from noust.core.variables.predefined import Deployment, reserved_in
from noust.core.variables.providers import (
    ProviderRegistry,
    SecretHttp,
    SecretProviderError,
)
from noust.core.variables.resolver import (
    Context,
    Resolver,
    UnresolvedReferenceError,
    context_for,
)
from noust.core.variables.store import AppVariable, VariableStore
from noust.validators.environment import EnvironmentValidationError


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def world(store: NoustStore) -> dict[str, Any]:
    """A team with a project and two environments, and an application in staging."""
    teams = TeamStore(store)
    team = teams.create_team("Acme", slug="acme")
    project = teams.create_project("acme", "Shop", slug="shop")
    staging = teams.create_environment("acme", "shop", "Staging", slug="staging")
    production = teams.get_environment("acme", "shop", "production")
    assert production is not None
    store.create_app(
        App(domain="shop.example.com", app_type="nodejs", source="x", app_path="/tmp/shop")
    )
    teams.move_app("shop.example.com", project.id, staging.id)
    return {"team": team, "project": project, "staging": staging, "production": production}


class TestExpressions:
    def test_the_three_kinds_are_found_in_order(self) -> None:
        found = expressions.find_references(
            "{{project.DB}}-{{ app.URL }}-{{secret:vault/shop/key#k}}-{{server.A}}"
        )

        assert [(r.kind, r.key) for r in found] == [
            ("project", "DB"),
            ("app", "URL"),
            ("secret", "vault/shop/key#k"),
            ("server", "A"),
        ]
        assert found[2].provider == "vault"
        assert found[2].path == "shop/key#k"

    def test_other_braces_are_the_operators_own_text(self) -> None:
        assert not expressions.has_references("Hello {{user.name}} {{ x }} ${PORT}")
        assert expressions.substitute("{{user.name}}", lambda _r: "X") == "{{user.name}}"

    def test_a_backslash_keeps_a_reference_as_text(self) -> None:
        assert not expressions.has_references(r"\{{project.DB}}")
        assert expressions.substitute(r"\{{project.DB}}", lambda _r: "X") == "{{project.DB}}"

    def test_secret_references_use_a_colon_and_the_others_a_dot(self) -> None:
        assert not expressions.has_references("{{secret.vault/x}}")
        assert not expressions.has_references("{{project:DB}}")

    @pytest.mark.parametrize(
        "text",
        ["{{project.1BAD}}", "{{app.NOPE}}", "{{secret:Vault/x}}", "{{secret:vault}}"],
    )
    def test_a_malformed_reference_is_an_error_not_a_literal(self, text: str) -> None:
        found = expressions.find_references(text)
        assert found, text
        with pytest.raises(ValidationError):
            expressions.check_reference(found[0])


class TestSharedVariables:
    def test_a_scope_keeps_its_variables_apart(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        project = world["project"].id
        variables.set(SERVER, 0, "REGION", "eu")
        variables.set(PROJECT, project, "REGION", "us")
        variables.set(ENVIRONMENT, world["staging"].id, "REGION", "staging-1")

        assert variables.get(SERVER, 0, "REGION").value == "eu"  # type: ignore[union-attr]
        assert variables.get(PROJECT, project, "REGION").value == "us"  # type: ignore[union-attr]
        assert [v.name for v in variables.list(ENVIRONMENT, world["staging"].id)] == ["REGION"]
        assert variables.list(TEAM, world["team"].id) == []

    def test_setting_again_changes_it_and_keeps_who_created_it(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        first = variables.set(SERVER, 0, "TOKEN", "a", actor="ana")
        second = variables.set(SERVER, 0, "TOKEN", "b", secret=True, audience="build", actor="bob")

        assert (second.value, second.secret, second.audience) == ("b", True, "build")
        assert (second.created_by, second.updated_by) == ("ana", "bob")
        assert second.created_at == first.created_at

    @pytest.mark.parametrize("name", ["1X", "A-B", "", "A B"])
    def test_a_name_that_is_not_an_environment_variable_is_refused(
        self, store: NoustStore, name: str
    ) -> None:
        with pytest.raises(EnvironmentValidationError):
            VariableStore(store).set(SERVER, 0, name, "v")

    def test_a_value_with_a_newline_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(EnvironmentValidationError):
            VariableStore(store).set(SERVER, 0, "A", "x\nUser=root")

    def test_the_server_has_owner_zero_and_nothing_else_does(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        with pytest.raises(ValidationError):
            variables.set(SERVER, 3, "A", "v")
        with pytest.raises(ValidationError):
            variables.set(PROJECT, 0, "A", "v")

    def test_an_owner_that_does_not_exist_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError, match="No project has the id 999"):
            VariableStore(store).set(PROJECT, 999, "A", "v")

    def test_deleting_a_project_environment_or_application_takes_its_variables(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        teams = TeamStore(store)
        variables.set(ENVIRONMENT, world["production"].id, "A", "1")
        variables.set(PROJECT, world["project"].id, "B", "2")
        app = store.get_app("shop.example.com")
        assert app is not None and app.id is not None
        variables.set(PREVIEW, app.id, "C", "3")

        store.delete_app("shop.example.com")
        assert variables.list(PREVIEW, app.id) == []
        teams.delete_environment("acme", "shop", "production")
        assert variables.list(ENVIRONMENT, world["production"].id) == []
        teams.delete_project("acme", "shop")
        assert variables.list(PROJECT, world["project"].id) == []

    def test_replace_makes_a_scope_hold_exactly_these(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        variables.set(SERVER, 0, "OLD", "1")
        variables.set(SERVER, 0, "KEEP", "1")

        variables.replace(
            SERVER, 0, {"KEEP": {"value": "2"}, "NEW": {"value": "3", "secret": True}}
        )

        assert {v.name: v.value for v in variables.list(SERVER)} == {"KEEP": "2", "NEW": "3"}

    def test_replace_changes_nothing_when_one_entry_is_bad(self, store: NoustStore) -> None:
        variables = VariableStore(store)
        variables.set(SERVER, 0, "KEEP", "1")

        with pytest.raises(EnvironmentValidationError):
            variables.replace(SERVER, 0, {"OK": {"value": "x"}, "bad name": {"value": "y"}})

        assert [v.name for v in variables.list(SERVER)] == ["KEEP"]

    def test_who_uses_a_variable_is_found_within_its_scope(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        app = store.get_app("shop.example.com")
        assert app is not None and app.id is not None
        variables.set_app_variables(
            app.id, [AppVariable("DB", expression="{{environment.DB}}"), AppVariable("X")]
        )

        assert variables.referencing_apps(ENVIRONMENT, world["staging"].id, "DB") == [
            "shop.example.com"
        ]
        assert variables.referencing_apps(ENVIRONMENT, world["production"].id, "DB") == []
        assert variables.referencing_apps(PROJECT, world["project"].id, "DB") == []

    def test_app_variables_without_anything_to_say_leave_no_row(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        app = store.get_app("shop.example.com")
        assert app is not None and app.id is not None
        variables.set_app_variables(app.id, [AppVariable("A", audience="build", build_value="t")])
        variables.set_app_variables(app.id, [AppVariable("A")])

        assert variables.app_variables(app.id) == {}

    def test_the_rows_given_are_all_the_rows_an_application_keeps(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        app = store.get_app("shop.example.com")
        assert app is not None and app.id is not None
        variables.set_app_variables(
            app.id,
            [AppVariable("A", expression="{{server.X}}"), AppVariable("B", audience="runtime")],
        )

        variables.set_app_variables(app.id, [AppVariable("A", expression="{{server.X}}")])

        assert list(variables.app_variables(app.id)) == ["A"]


class TestResolver:
    def resolver(self, world: dict[str, Any], store: NoustStore, **extra: Any) -> Resolver:
        context = context_for(
            "shop.example.com",
            Deployment(domain="shop.example.com", branch="main", commit="abc1234", port=3001),
            store=store,
        )
        return Resolver(context, variables=VariableStore(store), **extra)

    def test_the_context_knows_where_the_application_lives(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        context = context_for("shop.example.com", store=store)

        assert context.team_id == world["team"].id
        assert context.project_id == world["project"].id
        assert context.environment_id == world["staging"].id

    def test_each_scope_is_read_from_the_applications_own(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        variables.set(ENVIRONMENT, world["staging"].id, "API", "https://staging.api")
        variables.set(ENVIRONMENT, world["production"].id, "API", "https://api")
        variables.set(PROJECT, world["project"].id, "NAME", "shop")
        variables.set(TEAM, world["team"].id, "ORG", "acme")
        variables.set(SERVER, 0, "REGION", "eu")

        text = self.resolver(world, store).resolve(
            "{{environment.API}}/{{project.NAME}}/{{team.ORG}}/{{server.REGION}}"
        )

        assert text == "https://staging.api/shop/acme/eu"

    def test_the_deployments_own_values(self, world: dict[str, Any], store: NoustStore) -> None:
        text = self.resolver(world, store).resolve(
            "{{app.URL}} {{app.DOMAIN}} {{app.BRANCH}} {{app.COMMIT}} {{app.PORT}} {{app.HOST}}"
        )

        assert text == "https://shop.example.com shop.example.com main abc1234 3001 127.0.0.1"

    def test_a_field_the_deployment_does_not_have_yet_is_an_error(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        with pytest.raises(UnresolvedReferenceError, match="RELEASE"):
            self.resolver(world, store).resolve("{{app.RELEASE}}")

    def test_a_variable_that_does_not_exist_says_how_to_define_it(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        with pytest.raises(UnresolvedReferenceError) as caught:
            self.resolver(world, store).resolve("{{project.MISSING}}")

        assert "project has no variable MISSING" in caught.value.message
        assert "noust variables set project MISSING" in (caught.value.details or "")

    def test_an_application_in_no_project_cannot_use_a_project_variable(
        self, store: NoustStore
    ) -> None:
        resolver = Resolver(Context(domain="x.example.com"), variables=VariableStore(store))

        with pytest.raises(UnresolvedReferenceError, match="in no project"):
            resolver.resolve("{{project.A}}")

    def test_shared_variables_may_refer_to_each_other(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        variables.set(SERVER, 0, "HOST", "db.internal")
        variables.set(
            PROJECT, world["project"].id, "URL", "postgres://{{server.HOST}}/{{app.DOMAIN}}"
        )

        assert (
            self.resolver(world, store).resolve("{{project.URL}}")
            == "postgres://db.internal/shop.example.com"
        )

    def test_a_cycle_is_named(self, world: dict[str, Any], store: NoustStore) -> None:
        variables = VariableStore(store)
        variables.set(SERVER, 0, "A", "{{server.B}}")
        variables.set(SERVER, 0, "B", "{{server.A}}")

        with pytest.raises(UnresolvedReferenceError, match=r"server\.A -> server\.B -> server\.A"):
            self.resolver(world, store).resolve("{{server.A}}")

    def test_a_chain_that_is_too_deep_is_refused(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        for index in range(10):
            variables.set(SERVER, 0, f"V{index}", f"{{{{server.V{index + 1}}}}}")
        variables.set(SERVER, 0, "V10", "end")

        with pytest.raises(UnresolvedReferenceError, match="nest more than"):
            self.resolver(world, store).resolve("{{server.V0}}")

    def test_an_escaped_reference_stays_as_text(
        self, world: dict[str, Any], store: NoustStore
    ) -> None:
        assert self.resolver(world, store).resolve(r"\{{project.X}}") == "{{project.X}}"

    def test_a_literal_is_returned_as_it_is(self, world: dict[str, Any], store: NoustStore) -> None:
        assert self.resolver(world, store).resolve("plain") == "plain"


class Opener:
    """A secret manager answering over a fake socket."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float = 0) -> io.BytesIO:
        self.requests.append(request)
        for fragment, answer in self.answers.items():
            if fragment in request.full_url:
                if isinstance(answer, int):
                    raise HTTPError(
                        request.full_url,
                        answer,
                        "x",
                        {},
                        io.BytesIO(b'{"errors": ["denied"]}'),  # type: ignore[arg-type]
                    )
                return io.BytesIO(json.dumps(answer).encode())
        raise AssertionError(f"unexpected request {request.full_url}")


@pytest.fixture
def registry(store: NoustStore, tmp_path: Path) -> ProviderRegistry:
    secrets = SecretStore(root=tmp_path / "secrets")
    return ProviderRegistry(store, secrets)


def with_opener(registry: ProviderRegistry, opener: Opener) -> ProviderRegistry:
    return ProviderRegistry(registry.store, registry.secrets, SecretHttp(opener=opener))


class TestProviders:
    def test_a_provider_is_stored_without_its_token_in_the_database(
        self, registry: ProviderRegistry, store: NoustStore
    ) -> None:
        registry.save("vault", "vault", token="s.TOKEN", address="https://vault.example.com")

        assert registry.get("vault") is not None
        with store._transaction() as cursor:
            cursor.execute("SELECT * FROM secret_providers")
            assert "s.TOKEN" not in str(list(map(tuple, cursor.fetchall())))
        assert registry.secrets.read("secret-providers/vault") == "s.TOKEN"

    def test_vault_kv2_with_a_field(self, registry: ProviderRegistry) -> None:
        registry.save("vault", "vault", token="s.T", address="https://vault.example.com")
        opener = Opener(
            {"/v1/secret/data/shop/db": {"data": {"data": {"url": "pg://x", "u": "y"}}}}
        )

        value = with_opener(registry, opener).fetch("vault", "shop/db#url")

        assert value == "pg://x"
        assert opener.requests[0].get_header("X-vault-token") == "s.T"

    def test_vault_kv1_and_a_namespace(self, registry: ProviderRegistry) -> None:
        registry.save(
            "v1",
            "vault",
            token="t",
            address="https://vault.example.com",
            settings={"kv_version": "1", "mount": "kv", "namespace": "team-a"},
        )
        opener = Opener({"/v1/kv/app": {"data": {"only": "1"}}})

        assert with_opener(registry, opener).fetch("v1", "app") == "1"
        assert opener.requests[0].get_header("X-vault-namespace") == "team-a"

    def test_vault_with_several_fields_and_no_field_named_says_which_exist(
        self, registry: ProviderRegistry
    ) -> None:
        registry.save("vault", "vault", token="t", address="https://vault.example.com")
        opener = Opener({"/data/app": {"data": {"data": {"a": "1", "b": "2"}}}})

        with pytest.raises(SecretProviderError) as caught:
            with_opener(registry, opener).fetch("vault", "app")

        assert "a, b" in (caught.value.details or "")

    def test_doppler(self, registry: ProviderRegistry) -> None:
        registry.save(
            "doppler", "doppler", token="dp.st.x", settings={"project": "shop", "config": "prd"}
        )
        opener = Opener({"/v3/configs/config/secret": {"value": {"raw": "r", "computed": "c"}}})

        assert with_opener(registry, opener).fetch("doppler", "API_KEY") == "c"
        url = opener.requests[0].full_url
        assert "project=shop" in url and "config=prd" in url and "name=API_KEY" in url
        assert opener.requests[0].get_header("Authorization") == "Bearer dp.st.x"

    def test_infisical_with_universal_auth(self, registry: ProviderRegistry) -> None:
        registry.save(
            "inf",
            "infisical",
            token="client-secret",
            settings={"project_id": "p1", "environment": "prod", "client_id": "cid"},
        )
        opener = Opener(
            {
                "/auth/universal-auth/login": {"accessToken": "AT"},
                "/api/v3/secrets/raw/DB": {"secret": {"secretValue": "pw"}},
            }
        )

        assert with_opener(registry, opener).fetch("inf", "DB") == "pw"
        assert opener.requests[1].get_header("Authorization") == "Bearer AT"

    def test_a_refusal_carries_the_managers_own_words_and_never_the_token(
        self, registry: ProviderRegistry
    ) -> None:
        registry.save("vault", "vault", token="s.SECRET-TOKEN", address="https://vault.example.com")
        opener = Opener({"/v1/secret/data/x": 403})

        with pytest.raises(SecretProviderError) as caught:
            with_opener(registry, opener).fetch("vault", "x#f")

        text = f"{caught.value.message} {caught.value.details}"
        assert "403" in text and "denied" in text
        assert "SECRET-TOKEN" not in text

    def test_an_unreachable_manager_fails_instead_of_returning_nothing(
        self, registry: ProviderRegistry
    ) -> None:
        from urllib.error import URLError

        registry.save("vault", "vault", token="t", address="https://vault.example.com")

        def down(request: Any, timeout: float = 0) -> Any:
            raise URLError("connection refused")

        with pytest.raises(SecretProviderError, match="could not be reached"):
            ProviderRegistry(registry.store, registry.secrets, SecretHttp(opener=down)).fetch(
                "vault", "x#f"
            )

    def test_a_provider_of_another_team_does_not_exist_for_this_one(
        self, registry: ProviderRegistry, world: dict[str, Any]
    ) -> None:
        registry.save(
            "mine",
            "doppler",
            token="t",
            settings={"project": "p", "config": "c"},
            team_id=world["team"].id,
        )
        opener = Opener({"/v3/configs/config/secret": {"value": {"raw": "r"}}})
        client = with_opener(registry, opener)

        assert client.fetch("mine", "X", team_id=world["team"].id) == "r"
        with pytest.raises(SecretProviderError, match="no secret manager called mine"):
            client.fetch("mine", "X", team_id=None)

    def test_the_address_must_be_https(self, registry: ProviderRegistry) -> None:
        with pytest.raises(ValidationError):
            registry.save("vault", "vault", token="t", address="ftp://vault.example.com")

    def test_a_kind_without_a_public_service_needs_an_address(
        self, registry: ProviderRegistry
    ) -> None:
        with pytest.raises(ValidationError, match="needs an address"):
            registry.save("vault", "vault", token="t")

    def test_required_settings_and_unknown_ones_are_refused(
        self, registry: ProviderRegistry
    ) -> None:
        with pytest.raises(ValidationError, match="needs: project, config"):
            registry.save("d", "doppler", token="t")
        with pytest.raises(ValidationError, match="no setting colour"):
            registry.save(
                "d", "doppler", token="t", settings={"project": "p", "config": "c", "colour": "x"}
            )

    def test_deleting_a_provider_deletes_its_token(self, registry: ProviderRegistry) -> None:
        registry.save("vault", "vault", token="t", address="https://vault.example.com")

        assert registry.delete("vault")

        assert registry.secrets.read("secret-providers/vault") is None
        assert not registry.delete("vault")

    def test_resolving_a_secret_reference_fetches_it_and_remembers_it_for_the_log(
        self, world: dict[str, Any], store: NoustStore, registry: ProviderRegistry
    ) -> None:
        registry.save("doppler", "doppler", token="t", settings={"project": "p", "config": "c"})
        opener = Opener({"/v3/configs/config/secret": {"value": {"raw": "sk_live_abc"}}})
        resolver = Resolver(
            context_for("shop.example.com", store=store),
            variables=VariableStore(store),
            providers=with_opener(registry, opener),
        )

        assert resolver.resolve("key={{secret:doppler/STRIPE}}&k2={{secret:doppler/STRIPE}}") == (
            "key=sk_live_abc&k2=sk_live_abc"
        )
        assert len(opener.requests) == 1  # fetched once per deployment
        assert resolver.fetched == ["sk_live_abc"]


class TestPredefined:
    def test_the_environment_of_a_deployment(self) -> None:
        deployment = Deployment(
            domain="shop.example.com",
            ssl=True,
            branch="main",
            commit="abc1234",
            release="20261013-120000-abc1234",
            port=3001,
        )

        env = deployment.environment()

        assert env == {
            "NOUST_URL": "https://shop.example.com",
            "NOUST_DOMAIN": "shop.example.com",
            "NOUST_BRANCH": "main",
            "NOUST_COMMIT": "abc1234",
            "NOUST_RELEASE": "20261013-120000-abc1234",
        }

    def test_the_build_is_not_given_what_changes_on_every_deploy(self) -> None:
        deployment = Deployment(domain="a.example.com", commit="abc", release="r1", branch="main")

        build = deployment.environment(build=True)

        assert "NOUST_COMMIT" not in build and "NOUST_RELEASE" not in build
        assert build["NOUST_BRANCH"] == "main"

    def test_an_application_without_ssl_answers_on_http(self) -> None:
        assert Deployment(domain="a.example.com", ssl=False).url == "http://a.example.com"

    def test_reserved_names_are_found(self) -> None:
        assert reserved_in({"NOUST_URL": "x", "OTHER": "y"}) == ["NOUST_URL"]
