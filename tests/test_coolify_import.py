# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Coolify service template, converted to a Compose document Noust deploys.

The templates here are written for these tests in the style Coolify's catalogue uses (a header of
comments, ``SERVICE_*`` magic variables, a bare ``SERVICE_URL_<ID>_<PORT>`` entry that names the web
service, Traefik labels); none is copied from it. What is pinned is what the conversion promises: the
variables keep their names and get values of the right shape, the routing entry becomes a loopback
port, what means nothing here is dropped and said, what cannot be translated is a warning and never
a guess, and the result is judged by the same analysis as any pasted document.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

from noust.core.exceptions import ValidationError
from noust.deployers.compose_paste import analyze_document
from noust.recipes.coolify import MAX_TEMPLATE_BYTES, convert, read_header, read_magic, values_file

BLOG = """\
# documentation: https://docs.example.org/blog
# slogan: A tiny blog engine.
# tags: cms,blog
# logo: svgs/blog.svg
# port: 2368

services:
  blog:
    image: example/blog:5.2
    environment:
      - SERVICE_URL_BLOG_2368
      - url=$SERVICE_URL_BLOG
      - database__user=$SERVICE_USER_MYSQL
      - database__password=$SERVICE_PASSWORD_MYSQL
      - database__name=${MYSQL_DATABASE:-blog}
      - TZ
    volumes:
      - blog-content:/var/lib/blog/content
    depends_on:
      mysql:
        condition: service_healthy
    labels:
      - traefik.enable=true
      - traefik.http.routers.blog.rule=Host(`$SERVICE_FQDN_BLOG`)
      - coolify.managed=true
      - app.keep=yes
    networks:
      - coolify
    exclude_from_hc: true
  mysql:
    image: mysql:8.0
    environment:
      - MYSQL_USER=$SERVICE_USER_MYSQL
      - MYSQL_PASSWORD=$SERVICE_PASSWORD_MYSQL
      - MYSQL_ROOT_PASSWORD=$SERVICE_PASSWORD_MYSQLROOT
    volumes:
      - blog-mysql:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1"]
networks:
  coolify:
    external: true
volumes:
  blog-content: {}
  blog-mysql: {}
"""


def converted(text: str = BLOG, **kwargs: Any) -> Any:
    return convert(text, domain=kwargs.pop("domain", "blog.example.com"), **kwargs)


def document(result: Any) -> dict[str, Any]:
    return yaml.safe_load(result.document)  # type: ignore[no-any-return]


# -- the names ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind", "detail"),
    [
        ("SERVICE_URL_GHOST", "url", ("GHOST", None)),
        ("SERVICE_URL_GHOST_2368", "url", ("GHOST", 2368)),
        ("SERVICE_FQDN_API_PORTAL_8080", "fqdn", ("API_PORTAL", 8080)),
        ("SERVICE_FQDN_APP", "fqdn", ("APP", None)),
        ("SERVICE_USER_MYSQL", "generated", ("MYSQL", "user")),
        ("SERVICE_PASSWORD_MYSQL", "generated", ("MYSQL", "password32")),
        ("SERVICE_PASSWORD_64_REDIS", "generated", ("REDIS", "password64")),
        ("SERVICE_PASSWORDWITHSYMBOLS_APP", "generated", ("APP", "password32")),
        ("SERVICE_BASE64_APPKEY", "generated", ("APPKEY", "base64_32")),
        ("SERVICE_BASE64_64_APPKEY", "generated", ("APPKEY", "base64_64")),
        ("SERVICE_REALBASE64_KEY", "generated", ("KEY", "base64_32")),
        ("SERVICE_HEX_32_SALT", "generated", ("SALT", "hex32")),
        ("SERVICE_HEX_64_SALT", "generated", ("SALT", "hex64")),
        ("SERVICE_SUPABASEANON_KEY", "jwt", ("KEY", "anon")),
        ("SERVICE_SUPABASESERVICE_KEY", "jwt", ("KEY", "service_role")),
    ],
)
def test_a_magic_variable_is_read_from_its_name(
    name: str, kind: str, detail: tuple[Any, Any]
) -> None:
    magic = read_magic(name)

    assert magic is not None and magic.kind == kind
    if kind in ("url", "fqdn"):
        assert (magic.id, magic.port) == detail
    elif kind == "generated":
        assert (magic.id, magic.generator) == detail
    else:
        assert (magic.id, magic.role) == detail


@pytest.mark.parametrize(
    "name",
    ["SERVICE_URL", "SERVICE_", "SERVICE_PASSWORD", "MYSQL_PASSWORD", "SERVICE_OPENAI_API_KEY"],
)
def test_a_name_that_is_not_one_is_not_read_as_one(name: str) -> None:
    assert read_magic(name) is None


def test_the_header_is_read_until_the_first_line_of_the_document() -> None:
    header = read_header(BLOG)

    assert header == {
        "documentation": "https://docs.example.org/blog",
        "slogan": "A tiny blog engine.",
        "tags": "cms,blog",
        "logo": "svgs/blog.svg",
        "port": "2368",
    }
    assert read_header("services:\n  a:\n    image: x\n# port: 9\n") == {}


# -- the document ---------------------------------------------------------------------------------


def test_the_variables_keep_their_names_and_a_bare_dollar_becomes_braces() -> None:
    result = converted()

    environment = document(result)["services"]["blog"]["environment"]
    assert "url=${SERVICE_URL_BLOG}" in environment
    assert "database__user=${SERVICE_USER_MYSQL}" in environment
    assert "database__name=${MYSQL_DATABASE:-blog}" in environment
    assert "TZ" in environment  # a pass-through of the environment is not a declaration
    assert not re.search(r"(?<!\$)\$[A-Za-z_]", result.document)


def test_the_routing_entry_becomes_a_loopback_port_and_stops_being_an_entry() -> None:
    result = converted()

    blog = document(result)["services"]["blog"]
    assert blog["ports"] == ["127.0.0.1:${PORT:-2368}:2368"]
    assert "SERVICE_URL_BLOG_2368" not in result.document
    assert "SERVICE_URL_BLOG=${SERVICE_URL_BLOG}" in blog["environment"]
    assert result.routed == ("blog", 2368)
    assert "ports" not in document(result)["services"]["mysql"]


def test_the_host_port_asked_for_is_the_default_of_the_variable() -> None:
    result = converted(port=18080)

    assert document(result)["services"]["blog"]["ports"] == ["127.0.0.1:${PORT:-18080}:2368"]


def test_what_only_coolifys_proxy_reads_is_dropped_and_said() -> None:
    result = converted()

    blog = document(result)["services"]["blog"]
    assert blog["labels"] == ["app.keep=yes"]
    assert "networks" not in blog and "exclude_from_hc" not in blog
    assert "networks" not in document(result)
    said = " ".join(result.adjustments)
    assert (
        "Traefik/Coolify label" in said and "coolify network" in said and "exclude_from_hc" in said
    )


def test_a_service_without_labels_left_has_none() -> None:
    text = BLOG.replace("      - app.keep=yes\n", "")

    assert "labels" not in document(converted(text))["services"]["blog"]


def test_the_document_says_where_it_came_from_and_is_attributed() -> None:
    result = converted()

    head = result.document.split("\n\n", 1)[0]
    assert "Converted by Noust from a Coolify service template" in head
    assert "Apache-2.0" in head and "keep their rights" in head
    assert "documentation: https://docs.example.org/blog" in head
    assert result.metadata["port"] == "2368"


# -- the values -----------------------------------------------------------------------------------


def test_every_variable_the_document_reads_has_a_value_of_the_right_shape() -> None:
    result = converted()

    values = result.values
    assert values["SERVICE_URL_BLOG"] == "https://blog.example.com"
    assert re.fullmatch(r"[a-z][a-z0-9]{15}", values["SERVICE_USER_MYSQL"])
    assert re.fullmatch(r"[A-Za-z0-9]{32}", values["SERVICE_PASSWORD_MYSQL"])
    assert values["SERVICE_PASSWORD_MYSQL"] != values["SERVICE_PASSWORD_MYSQLROOT"]
    assert {v.name: v.origin for v in result.variables} == {
        "SERVICE_URL_BLOG": "domain",
        "SERVICE_USER_MYSQL": "generated",
        "SERVICE_PASSWORD_MYSQL": "generated",
        "SERVICE_PASSWORD_MYSQLROOT": "generated",
    }
    assert [v.name for v in result.variables if v.secret] == [
        "SERVICE_PASSWORD_MYSQL",
        "SERVICE_PASSWORD_MYSQLROOT",
    ]


def test_two_conversions_do_not_share_a_secret() -> None:
    assert (
        converted().values["SERVICE_PASSWORD_MYSQL"] != converted().values["SERVICE_PASSWORD_MYSQL"]
    )


def test_the_shapes_of_the_other_generators() -> None:
    text = """\
services:
  app:
    image: example/app:1
    environment:
      - SERVICE_URL_APP_80
      - A=$SERVICE_PASSWORD_64_X
      - B=$SERVICE_BASE64_X
      - C=$SERVICE_BASE64_64_X
      - D=$SERVICE_HEX_32_X
      - E=$SERVICE_HEX_64_X
      - F=$SERVICE_FQDN_APP
"""
    values = converted(text).values

    assert re.fullmatch(r"[A-Za-z0-9]{64}", values["SERVICE_PASSWORD_64_X"])
    assert re.fullmatch(r"[A-Za-z0-9+/=_-]{43,44}", values["SERVICE_BASE64_X"])
    assert len(values["SERVICE_BASE64_64_X"]) > 80
    assert re.fullmatch(r"[0-9a-f]{32}", values["SERVICE_HEX_32_X"])
    assert re.fullmatch(r"[0-9a-f]{64}", values["SERVICE_HEX_64_X"])
    assert values["SERVICE_FQDN_APP"] == "blog.example.com"


def test_without_a_domain_the_addresses_are_asked_for_and_nothing_is_invented() -> None:
    result = convert(BLOG)

    assert "SERVICE_URL_BLOG" not in result.values
    (ask,) = [v for v in result.variables if v.origin == "ask"]
    assert ask.name == "SERVICE_URL_BLOG" and "domain" in ask.note


def test_a_supabase_style_pair_of_tokens_is_signed_with_the_templates_own_secret() -> None:
    text = """\
services:
  api:
    image: example/api:1
    environment:
      - SERVICE_URL_API_8000
      - JWT_SECRET=$SERVICE_PASSWORD_JWT
      - ANON_KEY=$SERVICE_SUPABASEANON_KEY
      - SERVICE_KEY=$SERVICE_SUPABASESERVICE_KEY
"""
    values = converted(text).values

    anon, service = values["SERVICE_SUPABASEANON_KEY"], values["SERVICE_SUPABASESERVICE_KEY"]
    assert anon.count(".") == 2 and service.count(".") == 2 and anon != service
    import base64
    import hashlib
    import hmac
    import json

    for token, role in ((anon, "anon"), (service, "service_role")):
        head, body, signature = token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        assert claims["role"] == role
        expected = hmac.new(
            values["SERVICE_PASSWORD_JWT"].encode(), f"{head}.{body}".encode(), hashlib.sha256
        ).digest()
        assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expected


def test_a_token_with_no_secret_to_sign_it_is_asked_for() -> None:
    text = """\
services:
  api:
    image: example/api:1
    environment:
      - SERVICE_URL_API_8000
      - ANON_KEY=$SERVICE_SUPABASEANON_KEY
"""
    result = converted(text)

    assert "SERVICE_SUPABASEANON_KEY" not in result.values
    assert [(v.name, v.origin) for v in result.variables if v.origin == "ask"] == [
        ("SERVICE_SUPABASEANON_KEY", "ask")
    ]


def test_an_application_variable_named_like_one_is_asked_for_not_made() -> None:
    text = """\
services:
  app:
    image: example/app:1
    environment:
      - SERVICE_URL_APP_80
      - SERVICE_URL=$SERVICE_URL_APP
      - TERMS_OF_SERVICE_URL=$SERVICE_URL_APP/terms
      - KEY=${SERVICE_OPENAI_API_KEY}
"""
    result = converted(text)

    assert any(
        "SERVICE_OPENAI_API_KEY is named like a Coolify variable" in w for w in result.warnings
    )
    assert not any(
        "TERMS_OF_SERVICE_URL" in w or "SERVICE_URL is named" in w for w in result.warnings
    )


# -- which service is the web ------------------------------------------------------------------------


def test_with_two_routed_services_the_first_gets_the_domain_and_the_other_is_said() -> None:
    text = """\
services:
  web:
    image: example/web:1
    environment:
      - SERVICE_URL_WEB_3000
  api:
    image: example/api:1
    environment:
      - SERVICE_URL_API_4000
"""
    result = converted(text)

    assert result.routed == ("web", 3000)
    assert "ports" in document(result)["services"]["web"]
    assert "ports" not in document(result)["services"]["api"]
    assert any("web gets the domain and api stays inside" in w for w in result.warnings)


def test_without_a_routing_entry_the_header_port_goes_to_the_service_that_asks_for_an_address() -> (
    None
):
    text = """\
# port: 8080
services:
  db:
    image: postgres:16
  site:
    image: example/site:1
    environment:
      - URL=$SERVICE_URL_SITE
"""
    result = converted(text)

    assert result.routed == ("site", 8080)
    assert any("site was taken as the web service on port 8080" in w for w in result.warnings)


def test_a_guess_never_lands_on_a_database() -> None:
    text = """\
# port: 5432
services:
  db:
    image: postgres:16
  cache:
    image: redis:7
"""
    result = converted(text)

    assert result.routed is None
    assert any("deploys as a worker" in w for w in result.warnings)
    assert all("ports" not in s for s in document(result)["services"].values())


def test_a_template_with_no_web_at_all_is_a_worker_and_says_so() -> None:
    result = converted("services:\n  job:\n    image: example/job:1\n")

    assert result.routed is None and any("worker" in w for w in result.warnings)


def test_a_port_the_service_already_publishes_is_left_and_another_is_added() -> None:
    text = """\
services:
  git:
    image: example/git:1
    ports:
      - "2222:22"
    environment:
      - SERVICE_URL_GIT_3000
"""
    ports = document(converted(text))["services"]["git"]["ports"]
    assert ports == ["2222:22", "127.0.0.1:${PORT:-3000}:3000"]

    already = text.replace('"2222:22"', '"3000:3000"')
    result = converted(already)
    assert document(result)["services"]["git"]["ports"] == ["3000:3000"]
    assert any("already publishes port 3000" in w for w in result.warnings)


def test_environment_written_as_a_mapping_is_converted_too() -> None:
    text = """\
services:
  app:
    image: example/app:1
    environment:
      SERVICE_URL_APP_8080:
      URL: $SERVICE_URL_APP
      PASSWORD: $SERVICE_PASSWORD_APP
"""
    result = converted(text)

    environment = document(result)["services"]["app"]["environment"]
    assert "SERVICE_URL_APP_8080" not in environment
    assert environment["SERVICE_URL_APP"] == "${SERVICE_URL_APP}"
    assert environment["URL"] == "${SERVICE_URL_APP}"
    assert result.routed == ("app", 8080)


NAMED_VOLUMES = """\
services:
  app:
    image: example/app:1
    environment:
      - SERVICE_URL_APP_80
    volumes:
      - app-data:/data
      - app-cache:/cache:ro
      - type: volume
        source: app-long
        target: /long
      - type: bind
        source: ./config
        target: /config
      - ./relative:/relative
      - /srv/absolute:/absolute
      - ${APP_PATH}:/from-variable
      - /anonymous
  db:
    image: example/db:1
    volumes:
      - db-data:/var/lib/db
      - shared:/shared
volumes:
  shared:
    external: true
"""


def test_the_named_volumes_a_template_never_declares_are_declared_for_it() -> None:
    """
    Coolify declares the named volumes a service mentions; Compose refuses a project that
    refers to one nobody declared ("service refers to undefined volume"). 340 of the 345
    templates of the catalogue that use a named volume leave the declaration out, so a document
    converted without it failed to deploy in nearly every case. Found by deploying one.
    """
    result = converted(NAMED_VOLUMES)

    declared = document(result)["volumes"]
    assert set(declared) == {"app-data", "app-cache", "app-long", "db-data", "shared"}
    assert declared["shared"] == {"external": True}  # what the template did declare is left alone
    assert declared["app-data"] in ({}, None)
    assert any("declared 4 named volume" in note for note in result.adjustments)


def test_a_service_with_no_restart_policy_gets_the_one_coolify_gives_every_service() -> None:
    """
    Coolify's parser gives every service without a ``restart`` key ``unless-stopped``, and the
    templates lean on it: listmonk's application starts beside the one-off service that sets its
    database up, finds nothing, and exits; with no restart policy it stayed dead behind a 502.
    Found by deploying it.
    """
    text = """\
services:
  app:
    image: example/app:1
    environment:
      - SERVICE_URL_APP_80
  setup:
    image: example/app:1
    restart: "no"
  worker:
    image: example/worker:1
    restart: always
"""
    result = converted(text)

    services = document(result)["services"]
    assert services["app"]["restart"] == "unless-stopped"
    assert services["setup"]["restart"] == "no"  # what the template says is what it means
    assert services["worker"]["restart"] == "always"
    assert any(
        "restart: unless-stopped" in note and "1 service" in note for note in result.adjustments
    )


def test_a_template_whose_services_all_say_how_they_restart_is_not_told_so() -> None:
    text = (
        "services:\n  app:\n    image: example/app:1\n    restart: always\n"
        "    environment:\n      - SERVICE_URL_APP_80\n"
    )

    assert not any("restart" in note for note in converted(text).adjustments)


def test_what_is_not_a_named_volume_is_not_declared() -> None:
    result = converted(NAMED_VOLUMES)

    declared = set(document(result)["volumes"])
    for not_a_volume in ("config", "relative", "absolute", "anonymous", "APP_PATH", "${APP_PATH}"):
        assert not any(not_a_volume in name for name in declared)


def test_a_template_that_declares_all_it_uses_is_left_as_it_is() -> None:
    result = converted()  # BLOG declares blog-content and blog-mysql itself

    assert not any("named volume" in note for note in result.adjustments)
    assert set(document(result)["volumes"]) == {"blog-content", "blog-mysql"}


def test_a_template_without_any_volume_gets_no_volumes_section() -> None:
    text = "services:\n  app:\n    image: example/app:1\n    environment:\n      - SERVICE_URL_APP_80\n"

    assert "volumes" not in document(converted(text))


SHARED_DEFAULT = """\
services:
  stats:
    image: example/stats:3
    environment:
      - SERVICE_URL_STATS_3000
      - DATABASE_URL=postgres://$SERVICE_USER_PG:$SERVICE_PASSWORD_PG@db:5432/$STATS_DB
      - MODE=${STATS_MODE:-production}
      - MODE_AGAIN=${STATS_MODE}
      - GREETING=${GREETING:-hello world}
      - GREETING_AGAIN=${GREETING}
      - ONLY_DEFAULTED=${ONLY_DEFAULTED:-kept-where-it-is}
      - ONLY_PLAIN=${ONLY_PLAIN}
    depends_on:
      db:
        condition: service_healthy
  db:
    image: postgres:16
    environment:
      - POSTGRES_USER=$SERVICE_USER_PG
      - POSTGRES_PASSWORD=$SERVICE_PASSWORD_PG
      - POSTGRES_DB=${STATS_DB:-stats}
"""


def test_a_default_one_service_declares_is_the_value_another_service_reads_plainly() -> None:
    """
    Umami's template builds its ``DATABASE_URL`` with ``$POSTGRES_DB`` and gives the database
    service ``${POSTGRES_DB:-umami}``. Coolify turns the default into the variable's value; read
    as plain Compose the application connected to a database named after its user and died,
    found by deploying it.
    """
    result = converted(SHARED_DEFAULT)

    assert result.values["STATS_DB"] == "stats"
    assert result.values["STATS_MODE"] == "production"
    made = {v.name: v for v in result.variables if v.origin == "template"}
    assert set(made) == {"STATS_DB", "STATS_MODE"}
    assert not made["STATS_DB"].secret and "stats" in made["STATS_DB"].note


def test_a_default_that_cannot_be_written_to_an_env_file_is_left_to_the_operator() -> None:
    result = converted(SHARED_DEFAULT)

    assert "GREETING" not in result.values  # "hello world" has a space
    asked = {v.name for v in result.variables if v.origin == "ask"}
    assert "GREETING" in asked
    assert any("GREETING" in warning for warning in result.warnings)


def test_a_variable_only_read_with_its_default_or_only_plainly_is_not_given_a_value() -> None:
    result = converted(SHARED_DEFAULT)

    assert "ONLY_DEFAULTED" not in result.values and "ONLY_PLAIN" not in result.values


def test_the_values_file_carries_the_defaults_it_made() -> None:
    result = converted(SHARED_DEFAULT)
    written = dict(
        line.split("=", 1) for line in values_file(result.values).splitlines() if "=" in line
    )

    assert written["STATS_DB"] == "stats" and "GREETING" not in written


def test_a_file_mounted_from_inline_content_is_a_warning_not_a_guess() -> None:
    text = """\
services:
  app:
    image: example/app:1
    environment:
      - SERVICE_URL_APP_80
    volumes:
      - type: bind
        source: ./entrypoint.sh
        target: /entrypoint.sh
        content: |
          #!/bin/sh
          exec app
"""
    result = converted(text)

    assert any("/entrypoint.sh carries inline file content" in w for w in result.warnings)


# -- what comes out is judged like any pasted document -----------------------------------------------------


def test_the_converted_document_is_deployable_and_its_web_is_the_routed_service() -> None:
    result = converted()

    analysis = analyze_document(result.document)

    assert analysis.deployable and analysis.web == ["blog"]
    read = {v.name: v for v in analysis.variables}
    assert read["MYSQL_DATABASE"].default == "blog" and read["PORT"].default == "2368"
    assert set(result.values) <= set(read)
    # Every variable the template left without a default has a value here or is said to be asked.
    needed = {name for name, v in read.items() if v.required}
    assert needed <= set(result.values), needed - set(result.values)


def test_a_template_that_mounts_the_docker_socket_is_refused_like_any_compose_file() -> None:
    text = """\
services:
  agent:
    image: example/agent:1
    environment:
      - SERVICE_URL_AGENT_9000
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
"""
    analysis = analyze_document(converted(text).document)

    assert not analysis.deployable
    assert any("Docker socket" in problem for problem in analysis.refused)


# -- what is refused before anything is read ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["", "just: a mapping", "- a\n- list\n", "services: [a, b]\n", "services: {", "a: [1\n"]
)
def test_what_is_not_a_compose_template_is_refused(text: str) -> None:
    with pytest.raises(ValidationError) as raised:
        convert(text)

    assert raised.value.field == "document"


def test_a_template_that_is_far_too_large_is_refused() -> None:
    with pytest.raises(ValidationError):
        convert("services:\n  a:\n    image: x\n# " + "x" * MAX_TEMPLATE_BYTES)


def test_the_yaml_is_read_safely() -> None:
    with pytest.raises(ValidationError):
        convert("services: !!python/object/apply:os.system ['echo no']\n")


# -- the values file ----------------------------------------------------------------------------------------


def test_the_values_are_written_as_an_env_file() -> None:
    text = values_file({"A": "abc", "B": "https://x.example.com"})

    assert text == "A=abc\nB=https://x.example.com\n"


@pytest.mark.parametrize("value", ["a b", "a\nb", 'a"b', "a$b", "a`b", "a\\b", "a'b"])
def test_a_value_an_env_line_cannot_carry_is_refused(value: str) -> None:
    with pytest.raises(ValidationError):
        values_file({"A": value})


def test_what_the_generators_make_always_fits_an_env_file() -> None:
    result = converted()

    values_file(result.values)
