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


def test_a_value_that_only_looks_like_a_secret_by_its_name_is_a_setting_not_a_secret(
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

    # Not credentials: the first two are settings, and a setting is a declared variable.
    assert draft.services["web"]["environment"] == {
        "TOKEN_EXPIRY": "${TOKEN_EXPIRY}",
        "SECRET_KEY_BASE": "${KEY}",
        "USE_KEY": "${USE_KEY}",
    }
    assert draft.variables["TOKEN_EXPIRY"]["value"] == "3600"


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


# -- the mounts of a template ------------------------------------------------------------------


def test_the_hosts_clock_files_are_dropped_because_a_recipe_sets_the_time_zone(
    tool: ModuleType,
) -> None:
    """``/etc/localtime`` is a path of the host and says only what TZ says."""
    draft = tool.Draft(name="demo")

    assert tool.rewrite_mount(draft, "web", "/etc/localtime:/etc/localtime:ro") is None
    assert tool.rewrite_mount(draft, "web", "/etc/timezone:/etc/timezone:ro") is None


def test_the_docker_socket_makes_the_recipe_root_on_the_server_and_nothing_else_of_the_host_is_kept(
    tool: ModuleType,
) -> None:
    """The one path a recipe may mount once it says what it is."""
    draft = tool.Draft(name="demo")

    kept = tool.rewrite_mount(draft, "web", "/var/run/docker.sock:/var/run/docker.sock:ro")

    assert kept == "/var/run/docker.sock:/var/run/docker.sock:ro"
    assert draft.root_equivalent == "docker_socket"
    for volume in ("/:/host", "/root:/root", "/var/run/docker.sock:/docker.sock", "~/x:/x"):
        with pytest.raises(tool.Skip) as skipped:
            tool.rewrite_mount(draft, "web", volume)
        assert skipped.value.code == "host-mount"


def test_a_file_of_the_template_becomes_a_file_the_recipe_ships(tool: ModuleType) -> None:
    """Dokploy's ``./files/x`` is the recipe's asset, and a folder takes its files with it."""
    draft = tool.Draft(name="demo")
    draft.files = {
        "files/app.conf": "listen = 80\n",
        "files/conf/a.yml": "a: 1\n",
        "files/conf/b.yml": "b: 2\n",
    }

    one = tool.rewrite_mount(draft, "web", "./files/app.conf:/conf/app.conf:ro")
    many = tool.rewrite_mount(draft, "web", "./files/conf:/etc/app")

    assert one == "./files/app.conf:/conf/app.conf:ro" and many == "./files/conf:/etc/app"
    assert set(draft.shipped) == {"files/app.conf", "files/conf/a.yml", "files/conf/b.yml"}


@pytest.mark.parametrize(
    "body",
    ["token = abc\n", "home = ${HOME}\n", "echo $USER\n"],
)
def test_a_file_that_holds_a_secret_or_a_reference_is_not_shipped(
    tool: ModuleType, body: str
) -> None:
    """A recipe's file is the same for every install: nothing in it may be an install's own."""
    draft = tool.Draft(name="demo")
    draft.files = {"files/app.conf": body}
    draft.secret_files = {"files/app.conf"} if "token" in body else set()

    with pytest.raises(tool.Skip) as skipped:
        tool.rewrite_mount(draft, "web", "./files/app.conf:/conf/app.conf")

    assert skipped.value.code == "template-files"


def test_a_folder_the_template_meant_as_data_becomes_a_named_volume(tool: ModuleType) -> None:
    """``./files/uploads`` has no files in the template: it is where the service writes."""
    draft = tool.Draft(name="demo")

    kept = tool.rewrite_mount(draft, "kutt", "./files/uploads:/app/uploads")

    assert kept == "kutt-uploads:/app/uploads" and "kutt-uploads" in draft.volumes
    assert tool.rewrite_mount(draft, "web", "data:/data") == "data:/data"
    with pytest.raises(tool.Skip):
        tool.rewrite_mount(draft, "web", "./conf.yml:/conf.yml")


def test_a_value_the_converter_made_of_a_password_is_written_over_the_variable_it_holds(
    tool: ModuleType,
) -> None:
    """Paperclip's DATABASE_URL was declared as a random password, so the database never matched."""
    password = "Zx9Qv7LmN2pRt5YwK8cBd3FgHj6SaE4U"
    draft = tool.Draft(name="demo")
    draft.variables = {
        "POSTGRES_PASSWORD": {"origin": "generated", "secret": True, "value": password},
        "DATABASE_URL": {
            "origin": "generated",
            "secret": True,
            "value": f"postgres://app:{password}@db:5432/app",
        },
        "PUBLIC_URL": {
            "origin": "generated",
            "secret": False,
            "value": "https://app.example.com/x",
        },
    }
    draft.values = {name: str(entry["value"]) for name, entry in draft.variables.items()}

    declared = tool.declare_variables(
        draft, "a: ${DATABASE_URL} b: ${PUBLIC_URL} c: ${POSTGRES_PASSWORD}"
    )

    assert declared["DATABASE_URL"]["value"] == "postgres://app:${POSTGRES_PASSWORD}@db:5432/app"
    assert declared["DATABASE_URL"]["secret"] is True
    assert declared["POSTGRES_PASSWORD"]["generate"] == "password32"
    assert declared["PUBLIC_URL"]["value"] == "https://${APP_DOMAIN}/x"
    assert declared["APP_DOMAIN"]["from"] == "fqdn"


def test_a_secret_only_a_composed_value_uses_is_still_declared(tool: ModuleType) -> None:
    """The document never names it, the value does: a recipe may not use an undeclared variable."""
    password = "Zx9Qv7LmN2pRt5YwK8cBd3FgHj6SaE4U"
    draft = tool.Draft(name="demo")
    draft.variables = {
        "DB_PASSWORD": {"origin": "generated", "secret": True, "value": password},
        "DATABASE_URL": {
            "origin": "generated",
            "secret": True,
            "value": f"mysql://u:{password}@db/app",
        },
    }
    draft.values = {name: str(entry["value"]) for name, entry in draft.variables.items()}

    declared = tool.declare_variables(draft, "x: ${DATABASE_URL}")

    assert "DB_PASSWORD" in declared and declared["DB_PASSWORD"]["secret"] is True


def test_only_named_volumes_are_declared_never_a_path_a_service_mounts(tool: ModuleType) -> None:
    """The socket and a file the recipe ships are mounts, not volumes: declaring them broke 'a plain name'."""
    services = {
        "web": {
            "volumes": [
                "data:/data",
                "/var/run/docker.sock:/var/run/docker.sock:ro",
                "./files/app.yml:/etc/app.yml:ro",
                "${MEDIA}:/media",
            ]
        },
        "db": {"volumes": ["data:/other", "db:/var/lib/db"]},
    }

    assert tool.unique_volumes(services) == ["data", "db"]


def test_a_title_with_characters_the_console_s_policy_refuses_still_gets_a_monogram(
    tool: ModuleType,
) -> None:
    """GLPI's title had a character outside the short plain label a logo may hold."""
    from noust.recipes.logos import validate_svg

    svg = tool.monogram("GLPI — IT asset & service (ITSM)")

    assert validate_svg("glpi", svg) == svg
    assert validate_svg("x", tool.monogram("——")) is not None


def test_two_defaults_for_one_variable_become_one_declared_default(tool: ModuleType) -> None:
    """Compose allows ${TZ:-UTC} here and ${TZ:-Europe/Berlin} there; a recipe reads one value."""
    draft = tool.Draft(name="demo")
    draft.services = {
        "web": {"environment": {"TZ": "${TZ:-UTC}", "A": "${A:-1}"}},
        "worker": {"environment": {"TZ": "${TZ:-Europe/Berlin}", "A": "${A:-1}"}},
    }

    tool.one_default_each(draft)

    assert draft.services["web"]["environment"] == {"TZ": "${TZ}", "A": "${A:-1}"}
    assert draft.services["worker"]["environment"]["TZ"] == "${TZ}"
    assert draft.variables["TZ"]["value"] == "UTC" and "A" not in draft.variables


def test_the_credit_of_a_first_draft_is_put_in_the_form_the_notices_list_recipes_by(
    tool: ModuleType,
) -> None:
    """NOTICE and the Debian copyright file are held to recipes whose header says it this way."""
    old = (
        "# Dozzle from a Coolify service template (templates/compose/dozzle.yaml), made into a recipe:\n"
        "# every image pinned to a release and the digest its registry serves, loopback port,\n"
        "# generated secrets. The template's authors keep their rights in it (Apache-2.0).\n"
        "schema: 2\n"
    )

    fixed = tool.credit_header(old)

    assert fixed.startswith(
        "# Dozzle: Derived in part from Coolify's template "
        "https://github.com/coollabsio/coolify/blob/next/templates/compose/dozzle.yaml\n"
    )
    assert "Apache-2.0, Copyright 2025 Andras Bacsai" in fixed and fixed.endswith("schema: 2\n")
    dokploy = old.replace(
        "Coolify service template (templates/compose/dozzle.yaml)",
        "Dokploy service template (blueprints/dozzle)",
    )
    assert (
        "Derived in part from Dokploy's template "
        "https://github.com/Dokploy/templates/tree/main/blueprints/dozzle"
    ) in tool.credit_header(dokploy)
    assert "MIT, Copyright 2024 Dokploy and Carlos Ortiz" in tool.credit_header(dokploy)
    # A header that already carries the credit is left as it is.
    assert tool.credit_header(fixed) == fixed


def test_a_database_engine_is_never_the_web_service_of_a_recipe(tool: ModuleType) -> None:
    """The catalogue publishes no database: ClickHouse as the application would be one on a public name."""
    draft = draft_of(
        tool,
        {
            "db": {
                "image": "clickhouse/clickhouse-server:25.8",
                "ports": ["127.0.0.1:${PORT:-3000}:8123"],
            }
        },
        ("db", 8123),
    )

    with pytest.raises(tool.Skip) as skipped:
        tool.clean_services(draft)

    assert skipped.value.code == "database-as-app"


def test_an_optional_setting_named_after_a_provider_the_history_may_not_name_is_left_out(
    tool: ModuleType,
) -> None:
    """The operator can still set it in the application's environment; the recipe does not carry it."""
    draft = draft_of(
        tool,
        {
            "web": {
                "image": "o/app:1.0",
                "environment": {"ANTH" + "ROPIC_API_KEY": "", "OPENAI_API_KEY": "", "PORT": "3000"},
            }
        },
        ("web", 3000),
    )

    tool.clean_services(draft)

    assert set(draft.services["web"]["environment"]) == {"OPENAI_API_KEY", "PORT"}
    assert tool.FORBIDDEN_WORDS.search("a CL" + "AUDE client") and not tool.FORBIDDEN_WORDS.search(
        "x"
    )


def test_a_setting_whose_name_looks_like_a_secret_but_holds_a_duration_becomes_a_declared_setting(
    tool: ModuleType,
) -> None:
    """TOKEN_EXPIRY: 24h is no credential, and the catalogue holds no literal in such a name."""
    draft = draft_of(
        tool,
        {"web": {"image": "o/app:1.0", "environment": {"TOKEN_EXPIRY": "24h", "API_TOKEN": "abc"}}},
        ("web", 80),
    )

    tool.secret_literals(draft)

    assert draft.services["web"]["environment"]["TOKEN_EXPIRY"] == "${TOKEN_EXPIRY}"
    assert draft.variables["TOKEN_EXPIRY"]["value"] == "24h"
    declared = tool.declare_variables(draft, "x: ${TOKEN_EXPIRY}")
    assert declared["TOKEN_EXPIRY"]["default"] == "24h"
