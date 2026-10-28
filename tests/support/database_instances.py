# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_instances.py`` that other tests build on."""

from __future__ import annotations

from typing import Any

PG_PASSWORD = "pg-s3cret-value"
ROOT_PASSWORD = "root-s3cret-value"
APP_PASSWORD = "app-s3cret-value"
REDIS_PASSWORD = "redis-s3cret-value"


def container(
    name: str,
    image: str,
    *,
    env: list[str] | None = None,
    project: str | None = None,
    service: str | None = None,
    state: str = "running",
    ports: dict[str, Any] | None = None,
    exposed: tuple[str, ...] = (),
    cmd: list[str] | None = None,
) -> dict[str, Any]:
    """One object of ``docker inspect``, as Docker prints it."""
    labels = {}
    if project:
        labels["com.docker.compose.project"] = project
    if service:
        labels["com.docker.compose.service"] = service
    return {
        "Id": f"{name}-id",
        "Name": f"/{name}",
        "Config": {
            "Image": image,
            "Env": [*(env or []), "PATH=/usr/local/bin:/usr/bin"],
            "Labels": labels,
            "ExposedPorts": {port: {} for port in exposed},
            "Cmd": cmd,
            "Entrypoint": None,
        },
        "State": {"Status": state, "Running": state == "running"},
        "NetworkSettings": {"Ports": ports or {}},
        "HostConfig": {"PortBindings": {}},
    }


#: The database containers of a hosting server and Proggest, with one that only looks like one.
FLEET = [
    container(
        "empleo-arenna-db",
        "postgres:17-alpine",
        env=["POSTGRES_USER=empleo", f"POSTGRES_PASSWORD={PG_PASSWORD}", "POSTGRES_DB=empleo"],
        project="jobs-example-net",
        service="db",
        exposed=("5432/tcp",),
        ports={"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5434"}]},
    ),
    container(
        "arenna_tienda_mysql",
        "mysql:8.0",
        env=[
            f"MYSQL_ROOT_PASSWORD={ROOT_PASSWORD}",
            "MYSQL_DATABASE=tienda",
            "MYSQL_USER=tienda",
            f"MYSQL_PASSWORD={APP_PASSWORD}",
        ],
        project="shop-example-net",
        service="mysql",
        exposed=("3306/tcp", "33060/tcp"),
    ),
    container(
        "arenna_tienda_redis",
        "redis:7-alpine",
        project="shop-example-net",
        service="redis",
        exposed=("6379/tcp",),
        cmd=["redis-server", "--requirepass", REDIS_PASSWORD],
    ),
    container(
        "zabbix-zabbix-db-1",
        "mysql:8.0",
        env=[
            "MYSQL_RANDOM_ROOT_PASSWORD=yes",
            "MYSQL_USER=zabbix",
            f"MYSQL_PASSWORD={APP_PASSWORD}",
        ],
        project="zabbix",
        service="zabbix-db",
        state="exited",
        exposed=("3306/tcp",),
    ),
    container(
        "mariadb_contenedor",
        "mariadb:10.11",
        env=[f"MARIADB_ROOT_PASSWORD={ROOT_PASSWORD}"],
        exposed=("3306/tcp",),
        ports={"3306/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3307"}]},  # noqa: S104
    ),
    container(
        "proggest-postgres-1",
        "postgres:16-alpine",
        env=["POSTGRES_USER=proggest", f"POSTGRES_PASSWORD={PG_PASSWORD}", "POSTGRES_DB=proggest"],
        project="proggest",
        service="postgres",
        exposed=("5432/tcp",),
    ),
]

PS_LINES = "\n".join(
    [
        *(f"{entry['Id']}\t{entry['Config']['Image']}" for entry in FLEET),
        "zabbix-web-id\tzabbix/zabbix-web-nginx-mysql:alpine-7.0-latest",
        "zabbix-server-id\tzabbix/zabbix-server-mysql:alpine-7.0-latest",
    ]
)
