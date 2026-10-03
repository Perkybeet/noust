"""The databases an application already uses, read from its environment."""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.store import App
from noust.managers.database.detect_links import (
    AppReferences,
    Endpoint,
    detect,
    read_environment,
    references_in,
    resolve,
)

HOST_MYSQL = Endpoint(engine="mysql", family="mysql", ports=frozenset({3306}))
HOST_POSTGRES = Endpoint(engine="postgresql", family="postgresql", ports=frozenset({5432}))
HOST_REDIS = Endpoint(engine="redis", family="redis", ports=frozenset({6379}))
EMPLEO_DB = Endpoint(
    engine="postgresql@empleo-arennalabs-com.db",
    family="postgresql",
    ports=frozenset({5434}),
    project="empleo-arennalabs-com",
    service="db",
)
PROGGEST_PG = Endpoint(
    engine="postgresql@proggest.postgres",
    family="postgresql",
    ports=frozenset(),
    project="proggest",
    service="postgres",
)


class TestReferences:
    """What an environment names, without its secrets."""

    def test_a_prisma_url_names_engine_port_database_and_user(self) -> None:
        (ref,) = references_in(
            {"DATABASE_URL": "postgresql://app:s3cr%40t@127.0.0.1:5434/empleo?schema=public"}
        )

        assert (ref.engine, ref.host, ref.port, ref.database, ref.username) == (
            "postgresql",
            "127.0.0.1",
            5434,
            "empleo",
            "app",
        )
        assert "s3cr" not in repr(ref), "a reference never carries the password"

    def test_a_laravel_set_is_read_with_its_connection(self) -> None:
        env = {
            "DB_CONNECTION": "mysql",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "3306",
            "DB_DATABASE": "profleet_rent",
            "DB_USERNAME": "profleet",
            "DB_PASSWORD": "x",
        }

        (ref,) = references_in(env)

        assert (ref.engine, ref.port, ref.database, ref.username) == (
            "mysql",
            3306,
            "profleet_rent",
            "profleet",
        )

    def test_a_db_set_on_5432_without_connection_is_postgresql(self) -> None:
        (ref,) = references_in({"DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": "shop"})

        assert ref.engine == "postgresql"

    def test_redis_url_and_slot(self) -> None:
        (ref,) = references_in({"REDIS_URL": "redis://:pw@localhost:6379/2"})

        assert (ref.engine, ref.database) == ("redis", "2")

    def test_the_same_target_named_twice_is_one_reference(self) -> None:
        env = {
            "DATABASE_URL": "mysql://u:p@127.0.0.1:3306/shop",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "3306",
            "DB_DATABASE": "shop",
        }

        assert len(references_in(env)) == 1

    def test_values_that_are_not_database_urls_are_ignored(self) -> None:
        env = {
            "NEXT_PUBLIC_URL": "https://shop.example.com",
            "SMTP_URL": "smtp://mail:25",
            "EMPTY": "",
        }

        assert references_in(env) == []

    @pytest.mark.parametrize(
        "value",
        [
            "postgresql+asyncpg://u@db:5432/app",
            "jdbc:postgresql://db:5432/app",
            "mongodb+srv://u@cluster0.example.net/app",
        ],
    )
    def test_driver_suffixes_and_wrappers_are_read(self, value: str) -> None:
        (ref,) = references_in({"URL": value})

        assert ref.database == "app"


class TestResolve:
    """A reference reaches one engine, or none: never a guess."""

    def test_localhost_resolves_by_port_to_the_host_engine(self) -> None:
        (ref,) = references_in({"DATABASE_URL": "mysql://u:p@localhost:3306/shop"})

        assert resolve(ref, [HOST_MYSQL, HOST_POSTGRES]) == "mysql"

    def test_a_published_port_resolves_to_its_container(self) -> None:
        (ref,) = references_in({"DATABASE_URL": "postgresql://u:p@127.0.0.1:5434/empleo"})

        assert resolve(ref, [HOST_POSTGRES, EMPLEO_DB]) == "postgresql@empleo-arennalabs-com.db"

    def test_a_service_name_resolves_inside_its_own_project(self) -> None:
        (ref,) = references_in({"DATABASE_URL": "postgresql://u:p@postgres:5432/proggest"})

        assert resolve(ref, [PROGGEST_PG], project="proggest") == "postgresql@proggest.postgres"
        assert resolve(ref, [PROGGEST_PG], project="another") is None

    def test_another_server_resolves_to_nothing(self) -> None:
        (ref,) = references_in({"DATABASE_URL": "postgresql://u:p@db.example.net:5432/app"})

        assert resolve(ref, [HOST_POSTGRES, EMPLEO_DB]) is None

    def test_two_engines_on_one_port_are_not_guessed_between(self) -> None:
        (ref,) = references_in({"DATABASE_URL": "postgresql://u:p@localhost:5432/app"})
        twin = Endpoint(engine="postgresql@other.db", family="postgresql", ports=frozenset({5432}))

        assert resolve(ref, [HOST_POSTGRES, twin]) is None

    def test_detect_names_the_application(self) -> None:
        app = App(domain="profleet.rent", app_path="/var/www/apps/profleet-rent")
        refs = references_in({"DATABASE_URL": "mysql://u:p@127.0.0.1:3306/profleet_rent"})

        (found,) = detect([AppReferences(app=app, references=refs)], [HOST_MYSQL])

        assert (found.domain, found.engine, found.reference.database) == (
            "profleet.rent",
            "mysql",
            "profleet_rent",
        )


class TestReadEnvironment:
    """A .env is read only when it stays inside the application's tree."""

    def test_reads_the_env_in_place(self, tmp_path: Path) -> None:
        root = tmp_path / "shop"
        root.mkdir()
        (root / ".env").write_text('DATABASE_URL="mysql://u:p@localhost/shop"\n')

        env = read_environment(App(domain="shop.example.com", app_path=str(root)))

        assert env == {"DATABASE_URL": "mysql://u:p@localhost/shop"}

    def test_a_link_leading_outside_is_not_read(self, tmp_path: Path) -> None:
        root = tmp_path / "shop"
        root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.write_text("DATABASE_URL=mysql://root:p@localhost/mysql\n")
        (root / ".env").symlink_to(outside)

        assert read_environment(App(domain="shop.example.com", app_path=str(root))) == {}

    def test_no_env_is_empty(self, tmp_path: Path) -> None:
        root = tmp_path / "shop"
        root.mkdir()

        assert read_environment(App(domain="shop.example.com", app_path=str(root))) == {}
