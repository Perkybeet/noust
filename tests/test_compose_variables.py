# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The variables a Compose file asks for (checklist B-47).

Pinned here:

- what counts as a reference, in Compose's own grammar (``${NAME}``, ``$NAME``, the four
  operators, a default that is another variable, ``$$``), found in the parsed document and never
  in a comment;
- what each variable's state is against the ``.env`` and the process: set, default, unset, and
  missing when the file requires a value;
- that a deploy and an update refuse before building when a required value is missing, naming it
  with the file's own message and how to set it, and never when a preview is built;
- that values are never part of the answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.exceptions import DeploymentError
from noust.deployers.helpers import compose_variables as cv

# ---------------------------------------------------------------------------
# The grammar
# ---------------------------------------------------------------------------


def uses(text: str) -> list[tuple[str, str | None, str]]:
    return [(use.name, use.operator, use.argument) for use in cv.scan(text)]


def test_a_plain_reference_in_braces_or_bare() -> None:
    assert uses("${PORT}:80 and $HOST") == [("PORT", None, ""), ("HOST", None, "")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("${A:-x}", [("A", ":-", "x")]),
        ("${A-x}", [("A", "-", "x")]),
        ("${A:?needs A}", [("A", ":?", "needs A")]),
        ("${A?needs A}", [("A", "?", "needs A")]),
        ("${A:+yes}", [("A", ":+", "yes")]),
        ("${A+yes}", [("A", "+", "yes")]),
    ],
)
def test_each_operator_is_read_with_its_word(text: str, expected: list[Any]) -> None:
    assert uses(text) == expected


def test_a_default_that_is_another_variable_is_found_too() -> None:
    assert uses("${A:-${B:-b}}") == [("A", ":-", "${B:-b}"), ("B", ":-", "b")]


def test_a_doubled_dollar_is_a_dollar_not_a_reference() -> None:
    assert uses("price: $$5 and $${NOT}") == []


def test_what_is_not_a_name_is_left_alone() -> None:
    assert uses("$ 5, $1, ${}, ${1X}, cost $") == []


def test_an_unclosed_brace_ends_the_scan_without_failing() -> None:
    assert uses("${A} then ${B") == [("A", None, "")]


def test_a_word_with_a_closing_brace_inside_a_default_is_kept_whole() -> None:
    assert uses("${A:-${B}-end}") == [("A", ":-", "${B}-end"), ("B", None, "")]


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

COMPOSE = """
name: shop
# ${IN_A_COMMENT} is not a variable
services:
  web:
    image: shop:${TAG:-latest}
    ports:
      - "${PORT}:80"
    environment:
      DATABASE_URL: postgres://app:${DB_PASSWORD:?set the database password}@db/app
      SECRET_KEY: ${SECRET_KEY:?}
    volumes:
      - ${DATA_DIR:-./data}:/data
  worker:
    image: shop:${TAG:-latest}
    environment:
      - QUEUE=${QUEUE-default}
      - REGION=${REGION}
volumes:
  cache:
    name: ${CACHE_VOLUME:-cache}
x-extra:
  note: ${EXTENSION_VAR}
"""


def report(
    env: dict[str, str] | None = None, process: dict[str, str] | None = None
) -> cv.VariableReport:
    return cv.judge(
        yaml.safe_load(COMPOSE),
        env or {},
        file="compose.yaml",
        env_file="/srv/shop/.env" if env else None,
        process={} if process is None else process,
    )


def by_name(found: cv.VariableReport) -> dict[str, cv.ComposeVariable]:
    return {variable.name: variable for variable in found.variables}


def test_every_variable_the_file_asks_for_is_found_once_with_the_services_that_use_it() -> None:
    found = by_name(report())

    assert set(found) == {
        "TAG",
        "PORT",
        "DB_PASSWORD",
        "SECRET_KEY",
        "DATA_DIR",
        "QUEUE",
        "REGION",
        "CACHE_VOLUME",
        "EXTENSION_VAR",
    }
    assert found["TAG"].used_in == ("web", "worker")
    assert found["CACHE_VOLUME"].used_in == ("volumes",)
    assert found["EXTENSION_VAR"].used_in == ("x-extra",)
    assert "IN_A_COMMENT" not in found, "a comment is not the document"


def test_a_variable_nothing_gives_is_missing_when_required_default_when_it_has_one_unset_otherwise() -> (
    None
):
    found = by_name(report())

    assert found["DB_PASSWORD"].status == cv.MISSING
    assert (
        found["DB_PASSWORD"].required
        and found["DB_PASSWORD"].message == "set the database password"
    )
    assert found["SECRET_KEY"].status == cv.MISSING and found["SECRET_KEY"].message == ""
    assert found["TAG"].status == cv.DEFAULT and found["TAG"].default == "latest"
    assert found["DATA_DIR"].default == "./data"
    assert found["PORT"].status == cv.UNSET and found["REGION"].status == cv.UNSET
    assert [variable.name for variable in report().missing] == ["DB_PASSWORD", "SECRET_KEY"]
    assert {variable.name for variable in report().unset} == {"PORT", "REGION", "EXTENSION_VAR"}


def test_a_value_in_the_env_file_sets_it_and_says_where_from() -> None:
    found = by_name(report({"DB_PASSWORD": "x", "SECRET_KEY": "y", "PORT": "8080"}))

    assert (
        found["DB_PASSWORD"].status == cv.SET and found["DB_PASSWORD"].source == cv.SOURCE_ENV_FILE
    )
    assert found["PORT"].status == cv.SET
    assert report({"DB_PASSWORD": "x", "SECRET_KEY": "y"}).missing == ()


def test_a_value_in_the_process_environment_counts_and_wins() -> None:
    found = by_name(report({"DB_PASSWORD": "from-file"}, {"DB_PASSWORD": "from-process"}))

    assert found["DB_PASSWORD"].source == cv.SOURCE_PROCESS


def test_an_empty_value_does_not_satisfy_a_colon_form_but_does_a_bare_question_mark() -> None:
    """``${A:?}`` and ``${A:-x}`` read empty as unset; ``${A?}`` and ``${A-x}`` do not."""
    document = {
        "services": {"a": {"environment": {"X": "${EMPTY_COLON:?need}", "Y": "${EMPTY_BARE?need}"}}}
    }

    found = by_name(cv.judge(document, {"EMPTY_COLON": "", "EMPTY_BARE": ""}, process={}))

    assert found["EMPTY_COLON"].status == cv.MISSING
    assert found["EMPTY_BARE"].status == cv.SET


def test_the_variables_composes_and_every_process_define_are_not_the_stacks_to_give() -> None:
    document = {"services": {"a": {"image": "${COMPOSE_PROJECT_NAME}-${HOME}-${PATH}-${REAL}"}}}

    assert [v.name for v in cv.judge(document, {}, process={}).variables] == ["REAL"]


def test_a_document_that_is_not_a_mapping_asks_for_nothing() -> None:
    assert cv.judge(None, {}, process={}).variables == ()
    assert cv.judge(["${A}"], {}, process={}).variables == ()


def test_the_report_never_holds_a_value() -> None:
    dump = str(report({"DB_PASSWORD": "hunter2-secret", "SECRET_KEY": "y"}).to_dict())

    assert "hunter2-secret" not in dump


def test_a_pathologically_deep_document_is_not_followed_forever() -> None:
    node: Any = "${DEEP}"
    for _ in range(200):
        node = {"k": node}

    assert cv.judge({"services": {"a": node}}, {}, process={}).variables == ()


# ---------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------


def test_a_missing_required_value_is_refused_naming_each_with_its_own_message_and_how_to_set_it() -> (
    None
):
    with pytest.raises(DeploymentError) as caught:
        cv.require(report(), domain="shop.example.com")

    error = caught.value
    assert "compose.yaml requires values for DB_PASSWORD, SECRET_KEY" in error.message
    assert "DB_PASSWORD: set the database password" in error.details
    assert "noust env set shop.example.com DB_PASSWORD=<value> SECRET_KEY=<value>" in error.details
    assert "nothing that serves was touched" in error.details


def test_one_missing_value_is_said_in_the_singular() -> None:
    with pytest.raises(DeploymentError) as caught:
        cv.require(report({"SECRET_KEY": "y"}), domain="shop.example.com")

    assert "requires a value for DB_PASSWORD" in caught.value.message
    assert "Set it with" in caught.value.details


def test_nothing_missing_is_not_refused() -> None:
    cv.require(report({"DB_PASSWORD": "x", "SECRET_KEY": "y"}), domain="shop.example.com")


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------


def test_the_env_beside_the_compose_file_wins_over_the_one_at_the_root(tmp_path: Path) -> None:
    (tmp_path / "deploy").mkdir()
    (tmp_path / ".env").write_text("A=root\nB=root\n")
    (tmp_path / "deploy" / ".env").write_text("A=project\n")

    values, used = cv.read_values(tmp_path / "deploy" / "compose.yaml", tmp_path)

    assert values == {"A": "project", "B": "root"}
    assert used == str(tmp_path / "deploy" / ".env")


def test_no_env_is_no_values(tmp_path: Path) -> None:
    assert cv.read_values(tmp_path / "compose.yaml", tmp_path) == ({}, None)


def test_a_linked_env_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.env"
    outside.write_text("A=1\n")
    root = tmp_path / "app"
    root.mkdir()
    (root / ".env").symlink_to(outside)

    assert cv.read_values(root / "compose.yaml", root) == ({}, None)
