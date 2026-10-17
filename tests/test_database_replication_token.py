# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The join code, the names it carries and the text of a replica's files (C-14).

A join code is pasted by a person, so reading it is an allowlist: what :func:`encode` makes comes
back exactly, and every field that is not what it makes is refused with the field named. The
files' text is pure, so it is pinned here: the passfile line, the settings a standby starts with
and the connection string it follows with.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.managers.database import pg_hba, replication_files
from noust.managers.database.replication_token import (
    PREFIX,
    REQUIRED_SETTINGS,
    ReplicaToken,
    encode,
    parse,
)
from noust.validators.replica import (
    check_replica_host,
    check_replica_name,
    role_for,
    slot_for,
)

PASSWORD = "aB3" * 12
CA = (
    "-----BEGIN CERTIFICATE-----\n"
    + "MIIBszCCAVmgAwIBAgIUQ2xhdWRlVGVzdENlcnRpZmljYXRlMDAwMDEwCgYIKoZI\n" * 2
    + "-----END CERTIFICATE-----\n"
)


def token(**overrides: Any) -> ReplicaToken:
    """
    Build a code with sensible values.

    Args:
        **overrides: Fields to change.

    Returns:
        The code.
    """
    fields: dict[str, Any] = {
        "name": "reports",
        "host": "db.example.com",
        "port": 5432,
        "role": "noust_repl_reports",
        "password": PASSWORD,
        "slot": "noust_reports",
        "major": 16,
        "size": 1024,
        "requires": dict.fromkeys(REQUIRED_SETTINGS, 8),
        "ca": CA,
    }
    fields.update(overrides)
    return ReplicaToken(**fields)


def damaged(**changes: Any) -> str:
    """
    Make a code with fields changed behind the format's back.

    Args:
        **changes: Short field names and their new values; None removes the field.

    Returns:
        The code.
    """
    raw = encode(token())[len(PREFIX) :]
    body = json.loads(base64.urlsafe_b64decode(raw))
    for key, value in changes.items():
        if value is None:
            body.pop(key)
        else:
            body[key] = value
    encoded = base64.urlsafe_b64encode(json.dumps(body).encode()).decode()
    return PREFIX + encoded


# ------------------------------------------------------------------ the code


def test_a_code_comes_back_exactly_as_it_was_made() -> None:
    original = token()

    assert parse(encode(original)) == original


def test_a_code_without_an_authority_comes_back_without_one() -> None:
    assert parse(encode(token(ca=""))).ca == ""


def test_a_code_is_one_line_with_nothing_readable_in_it_but_its_prefix() -> None:
    code = encode(token())

    assert "\n" not in code and code.startswith("noust-replica:v1:")
    assert PASSWORD not in code, "the password is not sitting there in clear"


def test_surrounding_space_from_a_paste_is_ignored() -> None:
    assert parse(f"  {encode(token())}\n") == token()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "noust-replica:v1:",
        "noust-replica:v2:abcd",
        "noust-wasm:v1:abcd",
        "noust-replica:v1:!!!not base64!!!",
        "noust-replica:v1:" + base64.urlsafe_b64encode(b"not json").decode(),
        "noust-replica:v1:" + base64.urlsafe_b64encode(b"[1, 2]").decode(),
        "noust-replica:v1:" + base64.urlsafe_b64encode(b"x" * 70000).decode(),
        encode(token()) + "\nsecond line",
    ],
)
def test_text_that_is_not_a_code_is_refused(text: str) -> None:
    with pytest.raises(ValidationError, match="not a join code Noust made") as caught:
        parse(text)

    assert caught.value.field is not None


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"v": 2}, "token"),
        ({"e": "oracle"}, "token"),
        ({"e": "mysql"}, "role"),
        ({"f": "mariadb"}, "flavour"),
        ({"f": 3}, "f"),
        ({"n": "Reports"}, "name"),
        ({"n": "a;drop"}, "name"),
        ({"h": "bad host"}, "host"),
        ({"h": "x\n"}, "host"),
        ({"h": 5}, "h"),
        ({"u": "postgres"}, "role"),
        ({"s": "pg_replication"}, "role"),
        ({"w": "short"}, "password"),
        ({"w": "a" * 40 + "\n"}, "password"),
        ({"w": "!" * 40}, "password"),
        ({"p": 0}, "port"),
        ({"p": 70000}, "port"),
        ({"p": "5432"}, "port"),
        ({"p": True}, "port"),
        ({"m": 9}, "major"),
        ({"m": "16"}, "major"),
        ({"b": -1}, "size"),
        ({"c": "not pem"}, "ca"),
        ({"c": CA + "trailing"}, "ca"),
        ({"c": 7}, "c"),
        ({"r": {}}, "requires"),
        ({"r": []}, "requires"),
        ({"x": 1}, "token"),
        ({"w": None}, "token"),
    ],
)
def test_every_field_is_checked_and_the_refusal_names_it(
    changes: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        parse(damaged(**changes))

    assert caught.value.field == field, caught.value


def test_a_requirement_that_is_not_a_number_is_refused() -> None:
    requires = dict.fromkeys(REQUIRED_SETTINGS, 8)
    requires["max_connections"] = "many"  # type: ignore[assignment]

    with pytest.raises(ValidationError) as caught:
        parse(damaged(r=requires))

    assert caught.value.field == "max_connections"


def test_a_standby_may_require_no_prepared_transactions() -> None:
    requires = dict.fromkeys(REQUIRED_SETTINGS, 8)
    requires["max_prepared_transactions"] = 0

    assert parse(encode(token(requires=requires))).requires["max_prepared_transactions"] == 0


# ------------------------------------------------------------------ names


def mysql_token(**overrides: Any) -> ReplicaToken:
    """
    Build a MySQL code with sensible values.

    Args:
        **overrides: Fields to change.

    Returns:
        The code.
    """
    return token(
        engine="mysql",
        flavour="mariadb",
        slot="",
        major=1104,
        requires={"server_id": 1},
        **overrides,
    )


def test_a_mysql_code_comes_back_exactly_as_it_was_made() -> None:
    original = mysql_token()

    assert parse(encode(original)) == original


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"s": "noust_reports"}, "role"),
        ({"f": "percona"}, "flavour"),
        ({"f": ""}, "flavour"),
        ({"m": 16}, "major"),
        ({"m": 100000}, "major"),
        ({"r": {"max_connections": 100}}, "requires"),
        ({"r": {"server_id": 2**32}}, "server_id"),
        ({"r": {"server_id": -1}}, "server_id"),
    ],
)
def test_a_mysql_code_is_checked_for_what_only_mysql_has(
    changes: dict[str, Any], field: str
) -> None:
    raw = encode(mysql_token())[len(PREFIX) :]
    body = json.loads(base64.urlsafe_b64decode(raw))
    body.update(changes)
    text = PREFIX + base64.urlsafe_b64encode(json.dumps(body).encode()).decode()

    with pytest.raises(ValidationError) as caught:
        parse(text)

    assert caught.value.field == field, caught.value


def test_a_postgresql_code_has_no_flavour() -> None:
    body = json.loads(base64.urlsafe_b64decode(encode(token())[len(PREFIX) :]))
    body["f"] = "mariadb"

    with pytest.raises(ValidationError) as caught:
        parse(PREFIX + base64.urlsafe_b64encode(json.dumps(body).encode()).decode())

    assert caught.value.field == "flavour"


@pytest.mark.parametrize("name", ["reports", "eu_west", "a", "r2d2", "standby_1"])
def test_names_that_need_no_quoting_anywhere_are_accepted(name: str) -> None:
    assert check_replica_name(name) == name
    assert role_for(name) == f"noust_repl_{name}"
    assert slot_for(name) == f"noust_{name}"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Reports",
        "1abc",
        "_x",
        "x_",
        "a__b",
        "a-b",
        "a.b",
        "a b",
        "a;b",
        "a'b",
        'a"b',
        "a" * 21,
        "reports\n",
        "../x",
        7,
        None,
    ],
)
def test_a_name_that_would_need_quoting_or_escape_a_path_is_refused(name: object) -> None:
    with pytest.raises(ValidationError) as caught:
        check_replica_name(name)

    assert caught.value.field == "name"


@pytest.mark.parametrize(
    "host",
    ["db.example.com", "primary.noust.test", "203.0.113.7", "2001:db8::1", "a-b.example.org"],
)
def test_a_name_or_an_address_is_a_host(host: str) -> None:
    assert check_replica_host(host) == host


@pytest.mark.parametrize(
    "host",
    [
        "",
        " db.example.com",
        "db.example.com ",
        "db.example.com\n",
        "db example.com",
        "-db.example.com",
        "db..example.com",
        "db.example.com:5432",
        "db.example.com/x",
        "$(id)",
        "a" * 300,
        None,
        5,
    ],
)
def test_anything_else_is_not_a_host(host: object) -> None:
    with pytest.raises(ValidationError) as caught:
        check_replica_host(host)

    assert caught.value.field == "host"


# ------------------------------------------------------------------ the replica's files


def test_the_passfile_line_names_the_replication_pseudo_database() -> None:
    line = replication_files.passfile_line("db.example.com", 5432, "noust_repl_x", "pw")

    assert line == "db.example.com:5432:replication:noust_repl_x:pw\n"


def test_a_colon_or_a_backslash_in_a_passfile_field_is_escaped() -> None:
    line = replication_files.passfile_line("2001:db8::1", 5432, "r", "a:b\\c")

    assert line == "2001\\:db8\\:\\:1:5432:replication:r:a\\:b\\\\c\n"


def test_the_standby_keeps_its_own_settings_its_capacity_and_where_it_follows() -> None:
    own = (
        "# Do not edit this file manually!\n"
        "ssl_cert_file = '/etc/ssl/noust-db/postgresql/fullchain.pem'\n"
        "shared_buffers = '1GB'\n"
        "max_connections = '50'\n"
        "restore_command = 'cp /wal/%f %p'\n"
        "primary_conninfo = 'user=old'\n"
    )
    copied = (
        "# Do not edit this file manually!\n"
        "ssl_cert_file = '/the/primarys/fullchain.pem'\n"
        "listen_addresses = '*'\n"
        "archive_command = 'cp %p /wal/%f'\n"
        "shared_buffers = '4GB'\n"
        "max_connections = '300'\n"
        "primary_conninfo = 'user=r host=h'\n"
        "primary_slot_name = 'noust_x'\n"
    )

    text = replication_files.standby_auto_conf(
        own, copied, {"max_connections": 300, "max_wal_senders": 10}
    )

    assert replication_files.auto_conf_value(text, "ssl_cert_file") == (
        "/etc/ssl/noust-db/postgresql/fullchain.pem"
    ), "this server's own certificate, not the primary's"
    assert replication_files.auto_conf_value(text, "shared_buffers") == "1GB"
    assert replication_files.auto_conf_value(text, "listen_addresses") is None
    assert replication_files.auto_conf_value(text, "archive_command") is None
    assert replication_files.auto_conf_value(text, "max_connections") == "300"
    assert replication_files.auto_conf_value(text, "max_wal_senders") == "10"
    assert replication_files.auto_conf_value(text, "restore_command") is None
    assert replication_files.auto_conf_value(text, "primary_conninfo") == "user=r host=h"
    assert replication_files.auto_conf_value(text, "primary_slot_name") == "noust_x"
    assert text.startswith("# Do not edit this file manually!") and text.endswith("\n")
    assert text.count("max_connections") == 1


def test_a_server_with_no_settings_of_its_own_still_gets_a_valid_file() -> None:
    text = replication_files.standby_auto_conf(
        "", "primary_conninfo = 'user=r'\n", {"max_connections": 100}
    )

    assert text.splitlines()[0].startswith("#")
    assert replication_files.auto_conf_value(text, "max_connections") == "100"
    assert replication_files.auto_conf_value(text, "primary_conninfo") == "user=r"


def test_a_value_is_quoted_the_way_the_file_reads_it() -> None:
    assert replication_files.quote_setting("it's \\ ok") == "'it''s \\\\ ok'"


def test_a_setting_is_read_back_unquoted() -> None:
    text = "a = 'it''s'\nb = 5\na = 'last'\n# c = 'no'\n"

    assert replication_files.auto_conf_value(text, "a") == "last"
    assert replication_files.auto_conf_value(text, "b") == "5"
    assert replication_files.auto_conf_value(text, "c") is None


def test_a_connection_string_is_read_into_its_options() -> None:
    options = replication_files.conninfo_options(
        "user=noust_repl_x passfile='/var/lib/postgresql/.noust-replica/x.pgpass' "
        "host=db.example.com port=5432 sslmode='verify-full' application_name='it\\'s'"
    )

    assert options["user"] == "noust_repl_x"
    assert options["passfile"] == "/var/lib/postgresql/.noust-replica/x.pgpass"
    assert options["sslmode"] == "verify-full" and options["application_name"] == "it's"
    assert "password" not in options


# ------------------------------------------------------------------ pg_hba.conf


HBA = (
    "# TYPE  DATABASE        USER            ADDRESS                 METHOD\n"
    "local   all             postgres                                peer\n"
    "host    all             all             127.0.0.1/32            scram-sha-256\n"
)


def test_the_replication_line_is_hostssl_for_one_account_and_one_address() -> None:
    block = pg_hba.replication_block("noust_repl_x", "203.0.113.7")

    assert "hostssl replication noust_repl_x 203.0.113.7/32 scram-sha-256\n" in block
    assert block.startswith("# BEGIN noust replication: noust_repl_x\n")
    assert block.endswith("# END noust replication: noust_repl_x\n")
    assert "host " not in block.replace("hostssl", "")


@pytest.mark.parametrize(
    ("given", "written"),
    [
        ("203.0.113.7", "203.0.113.7/32"),
        ("10.0.0.0/24", "10.0.0.0/24"),
        ("2001:db8::1", "2001:db8::1/128"),
    ],
)
def test_an_address_is_written_as_a_network(given: str, written: str) -> None:
    assert pg_hba.replica_address(given) == written


@pytest.mark.parametrize(
    "given", ["", "all", "0.0.0.0/0", "::/0", "10.0.0.5 ", "10.0.0.5; reject", "x"]
)
def test_a_replica_is_never_allowed_from_everywhere_or_from_nonsense(given: str) -> None:
    with pytest.raises(ValueError):
        pg_hba.replica_address(given)


@pytest.mark.parametrize("account", ["x y", "a;b", "a'b", "", "x\n", "Ünï"])
def test_an_account_that_would_need_quoting_is_not_written(account: str) -> None:
    with pytest.raises(ValueError):
        pg_hba.replication_block(account, "10.0.0.5")


def test_the_block_is_put_above_every_connection_line_and_only_once() -> None:
    once = pg_hba.with_replication_block(HBA, "noust_repl_x", "10.0.0.5")
    twice = pg_hba.with_replication_block(once, "noust_repl_x", "10.0.0.6")

    assert once.index("hostssl replication") < once.index("local   all")
    assert once.startswith("# TYPE")
    assert twice.count("BEGIN noust replication") == 1
    assert "10.0.0.6/32" in twice and "10.0.0.5/32" not in twice


def test_taking_the_block_out_leaves_every_other_byte() -> None:
    other = pg_hba.with_block(HBA, "shop", "shop_remote")
    both = pg_hba.with_replication_block(other, "noust_repl_x", "10.0.0.5")

    assert pg_hba.without_replication_block(both, "noust_repl_x") == other
    assert pg_hba.without_replication_block(HBA, "noust_repl_x") == HBA
    assert pg_hba.has_replication_block(both, "noust_repl_x")
    assert not pg_hba.has_replication_block(both, "noust_repl_y")


def test_a_block_that_was_never_closed_is_not_guessed_at() -> None:
    broken = (
        HBA
        + "# BEGIN noust replication: noust_repl_x\nhostssl replication noust_repl_x 10.0.0.5/32 scram-sha-256\n"
    )

    assert pg_hba.without_replication_block(broken, "noust_repl_x") == broken


def test_the_two_kinds_of_block_do_not_touch_each_other() -> None:
    both = pg_hba.with_replication_block(
        pg_hba.with_block(HBA, "shop", "noust_repl_x"), "noust_repl_x", "10.0.0.5"
    )

    assert pg_hba.has_block(both, "noust_repl_x") and pg_hba.has_replication_block(
        both, "noust_repl_x"
    )
    assert pg_hba.has_block(pg_hba.without_replication_block(both, "noust_repl_x"), "noust_repl_x")
    assert pg_hba.has_replication_block(pg_hba.without_block(both, "noust_repl_x"), "noust_repl_x")
