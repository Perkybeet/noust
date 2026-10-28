# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_importers_caprover.py`` that other tests build on."""

from __future__ import annotations

import io
import json
from typing import Any
from urllib.error import HTTPError

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
