# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""scripts/recipe_import.py and scripts/recipe_logos.py: the rewrite of a template into a recipe, offline."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load(name: str) -> ModuleType:
    """Load a script of scripts/ as a module."""
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    """The importer."""
    return load("recipe_import")


@pytest.fixture(scope="module")
def logos() -> ModuleType:
    """The logos tool."""
    return load("recipe_logos")


def draft_of(tool: ModuleType, services: dict[str, Any], routed: tuple[str, int]) -> Any:
    """A draft with these services."""
    draft = tool.Draft(name="demo", routed=routed)
    draft.services = services
    return draft


# -- eligibility -------------------------------------------------------------------------------


def facts(**changes: Any) -> dict[str, Any]:
    """What GitHub says about a project that is eligible."""
    base = {
        "repository": "o/app",
        "stars": 5000,
        "archived": False,
        "license": "MIT",
        "pushed": "2026-10-01T00:00:00Z",
        "released": "2026-09-01T00:00:00Z",
    }
    return {**base, **changes}


def test_a_project_is_eligible_when_it_is_alive_known_and_free_to_run(tool: ModuleType) -> None:
    """docs/contributing-recipes.md: stars, a release in the year, not archived, a licence."""
    assert tool.not_eligible(facts()) is None


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"archived": True}, "archived"),
        ({"stars": 999}, "stars"),
        ({"released": "2020-01-01T00:00:00Z"}, "stale"),
        ({"license": "NOASSERTION"}, "licence"),
        ({"license": None}, "licence"),
        ({"license": "BUSL-1.1"}, "licence"),
    ],
)
def test_a_project_that_is_not_eligible_says_which_rule_it_breaks(
    tool: ModuleType, changes: dict[str, Any], code: str
) -> None:
    """The reason is a short code the survey counts."""
    verdict = tool.not_eligible(facts(**changes))

    assert verdict is not None and verdict.code == code


def test_a_repository_that_could_not_be_read_is_not_eligible(tool: ModuleType) -> None:
    """No facts, no recipe."""
    assert tool.not_eligible(None).code == "no-repository"


# -- the services ------------------------------------------------------------------------------


def test_a_service_is_rewritten_into_what_the_closed_schema_accepts(tool: ModuleType) -> None:
    """Labels and networks go; the environment is a mapping; one loopback port; restart is set."""
    draft = draft_of(
        tool,
        {
            "web": {
                "image": "o/app:1.0",
                "labels": ["traefik.enable=true"],
                "networks": ["coolify"],
                "container_name": "web",
                "environment": ["A=1", "B=${B:-2}", "EMPTY"],
                "ports": ["127.0.0.1:${PORT:-3000}:8080"],
                "volumes": ["data:/data"],
                "restart": "always",
            }
        },
        ("web", 8080),
    )

    tool.clean_services(draft)

    web = draft.services["web"]
    assert set(web) == {"image", "environment", "ports", "volumes", "restart"}
    assert web["environment"] == {"A": "1", "B": "${B:-2}", "EMPTY": ""}
    assert web["ports"] == ["127.0.0.1:${PORT}:8080"]
    assert web["restart"] == "unless-stopped"


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ({"privileged": True}, "uses-privileged"),
        ({"cap_add": ["NET_ADMIN"]}, "uses-cap_add"),
        ({"devices": ["/dev/fuse"]}, "uses-devices"),
        ({"network_mode": "host"}, "uses-network_mode"),
        ({"build": "."}, "uses-build"),
        ({"volumes": ["/var/run/docker.sock:/var/run/docker.sock"]}, "host-mount"),
        ({"volumes": ["./conf:/etc/app"]}, "host-mount"),
        ({"ports": ["53:53/udp"]}, "extra-port"),
        ({"healthcheck": {"test": ["CMD", "true"]}, "secrets": ["x"]}, "service-keys"),
    ],
)
def test_what_a_recipe_may_not_ask_of_the_host_is_a_reason_to_skip_the_template(
    tool: ModuleType, spec: dict[str, Any], code: str
) -> None:
    """Said by name, never guessed away."""
    draft = draft_of(tool, {"web": {"image": "o/app:1.0", **spec}}, ("web", 8080))

    with pytest.raises(tool.Skip) as skipped:
        tool.clean_services(draft)

    assert skipped.value.code == code


def test_what_waits_for_a_service_with_a_health_check_waits_for_it_to_answer(
    tool: ModuleType,
) -> None:
    """depends_on becomes service_healthy for a target that can say it is healthy."""
    draft = draft_of(
        tool,
        {
            "web": {"image": "o/app:1.0", "depends_on": ["db", "cache"]},
            "db": {"image": "postgres:16", "healthcheck": {"test": ["CMD", "pg_isready"]}},
            "cache": {"image": "redis:7"},
        },
        ("web", 8080),
    )

    tool.clean_services(draft)

    assert draft.services["web"]["depends_on"] == {
        "db": {"condition": "service_healthy"},
        "cache": {"condition": "service_started"},
    }


def test_a_service_that_waits_for_one_the_template_does_not_have_is_skipped(
    tool: ModuleType,
) -> None:
    """A dangling dependency is the template's, not something to fill in."""
    draft = draft_of(tool, {"web": {"image": "o/app:1.0", "depends_on": ["db"]}}, ("web", 80))

    with pytest.raises(tool.Skip, match="depends-on-missing"):
        tool.clean_services(draft)


# -- credentials -------------------------------------------------------------------------------


def test_a_credential_a_template_wrote_becomes_one_generated_variable_everywhere(
    tool: ModuleType,
) -> None:
    """The same literal in two services and in a connection string is one secret."""
    draft = draft_of(
        tool,
        {
            "web": {
                "image": "o/app:1.0",
                "environment": {
                    "DB_PASSWORD": "example",
                    "DATABASE_URL": "postgres://app:example@db/app",
                    "DEBUG": "true",
                },
            },
            "db": {"image": "postgres:16", "environment": {"POSTGRES_PASSWORD": "example"}},
        },
        ("web", 80),
    )

    tool.secret_literals(draft)

    assert draft.services["web"]["environment"]["DB_PASSWORD"] == "${DB_PASSWORD}"
    assert (
        draft.services["web"]["environment"]["DATABASE_URL"]
        == "postgres://app:${DB_PASSWORD}@db/app"
    )
    assert draft.services["db"]["environment"]["POSTGRES_PASSWORD"] == "${DB_PASSWORD}"
    assert draft.services["web"]["environment"]["DEBUG"] == "true"
    assert draft.variables["DB_PASSWORD"]["origin"] == "generated"


def test_a_value_that_only_looks_like_a_secret_by_its_name_is_left_alone(
    tool: ModuleType,
) -> None:
    """``TOKEN_EXPIRY: 3600`` and ``SECRET_KEY_BASE: ${X}`` are not credentials to replace."""
    draft = draft_of(
        tool,
        {
            "web": {
                "image": "o/app:1.0",
                "environment": {
                    "TOKEN_EXPIRY": "3600",
                    "SECRET_KEY_BASE": "${KEY}",
                    "USE_KEY": "no",
                },
            }
        },
        ("web", 80),
    )

    tool.secret_literals(draft)

    assert draft.services["web"]["environment"] == {
        "TOKEN_EXPIRY": "3600",
        "SECRET_KEY_BASE": "${KEY}",
        "USE_KEY": "no",
    }


@pytest.mark.parametrize(
    ("value", "form"),
    [
        ("Z" * 64, "password64"),
        ("0123456789abcdef" * 4, "hex64"),
        ("0123456789abcdef" * 2, "hex32"),
        ("A" * 43 + "=", "base64_32"),
        ("x" * 20, "password32"),
        (None, "password32"),
    ],
)
def test_a_generated_secret_takes_the_form_its_value_had(
    tool: ModuleType, value: str | None, form: str
) -> None:
    """A hex key stays hex and a long password stays long."""
    assert tool.generator_for("SOME_KEY", value) == form


# -- words -------------------------------------------------------------------------------------


def test_a_description_is_cut_where_a_sentence_ends_and_loses_its_markdown(
    tool: ModuleType,
) -> None:
    """The first sentences that fit, never a word cut in two."""
    text = "A **fast** [wiki](https://x.example). It stores pages. " + "More words. " * 40

    cut = tool.sentence(text, 80)

    assert (
        cut
        == "A fast wiki. It stores pages. More words. More words. More words. More words."[
            : len(cut)
        ]
    )
    assert len(cut) <= 80 and "*" not in cut and "](" not in cut
    assert tool.sentence("short", 100) == "short"


def test_a_category_comes_from_the_words_a_template_gives_and_is_always_in_the_closed_list(
    tool: ModuleType,
) -> None:
    """Never a free-text category."""
    from noust.recipes.model import CATEGORIES

    assert tool.category_of("monitoring", "uptime checks") == "monitoring"
    assert tool.category_of("project management") == "productivity"
    assert tool.category_of("") == "utilities"
    assert tool.category_of("zzz") in CATEGORIES


def test_tags_are_lower_case_words_of_at_most_eight_and_the_category_is_one(
    tool: ModuleType,
) -> None:
    """The recipe schema's rule for tags."""
    tags = tool.tags_of(
        {"tags": "Wiki, Team Docs,  KNOWLEDGE base, a/b, " + ", ".join(f"t{i}" for i in range(12))},
        {"tags": ["notes"]},
        "productivity",
    )

    assert len(tags) <= 8 and len(set(tags)) == len(tags)
    assert tags[:3] == ["wiki", "team", "docs"]
    assert all(tag == tag.lower() and " " not in tag for tag in tags)


def test_one_template_stands_for_a_project_and_the_catalogue_s_own_are_left_out(
    tool: ModuleType,
) -> None:
    """The plainer name of two templates of one project, most starred first."""

    def row(name: str, repository: str, stars: int, **extra: Any) -> dict[str, Any]:
        return {
            "name": name,
            "repository": repository,
            "facts": {"stars": stars},
            "skip": None,
            "have": False,
            **extra,
        }

    rows = [
        row("forgejo-with-mysql", "forgejo/forgejo", 100),
        row("forgejo", "forgejo/forgejo", 100),
        row("big-app", "o/big", 9000),
        row("held", "o/held", 5000),
        row("small", "o/small", 2000),
        row("have", "o/have", 7000, have=True),
        row("skipped", "o/skip", 8000, skip="stars: 1"),
    ]

    queue = tool.plan_queue(rows, {"o/held"})

    assert queue == ["big-app", "small", "forgejo"]


# -- the logos tool ----------------------------------------------------------------------------


def test_a_mark_is_found_by_the_recipe_s_name_or_by_an_alias_and_never_for_a_forbidden_one(
    logos: ModuleType,
) -> None:
    """Plausible has a mark in the collection and is given the monogram anyway."""
    collection = {
        "gitea": {"title": "Gitea", "file": ""},
        "wikidotjs": {"title": "Wiki.js", "file": ""},
        "plausibleanalytics": {"title": "Plausible Analytics", "file": ""},
    }

    assert logos.slug_for("gitea", "Gitea", collection) == "gitea"
    assert logos.slug_for("wikijs", "Wiki.js", collection) == "wikidotjs"
    assert logos.slug_for("anything", "Wiki.js", collection) == "wikidotjs"
    assert logos.slug_for("plausible", "Plausible Analytics", collection) is None
    assert logos.slug_for("nothing", "Nothing", collection) is None


def test_the_marks_in_the_package_are_the_collection_s_own_files() -> None:
    """The package carries each file unchanged from the release its NOTICE names."""
    notice = (
        Path(__file__).resolve().parents[2] / "src/noust/recipes/assets/_simple-icons/NOTICE"
    ).read_text()

    assert "CC0-1.0" in notice and "simple-icons" in notice
    assert "not the brands" in notice or "and not the brands" in notice


def test_the_survey_s_json_round_trips_what_the_plan_reads(
    tool: ModuleType, tmp_path: Path
) -> None:
    """The plan reads the survey file the survey wrote."""
    survey = tmp_path / "survey.json"
    survey.write_text(
        json.dumps(
            [{"name": "a", "repository": "o/a", "facts": {"stars": 3}, "skip": None, "have": False}]
        )
    )

    rows = json.loads(survey.read_text())

    assert tool.plan_queue(rows, set()) == ["a"]


def test_a_cut_description_never_goes_past_its_limit_with_the_ellipsis_included(
    tool: ModuleType,
) -> None:
    """A slogan of 100 characters and three dots was a recipe the schema refused."""
    words = " ".join(["wordy"] * 120)

    for limit in (40, 100, 400):
        cut = tool.sentence(words, limit)
        assert len(cut) <= limit and cut.endswith("...")


def test_docker_hub_is_read_through_a_mirror_only_when_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check that a pin is what Hub serves leaves the mirror unset; a survey of hundreds sets it."""
    pins = load("recipe_pins")
    monkeypatch.setattr(pins, "_token", lambda registry, repository: "token")

    monkeypatch.delenv("NOUST_PINS_MIRROR", raising=False)
    assert pins._endpoint("docker.io", "library/redis") == ("registry-1.docker.io", "token")
    assert pins._endpoint("ghcr.io", "o/app") == ("ghcr.io", "token")

    monkeypatch.setenv("NOUST_PINS_MIRROR", "mirror.example.test")
    assert pins._endpoint("docker.io", "library/redis") == ("mirror.example.test", None)
    # Only Hub's images go through it.
    assert pins._endpoint("ghcr.io", "o/app") == ("ghcr.io", "token")
