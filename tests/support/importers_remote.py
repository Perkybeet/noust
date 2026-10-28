# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_importers_remote.py`` that other tests build on."""

from __future__ import annotations

import io
import json
from typing import Any
from urllib.error import HTTPError

TOKEN = "1|abcDEF123456789"


class FakeServer:
    """urllib's opener, answering from a table and remembering what it was asked."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        self.requests.append(request)
        path = request.full_url.split("://", 1)[1].split("/", 1)[1]
        key = "/" + path
        if key not in self.answers:
            raise HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
        answer = self.answers[key]
        if isinstance(answer, HTTPError):
            raise answer
        body = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
        return _Response(body)


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]


COOLIFY_APP = {
    "uuid": "abc123",
    "name": "shop",
    "fqdn": "https://shop.example.com,https://www.shop.example.com",
    "git_repository": "acme/shop",
    "git_branch": "main",
    "git_commit_sha": "HEAD",
    "build_pack": "nixpacks",
    "install_command": "pnpm install --frozen-lockfile",
    "build_command": "pnpm build",
    "start_command": "pnpm start",
    "base_directory": "/apps/web",
    "publish_directory": "/dist",
    "ports_exposes": "3000",
    "health_check_enabled": True,
    "health_check_path": "/healthz",
    "health_check_timeout": 7,
    "limits_memory": "512m",
    "limits_cpus": "1.5",
    "watch_paths": "apps/web/**\n!apps/web/docs/**\n",
    "environment_name": "production",
}

COOLIFY_ENVS = [
    {"key": "DATABASE_URL", "value": "postgres://user:pw@db/shop", "is_preview": False},
    {"key": "NODE_ENV", "value": "production", "is_preview": False},
    {"key": "STRIPE_SECRET", "value": None, "is_shown_once": True},
    {"key": "NODE_ENV", "value": "development", "is_preview": True},
    {"key": "BUILD_ONLY", "value": "1", "is_buildtime": True, "is_runtime": False},
]


def coolify_server(app: dict[str, Any] | None = None, **extra: Any) -> FakeServer:
    answers = {
        "/api/v1/applications": [{**COOLIFY_APP, **(app or {})}],
        f"/api/v1/applications/{COOLIFY_APP['uuid']}": {**COOLIFY_APP, **(app or {})},
        f"/api/v1/applications/{COOLIFY_APP['uuid']}/envs": COOLIFY_ENVS,
        f"/api/v1/applications/{COOLIFY_APP['uuid']}/storages": [{"mount_path": "/app/uploads"}],
        f"/api/v1/applications/{COOLIFY_APP['uuid']}/scheduled-tasks": [
            {
                "name": "nightly",
                "frequency": "daily",
                "command": "node cleanup.js",
                "enabled": True,
            },
            {"name": "odd", "frequency": "whenever", "command": "x"},
            {"name": "off", "frequency": "hourly", "command": "x", "enabled": False},
        ],
        **extra,
    }
    return FakeServer(answers)


DOKPLOY_APP = {
    "applicationId": "app_1",
    "name": "Shop",
    "appName": "shop-abc",
    "sourceType": "github",
    "owner": "acme",
    "repository": "shop",
    "branch": "main",
    "buildPath": "/apps/web",
    "buildType": "nixpacks",
    "env": 'NODE_ENV=production\n# comment\nSESSION_SECRET=abc123\nNAME="Quoted value"\n',
    "buildArgs": "FOO=bar",
    "memoryLimit": "536870912",
    "cpuLimit": "1500000000",
    "watchPaths": ["apps/web/**", "!apps/web/docs/**"],
    "enableSubmodules": True,
    "replicas": 3,
    "placementSwarm": {"Constraints": ["node.role==manager"]},
    "domains": [
        {"host": "shop.example.com", "port": 3000, "https": True, "domainType": "application"},
        {"host": "pr-4.shop.example.com", "port": 3000, "domainType": "preview"},
    ],
    "mounts": [{"type": "volume", "mountPath": "/data"}],
}


def dokploy_server(app: dict[str, Any] | None = None, *, nested: bool = True) -> FakeServer:
    full = {**DOKPLOY_APP, **(app or {})}
    summary = {
        k: v
        for k, v in full.items()
        if k
        in (
            "applicationId",
            "name",
            "appName",
            "sourceType",
            "owner",
            "repository",
            "buildType",
            "domains",
        )
    }
    project: dict[str, Any] = {"projectId": "p1", "name": "Storefront"}
    if nested:
        project["environments"] = [{"name": "production", "applications": [summary]}]
    else:
        project["applications"] = [summary]
    return FakeServer(
        {"/api/project.all": [project], "/api/application.one?applicationId=app_1": full}
    )
