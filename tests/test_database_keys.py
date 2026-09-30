# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Redis key browser (noust.managers.database.keys).

What is defended: a page is SCAN plus one batch, both through stdin with
every argument hex-escaped; reads sign in as the read-only ACL user whose
rules are the read-only profile's, and a refused sign-in is never answered
by carrying on as another identity; previews are bounded; a key that is not
UTF-8 round-trips through its hex.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from noust.core import audit
from noust.core.exceptions import DatabaseNotFoundError, DatabaseQueryError, ValidationError
from noust.managers.database import keys as keys_module
from noust.managers.database.keys import (
    READ_ONLY_SECRET,
    READ_ONLY_USER,
    KeyBrowser,
    RedisReplyError,
    parse_csv_reply,
)
from noust.managers.database.redis import RedisManager
from tests.database_data_support import SqlRunner, installed_sql_runner, make_service


@pytest.fixture
def sql_runner() -> Iterator[SqlRunner]:
    """
    Yields:
        The SQL runner, installed over a fresh store.
    """
    yield from installed_sql_runner()


@pytest.fixture
def redis(sql_runner: SqlRunner) -> RedisManager:
    """
    Args:
        sql_runner: The runner.

    Returns:
        A Redis manager that answers the read-only user's set-up.
    """
    keys_module._provisioned_at.clear()
    sql_runner.only_knows("redis-cli")
    sql_runner.answer(hexed("ACL"), '"OK"\n')
    return RedisManager()


@pytest.fixture
def browser(redis: RedisManager) -> KeyBrowser:
    """
    Args:
        redis: The manager.

    Returns:
        A key browser over it.
    """
    return KeyBrowser(make_service(redis))


def hexed(text: str) -> str:
    """
    Args:
        text: An argument.

    Returns:
        How run_commands writes it on stdin.
    """
    return "".join(f"\\x{byte:02x}" for byte in text.encode())


# ============================================================ CSV


def test_a_csv_answer_is_read_token_by_token() -> None:
    assert parse_csv_reply('"0","a b","x\\"y","\\xff\\n"') == [b"0", b"a b", b'x"y', b"\xff\n"]
    assert parse_csv_reply("42") == [42]
    assert parse_csv_reply("NULL") == [None]
    assert parse_csv_reply('"0",') == [b"0"]


def test_an_error_answer_raises_with_the_servers_words() -> None:
    with pytest.raises(RedisReplyError) as caught:
        parse_csv_reply('ERROR,"NOPERM this user has no permissions"')

    assert "NOPERM" in caught.value.details


# ============================================================ scanning


def test_a_page_is_scan_then_one_batch(browser: KeyBrowser, sql_runner: SqlRunner) -> None:
    sql_runner.answer(hexed("SCAN"), '"17","user:1","\\xff\\xfe"\n')
    sql_runner.answer(
        hexed("TYPE"),
        '"string"\n-1\n56\n"hash"\n30\n72\n',
    )

    page = browser.scan("redis", "0", match="user:*", count=50)

    assert page.cursor == "17" and not page.done
    assert [(k.key, k.type, k.ttl, k.memory) for k in page.keys] == [
        ("user:1", "string", None, 56),
        ("\\xff\\xfe", "hash", 30, 72),
    ]
    assert page.keys[1].hex == "fffe"
    assert page.read_only_enforced
    reads = sql_runner.calls_carrying(hexed("SCAN"))
    assert reads[-1][:4] == ("redis-cli", "--user", READ_ONLY_USER, "-n")
    assert "--csv" in reads[-1]
    assert not any("user:*" in arg for call in sql_runner.calls for arg in call)


def test_the_read_only_user_holds_the_read_only_profile(
    browser: KeyBrowser, sql_runner: SqlRunner
) -> None:
    sql_runner.answer(hexed("SCAN"), '"0",\n')

    browser.scan("redis", "0")

    setup = sql_runner.sent(hexed("SETUSER"))[-1]
    for rule in ("reset", "on", "-@all", "+@read", "-@dangerous", "allkeys"):
        assert hexed(rule) in setup
    stored = browser.service.secrets.read(READ_ONLY_SECRET)
    assert stored and hexed(">" + stored) in setup
    call = sql_runner.calls_carrying(hexed("SETUSER"))[-1]
    assert all(stored not in arg for arg in call)


def test_the_read_only_user_is_reused_while_fresh(
    browser: KeyBrowser, sql_runner: SqlRunner
) -> None:
    sql_runner.answer(hexed("SCAN"), '"0",\n')

    browser.scan("redis", "0")
    browser.scan("redis", "0")

    assert len(sql_runner.sent(hexed("SETUSER"))) == 1


def test_a_refused_sign_in_is_set_up_again_never_ignored(
    browser: KeyBrowser, sql_runner: SqlRunner
) -> None:
    sql_runner.answer(hexed("SCAN"), '"0",\n')
    browser.scan("redis", "0")
    sql_runner.answer(hexed("SCAN"), 'ERROR,"WRONGPASS invalid username-password pair"\n', times=1)

    page = browser.scan("redis", "0")

    assert page.done
    assert len(sql_runner.sent(hexed("SETUSER"))) == 2


def test_a_refused_auth_on_stderr_is_not_taken_for_a_read(
    browser: KeyBrowser, sql_runner: SqlRunner
) -> None:
    """redis-cli carries on as the default user after a refused AUTH; that must not count."""
    sql_runner.answer(hexed("SCAN"), '"0",\n')
    browser.scan("redis", "0")
    sql_runner.answer(
        hexed("SCAN"),
        '"0","key"\n',
        stderr="AUTH failed: WRONGPASS invalid username-password pair\n",
        times=1,
    )

    page = browser.scan("redis", "0")

    assert page.keys == []
    assert len(sql_runner.sent(hexed("SETUSER"))) == 2


def test_a_server_without_acls_is_read_with_the_usual_identity_and_says_so(
    browser: KeyBrowser, sql_runner: SqlRunner
) -> None:
    sql_runner.answer(hexed("ACL"), "ERROR,\"ERR unknown command 'ACL'\"\n")
    sql_runner.answer(hexed("SCAN"), '"0",\n')

    page = browser.scan("redis", "0")

    assert not page.read_only_enforced
    assert "--user" not in sql_runner.calls_carrying(hexed("SCAN"))[-1]


@pytest.mark.parametrize(
    "kwargs",
    [{"cursor": "1;FLUSHALL"}, {"count": 0}, {"count": 5000}, {"key_type": "module"}],
)
def test_scan_arguments_are_checked(browser: KeyBrowser, kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        browser.scan("redis", "0", **kwargs)


def test_the_slot_must_be_a_number(browser: KeyBrowser) -> None:
    with pytest.raises(ValidationError):
        browser.scan("redis", "0 FLUSHALL")


def test_sql_engines_have_no_key_browser(sql_runner: SqlRunner) -> None:
    from noust.managers.database.postgres import PostgresManager

    with pytest.raises(DatabaseQueryError, match="no keys"):
        KeyBrowser(make_service(PostgresManager())).scan("postgresql", "0")


# ============================================================ previews


def test_a_hash_preview_is_bounded_and_audited(browser: KeyBrowser, sql_runner: SqlRunner) -> None:
    sql_runner.answer(hexed("TYPE"), '"hash"\n-1\n120\n')
    sql_runner.answer(hexed("HLEN"), '150\n"0","name","Ada","role","admin"\n')

    value = browser.preview("redis", "0", "user:1")

    assert value.type == "hash"
    assert value.length == 150
    assert value.value == [["name", "Ada"], ["role", "admin"]]
    assert value.truncated
    entry = audit.get_log().read(action="db.browse")[0]
    assert entry["details"]["key"] == "user:1"


def test_a_binary_string_is_shown_as_hex(browser: KeyBrowser, sql_runner: SqlRunner) -> None:
    sql_runner.answer(hexed("TYPE"), '"string"\n10\nNULL\n')
    sql_runner.answer(hexed("STRLEN"), '3\n"\\xff\\x00\\x01"\n')

    value = browser.preview("redis", "0", key_hex="fffe")

    assert value.value == {"bytes": 3, "hex": "ff0001"}
    assert value.ttl == 10
    assert value.hex == "fffe"
    assert not value.truncated


def test_a_missing_key_is_not_found(browser: KeyBrowser, sql_runner: SqlRunner) -> None:
    sql_runner.answer(hexed("TYPE"), '"none"\n-2\nNULL\n')

    with pytest.raises(DatabaseNotFoundError):
        browser.preview("redis", "0", "gone")


def test_a_preview_names_exactly_one_key(browser: KeyBrowser) -> None:
    with pytest.raises(ValidationError):
        browser.preview("redis", "0")
    with pytest.raises(ValidationError):
        browser.preview("redis", "0", key_hex="zz")
