# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Dokploy keeps beside an application: its mounts and the databases of its environment (N-13).

``tests/test_importers_remote.py`` pins the application. Here: the volumes and bind mounts become
``--persist`` paths and the commands that copy the data, and a database is the application's when a
variable holds its service name (``appName``), which is the host Dokploy's network gives it. The
answers are shaped from Dokploy's own router and schema (``project.all`` keeps each kind of
database in its own list of the environment with ``serviceColumns``; ``postgres.one`` has the
rest).
"""

from __future__ import annotations

from typing import Any

from noust.deployers.importers import dokploy
from noust.deployers.importers.base import Proposal
from tests.test_importers_remote import DOKPLOY_APP, TOKEN, FakeServer

APP = {
    **DOKPLOY_APP,
    "env": "DATABASE_URL=postgres://shop:pw@shop-db-x1y2z3:5432/shopdb\nNODE_ENV=production\n",
    "mounts": [
        {"type": "volume", "volumeName": "shop-uploads", "mountPath": "/app/uploads"},
        {"type": "bind", "hostPath": "/srv/media", "mountPath": "/var/media"},
        {"type": "file", "filePath": "nginx.conf", "content": "x", "mountPath": "/etc/nginx.conf"},
    ],
}


def environment(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": "production",
        "applications": [{"applicationId": "app_1", "name": "Shop", "appName": "shop-abc"}],
        "postgres": [
            {"postgresId": "pg1", "name": "shop db", "appName": "shop-db-x1y2z3"},
            {"postgresId": "pg2", "name": "reports", "appName": "reports-q9w8e7"},
        ],
        "redis": [{"redisId": "r1", "name": "cache", "appName": "cache-k3j2h1"}],
        **(extra or {}),
    }


def read(app: dict[str, Any] | None = None, env: dict[str, Any] | None = None) -> Proposal:
    server = FakeServer(
        {
            "/api/project.all": [
                {"projectId": "p1", "name": "Storefront", "environments": [env or environment()]}
            ],
            "/api/application.one?applicationId=app_1": {**APP, **(app or {})},
            "/api/postgres.one?postgresId=pg1": {
                "dockerImage": "postgres:16",
                "databaseName": "shopdb",
                "databaseUser": "shop",
                "databasePassword": "pw",
            },
        }
    )
    client = dokploy.connect("https://dokploy.example.com", TOKEN, opener=server)
    return dokploy.read_application(client, "app_1")


def test_a_volume_under_app_is_a_persistent_path_and_a_bind_mount_is_a_directory() -> None:
    proposal = read()

    uploads, media = proposal.storage
    assert (uploads.volume, uploads.persistent_path) == ("shop-uploads", "uploads")
    assert uploads.export == (
        "docker run --rm -v shop-uploads:/data:ro alpine tar -C /data -czf - . > shop-uploads.tar.gz"
    )
    assert (media.host_path, media.persistent_path) == ("/srv/media", None)
    assert proposal.persistent_paths == ["uploads"]


def test_a_file_dokploy_writes_is_said_with_the_command_that_keeps_one() -> None:
    warnings = read().warnings

    assert any("/etc/nginx.conf" in w and "noust app mount file" in w for w in warnings)


def test_a_dockerfile_application_has_no_known_working_directory() -> None:
    proposal = read({"buildType": "dockerfile"})

    assert proposal.persistent_paths == []


def test_the_database_named_by_a_variable_is_the_applications() -> None:
    proposal = read()

    (database,) = proposal.database_details
    assert (database.engine, database.name, database.reference) == (
        "postgresql",
        "shop db",
        "shop-db-x1y2z3",
    )
    assert (database.image, database.version) == ("postgres:16", "16")
    assert (database.database, database.user) == ("shopdb", "shop")
    assert database.linked is True
    assert proposal.databases == ["postgresql"]


def test_the_dump_finds_the_swarm_container_by_the_service_name() -> None:
    (database,) = read().database_details

    assert database.dump is not None
    assert "docker exec $(docker ps -qf name=^shop-db-x1y2z3\\.)" in database.dump
    assert database.restore == "noust db import shop_db shop-db-x1y2z3.dump -e postgresql"
    assert "pw" not in database.dump + (database.restore or "")


def test_a_database_no_variable_names_is_not_listed() -> None:
    names = {d.name for d in read().database_details}

    assert "reports" not in names and "cache" not in names


def test_an_application_in_another_environment_does_not_see_these_databases() -> None:
    other = environment({"applications": [{"applicationId": "someone_else"}]})

    assert read(env=other).database_details == []


def test_the_secrets_of_the_variables_are_not_carried() -> None:
    proposal = read()

    url = next(v for v in proposal.env if v.name == "DATABASE_URL")
    assert (url.secret, url.value) == (True, None)
