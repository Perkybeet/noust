# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an operator may call an object of Noust: one rule, with a table of where each name travels.

Probes, alert rules, channels, a team's channels, replicas and database instances each had a
pattern of their own, and the console told people the wrong one: the probe form's help said
"lower-case letters, digits, - and _" while the store refused the underscore, so
``admon_shoprent`` was turned down by the help's own rules. The rule is now one module
(``noust.validators.object_names``), and what the console shows as help comes out of it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.server_text import ServerText
from noust.validators.object_names import (
    KINDS,
    RULES,
    check_name,
    is_name,
    rule_for,
    typescript_rules,
)


def test_the_name_the_owner_could_not_give_a_probe_is_accepted() -> None:
    assert check_name("probe", "admon_shoprent") == "admon_shoprent"


@pytest.mark.parametrize("kind", ["probe", "alert_rule", "channel", "team_channel"])
def test_the_names_an_operator_gives_in_the_console_take_letters_digits_dashes_and_underscores(
    kind: str,
) -> None:
    for name in ("a", "shop-health", "shop_health", "a1-b_2", "x" + "y" * 20):
        assert check_name(kind, name) == name


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize(
    "bad",
    [
        "",
        "1shop",
        "-shop",
        "_shop",
        "Shop",
        "shop name",
        "shop.name",
        "shop/name",
        "shop\n",
        "shop\x00",
        "../x",
        "shöp",
        "a" * 200,
    ],
)
def test_nothing_else_is_a_name(kind: str, bad: str) -> None:
    assert not is_name(kind, bad)
    with pytest.raises(ValidationError) as refused:
        check_name(kind, bad)
    assert refused.value.field == "name"
    assert refused.value.details


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_a_name_that_is_not_text_is_refused_not_crashed_on(kind: str) -> None:
    for bad in (None, 3, b"shop", ["shop"], {"name": "shop"}):
        with pytest.raises(ValidationError):
            check_name(kind, bad)


def test_the_longest_name_of_each_kind_is_the_limit_of_where_it_travels() -> None:
    assert {kind: RULES[kind].max_length for kind in sorted(RULES)} == {
        "alert_rule": 48,
        "channel": 32,
        "db_instance": 24,
        "probe": 40,
        "replica": 20,
        "team_channel": 32,
    }
    for kind, rule in RULES.items():
        assert is_name(kind, "a" * rule.max_length)
        assert not is_name(kind, "a" * (rule.max_length + 1))


def test_a_team_channel_gives_the_room_the_team_prefix_leaves() -> None:
    # The key in the configuration is ``t<id>-<name>`` and the whole is a channel name of 32.
    assert is_name("team_channel", "a" * 28, max_length=28)
    assert not is_name("team_channel", "a" * 29, max_length=28)
    assert not is_name("team_channel", "ok", max_length=0)


def test_a_replica_is_underscores_because_it_becomes_a_role_and_a_slot() -> None:
    assert check_name("replica", "eu_west") == "eu_west"
    for bad in ("eu-west", "eu__west", "_eu", "eu_"):
        assert not is_name("replica", bad), bad


def test_an_instance_is_dashes_because_it_becomes_a_unit_a_project_and_a_volume() -> None:
    assert check_name("db_instance", "shop-cache") == "shop-cache"
    for bad in ("shop_cache", "shop--cache", "-shop", "shop-"):
        assert not is_name("db_instance", bad), bad


def test_every_kind_says_where_its_name_goes_when_it_is_narrow() -> None:
    narrow = {kind for kind, rule in RULES.items() if rule.shape != "words"}

    assert narrow == {"replica", "db_instance"}
    assert all(RULES[kind].why for kind in narrow)


def test_the_help_of_a_field_is_the_rule_and_a_sentence_with_a_code() -> None:
    help_ = rule_for("probe").help()

    assert isinstance(help_, ServerText)
    assert help_.code == "names.rule.words"
    assert help_.params == {"max": 40}
    assert help_.text == (
        "Use lower-case letters, digits, '-' and '_', starting with a letter, "
        "at most 40 characters."
    )
    assert rule_for("replica").help().code == "names.rule.underscores"
    assert rule_for("db_instance").help().code == "names.rule.dashes"


def test_a_refusal_carries_the_help_that_comes_from_the_rule() -> None:
    with pytest.raises(ValidationError) as refused:
        check_name("probe", "Bad Name")

    assert "Invalid probe name: 'Bad Name'" == refused.value.message
    assert rule_for("probe").help().text in refused.value.details


def test_the_message_of_each_kind_names_what_it_is_the_name_of() -> None:
    messages = {}
    for kind in sorted(KINDS):
        with pytest.raises(ValidationError) as refused:
            check_name(kind, "Bad Name")
        messages[kind] = refused.value.message

    assert messages == {
        "alert_rule": "Invalid rule name: 'Bad Name'",
        "channel": "Invalid channel name: 'Bad Name'",
        "db_instance": "'Bad Name' cannot be the name of a database instance",
        "probe": "Invalid probe name: 'Bad Name'",
        "replica": "'Bad Name' cannot be the name of a replica",
        "team_channel": "Invalid channel name: 'Bad Name'",
    }


def test_the_pattern_the_console_checks_agrees_with_the_one_the_server_does() -> None:
    # The console gets the rule as a regular expression and a length. The two languages must
    # accept the same names, so the expression is checked here the way a browser would use it.
    samples = ["a", "ab-c", "ab_c", "a--b", "a__b", "a-", "_a", "A", "a b", "a1", "1a", ""]
    for kind, rule in RULES.items():
        browser = re.compile(rule.javascript_pattern)
        for sample in samples:
            ours = is_name(kind, sample)
            theirs = browser.fullmatch(sample) is not None and 1 <= len(sample) <= rule.max_length
            assert ours == theirs, (kind, sample)


def test_the_typescript_file_the_console_reads_is_what_the_rules_generate() -> None:
    root = Path(__file__).resolve().parents[1]
    committed = (root / "panel" / "src" / "api" / "nameRules.gen.ts").read_text(encoding="utf-8")

    assert committed == typescript_rules(), (
        "panel/src/api/nameRules.gen.ts is stale: run python scripts/generate_name_rules.py"
    )


def test_the_generator_script_checks_and_writes_the_same_file(tmp_path: Path) -> None:
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "generate_name_rules.py"
    spec = importlib.util.spec_from_file_location("generate_name_rules", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--check"]) == 0
    stale = tmp_path / "nameRules.gen.ts"
    stale.write_text("// old\n", encoding="utf-8")
    assert module.main(["--check", "--output", str(stale)]) == 1
    assert module.main(["--output", str(stale)]) == 0
    assert stale.read_text(encoding="utf-8") == typescript_rules()
