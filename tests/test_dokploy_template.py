# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Dokploy template turned into a Compose document Noust deploys (checklist D-26).

Dokploy's catalogue is a directory per application with a ``docker-compose.yml`` and a
``template.toml`` (https://github.com/Dokploy/templates, MIT). The converter reads both and writes
the Compose document, its ``.env`` and the files the template mounts. These tests pin what
Dokploy's own processor (``packages/server/src/templates``) does with the expressions in a
template, which of that Noust keeps, and which paths and values it refuses to write: a template is
untrusted input.
"""

from __future__ import annotations

import base64
import json
import re

import pytest
import yaml

from noust.core.exceptions import ValidationError
from noust.deployers.compose_paste import analyze_document
from noust.recipes import dokploy
from noust.recipes.dokploy import convert

GHOST_COMPOSE = """\
services:
  ghost:
    image: ghost:6-alpine
    restart: always
    environment:
      database__client: mysql
      database__connection__host: db
      database__connection__user: root
      database__connection__password: ${DB_PASSWORD}
      database__connection__database: ghost
      url: http://${GHOST_HOST}
    volumes:
      - ghost:/var/lib/ghost/content

  db:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_PASSWORD}
    volumes:
      - db:/var/lib/mysql

volumes:
  ghost:
  db:
"""

GHOST_TEMPLATE = """\
[variables]
main_domain = "${domain}"
db_password = "${password:24}"

[config]
env = ["GHOST_HOST=${main_domain}", "DB_PASSWORD=${db_password}"]
mounts = []

[[config.domains]]
serviceName = "ghost"
port = 2_368
host = "${main_domain}"
"""


def document(result: dokploy.DokployImport) -> dict:
    loaded = yaml.safe_load(result.document)
    assert isinstance(loaded, dict)
    return loaded


# ---------------------------------------------------------------------------
# The common case
# ---------------------------------------------------------------------------


def test_the_service_the_template_names_is_published_on_the_loopback_address() -> None:
    result = convert(GHOST_COMPOSE, GHOST_TEMPLATE, domain="blog.example.com", port=3021)

    assert result.routed == ("ghost", 2368)
    assert document(result)["services"]["ghost"]["ports"] == ["127.0.0.1:${PORT:-3021}:2368"]
    assert "ports" not in document(result)["services"]["db"]


def test_the_variables_of_the_template_become_the_env_file() -> None:
    result = convert(GHOST_COMPOSE, GHOST_TEMPLATE, domain="blog.example.com", port=3021)

    assert result.values["GHOST_HOST"] == "blog.example.com"
    assert re.fullmatch(r"[a-z0-9]{24}", result.values["DB_PASSWORD"])
    assert (
        result.values["DB_PASSWORD"]
        != convert(GHOST_COMPOSE, GHOST_TEMPLATE, domain="x.io").values["DB_PASSWORD"]
    )


def test_a_password_is_a_secret_and_an_address_is_not() -> None:
    result = convert(GHOST_COMPOSE, GHOST_TEMPLATE, domain="blog.example.com")
    by_name = {v.name: v for v in result.variables}

    assert by_name["DB_PASSWORD"].secret is True
    assert by_name["GHOST_HOST"].secret is False
    assert by_name["DB_PASSWORD"].origin == "generated"
    assert by_name["GHOST_HOST"].origin == "domain"


def test_what_compose_needs_and_dokploy_did_is_done() -> None:
    result = convert(GHOST_COMPOSE, GHOST_TEMPLATE, domain="blog.example.com")
    services = document(result)["services"]

    assert services["db"]["restart"] == "unless-stopped"
    assert services["ghost"]["restart"] == "always"
    assert set(document(result)["volumes"]) == {"ghost", "db"}


def test_the_document_says_where_it_came_from_and_under_which_licence() -> None:
    result = convert(GHOST_COMPOSE, GHOST_TEMPLATE, domain="blog.example.com")

    assert result.document.startswith("# Converted by Noust from a Dokploy template.")
    assert "MIT" in result.document.split("\n\n", 1)[0]
    assert "https://github.com/Dokploy/templates" in result.document.split("\n\n", 1)[0]


def test_the_converted_document_is_one_noust_deploys() -> None:
    result = convert(GHOST_COMPOSE, GHOST_TEMPLATE, domain="blog.example.com", port=3021)

    found = analyze_document(result.document)

    assert found.deployable, found.refused


def test_a_template_without_a_toml_is_the_compose_file_and_says_what_it_lacks() -> None:
    result = convert(GHOST_COMPOSE, None)

    assert result.routed is None
    assert any("no template.toml" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# The expressions Dokploy's processor knows
# ---------------------------------------------------------------------------


def expand(expression: str, *, extra: str = "") -> str:
    template = (
        f'[variables]\nvalue = "{expression}"\n{extra}\n[config]\nenv = ["VALUE=${{value}}"]\n'
    )
    compose = "services:\n  app:\n    image: busybox\n    environment:\n      V: ${VALUE}\n"
    return convert(compose, template, domain="shop.example.com").values["VALUE"]


def test_password_defaults_to_sixteen_lowercase_letters_and_digits() -> None:
    assert re.fullmatch(r"[a-z0-9]{16}", expand("${password}"))


def test_password_with_a_length() -> None:
    assert re.fullmatch(r"[a-z0-9]{64}", expand("${password:64}"))


def test_hash_is_hexadecimal_and_one_digit_per_unit_of_length() -> None:
    assert re.fullmatch(r"[0-9a-f]{8}", expand("${hash}"))
    assert re.fullmatch(r"[0-9a-f]{22}", expand("${hash:22}"))


def test_base64_is_the_text_of_that_many_random_bytes() -> None:
    assert len(base64.b64decode(expand("${base64}"))) == 32
    assert len(base64.b64decode(expand("${base64:48}"))) == 48


def test_uuid() -> None:
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}", expand("${uuid}")
    )


def test_the_domain_is_the_one_the_operator_gave() -> None:
    assert expand("${domain}") == "shop.example.com"


def test_a_username_is_a_lowercase_name() -> None:
    assert re.fullmatch(r"[a-z][a-z0-9]{15}", expand("${username}"))


def test_an_email_is_an_address_on_the_domain_and_it_is_said_to_be_invented() -> None:
    template = '[variables]\nmail = "${email}"\n[config]\nenv = ["ADMIN_EMAIL=${mail}"]\n'
    compose = "services:\n  app:\n    image: busybox\n    environment:\n      E: ${ADMIN_EMAIL}\n"

    result = convert(compose, template, domain="shop.example.com")

    assert result.values["ADMIN_EMAIL"] == "admin@shop.example.com"
    assert any(v.name == "ADMIN_EMAIL" and "invents" in v.note for v in result.variables)


def test_timestamps_are_seconds_and_timestampms_milliseconds() -> None:
    seconds = int(expand("${timestamps}"))
    millis = int(expand("${timestampms}"))

    assert 1_700_000_000 < seconds < 4_000_000_000
    assert 1_700_000_000_000 < millis < 4_000_000_000_000


def test_a_timestamp_of_a_date() -> None:
    assert expand("${timestamps:2030-01-01T00:00:00Z}") == "1893456000"
    assert expand("${timestampms:2030-01-01T00:00:00Z}") == "1893456000000"


def test_a_random_port_is_a_registered_one() -> None:
    assert 20000 <= int(expand("${randomPort}")) <= 60000


def test_variables_are_resolved_in_the_order_the_template_defines_them() -> None:
    template = (
        '[variables]\nfirst = "${hash:6}"\nsecond = "x-${first}"\n'
        '[config]\nenv = ["A=${first}", "B=${second}"]\n'
    )
    compose = (
        "services:\n  app:\n    image: busybox\n    environment:\n      A: ${A}\n      B: ${B}\n"
    )

    result = convert(compose, template, domain="shop.example.com")

    assert result.values["B"] == f"x-{result.values['A']}"


def test_an_expression_nobody_defines_is_left_for_compose() -> None:
    template = '[config]\nenv = ["A=${unknown}"]\n'
    compose = "services:\n  app:\n    image: busybox\n    environment:\n      A: ${A}\n"

    result = convert(compose, template)

    assert result.values["A"] == "${unknown}"
    assert any("unknown" in w and "written as it is" in w for w in result.warnings)
    assert dokploy.env_file(result.values) == "A='${unknown}'\n"


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def decode(token: str) -> tuple[dict, dict]:
    head, payload, _signature = token.split(".")

    def part(text: str) -> dict:
        return json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))

    return part(head), part(payload)


def test_a_jwt_signed_with_a_variable_and_a_payload_variable() -> None:
    template = (
        '[variables]\nsecret = "${password:32}"\n'
        'claims = """{"role": "anon", "iss": "supabase", "exp": ${timestamps:2030-01-01T00:00:00Z}}"""\n'
        'token = "${jwt:secret:claims}"\n'
        '[config]\nenv = ["TOKEN=${token}", "SECRET=${secret}"]\n'
    )
    compose = "services:\n  app:\n    image: busybox\n    environment:\n      T: ${TOKEN}\n      S: ${SECRET}\n"

    result = convert(compose, template, domain="shop.example.com")

    head, payload = decode(result.values["TOKEN"])
    assert head == {"alg": "HS256", "typ": "JWT"}
    assert payload["role"] == "anon"
    assert payload["iss"] == "supabase"
    assert payload["exp"] == 1893456000
    assert "iat" in payload


def test_a_jwt_is_signed_with_the_secret_the_template_made() -> None:
    import hashlib
    import hmac

    template = (
        '[variables]\nsecret = "${password:32}"\nclaims = """{"role": "x"}"""\n'
        'token = "${jwt:secret:claims}"\n[config]\nenv = ["TOKEN=${token}", "SECRET=${secret}"]\n'
    )
    compose = "services:\n  app:\n    image: busybox\n    environment:\n      T: ${TOKEN}\n      S: ${SECRET}\n"

    result = convert(compose, template, domain="shop.example.com")

    head, payload, signature = result.values["TOKEN"].split(".")
    expected = hmac.new(
        result.values["SECRET"].encode(), f"{head}.{payload}".encode(), hashlib.sha256
    ).digest()
    assert signature == base64.urlsafe_b64encode(expected).rstrip(b"=").decode()


def test_jwt_with_a_number_is_that_many_random_bytes_in_hex() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", expand("${jwt:32}"))


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


def test_a_second_domain_on_another_service_is_said_not_routed() -> None:
    template = (
        GHOST_TEMPLATE
        + '\n[[config.domains]]\nserviceName = "db"\nport = 3306\nhost = "${main_domain}"\n'
    )

    result = convert(GHOST_COMPOSE, template, domain="blog.example.com")

    assert result.routed == ("ghost", 2368)
    assert any("db" in w and "noust.nginx.yaml" in w for w in result.warnings)
    assert "ports" not in document(result)["services"]["db"]


def test_a_domain_with_a_path_is_said() -> None:
    template = GHOST_TEMPLATE.replace("port = 2_368", 'port = 2_368\npath = "/admin"')

    result = convert(GHOST_COMPOSE, template, domain="blog.example.com")

    assert any("/admin" in w for w in result.warnings)


def test_a_domain_that_names_a_service_the_compose_file_lacks_is_a_warning() -> None:
    template = GHOST_TEMPLATE.replace('serviceName = "ghost"', 'serviceName = "ghosts"')

    result = convert(GHOST_COMPOSE, template, domain="blog.example.com")

    assert result.routed is None
    assert any("ghosts" in w for w in result.warnings)


def test_a_template_that_builds_addresses_needs_the_domain() -> None:
    with pytest.raises(ValidationError, match="domain"):
        convert(GHOST_COMPOSE, GHOST_TEMPLATE)


def test_a_template_that_never_uses_the_domain_converts_without_one() -> None:
    template = '[config]\nenv = ["A=1"]\n[[config.domains]]\nserviceName = "ghost"\nport = 2368\n'

    result = convert(GHOST_COMPOSE, template)

    assert result.routed == ("ghost", 2368)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def test_the_env_may_be_a_table() -> None:
    template = '[config.env]\nGHOST_HOST = "x.example.com"\nDB_PASSWORD = "${password:12}"\n'

    result = convert(GHOST_COMPOSE, template)

    assert result.values["GHOST_HOST"] == "x.example.com"
    assert re.fullmatch(r"[a-z0-9]{12}", result.values["DB_PASSWORD"])


def test_comments_blank_lines_and_junk_in_the_env_list_are_skipped() -> None:
    template = (
        '[config]\nenv = ["# a comment", "", "A=1", "no equals sign", "B=two words", "1BAD=x"]\n'
    )
    compose = "services:\n  app:\n    image: busybox\n"

    result = convert(compose, template)

    assert result.values == {"A": "1", "B": "two words"}
    assert any("no equals sign" in w for w in result.warnings)
    assert any("1BAD" in w for w in result.warnings)


def test_values_with_spaces_dollars_and_quotes_are_written_so_they_read_back() -> None:
    values = {"A": "two words", "B": "cost $5", "C": "it's", "D": "plain"}

    text = dokploy.env_file(values)

    assert "A='two words'" in text or "A=two words" in text
    assert "B='cost $5'" in text
    assert "D=plain" in text
    assert "C=" in text


def test_a_value_that_cannot_be_written_is_refused() -> None:
    with pytest.raises(ValidationError):
        dokploy.env_file({"A": "line one\nline two"})
    with pytest.raises(ValidationError):
        dokploy.env_file({"A": "it's $5"})


# ---------------------------------------------------------------------------
# Mounted files
# ---------------------------------------------------------------------------


NGINX_COMPOSE = """\
services:
  web:
    image: nginx:1.27
    volumes:
      - ../files/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ../files/README.md:/readme.md
"""

NGINX_TEMPLATE = """\
[variables]
main_domain = "${domain}"
token = "${password:10}"

[[config.domains]]
serviceName = "web"
port = 80
host = "${main_domain}"

[[config.mounts]]
filePath = "/nginx/nginx.conf"
content = \"\"\"
server { server_name ${main_domain}; set $t ${token}; }
\"\"\"

[[config.mounts]]
filePath = "README.md"
content = "hello ${main_domain}"
"""


def test_a_mounted_file_is_written_next_to_the_document_and_expanded() -> None:
    result = convert(NGINX_COMPOSE, NGINX_TEMPLATE, domain="site.example.com")

    assert set(result.files) == {"files/nginx/nginx.conf", "files/README.md"}
    assert "server_name site.example.com;" in result.files["files/nginx/nginx.conf"]
    assert result.files["files/README.md"] == "hello site.example.com"
    assert "set $t " in result.files["files/nginx/nginx.conf"]


def test_the_compose_file_finds_them_where_they_were_written() -> None:
    result = convert(NGINX_COMPOSE, NGINX_TEMPLATE, domain="site.example.com")

    volumes = document(result)["services"]["web"]["volumes"]
    assert volumes == [
        "./files/nginx/nginx.conf:/etc/nginx/nginx.conf:ro",
        "./files/README.md:/readme.md",
    ]


@pytest.mark.parametrize(
    "path",
    ["../escape", "a/../../escape", "/etc/passwd/../../x", "a\\b", "", "a/\x00b", ".", "a/./.."],
)
def test_a_path_that_leaves_the_folder_is_refused(path: str) -> None:
    template = f'[[config.mounts]]\nfilePath = {json.dumps(path)}\ncontent = "x"\n'

    with pytest.raises(ValidationError, match="path"):
        convert("services:\n  a:\n    image: busybox\n", template)


def test_an_absolute_path_is_read_from_the_folder_not_from_the_machine() -> None:
    template = '[[config.mounts]]\nfilePath = "/etc/hosts"\ncontent = "x"\n'

    result = convert("services:\n  a:\n    image: busybox\n", template)

    assert list(result.files) == ["files/etc/hosts"]


def test_a_template_with_too_many_or_too_large_files_is_refused() -> None:
    many = "".join(f'[[config.mounts]]\nfilePath = "f{i}"\ncontent = "x"\n' for i in range(70))
    large = f'[[config.mounts]]\nfilePath = "big"\ncontent = "{"x" * (300 * 1024)}"\n'

    with pytest.raises(ValidationError, match="files"):
        convert("services:\n  a:\n    image: busybox\n", many)
    with pytest.raises(ValidationError, match="large"):
        convert("services:\n  a:\n    image: busybox\n", large)


def test_an_env_file_the_template_mounts_is_found_where_it_was_written() -> None:
    compose = (
        "services:\n  web:\n    image: nginx\n    env_file:\n      - ../files/config/app.env\n"
    )
    template = (
        '[[config.mounts]]\nfilePath = "/config/app.env"\ncontent = "A=1"\n'
        '[[config.domains]]\nserviceName = "web"\nport = 80\n'
    )

    result = convert(compose, template)

    assert document(result)["services"]["web"]["env_file"] == ["./files/config/app.env"]
    assert analyze_document(result.document, provided=result.files).deployable
    assert not analyze_document(result.document).deployable


def test_a_dot_slash_in_a_mounted_path_is_the_same_file() -> None:
    template = '[[config.mounts]]\nfilePath = "./Caddyfile"\ncontent = "x"\n'

    result = convert("services:\n  a:\n    image: busybox\n", template)

    assert list(result.files) == ["files/Caddyfile"]


def test_a_file_the_compose_file_asks_for_and_the_template_lacks_is_a_warning() -> None:
    result = convert(
        NGINX_COMPOSE, '[config]\nenv = []\n[[config.domains]]\nserviceName = "web"\nport = 80\n'
    )

    assert any("files/nginx/nginx.conf" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Dokploy's own things
# ---------------------------------------------------------------------------


def test_the_dokploy_network_is_dropped_and_said() -> None:
    compose = (
        "services:\n  a:\n    image: busybox\n    networks:\n      - dokploy-network\n"
        "networks:\n  dokploy-network:\n    external: true\n"
    )

    result = convert(compose, None)
    loaded = document(result)

    assert "networks" not in loaded
    assert "networks" not in loaded["services"]["a"]
    assert any("dokploy-network" in a for a in result.adjustments)


def test_traefik_labels_are_dropped() -> None:
    compose = "services:\n  a:\n    image: busybox\n    labels:\n      - traefik.enable=true\n      - com.example.keep=1\n"

    loaded = document(convert(compose, None))

    assert loaded["services"]["a"]["labels"] == ["com.example.keep=1"]


def test_isolated_deployment_is_said_to_have_no_equivalent() -> None:
    result = convert(
        GHOST_COMPOSE,
        GHOST_TEMPLATE.replace("[config]", "[config]\nisolated = true"),
        domain="x.io",
    )

    assert any("isolated" in w for w in result.warnings)


def test_a_template_that_mounts_the_docker_socket_is_refused_like_any_compose_file() -> None:
    compose = "services:\n  a:\n    image: busybox\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n"

    found = analyze_document(convert(compose, None).document)

    assert not found.deployable


# ---------------------------------------------------------------------------
# Untrusted input
# ---------------------------------------------------------------------------


def test_a_template_that_is_not_toml_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"template\.toml"):
        convert(GHOST_COMPOSE, "this is [not toml")


def test_a_compose_file_that_is_not_yaml_or_has_no_services_is_refused() -> None:
    with pytest.raises(ValidationError):
        convert("{unclosed", None)
    with pytest.raises(ValidationError, match="services"):
        convert("version: '3'\n", None)


def test_too_large_a_template_is_refused() -> None:
    with pytest.raises(ValidationError, match="large"):
        convert(GHOST_COMPOSE, "# " + "x" * (1100 * 1024))
    with pytest.raises(ValidationError, match="large"):
        convert("# " + "x" * (300 * 1024) + "\nservices: {}\n", None)


def test_a_toml_nested_far_too_deep_is_refused_not_a_crash() -> None:
    with pytest.raises(ValidationError):
        convert(GHOST_COMPOSE, "a = " + "[" * 500 + "]" * 500)


def test_a_variable_name_with_a_shell_in_it_never_reaches_the_env_file() -> None:
    template = '[config]\nenv = ["A;rm -rf /=1", "OK=1"]\n'

    result = convert("services:\n  a:\n    image: busybox\n", template)

    assert result.values == {"OK": "1"}


def test_the_result_is_what_a_second_conversion_of_the_same_text_would_shape() -> None:
    first = convert(GHOST_COMPOSE, GHOST_TEMPLATE, domain="blog.example.com", port=3021)
    second = convert(GHOST_COMPOSE, GHOST_TEMPLATE, domain="blog.example.com", port=3021)

    assert first.document == second.document
    assert set(first.values) == set(second.values)
