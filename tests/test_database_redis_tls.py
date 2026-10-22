# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
TLS for the Redis family in an instance: Redis, Valkey and KeyDB (4.0, checklist C-25).

Redis speaks TLS on a port of its own and can leave the plaintext one shut, so there is one mode that
means what it says: ``required``, the instance's one port serving TLS and nothing else. (An
``optional`` mode would need a second published port; it is refused with that reason.) Noust's own
client inside the container is told to speak it, so the health check, the key browser, the backups and
every other thing the Redis manager does go on working. What is pinned here is what the Compose file
says, what the client is told, and what a link to such an instance may ask for; that a real server
serves it is ``tests/integration/engines_run.py`` (``redis_family_tls``).
"""

from __future__ import annotations

import pytest

from noust.core.db_instance_store import TLS_OFF, TLS_OPTIONAL, TLS_REQUIRED
from noust.managers.database import instance_compose
from noust.managers.database.instance_compose import TLS_MOUNT, tls_supported
from noust.managers.database.instance_records import stub_instance
from noust.managers.database.redis import RedisManager
from tests.test_database_instance_compose import record

FAMILY = ("redis", "valkey", "keydb")


class TestWhatIsOffered:
    @pytest.mark.parametrize("flavour", FAMILY)
    def test_the_one_port_can_serve_tls_and_nothing_else(self, flavour: str) -> None:
        assert tls_supported(flavour, "7.2", TLS_REQUIRED) is None
        assert tls_supported(flavour, "7.2", TLS_OFF) is None

    @pytest.mark.parametrize("flavour", FAMILY)
    def test_optional_is_refused_with_the_reason_and_the_way(self, flavour: str) -> None:
        reason = tls_supported(flavour, "7.2", TLS_OPTIONAL)

        assert reason is not None
        assert "port of their own" in reason and "required" in reason

    def test_a_redis_without_tls_in_it_is_refused(self) -> None:
        reason = tls_supported("redis", "5.0", TLS_REQUIRED)

        assert reason is not None and "6.0" in reason

    def test_clickhouse_is_still_not_offered(self) -> None:
        reason = tls_supported("clickhouse", "26.8", TLS_REQUIRED)

        assert reason is not None and "ClickHouse" in reason


class TestTheComposeFile:
    @pytest.mark.parametrize(
        ("flavour", "program"),
        [("redis", "redis-server"), ("valkey", "valkey-server"), ("keydb", "keydb-server")],
    )
    def test_the_server_is_started_with_its_tls_port_in_place_of_the_plain_one(
        self, flavour: str, program: str
    ) -> None:
        command = instance_compose.engine_command(flavour, 256, tls=TLS_REQUIRED)

        assert command is not None
        assert command[:2] == [program, instance_compose._CONFIG_MOUNTS[flavour]]
        flags = dict(zip(command[2::2], command[3::2], strict=True))
        assert flags == {
            "--port": "0",
            "--tls-port": "6379",
            "--tls-cert-file": f"{TLS_MOUNT}/server.crt",
            "--tls-key-file": f"{TLS_MOUNT}/server.key",
            "--tls-ca-cert-file": f"{TLS_MOUNT}/ca.crt",
            "--tls-auth-clients": "no",
        }

    def test_without_tls_the_command_is_what_it_was(self) -> None:
        assert instance_compose.engine_command("redis", 256) == [
            "redis-server",
            "/usr/local/etc/redis/redis.conf",
        ]

    @pytest.mark.parametrize(
        ("flavour", "cli"),
        [("redis", "redis-cli"), ("valkey", "valkey-cli"), ("keydb", "keydb-cli")],
    )
    def test_the_health_check_speaks_tls_when_the_port_does(self, flavour: str, cli: str) -> None:
        document = instance_compose.build_document(record(flavour, tls_mode=TLS_REQUIRED))
        test = document["services"]["db"]["healthcheck"]["test"]

        assert f"{cli} --tls --cacert {TLS_MOUNT}/ca.crt ping" in test[1]
        assert f"$${flavour.upper()}_PASSWORD" in test[1]

    def test_the_certificate_is_mounted_for_the_engine(self) -> None:
        document = instance_compose.build_document(record("valkey", tls_mode=TLS_REQUIRED))

        assert any(
            volume.endswith(f":{TLS_MOUNT}:ro") for volume in document["services"]["db"]["volumes"]
        )

    def test_the_published_port_is_the_same_one(self) -> None:
        plain = instance_compose.build_document(record("redis"))["services"]["db"]["ports"]
        secure = instance_compose.build_document(record("redis", tls_mode=TLS_REQUIRED))[
            "services"
        ]["db"]["ports"]

        assert plain == secure == ["127.0.0.1:55432:6379"]

    def test_the_password_stays_out_of_the_command_line(self) -> None:
        command = instance_compose.engine_command("redis", 256, tls=TLS_REQUIRED) or []

        assert not any("requirepass" in part for part in command)


class TestTheClient:
    def serving(self, flavour: str = "redis", mode: str = TLS_REQUIRED) -> RedisManager:
        return RedisManager().bind(stub_instance(record(flavour, tls_mode=mode), state="running"))

    def test_the_client_inside_the_container_is_told_to_speak_tls(self) -> None:
        manager = self.serving()

        assert manager._cli_tls() == ["--tls", "--cacert", f"{TLS_MOUNT}/ca.crt"]

    def test_an_instance_that_does_not_serve_it_is_spoken_to_in_the_clear(self) -> None:
        assert self.serving(mode=TLS_OFF)._cli_tls() == []

    def test_the_server_of_the_host_is_spoken_to_in_the_clear(self) -> None:
        assert RedisManager()._cli_tls() == []

    def test_every_command_carries_it(self, runner) -> None:  # type: ignore[no-untyped-def]
        manager = self.serving()

        manager._execute_redis("PING")

        call = runner.calls[-1]
        programs = call.index("redis-cli,valkey-cli,keydb-cli")
        assert call[programs + 1 : programs + 4] == ("--tls", "--cacert", f"{TLS_MOUNT}/ca.crt")
        assert call[-1] == "PING"

    def test_a_batch_of_commands_carries_it_too(self, runner) -> None:  # type: ignore[no-untyped-def]
        manager = self.serving()

        manager.run_commands([("PING",)])

        call = runner.calls[-1]
        assert "--tls" in call and "--csv" in call

    def test_an_interactive_session_carries_it_too(self) -> None:
        argv = self.serving().get_interactive_command()

        assert "--tls" in argv and argv[argv.index("--cacert") + 1] == f"{TLS_MOUNT}/ca.crt"


class TestWhatTheStatusSays:
    def test_the_instance_knows_the_mode_it_serves(self) -> None:
        assert stub_instance(record("redis", tls_mode=TLS_REQUIRED)).tls_mode == TLS_REQUIRED
        assert stub_instance(record("redis")).tls_mode == TLS_OFF


class TestWhatAnApplicationIsGiven:
    def test_the_scheme_says_the_port_speaks_tls(self) -> None:
        from noust.managers.database.urls import connection_url

        plain = connection_url("redis", database="0", user=None, password="pw", port=6379)
        secure = connection_url(
            "redis@cache", database="0", user=None, password="pw", port=6379, secure=True
        )

        assert plain.startswith("redis://:pw@") and secure.startswith("rediss://:pw@")

    def test_the_other_engines_keep_their_scheme_and_carry_tls_in_an_option(self) -> None:
        from noust.managers.database.urls import connection_url

        url = connection_url(
            "postgresql", database="d", user="u", password="p", port=1, secure=True
        )

        assert url.startswith("postgresql://")

    @pytest.mark.parametrize(
        ("mode", "options"),
        [
            ("require", {"ssl_cert_reqs": "none"}),
            (
                "verify-ca",
                {
                    "ssl_cert_reqs": "required",
                    "ssl_ca_certs": "/ca.crt",
                    "ssl_check_hostname": "false",
                },
            ),
            (
                "verify-full",
                {
                    "ssl_cert_reqs": "required",
                    "ssl_ca_certs": "/ca.crt",
                    "ssl_check_hostname": "true",
                },
            ),
        ],
    )
    def test_each_mode_is_what_a_redis_client_reads(self, mode: str, options: dict) -> None:
        from pathlib import Path

        from noust.managers.database.tls_links import client_options

        assert client_options("redis@cache", mode, Path("/ca.crt")) == options


class TestPublicAccess:
    def test_an_instance_that_serves_tls_only_is_already_what_public_access_asks(self) -> None:
        manager = RedisManager().bind(
            stub_instance(record("valkey", tls_mode=TLS_REQUIRED), state="running")
        )

        assert manager.tls_only_port is True
        assert manager.remote_tls_blocker() is None
        manager.set_remote_tls("default", "0", True)  # nothing to do, nothing refused
        manager.set_remote_tls("default", "0", False)

    def test_one_that_does_not_is_what_it_was(self) -> None:
        manager = RedisManager().bind(stub_instance(record("valkey"), state="running"))

        assert manager.tls_only_port is False
        assert manager.remote_tls_blocker() is not None
