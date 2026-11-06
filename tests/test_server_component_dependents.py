# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who depends on a piece of the server's software, read from the stores that already know (item 118).

Each answer is the real question put to a real store: an application behind nginx, a tunnel, a
published port, a zone of Noust's DNS, a remote backup destination. A name is a name, never a
sentence, and a question that cannot be answered raises instead of answering "nobody".
"""

from __future__ import annotations

import pytest

from noust.core.exceptions import EngineInUseError
from noust.core.store import App, Database, Site, get_store
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.server import component_dependents as who
from noust.managers.server.component_dependents import Dependent


def app(domain: str, app_type: str = "static", webserver: str = "nginx") -> None:
    get_store().create_app(
        App(
            domain=domain,
            app_type=app_type,
            app_path=f"/var/www/apps/{domain}",
            webserver=webserver,
        )
    )


class TestTheWebServers:
    def test_nothing_is_behind_a_server_with_no_sites(self) -> None:
        assert who.nginx_users() == []
        assert who.apache_users() == []

    def test_the_applications_and_the_sites_of_no_application_are_named_by_server(self) -> None:
        app("shop.example.com")
        app("blog.example.com", webserver="apache")
        get_store().create_site(Site(domain="status.example.com", webserver="nginx"))

        assert who.nginx_users() == [
            Dependent("application", ("shop.example.com",)),
            Dependent("site", ("status.example.com",)),
        ]
        assert who.apache_users() == [Dependent("application", ("blog.example.com",))]

    def test_a_site_that_is_an_applications_is_counted_with_the_application_once(self) -> None:
        get_store().create_app(
            App(domain="shop.example.com", app_type="static", app_path="/x", webserver="nginx")
        )
        shop = get_store().get_app("shop.example.com")
        assert shop is not None
        get_store().create_site(Site(app_id=shop.id, domain="shop.example.com", webserver="nginx"))

        assert who.nginx_users() == [Dependent("application", ("shop.example.com",))]


class TestTheRuntimes:
    def test_node_is_what_the_node_types_and_monorepos_run_on(self) -> None:
        app("web.example.com", "nextjs")
        app("api.example.com", "nodejs")
        app("repo.example.com", "monorepo")
        app("docs.example.com", "static")

        assert who.node_users() == [
            Dependent("application", ("api.example.com", "repo.example.com", "web.example.com"))
        ]

    def test_php_is_what_php_fpm_applications_run_on(self) -> None:
        app("blog.example.com", "php-fpm")
        app("web.example.com", "nextjs")

        assert who.php_users() == [Dependent("application", ("blog.example.com",))]

    def test_docker_is_what_compose_and_container_applications_run_on(self) -> None:
        app("stack.example.com", "docker-compose")
        app("box.example.com", "container")
        app("web.example.com", "nextjs")

        assert who.docker_users() == [
            Dependent("application", ("box.example.com", "stack.example.com"))
        ]


class TestWhatNoustKeeps:
    def test_a_tunnel_runs_the_connector(self) -> None:
        from noust.core.tunnel_store import Tunnel, TunnelStore

        assert who.tunnel_users() == []
        store = get_store()
        store._get_connection().execute(
            "INSERT INTO dns_providers (name, kind, settings, created_at) "
            "VALUES ('cf', 'cloudflare', '{}', 't')"
        )
        TunnelStore().create(Tunnel("main", "cf", "acc", "id", "noust-tunnel-main"))

        assert who.tunnel_users() == [Dependent("tunnel", ("main",))]

    def test_a_published_port_is_an_nginx_stream_block(self) -> None:
        from noust.core.stream_store import StreamExposure, StreamStore

        assert who.stream_users() == []
        StreamStore().create(StreamExposure("pg", "tcp", 5433, "10.0.0.5", 5432))

        assert who.stream_users() == [Dependent("published_port", ("pg",))]
        # nginx is what holds it.
        assert Dependent("published_port", ("pg",)) in who.nginx_users()

    def test_a_zone_of_noust_dns_needs_knot(self) -> None:
        from noust.core.authdns_store import AuthDnsStore, AuthDnsZone

        assert who.dns_zone_users() == []
        AuthDnsStore().create_zone(
            AuthDnsZone(
                name="example.org",
                nameservers=("ns1.example.org",),
                contact="hostmaster@example.org",
            )
        )

        assert who.dns_zone_users() == [Dependent("zone", ("example.org",))]

    def test_a_remote_backup_destination_is_uploaded_to_with_rclone(self) -> None:
        from noust.core.store import BackupDestinationRecord

        assert who.backup_destination_users() == []
        get_store().save_backup_destination(BackupDestinationRecord(name="offsite", backend="s3"))

        assert who.backup_destination_users() == [Dependent("destination", ("offsite",))]

    def test_the_waf_is_what_a_site_with_it_on_needs(self) -> None:
        from noust.managers.site_policy import SitePolicyManager

        app("shop.example.com")
        app("blog.example.com")
        assert who.waf_users() == []
        SitePolicyManager().records.set_document(
            "shop.example.com", '{"waf": "detect"}', updated_by="cli:root"
        )
        SitePolicyManager().records.set_document(
            "blog.example.com", '{"http3": true}', updated_by="cli:root"
        )

        assert who.waf_users() == [Dependent("application", ("shop.example.com",))]


class TestTheEngines:
    def test_a_database_the_store_tracks_is_what_an_engine_holds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No engine is on this machine as far as the test knows: a runner of CI has PostgreSQL
        # installed, and asking it whether it runs would start a real systemctl.
        monkeypatch.setattr(BaseDatabaseManager, "is_installed", lambda self: False)
        get_store().create_database(Database(name="shop", engine="postgresql"))
        get_store().create_database(Database(name="wiki", engine="mysql"))

        assert who.postgresql_users() == [Dependent("database", ("shop",))]
        assert who.mysql_users() == [Dependent("database", ("wiki",))]
        assert who.redis_users() == []
        assert who.mongodb_users() == []

    def test_an_engine_that_runs_and_cannot_be_read_is_not_taken_for_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers.database import engine_removal

        def unreadable(manager: object) -> list[str]:
            raise EngineInUseError("PostgreSQL is running, and Noust cannot say what it holds")

        monkeypatch.setattr(engine_removal, "databases_held", unreadable)

        found = who.postgresql_users()

        assert found == [
            Dependent("unreadable", ("PostgreSQL is running, and Noust cannot say what it holds",))
        ]


def test_a_dependent_is_data_for_the_api() -> None:
    assert Dependent("application", ("a.example.com", "b.example.com")).to_dict() == {
        "kind": "application",
        "names": ["a.example.com", "b.example.com"],
    }
