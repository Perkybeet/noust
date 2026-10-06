# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The closed schema of recipe version 2: what loads, and every way it refuses."""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest
import yaml

from noust.recipes import RecipeError, parse_recipe
from noust.recipes.model import CATEGORIES
from tests.recipes.factory import (
    DIGEST,
    compose_document,
    compose_recipe,
    native_recipe,
    use_demo_assets,
)


@pytest.fixture(autouse=True)
def demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the recipe named ``demo`` use the logo of a shipped recipe as its own."""
    use_demo_assets(monkeypatch)


def refused(data: dict[str, Any], message: str) -> RecipeError:
    """Parse ``data`` and check that it is refused with ``message``."""
    with pytest.raises(RecipeError, match=re.escape(message)) as failure:
        parse_recipe("demo", data)
    assert "src/noust/recipes/demo.yaml" in failure.value.details
    return failure.value


# ---------------------------------------------------------------------------
# What loads
# ---------------------------------------------------------------------------


def test_a_minimal_compose_recipe_is_valid() -> None:
    """Kind compose: the document, its images and its web port are read."""
    recipe = parse_recipe("demo", compose_recipe())

    assert (recipe.kind, recipe.app_type, recipe.layout) == ("compose", "docker-compose", "inplace")
    assert recipe.runs_in_docker is True
    assert [(image.repository, image.tag, image.digest) for image in recipe.images] == [
        ("ghcr.io/o/app", "1.2.3", DIGEST)
    ]
    assert recipe.digest_verified is True
    assert recipe.summary()["runs_in_docker"] is True
    assert recipe.variable("SECRET") is not None and recipe.variable("SECRET").secret is True


def test_a_minimal_native_recipe_is_valid() -> None:
    """Kind native: a pinned git source and the deployer that builds it."""
    recipe = parse_recipe("demo", native_recipe())

    assert (recipe.kind, recipe.app_type, recipe.layout) == ("native", "nodejs", "releases")
    assert recipe.runs_in_docker is False
    assert recipe.source is not None and recipe.source.ref == "v1.2.3"
    assert recipe.summary()["digest_verified"] is None


def test_the_catalogue_fields_are_carried_to_the_summary() -> None:
    """Title, slogan, category, tags, version, date and the three links."""
    recipe = parse_recipe(
        "demo",
        compose_recipe(
            docs="https://example.com/docs",
            repository="https://github.com/o/r",
            license="AGPL-3.0-only",
            revision=3,
            changelog=[{"version": "1.2.3", "summary": "What changed."}],
        ),
    )
    summary = recipe.summary()

    assert summary["slogan"] == "A demo."
    assert summary["category"] == "utilities" and summary["tags"] == ["demo"]
    assert (summary["version"], summary["revision"], summary["updated"]) == (
        "1.2.3",
        3,
        "2026-10-06",
    )
    assert summary["docs"] == "https://example.com/docs"
    assert summary["repository"] == "https://github.com/o/r"
    assert recipe.to_dict()["changelog"] == [{"version": "1.2.3", "summary": "What changed."}]


def test_an_unavailable_recipe_is_listed_with_its_reason_and_nothing_to_deploy() -> None:
    """The listed-only form: metadata and a reason, no version, no source."""
    recipe = parse_recipe(
        "demo",
        {
            "schema": 2,
            "name": "demo",
            "kind": "native",
            "title": "Demo",
            "slogan": "A demo.",
            "description": "A demo recipe.",
            "category": "other",
            "homepage": "https://example.com",
            "available": False,
            "unavailable_reason": "Waiting for something.",
        },
    )

    assert recipe.available is False and recipe.unavailable_reason == "Waiting for something."
    assert recipe.summary()["app_type"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"app_type": "nodejs"},
        {"source": {"git": "https://g/r", "ref": "v1"}},
        {"layout": "inplace"},
    ],
)
def test_an_unavailable_recipe_has_nothing_else(extra: dict[str, Any]) -> None:
    """A recipe that cannot be deployed does not half-define how."""
    data = {
        "schema": 2,
        "name": "demo",
        "title": "Demo",
        "slogan": "A demo.",
        "description": "A demo recipe.",
        "category": "other",
        "homepage": "https://example.com",
        "available": False,
        "unavailable_reason": "Waiting.",
        **extra,
    }

    refused(data, "an unavailable recipe has no")


# ---------------------------------------------------------------------------
# Closed schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"colour": "blue"}, "unknown keys: colour"),
        ({"schema": 3}, "schema must be 2"),
        ({"kind": "docker"}, "kind must be one of native, compose"),
        ({"name": "other"}, "name must be the file's name"),
        ({"category": "Networking"}, "category must be one of"),
        ({"category": None}, "category must be one of"),
        ({"tags": ["a", "a"]}, "each different"),
        ({"tags": ["Not Valid"]}, "must be lower-case letters"),
        ({"tags": [f"t{number}" for number in range(9)]}, "tags are at most 8"),
        ({"slogan": "one\ntwo"}, "slogan must be one line"),
        ({"slogan": "x" * 101}, "slogan must be one line"),
        ({"description": "x" * 401}, "description is longer than 400"),
        ({"version": 1.2}, "version must be a string"),
        ({"version": "has space"}, "version must be a string"),
        ({"revision": 0}, "revision must be a whole number"),
        ({"updated": "yesterday"}, "updated must be a date"),
        ({"updated": None}, "updated must be a date"),
        ({"homepage": "http://example.com"}, "homepage must be an https"),
        ({"docs": "ftp://example.com"}, "docs must be an https"),
        ({"license": "not a licence!"}, "license must be an SPDX"),
        ({"logo": None}, "logo is required"),
        ({"logo": "missing.svg"}, "is not in assets/demo/"),
        ({"logo": "../logo.svg"}, "is not a plain relative path"),
        ({"port": 80}, "port must be a number from 1024 to 65535"),
        ({"health": {"path": "healthz"}}, "health.path must start with /"),
        ({"health": {"timeout": 1}}, "out of range"),
        ({"health": {"colour": 1}}, "health takes path, expect and timeout"),
        ({"changelog": "x"}, "changelog is a list"),
        ({"changelog": [{"version": "1"}]}, "needs a version and a summary"),
        ({"app_type": "nodejs"}, "unknown keys: app_type"),
        ({"source": {"git": "https://g/r", "ref": "v1"}}, "unknown keys: source"),
        ({"notes": ["{{ unclosed"]}, "not a valid template"),
    ],
)
def test_the_loader_refuses_what_it_does_not_understand(
    overrides: dict[str, Any], message: str
) -> None:
    """Every refusal names the problem and the file."""
    refused(compose_recipe(**overrides), message)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"compose": "services: {}"}, "unknown keys: compose"),
        ({"digest_verified": True}, "unknown keys: digest_verified"),
        ({"public_ports": []}, "unknown keys: public_ports"),
        ({"app_type": "cobol"}, "app_type 'cobol'"),
        ({"app_type": "docker-compose"}, "is kind: compose, not native"),
        ({"layout": "sideways"}, "layout must be one of"),
        ({"source": {"git": "https://github.com/o/r"}}, "fixed ref"),
        ({"source": {"archive": "https://h/a.tar.gz"}}, "exactly one of sha256"),
        ({"source": {"archive": "https://h/a.tar.gz", "sha256": "xyz"}}, "64 hexadecimal"),
        ({"build": {"colour": "x"}}, "unknown build keys: colour"),
        ({"build": {}}, "at least one key"),
        ({"build": {"start": "node a.js && node b.js"}}, "never through a shell"),
        ({"build": {"start": "node a.js | tee log"}}, "never through a shell"),
        ({"build": {"start": "node 'unclosed"}}, "not a command line"),
        ({"build": {"install": ""}}, "non-empty string"),
        ({"build": {"base_directory": "../x"}}, "plain relative path"),
        ({"build": {"runtime": "node22"}}, "node@22 or python@3.12"),
    ],
)
def test_the_native_half_of_the_schema_is_closed_too(
    overrides: dict[str, Any], message: str
) -> None:
    """A native recipe may not carry a compose recipe's keys, and its blocks are checked."""
    refused(native_recipe(**overrides), message)


def test_every_category_of_the_closed_list_is_accepted() -> None:
    """The list is closed, not unusable."""
    for category in CATEGORIES:
        assert parse_recipe("demo", compose_recipe(category=category)).category == category


def test_the_build_block_has_the_shape_of_the_native_build_lane() -> None:
    """install, build, start, base_directory, publish_directory and runtime."""
    recipe = parse_recipe(
        "demo",
        native_recipe(
            build={
                "base_directory": "app",
                "install": "npm ci",
                "build": "npm run build",
                "start": "node dist/server.js --port 8080",
                "publish_directory": "dist",
                "runtime": "node@22",
            }
        ),
    )

    assert recipe.build is not None
    assert recipe.build.to_dict() == {
        "base_directory": "app",
        "install": "npm ci",
        "build": "npm run build",
        "start": "node dist/server.js --port 8080",
        "publish_directory": "dist",
        "runtime": "node@22",
    }


# ---------------------------------------------------------------------------
# requires
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requires", "message"),
    [
        (["oracle"], "requires oracle"),
        (["postgres", "postgres"], "the same service twice"),
        (["postgres", "mysql"], "one SQL database"),
        (["redis", "mariadb", "mysql"], "one SQL database"),
    ],
)
def test_requires_is_a_closed_list(requires: list[str], message: str) -> None:
    """Only the engines the managers know, and one SQL database."""
    refused(native_recipe(requires=requires), message)


@pytest.mark.parametrize("requires", [["postgres"], ["redis"], ["mongodb"]])
def test_a_compose_recipe_cannot_use_the_servers_databases(requires: list[str]) -> None:
    """Its containers cannot reach 127.0.0.1 of the host: the database goes in the stack."""
    refused(compose_recipe(requires=requires), "cannot use the server's databases")


def test_a_compose_recipe_may_ask_for_s3() -> None:
    """S3 is an external endpoint the operator names, reachable from a container."""
    recipe = parse_recipe(
        "demo",
        compose_recipe(
            requires=["s3"],
            variables={
                "SECRET": {"generate": "password32"},
                "S3_ENDPOINT": {"from": "s3.endpoint"},
                "S3_SECRET": {"from": "s3.secret_key"},
            },
            compose=compose_document(
                **{
                    'SECRET: "${SECRET}"': 'SECRET: "${SECRET}"\n      E: "${S3_ENDPOINT}"\n      K: "${S3_SECRET}"'
                }
            ),
        ),
    )

    assert recipe.requires == ("s3",)
    endpoint, secret = recipe.variable("S3_ENDPOINT"), recipe.variable("S3_SECRET")
    assert endpoint is not None and endpoint.is_required and endpoint.secret is False
    assert secret is not None and secret.is_required and secret.secret is True


@pytest.mark.parametrize("engine", ["postgres", "mysql", "mariadb", "redis", "valkey", "mongodb"])
def test_a_native_recipe_can_require_each_engine(engine: str) -> None:
    """Every engine the existing managers provision, and a variable from it."""
    recipe = parse_recipe(
        "demo",
        native_recipe(
            requires=[engine],
            variables={"DATABASE_URL": {"from": f"{engine}.url"}},
        ),
    )

    assert recipe.requires == (engine,)
    variable = recipe.variable("DATABASE_URL")
    assert variable is not None and variable.secret is True and variable.origin() == "provisioned"


def test_a_variable_cannot_come_from_a_service_the_recipe_did_not_ask_for() -> None:
    """from: postgres.url needs requires: [postgres]."""
    refused(
        native_recipe(variables={"DATABASE_URL": {"from": "postgres.url"}}),
        "does not require postgres",
    )


# ---------------------------------------------------------------------------
# variables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        ({"1BAD": {"default": "x"}}, "not a usable name"),
        ({"PORT": {"default": "1"}}, "Noust provides PORT itself"),
        ({"A": "x"}, "must be a mapping"),
        ({"A": {}}, "needs exactly one of generate, from, value, default or required"),
        ({"A": {"default": "x", "generate": "hex32"}}, "needs exactly one of"),
        ({"A": {"colour": "x", "default": "x"}}, "unknown keys: colour"),
        ({"A": {"generate": "rot13"}}, "generate must be one of"),
        ({"A": {"generate": "jwt"}}, "a jwt needs signed_with and role"),
        ({"A": {"generate": "jwt", "signed_with": "B", "role": "anon"}}, "not declared"),
        (
            {"A": {"generate": "hex32", "role": "anon"}},
            "signed_with and role are for generate: jwt",
        ),
        ({"A": {"from": "nowhere"}}, "from must be one of"),
        ({"A": {"from": "url", "service": 3}}, "service must be a service name"),
        ({"A": {"default": "x", "service": "web"}}, "service and port are for from: url"),
        ({"A": {"default": 3}}, "default must be a string"),
        ({"A": {"value": 3}}, "value must be a string"),
        ({"A": {"required": False}}, "required is true, or leave it out"),
        ({"A": {"default": "x", "secret": "yes"}}, "secret must be true or false"),
        ({"A": {"default": "x", "description": "two\nlines"}}, "one line"),
        ({"A": {"value": "${B}"}, "B": {"value": "${A}"}}, "in a circle"),
        ({"A": {"value": "${NOPE}"}}, "does not declare"),
        ({"A": {"value": "$HOME"}}, "bare $"),
        ({"A": {"value": "${unclosed"}}, "not closed"),
    ],
)
def test_variables_are_a_closed_vocabulary(variables: dict[str, Any], message: str) -> None:
    """Exactly one origin per variable, from a closed list, and nothing dangling."""
    refused(native_recipe(variables=variables), message)


def test_secrecy_follows_how_a_value_is_made() -> None:
    """Generated secrets, provisioned URLs and what is composed from them are secret."""
    recipe = parse_recipe(
        "demo",
        native_recipe(
            requires=["postgres"],
            variables={
                "A_USER": {"generate": "user"},
                "A_PASS": {"generate": "password32"},
                "A_HOST": {"from": "postgres.host"},
                "A_URL": {"from": "postgres.url"},
                "A_URLISH": {"value": "${A_PASS}@x"},
                "A_PLAIN": {"value": "${A_HOST}:5432"},
                "A_FORCED": {"default": "v", "secret": True},
                "A_DOMAIN": {"from": "fqdn"},
            },
        ),
    )

    secret = {variable.name: variable.secret for variable in recipe.variables}
    assert secret == {
        "A_USER": False,
        "A_PASS": True,
        "A_HOST": False,
        "A_URL": True,
        "A_URLISH": True,
        "A_PLAIN": False,
        "A_FORCED": True,
        "A_DOMAIN": False,
    }


def test_a_jwt_is_signed_with_a_declared_variable() -> None:
    """The jwt pair of a stack: two tokens, one shared secret."""
    recipe = parse_recipe(
        "demo",
        native_recipe(
            variables={
                "JWT_SECRET": {"generate": "password64"},
                "ANON_KEY": {"generate": "jwt", "signed_with": "JWT_SECRET", "role": "anon"},
                "SERVICE_KEY": {
                    "generate": "jwt",
                    "signed_with": "JWT_SECRET",
                    "role": "service_role",
                },
            }
        ),
    )

    assert [variable.role for variable in recipe.variables if variable.generate == "jwt"] == [
        "anon",
        "service_role",
    ]


def test_url_and_fqdn_may_name_the_service_and_port_nginx_routes_to() -> None:
    """Per service and port: the one web endpoint this release routes."""
    document = compose_document(
        **{'SECRET: "${SECRET}"': 'SECRET: "${SECRET}"\n      U: "${PUBLIC}"'}
    )
    ok = compose_recipe(
        variables={
            "SECRET": {"generate": "hex32"},
            "PUBLIC": {"from": "url", "service": "web", "port": 8080},
        },
        compose=document,
    )
    assert parse_recipe("demo", ok).variable("PUBLIC") is not None

    wrong_port = copy.deepcopy(ok)
    wrong_port["variables"]["PUBLIC"]["port"] = 9999
    refused(wrong_port, "routes one web endpoint (web:8080)")

    wrong_service = copy.deepcopy(ok)
    wrong_service["variables"]["PUBLIC"]["service"] = "db"
    refused(wrong_service, "routes one web endpoint (web:8080)")


# ---------------------------------------------------------------------------
# The Compose document
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (f"@{DIGEST}", "", "has no digest"),
        (f":1.2.3@{DIGEST}", f"@{DIGEST}", "has no tag"),
        (f":1.2.3@{DIGEST}", f":latest@{DIGEST}", "moves; pin a release"),
        (f":1.2.3@{DIGEST}", f":stable@{DIGEST}", "moves; pin a release"),
        (f":1.2.3@{DIGEST}", ":1.2.3@sha256:short", "is not an image reference"),
        ("ghcr.io/o/app", "Not/Valid/UPPER", "is not an image reference"),
        ("image: ghcr.io/o/app:1.2.3", "image: ${IMAGE}", "fixed reference"),
        ("restart: unless-stopped", "restart: always", "needs restart: unless-stopped"),
        ('"127.0.0.1:${PORT}:8080"', '"8080:8080"', "port '8080:8080' is not allowed"),
        ('"127.0.0.1:${PORT}:8080"', '"0.0.0.0:${PORT}:8080"', "is not allowed"),
        ('"127.0.0.1:${PORT}:8080"', '"127.0.0.1:9000:8080"', "is not allowed"),
        (
            '"127.0.0.1:${PORT}:8080"',
            '"127.0.0.1:${PORT}:8080"\n      - "127.0.0.1:${PORT}:9090"',
            "found 2",
        ),
        ("- data:/data", "- /var/lib/data:/data", "must be a declared named volume"),
        ("- data:/data", "- ./../x:/data", "mounts a file the recipe does not ship"),
        ("- data:/data", "- other:/data", "volume other is not declared"),
        (
            "volumes:\n  data:",
            "volumes:\n  data:\n  extra:",
            "volumes extra are declared and not used",
        ),
        (
            "volumes:\n  data:",
            "volumes:\n  data:\n    driver: nfs",
            "must be declared with no options",
        ),
        ("restart: unless-stopped", "restart: unless-stopped\n    privileged: true", "may not"),
        ("restart: unless-stopped", "restart: unless-stopped\n    network_mode: host", "may not"),
        ("restart: unless-stopped", "restart: unless-stopped\n    cap_add: [SYS_ADMIN]", "may not"),
        ("restart: unless-stopped", "restart: unless-stopped\n    devices: [/dev/fuse]", "may not"),
        ("restart: unless-stopped", "restart: unless-stopped\n    build: .", "may not"),
        ("restart: unless-stopped", "restart: unless-stopped\n    env_file: .env", "may not"),
        (
            "restart: unless-stopped",
            "restart: unless-stopped\n    security_opt: [seccomp:unconfined]",
            "security_opt",
        ),
        (
            "restart: unless-stopped",
            "restart: unless-stopped\n    depends_on: [ghost]",
            "depends on ghost",
        ),
        ('SECRET: "${SECRET}"', 'SECRET: "${SECRET} $HOME"', "bare $"),
        ('SECRET: "${SECRET}"', 'SECRET: "${SECRET} ${MISSING}"', "does not declare"),
    ],
)
def test_the_compose_document_is_held_to_what_an_untrusted_one_is(
    old: str, new: str, message: str
) -> None:
    """Pinned images, loopback ports, named volumes, no root on the host."""
    text = compose_document()
    assert old in text, old
    refused(compose_recipe(compose=text.replace(old, new)), message)


def test_the_document_may_mount_a_docker_socket_never() -> None:
    """The guard's own refusal is not weakened by the closed schema."""
    text = compose_document().replace("- data:/data", "- /var/run/docker.sock:/var/run/docker.sock")

    refused(compose_recipe(compose=text), "must be a declared named volume")


def test_the_top_level_is_services_and_volumes_only() -> None:
    """No networks, no secrets, no name."""
    refused(
        compose_recipe(compose=compose_document() + "networks:\n  x: {}\n"),
        "only services and volumes",
    )
    refused(compose_recipe(compose="not: [valid"), "not valid YAML")
    refused(compose_recipe(compose="services: {}"), "has no services")
    refused(compose_recipe(compose=""), "must hold a Docker Compose document")
    refused(compose_recipe(compose="x" * 60_000), "larger than")


def test_tag_only_images_are_allowed_when_the_recipe_says_so() -> None:
    """digest_verified: false is the honest form of a pin that could not be read."""
    text = compose_document().replace(f"@{DIGEST}", "")
    recipe = parse_recipe("demo", compose_recipe(compose=text, digest_verified=False))

    assert recipe.digest_verified is False
    assert recipe.images[0].digest is None
    assert recipe.summary()["digest_verified"] is False
    refused(
        compose_recipe(compose=text, digest_verified="no"), "digest_verified must be true or false"
    )


def test_public_ports_must_be_declared_and_used() -> None:
    """What a stack opens to the network is said in the recipe, and matches the document."""
    text = compose_document().replace(
        '- "127.0.0.1:${PORT}:8080"', '- "127.0.0.1:${PORT}:8080"\n      - "22000:22000/tcp"'
    )
    ports = [{"port": 22000, "protocol": "tcp", "purpose": "Sync."}]

    recipe = parse_recipe("demo", compose_recipe(compose=text, public_ports=ports))
    assert [(entry.port, entry.protocol) for entry in recipe.public_ports] == [(22000, "tcp")]

    refused(compose_recipe(compose=text), "port '22000:22000/tcp' is not allowed")
    refused(
        compose_recipe(public_ports=ports),
        "public_ports declares 22000/tcp, which no service publishes",
    )
    refused(
        compose_recipe(compose=text, public_ports=[{"port": 22000, "protocol": "tcp"}]),
        "needs port, protocol and purpose",
    )
    refused(
        compose_recipe(
            compose=text, public_ports=[{"port": 22000, "protocol": "icmp", "purpose": "x"}]
        ),
        "is not a port, tcp or udp",
    )


def test_a_loopback_port_may_come_from_a_variable_with_a_numeric_default() -> None:
    """Mailpit's SMTP: 127.0.0.1 only, the port the operator's."""
    text = compose_document().replace(
        '- "127.0.0.1:${PORT}:8080"',
        '- "127.0.0.1:${PORT}:8080"\n      - "127.0.0.1:${SMTP_PORT}:1025"',
    )
    variables = {"SECRET": {"generate": "hex32"}, "SMTP_PORT": {"default": "1025"}}

    assert parse_recipe("demo", compose_recipe(compose=text, variables=variables)) is not None
    refused(
        compose_recipe(compose=text, variables={**variables, "SMTP_PORT": {"default": "twenty"}}),
        "needs a default from 1024 to 65535",
    )
    refused(
        compose_recipe(compose=text, variables={"SECRET": {"generate": "hex32"}}),
        "does not declare",
    )


def test_a_mounted_host_directory_is_the_operators_through_a_variable_inside_the_app() -> None:
    """${MEDIA_PATH}:/media, defaulting to ./media: nothing outside the application by default."""
    text = compose_document().replace(
        "- data:/data", '- data:/data\n      - "${MEDIA_PATH}:/media:ro"'
    )
    variables = {"SECRET": {"generate": "hex32"}, "MEDIA_PATH": {"default": "./media"}}

    recipe = parse_recipe("demo", compose_recipe(compose=text, variables=variables))
    assert recipe.variable("MEDIA_PATH") is not None

    for bad in ("/srv/media", "../media", "./a/../b"):
        refused(
            compose_recipe(compose=text, variables={**variables, "MEDIA_PATH": {"default": bad}}),
            "needs a default inside the application",
        )


def test_files_shipped_beside_the_document_can_be_mounted() -> None:
    """A ./path the recipe ships; a path it does not ship is refused."""
    text = compose_document().replace(
        "- data:/data", "- data:/data\n      - ./conf/app.conf:/etc/app.conf:ro"
    )

    refused(compose_recipe(compose=text), "mounts a file the recipe does not ship")
    refused(
        compose_recipe(compose=text, files={"conf/app.conf": "nothing.conf"}),
        "is not in assets/demo/",
    )


def test_variables_the_document_declares_by_how_it_uses_them() -> None:
    """${X:-default} is an optional setting, ${X:?why} one the operator must give."""
    text = compose_document().replace(
        'SECRET: "${SECRET}"',
        'SECRET: "${SECRET}"\n      MODE: "${MODE:-fast}"\n      LICENSE: "${LICENSE:?Your licence key}"',
    )

    recipe = parse_recipe("demo", compose_recipe(compose=text))

    mode, licence = recipe.variable("MODE"), recipe.variable("LICENSE")
    assert mode is not None and mode.default == "fast" and mode.is_input and not mode.is_required
    assert licence is not None and licence.required and licence.description == "Your licence key"
    clash = text.replace(
        'MODE: "${MODE:-fast}"', 'MODE: "${MODE:-slow}"\n      OTHER: "${MODE:-fast}"'
    )
    refused(compose_recipe(compose=clash), "two different defaults or demands")


def test_a_declared_variable_the_document_never_uses_is_refused() -> None:
    """Dead configuration is a typo waiting to be an incident."""
    refused(
        compose_recipe(variables={"SECRET": {"generate": "hex32"}, "UNUSED": {"default": "x"}}),
        "declared and the compose document never uses them",
    )


def test_a_variable_used_only_to_make_another_is_used() -> None:
    """A value built from it, or a jwt signed with it, counts as a use."""
    text = compose_document().replace('SECRET: "${SECRET}"', 'SECRET: "${COMPOSED}"')
    variables = {
        "SECRET": {"generate": "password64"},
        "COMPOSED": {"value": "${SECRET}-x"},
    }

    assert parse_recipe("demo", compose_recipe(compose=text, variables=variables)) is not None


def test_the_yaml_of_a_recipe_file_is_plain_data() -> None:
    """A recipe is parsed with safe_load: tags that build objects never run."""
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load("!!python/object/apply:os.system ['true']")
