# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Coolify keeps beside an application: its storage and its databases (checklist N-13).

``tests/test_importers_remote.py`` pins the application itself. Here the two things the first
importer only waved at: the persistent storage (Coolify answers ``{"persistent_storages": [...],
"file_storages": [...]}``, not a list, which is what made the first reading silently find nothing)
and the databases, which are separate resources of the same environment that the application finds
by the host name Coolify gives them, the database's uuid. The doubles are shaped from Coolify's own
controllers (``ApplicationsController::storages``, ``DatabasesController::databases``): the model's
attributes, ``database_type`` appended, ``id`` hidden.
"""

from __future__ import annotations

import io
import json
from typing import Any
from urllib.error import HTTPError

from noust.deployers.app_export import plan_import, proposal_document
from noust.deployers.importers import coolify
from noust.deployers.importers.base import Proposal

TOKEN = "1|abcDEF123456789"
APP = "abc123"
POSTGRES = "k8w0cwsg4g4s4s8wgkc8ok8o"
REDIS = "z0c4ookwwgs0ocgcw4wsc8g4"
MYSQL = "m1ysql0wgs0ocgcw4wsc8g4x"

APPLICATION = {
    "uuid": APP,
    "name": "shop",
    "fqdn": "https://shop.example.com",
    "git_repository": "acme/shop",
    "git_branch": "main",
    "build_pack": "nixpacks",
    "ports_exposes": "3000",
    "environment_id": 7,
}

DATABASES = [
    {
        "uuid": POSTGRES,
        "name": "shop-db",
        "image": "postgres:16-alpine",
        "database_type": "standalone-postgresql",
        "environment_id": 7,
        "postgres_user": "shop",
        "postgres_db": "shopdb",
        "status": "running:healthy",
    },
    {
        "uuid": REDIS,
        "name": "cache",
        "image": "redis:7.2",
        "database_type": "standalone-redis",
        "environment_id": 7,
        "status": "running:healthy",
    },
    {
        "uuid": MYSQL,
        "name": "reports",
        "image": "mysql:8.4",
        "database_type": "standalone-mysql",
        "environment_id": 7,
        "mysql_user": "reports",
        "mysql_database": "reports",
    },
    {
        "uuid": "elsewhere0000000000000000",
        "name": "other-team",
        "image": "postgres:15",
        "database_type": "standalone-postgresql",
        "environment_id": 99,
    },
]

STORAGES = {
    "persistent_storages": [
        {"name": "abc123-uploads", "mount_path": "/app/uploads", "host_path": None},
        {"name": "abc123-state", "mount_path": "/var/lib/shop", "host_path": None},
        {"name": None, "mount_path": "/app/media", "host_path": "/srv/shop-media"},
    ],
    "file_storages": [
        {
            "fs_path": "/data/coolify/applications/abc123/app.conf",
            "mount_path": "/etc/shop.conf",
            "is_directory": False,
            "is_based_on_git": False,
            "content": "x=1",
        },
    ],
}

ENVS = [
    {
        "key": "DATABASE_URL",
        "value": f"postgres://shop:pw@{POSTGRES}:5432/shopdb",
        "is_preview": False,
    },
    {"key": "REDIS_URL", "value": f"redis://default:pw@{REDIS}:6379/0", "is_preview": False},
    {"key": "NODE_ENV", "value": "production", "is_preview": False},
]


class Server:
    """urllib's opener, answering from a table."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.paths: list[str] = []

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        path = "/" + request.full_url.split("://", 1)[1].split("/", 1)[1]
        self.paths.append(path)
        if path not in self.answers:
            raise HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
        answer = self.answers[path]
        if isinstance(answer, HTTPError):
            raise answer
        body = json.dumps(answer).encode()

        class Response:
            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def read(self, limit: int = -1) -> bytes:
                return body

        return Response()


def read(app: dict[str, Any] | None = None, **answers: Any) -> Proposal:
    base = f"/api/v1/applications/{APP}"
    table = {
        base: {**APPLICATION, **(app or {})},
        f"{base}/envs": ENVS,
        f"{base}/storages": STORAGES,
        f"{base}/scheduled-tasks": [],
        "/api/v1/databases": DATABASES,
    }
    table.update({key.replace("__", "/"): value for key, value in answers.items()})
    client = coolify.connect("https://coolify.example.com", TOKEN, opener=Server(table))
    return coolify.read_application(client, APP)


# --------------------------------------------------------------------------- storage


def test_the_real_shape_of_the_storage_answer_is_read() -> None:
    proposal = read()

    assert [s.mount_path for s in proposal.storage] == [
        "/app/uploads",
        "/var/lib/shop",
        "/app/media",
    ]


def test_a_mount_under_the_buildpack_directory_becomes_a_persistent_path() -> None:
    proposal = read()

    assert proposal.persistent_paths == ["uploads", "media"]
    uploads = proposal.storage[0]
    assert uploads.persistent_path == "uploads"
    assert uploads.volume == "abc123-uploads"


def test_a_mount_outside_the_application_is_said_not_guessed() -> None:
    state = read().storage[1]

    assert state.persistent_path is None
    assert "outside the application's own directory" in state.describe()


def test_the_command_that_copies_a_volume_out_mounts_it_read_only() -> None:
    uploads = read().storage[0]

    assert uploads.export == (
        "docker run --rm -v abc123-uploads:/data:ro alpine tar -C /data -czf - . "
        "> abc123-uploads.tar.gz"
    )
    assert uploads.restore == "tar -xzf FILE.tar.gz -C /var/www/apps/APP/shared/uploads"


def test_a_bind_mount_is_a_directory_of_the_old_server() -> None:
    media = read().storage[2]

    assert media.volume is None
    assert media.host_path == "/srv/shop-media"
    assert media.export == "tar -C /srv/shop-media -czf srv-shop-media.tar.gz ."


def test_a_file_coolify_writes_itself_is_said_with_the_command_that_keeps_one() -> None:
    proposal = read()

    assert any("/etc/shop.conf" in w and "noust app mount file" in w for w in proposal.warnings)


def test_the_older_list_shape_still_reads() -> None:
    proposal = read(**{"__api__v1__applications__abc123__storages": [{"mount_path": "/app/data"}]})

    assert proposal.persistent_paths == ["data"]


def test_a_dockerfile_application_has_no_known_working_directory() -> None:
    proposal = read({"build_pack": "dockerfile"})

    assert proposal.persistent_paths == []
    assert all(s.persistent_path is None for s in proposal.storage)


def test_storage_that_cannot_be_read_is_a_warning_not_a_failure() -> None:
    forbidden = HTTPError("https://x/y", 403, "Forbidden", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
    proposal = read(**{"__api__v1__applications__abc123__storages": forbidden})

    assert any("storage could not be read" in w for w in proposal.warnings)


# --------------------------------------------------------------------------- databases


def test_a_database_whose_uuid_is_in_a_variable_is_the_one_the_application_uses() -> None:
    proposal = read()

    linked = {d.name: d for d in proposal.database_details if d.linked}
    assert set(linked) == {"shop-db", "cache"}
    assert linked["shop-db"].engine == "postgresql"
    assert linked["shop-db"].reference == POSTGRES
    assert (linked["shop-db"].database, linked["shop-db"].user) == ("shopdb", "shop")
    assert linked["shop-db"].version == "16-alpine"


def test_a_database_of_the_same_environment_that_nothing_names_is_listed_not_linked() -> None:
    proposal = read()

    reports = next(d for d in proposal.database_details if d.name == "reports")
    assert reports.linked is False
    assert "no variable of the application names it" in (reports.note or "")
    assert not any("reports" in w for w in proposal.warnings)


def test_a_database_of_another_environment_is_not_this_applications() -> None:
    proposal = read()

    assert "other-team" not in {d.name for d in proposal.database_details}


def test_the_engines_it_needs_are_the_linked_ones_a_host_can_run() -> None:
    assert read().databases == ["postgresql", "redis"]


def test_the_dump_reads_the_password_inside_the_container_never_on_the_line() -> None:
    shop = next(d for d in read().database_details if d.name == "shop-db")

    assert shop.dump == (
        f"docker exec {POSTGRES} sh -c "
        '\'pg_dump -U "${POSTGRES_USER:-postgres}" -Fc "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"\' '
        f"> {POSTGRES}.dump"
    )
    assert shop.restore == f"noust db import shop_db {POSTGRES}.dump -e postgresql"
    assert "pw" not in (shop.dump + shop.restore)


def test_mysql_gets_the_password_through_the_environment_of_the_client() -> None:
    proposal = read(
        **{
            "__api__v1__applications__abc123__envs": [
                {"key": "DB", "value": f"mysql://u:p@{MYSQL}/reports", "is_preview": False}
            ]
        },
    )

    reports = next(d for d in proposal.database_details if d.name == "reports")
    assert reports.linked is True
    assert reports.dump is not None
    assert 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump' in reports.dump
    assert "-p" not in reports.dump.replace("-pw", "")


def test_a_cache_has_no_dump_and_says_so_by_leaving_it_out() -> None:
    cache = next(d for d in read().database_details if d.name == "cache")

    assert cache.engine == "redis"
    assert cache.dump is None
    assert cache.restore is None


def test_a_database_noust_runs_in_a_container_is_pointed_at_instances() -> None:
    keydb = {
        "uuid": "keydb0000000000000000000",
        "name": "kv",
        "image": "eqalpha/keydb",
        "database_type": "standalone-keydb",
        "environment_id": 7,
    }
    proposal = read(
        **{
            "__api__v1__databases": [*DATABASES, keydb],
            "__api__v1__applications__abc123__envs": [
                {"key": "KV", "value": "redis://keydb0000000000000000000:6379", "is_preview": False}
            ],
        }
    )

    kv = next(d for d in proposal.database_details if d.name == "kv")
    assert "noust db instance create NAME --engine keydb" in kv.describe()
    assert "keydb" not in proposal.databases


def test_what_is_used_is_said_in_a_sentence_with_both_commands() -> None:
    warnings = read().warnings

    sentence = next(w for w in warnings if "shop-db" in w)
    assert "noust db install postgresql" in sentence
    assert f"docker exec {POSTGRES}" in sentence
    assert f"noust db import shop_db {POSTGRES}.dump -e postgresql" in sentence


def test_databases_that_cannot_be_listed_are_a_warning_not_a_failure() -> None:
    forbidden = HTTPError("https://x/y", 403, "Forbidden", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
    proposal = read(**{"__api__v1__databases": forbidden})

    assert proposal.database_details == []
    assert any("databases could not be listed" in w for w in proposal.warnings)


def test_an_application_that_names_no_database_and_lives_elsewhere_finds_none() -> None:
    proposal = read(
        {"environment_id": 8},
        **{
            "__api__v1__applications__abc123__envs": [
                {"key": "NODE_ENV", "value": "production", "is_preview": False}
            ]
        },
    )

    assert proposal.database_details == []
    assert proposal.databases == []


def test_the_secrets_the_token_could_read_go_no_further_than_the_search() -> None:
    proposal = read()

    url = next(v for v in proposal.env if v.name == "DATABASE_URL")
    assert (url.secret, url.value, url.required) == (True, None, True)
    assert "pw" not in json.dumps([v.__dict__ for v in proposal.env])
    assert all("postgres://shop:pw" not in w for w in proposal.warnings)


def test_an_import_plan_keeps_the_persistent_paths() -> None:
    proposal = read()
    document = proposal_document(
        proposal, domain="shop.example.com", source="https://example.com/shop.git"
    )

    assert document["app"]["persistent_paths"] == ["uploads", "media"]
    plan = plan_import(
        document, env={"DATABASE_URL": "postgres://u:p@db/shop", "REDIS_URL": "redis://:p@cache"}
    )
    assert list(plan.create.persistent_paths) == ["uploads", "media"]
