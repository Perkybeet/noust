# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
CapRover, read through its API (checklist N-13).

The server is faked at urllib's seam with the answers CapRover's own code gives
(``LoginRouter``, ``AppDefinitionRouter``, ``ApiStatusCodes``, ``AppDefinition.ts``): a login that
exchanges the dashboard's password for a token, an error that comes with HTTP 200 and a ``status`` of
1000 or more, and one ``appDefinitions`` list holding every application, databases included. What
is pinned: the password is used for the login only and is nowhere else, the repository's credentials
the definition carries are never read, a database is found by the service name CapRover gives it,
and what has no equivalent here is said.
"""

from __future__ import annotations

import io
import json
from typing import Any
from urllib.error import HTTPError

import pytest

from noust.core.exceptions import IntegrationError
from noust.deployers.importers import caprover
from noust.deployers.importers.base import Proposal

PASSWORD = "captain42"
SESSION = "tok-123-session"
ROOT = "apps.example.com"


def definition(name: str, **fields: Any) -> dict[str, Any]:
    """An application definition with what CapRover fills in by default."""
    base: dict[str, Any] = {
        "appName": name,
        "description": "",
        "deployedVersion": 1,
        "notExposeAsWebApp": False,
        "hasPersistentData": False,
        "hasDefaultSubDomainSsl": True,
        "containerHttpPort": 80,
        "captainDefinitionRelativeFilePath": "./captain-definition",
        "forceSsl": True,
        "websocketSupport": False,
        "instanceCount": 1,
        "networks": [],
        "customDomain": [],
        "ports": [],
        "volumes": [],
        "envVars": [],
        "versions": [{"version": 1, "deployedImageName": f"img-captain-{name}", "gitHash": "abc"}],
    }
    return {**base, **fields}


SHOP = definition(
    "shop",
    containerHttpPort=3000,
    customDomain=[{"publicDomain": "Shop.Example.com", "hasSsl": True}],
    envVars=[
        {"key": "NODE_ENV", "value": "production"},
        {"key": "DATABASE_URL", "value": "postgres://app:pw@srv-captain--shop-db:5432/shop"},
        {"key": "SESSION_SECRET", "value": "hunter2"},
        {"key": "CACHE", "value": "redis://srv-captain--cache:6379"},
    ],
    volumes=[
        {"containerPath": "/app/uploads", "volumeName": "shop-uploads"},
        {"containerPath": "/var/lib/shop", "hostPath": "/srv/shop"},
    ],
    appPushWebhook={
        "tokenVersion": "v1",
        "pushWebhookToken": "hook-token-xyz",
        "repoInfo": {
            "repo": "github.com/acme/shop",
            "branch": "release",
            "user": "ci-bot",
            "password": "REPO-PASSWORD",
            "sshKey": "-----BEGIN OPENSSH PRIVATE KEY-----\nXXXX",
        },
    },
    instanceCount=3,
    preDeployFunction="async function (a) { return a }",
    customNginxConfig="location /x { return 204; }",
    httpAuth={"user": "staff"},
    networks=["captain-overlay-net", "extra-net"],
    ports=[{"containerPort": 2222, "hostPort": 2222}],
    forceSsl=False,
)

DB = definition(
    "shop-db",
    notExposeAsWebApp=True,
    envVars=[{"key": "POSTGRES_PASSWORD", "value": "pw"}],
    volumes=[{"containerPath": "/var/lib/postgresql/data", "volumeName": "shop-db-data"}],
    versions=[{"version": 1, "deployedImageName": "postgres:16-alpine"}],
)
CACHE = definition(
    "cache",
    notExposeAsWebApp=True,
    versions=[{"version": 1, "deployedImageName": "redis:7"}],
)
WORKER = definition(
    "who",
    versions=[{"version": 1, "deployedImageName": "traefik/whoami:v1.10"}],
)
LEGACY = definition(
    "old", isLegacyAppName=True, volumes=[{"containerPath": "/data", "volumeName": "old-data"}]
)


class Server:
    """urllib's opener, answering from a table and remembering what it was asked."""

    def __init__(
        self, definitions: list[dict[str, Any]], *, login: dict[str, Any] | None = None
    ) -> None:
        self.requests: list[Any] = []
        self.login = login or {
            "status": 100,
            "description": "Login succeeded",
            "data": {"token": SESSION},
        }
        self.definitions = definitions

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        self.requests.append(request)
        path = "/" + request.full_url.split("://", 1)[1].split("/", 1)[1]
        if path == "/api/v2/login":
            answer: Any = self.login
        elif path == "/api/v2/user/apps/appDefinitions":
            if request.get_header("X-captain-auth") != SESSION:
                answer = {"status": 1106, "description": "Auth token corrupted"}
            else:
                answer = {
                    "status": 100,
                    "description": "App definitions are retrieved.",
                    "data": {
                        "appDefinitions": self.definitions,
                        "rootDomain": ROOT,
                        "captainSubDomain": "captain",
                        "defaultNginxConfig": "# nginx",
                    },
                }
        else:
            raise HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
        body = json.dumps(answer).encode()

        class Response:
            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def read(self, limit: int = -1) -> bytes:
                return body

        return Response()


def server(*definitions: dict[str, Any], **kwargs: Any) -> Server:
    return Server(list(definitions) or [SHOP, DB, CACHE, WORKER], **kwargs)


def read(name: str = "shop", *definitions: dict[str, Any]) -> Proposal:
    fake = server(*definitions)
    client = caprover.connect("https://captain.apps.example.com", PASSWORD, opener=fake)
    return caprover.read_application(client, name)


# --------------------------------------------------------------------------- the login


def test_the_password_is_exchanged_for_a_token_and_sent_once() -> None:
    fake = server()

    client = caprover.connect("https://captain.apps.example.com", PASSWORD, opener=fake)
    caprover.list_applications(client)

    login, listing = fake.requests
    assert json.loads(login.data) == {"password": PASSWORD}
    assert login.get_header("X-namespace") == "captain"
    assert login.get_header("X-captain-auth") is None
    assert listing.get_header("X-captain-auth") == SESSION
    assert listing.get_header("X-namespace") == "captain"
    assert listing.data is None
    assert PASSWORD not in listing.full_url


def test_a_wrong_password_is_an_error_in_words_though_the_answer_is_http_200() -> None:
    fake = server(login={"status": 1105, "description": "Wrong password"})

    with pytest.raises(IntegrationError) as caught:
        caprover.connect("https://captain.apps.example.com", "nope", opener=fake)

    assert "Wrong password" in caught.value.message
    assert "dashboard's password" in caught.value.details


def test_two_factor_is_said_not_attempted() -> None:
    fake = server(login={"status": 1114, "description": "Enter OTP token as well"})

    with pytest.raises(IntegrationError) as caught:
        caprover.connect("https://captain.apps.example.com", PASSWORD, opener=fake)

    assert "one-time code" in caught.value.details


def test_something_that_is_not_caprover_is_not_taken_for_it() -> None:
    fake = server(login={"hello": "world"})

    with pytest.raises(IntegrationError) as caught:
        caprover.connect("https://captain.apps.example.com", PASSWORD, opener=fake)

    assert "not CapRover's" in caught.value.message


def test_plain_http_is_refused_off_the_loopback_address() -> None:
    from noust.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        caprover.connect("http://captain.apps.example.com", PASSWORD, opener=server())


# --------------------------------------------------------------------------- the list


def test_the_applications_are_listed_with_their_names_domains_and_what_they_run() -> None:
    client = caprover.connect("https://captain.apps.example.com", PASSWORD, opener=server())

    found = {item.name: item for item in caprover.list_applications(client)}

    assert set(found) == {"shop", "shop-db", "cache", "who"}
    assert found["shop"].domains == ("shop.example.com", f"shop.{ROOT}")
    assert found["shop"].source == "https://github.com/acme/shop"
    assert found["shop"].build == "captain-definition"
    assert found["shop-db"].build == "database (postgresql)"
    assert found["shop-db"].domains == ()
    assert found["who"].build == "image"
    assert found["who"].source == "traefik/whoami:v1.10"


# --------------------------------------------------------------------------- an application


def test_the_repository_comes_without_a_user_or_a_credential() -> None:
    proposal = read()

    assert proposal.source == "https://github.com/acme/shop"
    assert proposal.branch == "release"
    dumped = json.dumps([proposal.__dict__], default=lambda o: o.__dict__)
    for secret in ("REPO-PASSWORD", "OPENSSH PRIVATE KEY", "hook-token-xyz", "ci-bot"):
        assert secret not in dumped


def test_a_repository_with_a_user_in_its_address_loses_it() -> None:
    app = definition(
        "x",
        appPushWebhook={
            "repoInfo": {"repo": "https://deploy:tok@git.example.com/a/b.git", "branch": "main"}
        },
    )

    proposal = read("x", app)

    assert proposal.source == "https://git.example.com/a/b.git"


def test_the_domains_the_port_and_the_variables() -> None:
    proposal = read()

    assert proposal.domains == ["shop.example.com", f"shop.{ROOT}"]
    assert proposal.port == 3000
    env = {v.name: v for v in proposal.env}
    assert env["NODE_ENV"].value == "production"
    assert (env["SESSION_SECRET"].secret, env["SESSION_SECRET"].value) == (True, None)
    # a connection string with a password in it names CapRover's database: it is given again
    assert (env["DATABASE_URL"].secret, env["DATABASE_URL"].value) == (True, None)


def test_the_default_port_is_80_as_caprover_says() -> None:
    proposal = read("who", WORKER)

    assert proposal.port == 80


def test_a_volume_under_app_is_a_persistent_path_and_one_elsewhere_is_said() -> None:
    proposal = read()

    uploads, state = proposal.storage
    assert uploads.persistent_path == "uploads"
    assert uploads.volume == "shop-uploads"
    assert uploads.export == (
        "docker run --rm -v shop-uploads:/data:ro alpine tar -C /data -czf - . > shop-uploads.tar.gz"
    )
    assert state.host_path == "/srv/shop"
    assert state.persistent_path is None
    assert proposal.persistent_paths == ["uploads"]


def test_a_legacy_application_names_its_volume_with_the_namespace() -> None:
    proposal = read("old", LEGACY)

    assert proposal.storage[0].volume == "captain--old-data"


def test_the_database_the_application_connects_to_is_found_by_its_service_name() -> None:
    proposal = read()

    linked = {d.name: d for d in proposal.database_details}
    assert set(linked) == {"shop-db", "cache"}
    db = linked["shop-db"]
    assert (db.engine, db.reference, db.version, db.linked) == (
        "postgresql",
        "srv-captain--shop-db",
        "16-alpine",
        True,
    )
    assert db.dump is not None and db.restore is not None
    assert "docker exec $(docker ps -qf name=^srv-captain--shop-db\\.)" in db.dump
    assert db.restore == "noust db import shop_db shop_db.dump -e postgresql"
    assert linked["cache"].dump is None
    assert proposal.databases == ["postgresql", "redis"]


def test_a_database_that_is_not_named_by_any_variable_is_not_the_applications() -> None:
    other = definition("lonely", versions=[{"version": 1, "deployedImageName": "mysql:8"}])

    proposal = read("shop", SHOP, DB, CACHE, other)

    assert "lonely" not in {d.name for d in proposal.database_details}


def test_what_caprover_does_that_has_no_equivalent_is_said() -> None:
    warnings = "\n".join(read().warnings)

    for fragment in (
        "3 instances",
        "pre-deploy function",
        "custom nginx configuration",
        "HTTP basic authentication (user staff)",
        "noust app policy set DOMAIN --password",
        "captain-overlay-net, extra-net",
        "2222->2222",
        "--no-force-https",
    ):
        assert fragment in warnings


def test_an_application_built_here_is_a_container_the_operator_creates() -> None:
    proposal = read()

    assert proposal.app_type == "container"
    assert proposal.equivalent == (
        "noust create -d DOMAIN -s https://github.com/acme/shop --type container --builder dockerfile"
    )


def test_a_registry_image_is_created_by_its_image() -> None:
    proposal = read("who", WORKER)

    assert proposal.image == "traefik/whoami:v1.10"
    assert proposal.equivalent == "noust create -d DOMAIN --image traefik/whoami:v1.10"


def test_a_database_application_says_it_is_a_database() -> None:
    proposal = read("shop-db", SHOP, DB)

    assert any(
        "database image postgres:16-alpine" in w and "noust db install postgresql" in w
        for w in proposal.warnings
    )


def test_an_application_that_does_not_exist_is_an_error() -> None:
    with pytest.raises(IntegrationError, match="no application 'ghost'"):
        read("ghost")


def test_nothing_is_written_to_caprover() -> None:
    fake = server()
    client = caprover.connect("https://captain.apps.example.com", PASSWORD, opener=fake)

    caprover.read_application(client, "shop")

    assert [r.get_method() for r in fake.requests] == ["POST", "GET", "GET"][: len(fake.requests)]
    assert [r.full_url.rsplit("/api", 1)[1] for r in fake.requests if r.get_method() == "POST"] == [
        "/v2/login"
    ]
