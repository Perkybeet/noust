# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Recipe variables: the forms a value is generated in, references, precedence."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

import pytest

from noust.core.exceptions import ValidationError
from noust.recipes import RecipeError
from noust.recipes.model import GENERATORS, RecipeVariable
from noust.recipes.variables import (
    DeploymentContext,
    ReferenceSyntaxError,
    generate_value,
    missing_inputs,
    references_in_order,
    require_inputs,
    resolve_variables,
    scan_references,
    substitute,
)

CONTEXT = DeploymentContext(
    domain="app.example.com",
    ssl=True,
    port=3001,
    app_name="app-example-com",
    app_path="/var/www/apps/app-example-com",
)


# ---------------------------------------------------------------------------
# Generated forms
# ---------------------------------------------------------------------------


def test_a_user_is_sixteen_lower_case_characters_starting_with_a_letter() -> None:
    """The form Coolify's SERVICE_USER_ has, safe as a database or system user."""
    users = {generate_value("user") for _ in range(50)}

    assert len(users) == 50
    assert all(re.fullmatch(r"[a-z][a-z0-9]{15}", user) for user in users)


@pytest.mark.parametrize(("form", "length"), [("password32", 32), ("password64", 64)])
def test_passwords_are_letters_and_digits_of_the_length_they_say(form: str, length: int) -> None:
    """Alphanumeric, so they go into URLs, .env files and unit files unescaped."""
    value = generate_value(form)

    assert re.fullmatch(f"[A-Za-z0-9]{{{length}}}", value)
    assert generate_value(form) != value


@pytest.mark.parametrize(("form", "length"), [("hex32", 32), ("hex64", 64)])
def test_hex_forms_are_hexadecimal_of_that_many_characters(form: str, length: int) -> None:
    """hexN is N hexadecimal characters."""
    assert re.fullmatch(f"[0-9a-f]{{{length}}}", generate_value(form))


@pytest.mark.parametrize(("form", "size"), [("base64_32", 32), ("base64_64", 64)])
def test_base64_forms_are_the_base64_of_that_many_random_bytes(form: str, size: int) -> None:
    """True base64, not Coolify's misnamed alphanumeric."""
    value = generate_value(form)

    assert len(base64.b64decode(value, validate=True)) == size
    assert generate_value(form) != value


def test_a_jwt_is_an_hs256_token_with_the_role_signed_with_the_secret() -> None:
    """The pair of keys a stack such as Supabase's is configured with."""
    token = generate_value("jwt", signing_secret="s3cret", role="service_role")

    header, payload, signature = token.split(".")
    padded = lambda part: part + "=" * (-len(part) % 4)  # noqa: E731
    assert json.loads(base64.urlsafe_b64decode(padded(header))) == {"alg": "HS256", "typ": "JWT"}
    claims = json.loads(base64.urlsafe_b64decode(padded(payload)))
    assert claims["role"] == "service_role" and claims["exp"] > claims["iat"]
    expected = hmac.new(b"s3cret", f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(padded(signature)) == expected


def test_a_jwt_without_a_secret_and_an_unknown_form_are_errors() -> None:
    """Nothing is signed with nothing, and the list of forms is closed."""
    with pytest.raises(ValidationError, match="nothing to sign with"):
        generate_value("jwt")
    with pytest.raises(ValidationError, match="Unknown form"):
        generate_value("rot13")


def test_every_form_of_the_closed_list_generates() -> None:
    """The list and the generator agree."""
    for form in GENERATORS:
        assert generate_value(form, signing_secret="x", role="anon")


# ---------------------------------------------------------------------------
# References: ${VAR}, ${VAR:-default}, ${VAR:?message}
# ---------------------------------------------------------------------------


def test_references_are_found_with_their_operators() -> None:
    """The syntax Compose and the shell share."""
    found = scan_references("a ${A} b ${B:-x y} c ${C:?why} d ${D-z} e ${E:+yes} $$HOME")

    assert [(ref.name, ref.operator, ref.argument) for ref in found] == [
        ("A", None, ""),
        ("B", ":-", "x y"),
        ("C", ":?", "why"),
        ("D", "-", "z"),
        ("E", ":+", "yes"),
    ]
    assert found[1].default == "x y" and found[2].requires_value is True


@pytest.mark.parametrize("text", ["$HOME", "cost: $5", "${unclosed", "${A b}", "${A:=x}", "$"])
def test_what_the_syntax_does_not_have_is_refused(text: str) -> None:
    """A bare $NAME is doubt about whether it is a variable; refuse it."""
    with pytest.raises(ReferenceSyntaxError):
        scan_references(text)


def test_substitute_fills_defaults_and_escapes() -> None:
    """:- fills unset or empty, - only unset, $$ is a dollar, :+ is the alternative."""
    values = {"A": "1", "EMPTY": ""}

    assert substitute("${A}-${B:-2}-${EMPTY:-3}-${EMPTY-4}-$$", values, what="x") == "1-2-3--$"
    assert substitute("${A:+yes}${B:+no}", values, what="x") == "yes"


def test_a_demanded_variable_with_no_value_is_an_error_with_its_message() -> None:
    """${VAR:?message} blocks until it is filled."""
    with pytest.raises(RecipeError, match="LICENSE is required \\(your key\\)"):
        substitute("${LICENSE:?your key}", {}, what="the document")
    with pytest.raises(RecipeError, match="LICENSE is required"):
        substitute("${LICENSE:?your key}", {"LICENSE": ""}, what="the document")
    with pytest.raises(RecipeError, match="has no value"):
        substitute("${NOPE}", {}, what="the document")
    assert substitute("${LICENSE:?your key}", {"LICENSE": "abc"}, what="the document") == "abc"


def test_variables_are_ordered_after_what_they_are_made_from() -> None:
    """A dependency first, a circle refused."""
    first = RecipeVariable(name="A", value="${B}-x")
    second = RecipeVariable(name="B", generate="hex32")
    third = RecipeVariable(name="C", generate="jwt", signed_with="B", role="anon")

    assert [variable.name for variable in references_in_order([first, second, third])] == [
        "B",
        "A",
        "C",
    ]
    with pytest.raises(RecipeError, match="in a circle"):
        references_in_order(
            [RecipeVariable(name="A", value="${B}"), RecipeVariable(name="B", value="${A}")]
        )


# ---------------------------------------------------------------------------
# Required inputs
# ---------------------------------------------------------------------------


def test_every_missing_required_variable_is_named_at_once() -> None:
    """The operator fixes them in one go, before anything exists."""
    variables = [
        RecipeVariable(name="LICENSE", required=True, description="Your licence key"),
        RecipeVariable(name="S3_ENDPOINT", source="s3.endpoint"),
        RecipeVariable(name="MODE", default="fast"),
        RecipeVariable(name="TOKEN", generate="hex32"),
    ]

    assert [variable.name for variable in missing_inputs(variables, {})] == [
        "LICENSE",
        "S3_ENDPOINT",
    ]
    with pytest.raises(RecipeError, match="needs a value for LICENSE, S3_ENDPOINT") as failure:
        require_inputs("Demo", variables, {})
    assert "Your licence key" in failure.value.details and "--var" in failure.value.details
    assert failure.value.field == "variables"

    require_inputs("Demo", variables, {"LICENSE": "k", "S3_ENDPOINT": "https://s3.example.com"})
    with pytest.raises(RecipeError):
        require_inputs("Demo", variables, {"LICENSE": "", "S3_ENDPOINT": "x"})
    # An update has them already.
    require_inputs("Demo", variables, {}, {"LICENSE": "k", "S3_ENDPOINT": "x"})


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_the_deployment_gives_its_url_host_scheme_and_paths() -> None:
    """from: url, fqdn, scheme, https, port, app_name and app_path."""
    assert CONTEXT.value("url") == "https://app.example.com"
    assert CONTEXT.value("fqdn") == "app.example.com"
    assert (CONTEXT.value("scheme"), CONTEXT.value("https")) == ("https", "true")
    assert CONTEXT.value("port") == "3001"
    assert CONTEXT.value("app_name") == "app-example-com"
    assert CONTEXT.value("app_path") == "/var/www/apps/app-example-com"
    plain = DeploymentContext("a.b", False, None, "a-b", "/x")
    assert (plain.value("url"), plain.value("scheme"), plain.value("https")) == (
        "http://a.b",
        "http",
        "false",
    )
    with pytest.raises(ValidationError, match="no port"):
        plain.value("port")


def test_resolution_gives_each_variable_its_value_from_where_it_comes() -> None:
    """Generated, from the deployment, from a requirement, composed, default."""
    variables = [
        RecipeVariable(name="TOKEN", generate="password32", secret=True),
        RecipeVariable(name="PUBLIC", source="url"),
        RecipeVariable(name="DB", source="postgres.url", secret=True),
        RecipeVariable(name="HOOK", value="${PUBLIC}/hook?p=${PORT}"),
        RecipeVariable(name="MODE", default="fast"),
    ]

    resolved = resolve_variables(
        variables,
        context=CONTEXT,
        provisioned={"postgres": {"url": "postgres://u:p@127.0.0.1:5432/d"}},
        overrides={},
    )

    assert re.fullmatch("[A-Za-z0-9]{32}", resolved.values["TOKEN"])
    assert resolved.values["PUBLIC"] == "https://app.example.com"
    assert resolved.values["DB"] == "postgres://u:p@127.0.0.1:5432/d"
    assert resolved.values["HOOK"] == "https://app.example.com/hook?p=3001"
    assert resolved.values["MODE"] == "fast"
    assert resolved.secrets == {"TOKEN", "DB"}
    assert resolved.generated == {"TOKEN"}


def test_the_operator_wins_then_what_the_application_has_then_the_recipe() -> None:
    """An override beats an existing value, which beats generating: secrets persist."""
    variables = [
        RecipeVariable(name="TOKEN", generate="password32"),
        RecipeVariable(name="MODE", default="fast"),
        RecipeVariable(name="NEW", generate="hex32"),
    ]

    resolved = resolve_variables(
        variables,
        context=CONTEXT,
        provisioned={},
        overrides={"MODE": "slow", "EXTRA": "kept"},
        existing={"TOKEN": "old-token-1234567890123456789012", "MODE": "edited"},
    )

    assert resolved.values["TOKEN"] == "old-token-1234567890123456789012"
    assert resolved.values["MODE"] == "slow"
    assert "NEW" in resolved.generated and "TOKEN" not in resolved.generated
    assert resolved.values["EXTRA"] == "kept"


def test_a_jwt_is_signed_with_the_value_the_application_already_has() -> None:
    """The pair stays consistent when the secret was set by the operator."""
    variables = [
        RecipeVariable(name="SECRET", generate="password64"),
        RecipeVariable(name="ANON", generate="jwt", signed_with="SECRET", role="anon"),
    ]

    resolved = resolve_variables(
        variables, context=CONTEXT, provisioned={}, overrides={"SECRET": "chosen-by-hand"}
    )

    header, payload, signature = resolved.values["ANON"].split(".")
    expected = hmac.new(b"chosen-by-hand", f"{header}.{payload}".encode(), hashlib.sha256).digest()
    padded = signature + "=" * (-len(signature) % 4)
    assert base64.urlsafe_b64decode(padded) == expected


def test_a_requirement_that_was_not_provisioned_says_how_to_fix_it() -> None:
    """from: redis.url with no Redis: the message names it."""
    with pytest.raises(RecipeError, match=r"redis\.url, which was not provisioned") as failure:
        resolve_variables(
            [RecipeVariable(name="CACHE", source="redis.url")],
            context=CONTEXT,
            provisioned={},
            overrides={},
        )
    assert "redis" in failure.value.details


def test_a_required_variable_without_a_value_stops_resolution() -> None:
    """The last line of defence behind require_inputs."""
    with pytest.raises(RecipeError, match="LICENSE is required"):
        resolve_variables(
            [RecipeVariable(name="LICENSE", required=True)],
            context=CONTEXT,
            provisioned={},
            overrides={},
        )
