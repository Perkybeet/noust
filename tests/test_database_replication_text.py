# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The words of MySQL and MariaDB replication, and the helpers every engine's replicas share (C-14).

The two servers agree on what replication is and disagree on every word for it, so the statements
are pinned here in both spellings; the status the server prints is read by whichever name it uses;
and the pure helpers of the shared base (what an error shows of a client's output, what an engine is
told to listen on, how a setting file is read back) are held to their contracts.
"""

from __future__ import annotations

import pytest

from noust.managers.database.mysql_include import parse_include, render_include
from noust.managers.database.replication_base import clip, literal, merge_listen
from noust.managers.database.replication_mysql_text import (
    account_host,
    change_source,
    dump_options,
    forget_binary_log,
    parse_version,
    parse_vertical,
    pick,
    start_statement,
    status_statement,
    stop_statement,
)

# ------------------------------------------------------------------ versions and accounts


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("11.4.5-MariaDB-ubu2404", (11, 4, 5)),
        ("10.11.14-MariaDB-0ubuntu0.24.04.1", (10, 11, 14)),
        ("5.5.5-10.6.12-MariaDB", (10, 6, 12)),
        ("8.4.3", (8, 4, 3)),
        ("8.0.40-0ubuntu0.24.04.1", (8, 0, 40)),
        (" 9.7.0 ", (9, 7, 0)),
    ],
)
def test_a_servers_version_is_read(text: str, expected: tuple[int, int, int]) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "MariaDB", "11.4", "x.y.z"])
def test_what_is_not_a_version_is_not_one(text: str) -> None:
    assert parse_version(text) is None


@pytest.mark.parametrize(
    ("source", "host"),
    [
        ("203.0.113.7/32", "203.0.113.7"),
        ("10.0.0.0/24", "10.0.0.0/255.255.255.0"),
        ("10.1.0.0/16", "10.1.0.0/255.255.0.0"),
        ("2001:db8::1/128", "2001:db8::1"),
    ],
)
def test_the_host_of_the_account_is_an_address_or_an_address_and_mask(
    source: str, host: str
) -> None:
    assert account_host(source) == host


def test_an_ipv6_network_has_no_form_as_an_account_host() -> None:
    with pytest.raises(ValueError, match="IPv6 network"):
        account_host("2001:db8::/64")


# ------------------------------------------------------------------ statements


def test_mariadb_is_pointed_at_its_primary_with_gtids_and_verified_tls() -> None:
    statement = change_source(
        True,
        host="db.example.com",
        port=3306,
        account="noust_repl_x",
        password="pw",
        authority="/etc/ssl/ca.pem",
    )

    assert statement.startswith("CHANGE MASTER TO MASTER_HOST = 'db.example.com'")
    assert "MASTER_PORT = 3306" in statement and "MASTER_USER = 'noust_repl_x'" in statement
    assert "MASTER_PASSWORD = 'pw'" in statement
    assert "MASTER_USE_GTID = slave_pos" in statement
    assert "MASTER_SSL = 1" in statement and "MASTER_SSL_VERIFY_SERVER_CERT = 1" in statement
    assert "MASTER_SSL_CA = '/etc/ssl/ca.pem'" in statement
    assert statement.endswith(";") and "SOURCE_" not in statement


def test_mysql_is_pointed_at_its_source_with_auto_positioning_and_verified_tls() -> None:
    statement = change_source(
        False,
        host="db.example.com",
        port=3306,
        account="noust_repl_x",
        password="pw",
        authority="/etc/ssl/ca.pem",
    )

    assert statement.startswith("CHANGE REPLICATION SOURCE TO SOURCE_HOST = 'db.example.com'")
    assert "SOURCE_AUTO_POSITION = 1" in statement
    assert "SOURCE_SSL = 1" in statement and "SOURCE_SSL_VERIFY_SERVER_CERT = 1" in statement
    assert "SOURCE_SSL_CA = '/etc/ssl/ca.pem'" in statement and "MASTER_" not in statement


def test_a_value_that_is_a_quote_or_a_backslash_cannot_end_the_statement() -> None:
    statement = change_source(
        True,
        host="h",
        port=3306,
        account="a",
        password="p'; DROP USER x; --\\",
        authority="/ca",
    )

    assert "MASTER_PASSWORD = 'p''; DROP USER x; --\\\\'" in statement
    assert literal("it's") == "'it''s'" and literal("a\\b") == "'a\\\\b'"


def test_the_port_cannot_be_anything_but_a_number() -> None:
    with pytest.raises(ValueError):
        change_source(True, host="h", port="3306; DROP", account="a", password="p", authority="/c")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("mariadb", "start", "stop", "status"),
    [
        (True, "START SLAVE;", "STOP SLAVE;", "SHOW SLAVE STATUS"),
        (False, "START REPLICA;", "STOP REPLICA;", "SHOW REPLICA STATUS"),
    ],
)
def test_each_flavour_uses_its_own_verbs(mariadb: bool, start: str, stop: str, status: str) -> None:
    assert start_statement(mariadb) == start
    assert stop_statement(mariadb) == stop
    assert status_statement(mariadb) == status
    assert stop_statement(mariadb, "IO_THREAD").endswith("IO_THREAD;")


def test_the_binary_log_is_emptied_with_the_name_the_server_has() -> None:
    assert forget_binary_log((8, 0, 40)) == "RESET MASTER;"
    assert forget_binary_log((8, 4, 0)) == "RESET BINARY LOGS AND GTIDS;"
    assert forget_binary_log((9, 7, 1)) == "RESET BINARY LOGS AND GTIDS;"


def test_a_client_verifies_the_certificate_and_the_name_in_the_flavours_words() -> None:
    assert dump_options(True, "/ca.pem") == [
        "--ssl",
        "--ssl-ca=/ca.pem",
        "--ssl-verify-server-cert",
    ]
    assert dump_options(False, "/ca.pem") == ["--ssl-mode=VERIFY_IDENTITY", "--ssl-ca=/ca.pem"]


# ------------------------------------------------------------------ status


MARIADB_STATUS = """*************************** 1. row ***************************
                Slave_IO_State: Waiting for master to send event
                   Master_Host: db.example.com
                   Master_Port: 3306
                  Master_User: noust_repl_x
              Slave_IO_Running: Yes
             Slave_SQL_Running: Yes
                   Last_IO_Error: error connecting: a: b
                  Last_SQL_Error:
          Seconds_Behind_Master: 0
              Master_SSL_Allowed: Yes
 Master_SSL_Verify_Server_Cert: Yes
"""

MYSQL_STATUS = """*************************** 1. row ***************************
               Replica_IO_State: Waiting for source to send event
                    Source_Host: db.example.com
              Replica_IO_Running: Yes
             Replica_SQL_Running: Yes
        Seconds_Behind_Source: 4
        Source_SSL_Allowed: Yes
"""


def test_a_status_is_read_by_column_name() -> None:
    row = parse_vertical(MARIADB_STATUS)

    assert row["Slave_IO_Running"] == "Yes" and row["Master_Host"] == "db.example.com"
    assert row["Last_SQL_Error"] == ""
    assert row["Last_IO_Error"] == "error connecting: a: b", "only the first colon splits"


def test_a_server_that_follows_nothing_prints_no_row() -> None:
    assert parse_vertical("") == {}
    assert parse_vertical("\n") == {}


def test_only_the_first_row_is_read() -> None:
    two = MARIADB_STATUS + MARIADB_STATUS.replace("1. row", "2. row").replace(
        "db.example.com", "other.example.com"
    )

    assert parse_vertical(two)["Master_Host"] == "db.example.com"


def test_a_field_is_found_under_whichever_name_the_server_uses() -> None:
    maria, mysql = parse_vertical(MARIADB_STATUS), parse_vertical(MYSQL_STATUS)

    assert pick(maria, "Replica_IO_Running", "Slave_IO_Running") == "Yes"
    assert pick(mysql, "Replica_IO_Running", "Slave_IO_Running") == "Yes"
    assert pick(mysql, "Source_Host", "Master_Host") == "db.example.com"
    assert pick(mysql, "Seconds_Behind_Source", "Seconds_Behind_Master") == "4"
    assert pick(mysql, "Nope", "Nothing") == ""


# ------------------------------------------------------------------ the shared base


def test_an_error_keeps_the_end_of_the_output_and_cuts_the_long_lines() -> None:
    output = (
        "first\n" + "x" * 5000 + "\nERROR 1153: Got a packet bigger than 'max_allowed_packet'\n"
    )

    shown = clip(output)

    assert shown.endswith("Got a packet bigger than 'max_allowed_packet'")
    assert "x" * 200 not in shown and "more)" in shown
    assert len(shown) <= 2000


def test_a_short_output_is_shown_whole() -> None:
    assert clip("  ERROR 1045: Access denied\n") == "ERROR 1045: Access denied"
    assert clip("") == ""


@pytest.mark.parametrize(
    ("current", "wanted", "merged"),
    [
        ("localhost", "10.0.0.5", "localhost,10.0.0.5"),
        ("127.0.0.1", "*", "*"),
        ("*", "10.0.0.5", "*"),
        ("localhost,10.0.0.5", "10.0.0.5", "localhost,10.0.0.5"),
        ("localhost", "10.0.0.5, 10.0.1.5", "localhost,10.0.0.5,10.0.1.5"),
    ],
)
def test_what_an_engine_listens_on_grows_and_never_repeats(
    current: str, wanted: str, merged: str
) -> None:
    assert merge_listen(current, wanted) == merged


def test_noust_settings_are_written_and_read_back_in_order() -> None:
    settings = {"server_id": "7", "bind-address": "10.0.0.5", "log_bin": "noust-bin"}

    text = render_include("what a primary needs", settings)

    assert text.startswith("# Generated by Noust")
    assert "[mysqld]\nserver_id = 7\n" in text
    assert parse_include(text) == settings
    assert list(parse_include(text)) == ["server_id", "bind-address", "log_bin"]


def test_a_file_that_is_not_noust_s_reads_as_its_settings_and_nothing_else() -> None:
    text = "# a comment\n[mysqld]\n# max_connections = 5\nmax_connections = 200\n\n[client]\n"

    assert parse_include(text) == {"max_connections": "200"}
