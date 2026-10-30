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
            "cache": {"image": "o/cache:7"},
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
        ("0123456789abcdef" * 8, "hex64"),  # imgproxy's template asked for 128 digits
        ("0123456789abcdef" * 3, "hex32"),
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


@pytest.mark.parametrize(
    ("volume", "kept"),
    [
        ("${DB_DATA_LOCATION}:/var/lib/postgresql/data", "web-data:/var/lib/postgresql/data"),
        ("${VOLUME_US:-./metadata}:/var/lib/postgresql/data", "web-data:/var/lib/postgresql/data"),
        ("/apprise-api/config:/config", "web-config:/config"),
        ("/tmp/kestra-wd:/tmp/kestra-wd", "web-kestra-wd:/tmp/kestra-wd"),
        ("${MEDIA_PATH}:/media:ro", "web-media:/media:ro"),
    ],
)
def test_a_folder_of_the_host_that_holds_a_service_s_data_becomes_a_named_volume(
    tool: ModuleType, volume: str, kept: str
) -> None:
    """A template written for one person's server mounted the folder; the stack keeps it in a volume."""
    draft = tool.Draft(name="demo")

    assert tool.rewrite_mount(draft, "web", volume) == kept
    assert kept.split(":", 1)[0] in draft.volumes


@pytest.mark.parametrize(
    "volume",
    [
        "/:/userdata:ro",
        "/etc/nginx:/etc/nginx",
        "/var/lib/docker/containers/:/var/lib/docker/containers/",
        "/var/log:/logs",
        "/etc/resolv.conf:/etc/resolv.conf:ro",
        "/data/app.db:/app.db",
        "/data:/etc/app.conf",
        "/var/run/docker.sock:/docker.sock",
    ],
)
def test_a_path_that_is_the_server_s_own_or_a_file_is_still_not_a_volume(
    tool: ModuleType, volume: str
) -> None:
    """The system's folders, the root and single files stay a reason to skip."""
    draft = tool.Draft(name="demo")

    with pytest.raises(tool.Skip) as skipped:
        tool.rewrite_mount(draft, "web", volume)

    assert skipped.value.code == "host-mount"


def test_a_postgresql_18_volume_is_mounted_on_the_parent_of_the_data_folder(
    tool: ModuleType,
) -> None:
    """The 18 image refuses a volume at `/var/lib/postgresql/data`: Kestra's database kept failing."""
    services = {
        "db": {
            "image": "postgres:18.6@sha256:" + "a" * 64,
            "volumes": ["data:/var/lib/postgresql/data"],
        },
        "vectors": {
            "image": "pgvector/pgvector:pg18",
            "volumes": ["v:/var/lib/postgresql/data:rw"],
        },
        "old": {"image": "postgres:17.11-alpine", "volumes": ["o:/var/lib/postgresql/data"]},
        "web": {"image": "o/app:18.1", "volumes": ["w:/var/lib/postgresql/data"]},
    }

    moved = tool.fix_postgres_data_directory(services)

    assert moved == ["db", "vectors"]
    assert services["db"]["volumes"] == ["data:/var/lib/postgresql"]
    assert services["vectors"]["volumes"] == ["v:/var/lib/postgresql:rw"]
    assert services["old"]["volumes"] == ["o:/var/lib/postgresql/data"]
    assert services["web"]["volumes"] == ["w:/var/lib/postgresql/data"]


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


def test_a_file_that_holds_a_secret_nobody_named_is_not_shipped(tool: ModuleType) -> None:
    """A recipe's file is the same for every install: a secret in it must come from a variable."""
    draft = tool.Draft(name="demo")
    draft.files = {"files/app.conf": "token = abc\n"}
    draft.secret_files = {"files/app.conf"}

    with pytest.raises(tool.Skip) as skipped:
        tool.rewrite_mount(draft, "web", "./files/app.conf:/conf/app.conf")

    assert skipped.value.code == "template-files"


@pytest.mark.parametrize(
    ("body", "rendered"),
    [("home = ${HOME}\n", "home = $${HOME}\n"), ("echo $USER\n", "echo $$USER\n")],
)
def test_a_dollar_of_a_file_s_own_is_written_for_the_renderer_and_the_file_is_rendered(
    tool: ModuleType, body: str, rendered: str
) -> None:
    """The renderer reads `${NAME}` as a variable of the stack, so the file's own are doubled."""
    draft = tool.Draft(name="demo")
    draft.files = {"files/app.conf": body}

    tool.rewrite_mount(draft, "web", "./files/app.conf:/conf/app.conf")

    assert draft.rendered == {"files/app.conf": rendered} and draft.shipped == {}


@pytest.mark.parametrize("folder", ["./nginx/ssl", "./data", "./kutt/custom", "./backups"])
def test_a_folder_named_like_data_beside_the_template_is_the_service_s_to_keep_what_it_makes(
    tool: ModuleType, folder: str
) -> None:
    """Dify mounts `./nginx/ssl`, which the template holds nothing in: a named volume is that folder."""
    draft = tool.Draft(name="demo")

    kept = tool.rewrite_mount(draft, "web", f"{folder}:/srv/x")

    assert (
        kept.endswith(":/srv/x")
        and not kept.startswith("./")
        and kept.split(":")[0] in draft.volumes
    )


@pytest.mark.parametrize("folder", ["./conf", "./config", "./../data"])
def test_a_folder_that_is_configuration_or_leaves_the_template_is_left_to_a_person(
    tool: ModuleType, folder: str
) -> None:
    """What the operator would have written, or a path out of the template's directory."""
    draft = tool.Draft(name="demo")

    with pytest.raises(tool.Skip) as skipped:
        tool.rewrite_mount(draft, "web", f"{folder}:/srv/x")

    assert skipped.value.code == "host-mount"


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


def test_two_variables_the_converter_gave_one_random_value_stay_one_secret(
    tool: ModuleType,
) -> None:
    """Dokploy expands one `db_pass` into the database's and the application's variable alike."""
    draft = draft_of(
        tool,
        {
            "db": {"image": "o/db:1", "environment": {"DB_PASSWORD": "${DB_PASSWORD}"}},
            "web": {"image": "o/app:1", "environment": {"APP_DB_PASSWORD": "${APP_DB_PASSWORD}"}},
        },
        ("web", 80),
    )
    value = "A" * 32
    draft.variables = {
        "DB_PASSWORD": {"origin": "generated", "secret": True, "value": value},
        "APP_DB_PASSWORD": {"origin": "generated", "secret": True, "value": value},
    }
    draft.values = {"DB_PASSWORD": value, "APP_DB_PASSWORD": value}

    declared = tool.declare_variables(draft, "a: ${DB_PASSWORD}\nb: ${APP_DB_PASSWORD}\n")

    assert declared["DB_PASSWORD"]["generate"] == "password32"
    assert declared["APP_DB_PASSWORD"]["value"] == "${DB_PASSWORD}"
    assert declared["APP_DB_PASSWORD"]["secret"] is True


@pytest.mark.parametrize(
    ("image", "needle"),
    [
        ("postgres:17", "pg_isready"),
        ("mariadb:11", "healthcheck.sh"),
        ("mysql:8.4", "mysqladmin ping"),
        ("valkey/valkey:9", "valkey-cli"),
        ("redis:7", "redis-cli"),
        ("mongo:8", "mongosh"),
    ],
)
def test_a_database_without_a_health_check_gets_one_and_what_uses_it_waits_for_it(
    tool: ModuleType, image: str, needle: str
) -> None:
    """Without it the application starts before the engine answers and restarts until it does."""
    draft = draft_of(
        tool,
        {
            "web": {"image": "o/app:1.0", "ports": ["127.0.0.1:${PORT}:80"], "depends_on": ["db"]},
            "db": {"image": image},
        },
        ("web", 80),
    )

    tool.clean_services(draft)

    check = draft.services["db"]["healthcheck"]
    assert needle in " ".join(check["test"])
    assert draft.services["web"]["depends_on"] == {"db": {"condition": "service_healthy"}}


def test_a_database_that_has_its_own_health_check_keeps_it(tool: ModuleType) -> None:
    """What a template wrote is what its authors tested."""
    own = {"test": ["CMD", "true"], "interval": "5s"}
    draft = draft_of(
        tool,
        {
            "web": {"image": "o/app:1.0", "ports": ["127.0.0.1:${PORT}:80"], "depends_on": ["db"]},
            "db": {"image": "postgres:17", "healthcheck": own},
        },
        ("web", 80),
    )

    tool.clean_services(draft)

    assert draft.services["db"]["healthcheck"] == own


def test_the_stack_s_env_file_becomes_the_variables_the_service_takes(tool: ModuleType) -> None:
    """`env_file: .env` is every variable of the stack; the recipe has no file, so it names them."""
    draft = draft_of(
        tool,
        {
            "web": {
                "image": "o/app:1.0",
                "env_file": [{"path": ".env", "required": False}],
                "environment": {"ONE": "own"},
                "ports": ["127.0.0.1:${PORT}:80"],
            },
            "worker": {"image": "o/app:1.0", "env_file": ".env"},
        },
        ("web", 80),
    )
    draft.values = {"ONE": "x", "TWO": "y", "bad-name": "z"}

    tool.clean_services(draft)

    assert draft.services["web"]["environment"] == {"ONE": "own", "TWO": "${TWO}"}
    assert draft.services["worker"]["environment"] == {"ONE": "${ONE}", "TWO": "${TWO}"}
    assert "env_file" not in draft.services["web"] and "env_file" not in draft.services["worker"]


def test_another_env_file_is_still_a_reason_to_skip(tool: ModuleType) -> None:
    """A file the recipe does not ship cannot be read."""
    draft = draft_of(
        tool, {"web": {"image": "o/app:1.0", "env_file": ["./secrets.env"]}}, ("web", 80)
    )

    with pytest.raises(tool.Skip) as caught:
        tool.clean_services(draft)

    assert caught.value.code == "service-keys"


def test_a_dollar_the_container_s_shell_reads_is_written_the_way_compose_passes_it_on(
    tool: ModuleType,
) -> None:
    """A command with $(...) or $VAR is the shell's; Compose would read it as its own variable."""
    shell = "export K=$(cat /run/k) && exec app --key $K --home $${HOME} ${REAL}"
    draft = draft_of(
        tool,
        {
            "web": {
                "image": "o/app:1.0",
                "command": ["sh", "-c", shell],
                "healthcheck": {"test": ["CMD-SHELL", "curl -f http://localhost:$PORT/"]},
            }
        },
        ("web", 80),
    )

    tool.clean_services(draft)

    assert draft.services["web"]["command"][2] == (
        "export K=$$(cat /run/k) && exec app --key $$K --home $${HOME} ${REAL}"
    )
    assert draft.services["web"]["healthcheck"]["test"][1] == "curl -f http://localhost:$$PORT/"


def test_a_file_with_a_secret_the_template_made_is_shipped_to_be_rendered(
    tool: ModuleType,
) -> None:
    """The secret in the file is the variable's, filled in when the file is written, never typed in it."""
    secret = "Q" * 32
    draft = draft_of(
        tool,
        {"web": {"image": "o/app:1.0", "volumes": ["./files/app.conf:/etc/app.conf"]}},
        ("web", 80),
    )
    draft.variables = {"APP_SECRET": {"origin": "generated", "secret": True, "value": secret}}
    draft.values = {"APP_SECRET": secret}
    body = f"secret = {secret}\nurl = https://app.example.com\nhost = app.example.com\nprice = $5\n"
    draft.files = {"files/app.conf": body}
    draft.secret_files = {"files/app.conf"}

    tool.clean_services(draft)

    assert draft.services["web"]["volumes"] == ["./files/app.conf:/etc/app.conf"]
    assert draft.shipped == {}
    assert draft.rendered["files/app.conf"] == (
        "secret = ${APP_SECRET}\nurl = ${APP_URL}\nhost = ${APP_DOMAIN}\nprice = $$5\n"
    )


def test_a_file_that_holds_a_secret_nobody_can_name_is_still_a_reason_to_skip(
    tool: ModuleType,
) -> None:
    """A file flagged as secret in which no variable of the stack appears cannot be made safe."""
    draft = draft_of(
        tool,
        {"web": {"image": "o/app:1.0", "volumes": ["./files/app.conf:/etc/app.conf"]}},
        ("web", 80),
    )
    draft.files = {"files/app.conf": "token = abcdef0123456789\n"}
    draft.secret_files = {"files/app.conf"}

    with pytest.raises(tool.Skip) as caught:
        tool.clean_services(draft)

    assert caught.value.code == "template-files"


def test_the_variables_a_rendered_file_names_are_declared_and_the_file_is_listed_to_render(
    tool: ModuleType,
) -> None:
    """The loader checks every name a rendered file uses against the recipe's variables."""
    draft = draft_of(tool, {"web": {"image": "o/app:1.0"}}, ("web", 80))
    draft.rendered = {"files/app.conf": "a = ${APP_URL}\nb = ${APP_DOMAIN}\n"}

    declared = tool.declare_variables(draft, "x: 1\n" + "\n".join(draft.rendered.values()))

    assert declared["APP_URL"]["from"] == "url" and declared["APP_DOMAIN"]["from"] == "fqdn"


# -- the long tail: ports a stack opens on purpose --------------------------------------------------


def test_a_port_a_template_opens_to_the_network_is_declared_not_a_reason_to_skip_it(
    tool: ModuleType,
) -> None:
    """The format has `public_ports` for what a stack opens on purpose (a WebRTC media port)."""
    draft = draft_of(
        tool,
        {
            "web": {
                "image": "o/app:1.0",
                "ports": ["127.0.0.1:${PORT}:80", "10000:10000/udp", "4443:4443"],
            }
        },
        ("web", 80),
    )

    tool.clean_services(draft)

    assert draft.services["web"]["ports"] == [
        "127.0.0.1:${PORT}:80",
        "10000:10000/udp",
        "4443:4443",
    ]
    assert [(p["port"], p["protocol"]) for p in draft.public_ports] == [
        (10000, "udp"),
        (4443, "tcp"),
    ]
    assert all("web" in p["purpose"] for p in draft.public_ports)


def test_the_port_a_template_hands_to_the_application_is_the_port_it_listens_on(
    tool: ModuleType,
) -> None:
    """Coolify's `PORT: ${PORT}` is the host's port; inside the container the application listens on its own."""
    draft = draft_of(
        tool,
        {
            "web": {
                "image": "o/app:1.0",
                "ports": ["127.0.0.1:${PORT:-3000}:3001"],
                "environment": {"PORT": "${PORT}", "PUBLIC": "http://localhost:${PORT:-3000}/"},
            }
        },
        ("web", 3001),
    )

    tool.clean_services(draft)

    assert draft.services["web"]["environment"] == {
        "PORT": "3001",
        "PUBLIC": "http://localhost:3001/",
    }
    assert draft.services["web"]["ports"] == ["127.0.0.1:${PORT}:3001"]


def test_a_template_that_names_no_web_service_can_be_given_one_by_a_person(
    tool: ModuleType, tmp_path: Path
) -> None:
    """The console of an object store is the web service nobody told the template about."""
    template = tmp_path / "demo.yaml"
    template.write_text(
        "# slogan: A store\nservices:\n  store:\n    image: o/store:1.0\n    ports:\n      - '9001'\n"
    )
    source = tool.Source(key="demo", name="demo", coolify=template)

    tool.ROUTES["demo"] = ("store", 9001)
    try:
        draft, _ = tool.convert_template(source)
    finally:
        del tool.ROUTES["demo"]

    assert draft.routed == ("store", 9001)


def test_the_web_service_a_person_chose_gets_its_loopback_port(tool: ModuleType) -> None:
    """The template never published it, so the recipe does."""
    draft = draft_of(tool, {"store": {"image": "o/store:1.0"}}, ("store", 9001))

    tool.clean_services(draft)

    assert draft.services["store"]["ports"] == ["127.0.0.1:${PORT}:9001"]


@pytest.mark.parametrize(
    ("template", "name"),
    [("open_notebook", "open-notebook"), ("bolt.diy", "bolt-diy"), ("Plain-Name", "plain-name")],
)
def test_a_template_s_name_becomes_one_a_recipe_may_have(
    tool: ModuleType, template: str, name: str
) -> None:
    """A recipe's name is letters, digits and dashes: `open_notebook` was drafted, run, and refused."""
    assert tool.recipe_name(template) == name
    assert tool.NAME_OK.fullmatch(name)


@pytest.mark.parametrize(
    ("value", "source"), [("app.example.com", "fqdn"), ("https://app.example.com", "url")]
)
def test_a_variable_that_holds_a_bare_host_name_is_the_domain_and_one_with_a_scheme_is_the_url(
    tool: ModuleType, value: str, source: str
) -> None:
    """Tuwunel's SERVER_NAME was given `https://domain` and refused to start."""
    draft = draft_of(tool, {"web": {"image": "o/app:1.0"}}, ("web", 80))
    draft.variables = {"SERVER_NAME": {"origin": "domain", "secret": False, "value": value}}
    draft.values = {"SERVER_NAME": value}

    declared = tool.declare_variables(draft, "a: ${SERVER_NAME}\n")

    assert declared["SERVER_NAME"]["from"] == source


def test_a_file_a_coolify_template_writes_inline_becomes_a_file_the_recipe_ships(
    tool: ModuleType, tmp_path: Path
) -> None:
    """`content:` of a bind volume is the file's text: it is shipped, and its references are the recipe's variables."""
    template = tmp_path / "demo.yaml"
    template.write_text(
        "# slogan: A demo\n"
        "services:\n"
        "  web:\n"
        "    image: o/app:1.0\n"
        "    environment:\n"
        "      - SERVICE_URL_WEB_80\n"
        "      - DB_PASSWORD=$SERVICE_PASSWORD_DB\n"
        "    volumes:\n"
        "      - type: bind\n"
        "        source: ./app.conf\n"
        "        target: /etc/app.conf\n"
        "        read_only: true\n"
        "        content: |\n"
        "          password = ${SERVICE_PASSWORD_DB}\n"
        "          host $host;\n"
    )
    source = tool.Source(key="demo", name="demo", coolify=template)

    draft, _ = tool.convert_template(source)
    tool.clean_services(draft)

    assert draft.services["web"]["volumes"] == ["./files/web/app.conf:/etc/app.conf:ro"]
    assert (
        draft.rendered["files/web/app.conf"]
        == "password = ${SERVICE_PASSWORD_DB}\nhost $${host};\n"
    )


def test_in_a_rendered_file_only_a_variable_the_recipe_declares_stays_a_reference(
    tool: ModuleType,
) -> None:
    """Everything else with a dollar is text for the program that reads the file."""
    draft = tool.Draft(name="demo")
    draft.variables = {"KNOWN": {"origin": "generated", "secret": True, "value": None}}

    made = tool.render_body(
        draft, "a=${KNOWN} b=$KNOWN c=${OTHER} d=$other e=$$ f=$(cmd) g=$", secret=False
    )

    assert made == "a=${KNOWN} b=${KNOWN} c=$${OTHER} d=$$other e=$$$$ f=$$(cmd) g=$$"


def test_a_routed_port_the_image_does_not_expose_follows_the_one_port_it_does(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dokploy routed Apache Answer to 9080, the image listens on 80, and nothing configures 9080."""
    monkeypatch.setattr(
        tool.recipe_draft, "image_config", lambda reference: {"ExposedPorts": {"80/tcp": {}}}
    )
    draft = draft_of(
        tool,
        {"web": {"image": "o/app:1.0@sha256:" + "a" * 64, "ports": ["127.0.0.1:${PORT}:9080"]}},
        ("web", 9080),
    )

    tool.align_routed_port(draft)

    assert draft.services["web"]["ports"] == ["127.0.0.1:${PORT}:80"] and draft.routed == (
        "web",
        80,
    )


@pytest.mark.parametrize(
    ("exposed", "environment"),
    [
        ({"80/tcp": {}, "443/tcp": {}}, {}),
        ({}, {}),
        ({"80/tcp": {}}, {"LISTEN": "9080"}),
        ({"9080/tcp": {}}, {}),
    ],
)
def test_a_routed_port_is_left_alone_when_the_image_says_nothing_clear_or_the_template_sets_it(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    exposed: dict[str, dict[str, Any]],
    environment: dict[str, str],
) -> None:
    """More than one port, none, a port the template configures: what the template says stands."""
    monkeypatch.setattr(
        tool.recipe_draft, "image_config", lambda reference: {"ExposedPorts": exposed}
    )
    draft = draft_of(
        tool,
        {
            "web": {
                "image": "o/app:1.0@sha256:" + "a" * 64,
                "ports": ["127.0.0.1:${PORT}:9080"],
                "environment": environment,
            }
        },
        ("web", 9080),
    )

    tool.align_routed_port(draft)

    assert draft.services["web"]["ports"] == ["127.0.0.1:${PORT}:9080"]


def test_an_image_that_cannot_be_read_leaves_the_port_as_it_is(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry is asked; its failure is not the template's."""

    def broken(reference: str) -> dict[str, Any]:
        raise RuntimeError("registry down")

    monkeypatch.setattr(tool.recipe_draft, "image_config", broken)
    draft = draft_of(
        tool, {"web": {"image": "o/app:1.0", "ports": ["127.0.0.1:${PORT}:9080"]}}, ("web", 9080)
    )

    tool.align_routed_port(draft)

    assert draft.routed == ("web", 9080)


def test_a_default_that_is_a_credential_becomes_a_generated_secret(tool: ModuleType) -> None:
    """Zitadel's template said `AdminPassword123!`: the recipe starts closed, with a password of its own."""
    draft = draft_of(tool, {"web": {"image": "o/app:1.0"}}, ("web", 80))
    draft.variables = {
        "ADMIN_PASSWORD": {"origin": "template", "secret": False, "value": "AdminPassword123!"},
        "TOKEN_TTL": {"origin": "template", "secret": False, "value": "24h"},
        "API_SECRET": {"origin": "template", "secret": False, "value": ""},
    }
    draft.values = {"ADMIN_PASSWORD": "AdminPassword123!", "TOKEN_TTL": "24h", "API_SECRET": ""}

    declared = tool.declare_variables(
        draft, "a: ${ADMIN_PASSWORD}\nb: ${TOKEN_TTL}\nc: ${API_SECRET}\n"
    )

    assert (
        declared["ADMIN_PASSWORD"]["generate"] == "password32"
        and declared["ADMIN_PASSWORD"]["secret"]
    )
    assert declared["TOKEN_TTL"]["default"] == "24h"
    assert declared["API_SECRET"]["default"] == "" and declared["API_SECRET"]["secret"]


def test_a_default_that_names_the_sample_domain_follows_the_deployment_s_domain(
    tool: ModuleType,
) -> None:
    """`admin@app.example.com` is an address nobody reads."""
    draft = draft_of(tool, {"web": {"image": "o/app:1.0"}}, ("web", 80))
    draft.variables = {
        "ADMIN_EMAIL": {"origin": "template", "secret": False, "value": "admin@app.example.com"}
    }
    draft.values = {"ADMIN_EMAIL": "admin@app.example.com"}

    declared = tool.declare_variables(draft, "a: ${ADMIN_EMAIL}\n")

    assert declared["ADMIN_EMAIL"]["value"] == "admin@${APP_DOMAIN}"
    assert declared["APP_DOMAIN"]["from"] == "fqdn"


def test_a_setting_made_of_other_variables_is_a_value_filled_in_at_the_install(
    tool: ModuleType,
) -> None:
    """Spliit's database address names the user, the password and the database it was given."""
    draft = draft_of(tool, {"web": {"image": "o/app:1.0"}}, ("web", 80))
    draft.variables = {
        "DB_PASSWORD": {"origin": "generated", "secret": True, "value": "x" * 32},
        "DB_NAME": {"origin": "template", "secret": False, "value": "app"},
        "DB_URL": {
            "origin": "template",
            "secret": False,
            "value": "postgresql://app:${DB_PASSWORD}@db:5432/${DB_NAME}",
        },
        "LOG_FORMAT": {"origin": "template", "secret": False, "value": "${DB_NAME}-log"},
    }
    draft.values = {name: entry["value"] for name, entry in draft.variables.items()}

    declared = tool.declare_variables(
        draft, "a: ${DB_PASSWORD}\nb: ${DB_NAME}\nc: ${DB_URL}\nd: ${LOG_FORMAT}\n"
    )

    assert declared["DB_URL"]["value"] == "postgresql://app:${DB_PASSWORD}@db:5432/${DB_NAME}"
    assert declared["DB_URL"]["secret"] is True
    assert declared["LOG_FORMAT"]["value"] == "${DB_NAME}-log"
    assert "secret" not in declared["LOG_FORMAT"]


def test_what_follows_a_hash_in_a_template_s_env_line_is_its_comment_not_the_default(
    tool: ModuleType,
) -> None:
    """Pastefy's `CLIENT_SECRET= # GitHub API Secret` was a default made of the comment."""
    draft = draft_of(tool, {"web": {"image": "o/app:1.0"}}, ("web", 80))
    draft.variables = {
        "CLIENT_ID": {"origin": "template", "secret": False, "value": "# GitHub API"}
    }
    draft.values = {"CLIENT_ID": "# GitHub API"}

    declared = tool.declare_variables(draft, "a: ${CLIENT_ID}\n")

    assert declared["CLIENT_ID"]["default"] == ""


def test_a_ssh_port_mapped_to_another_host_port_is_left_out_and_said(tool: ModuleType) -> None:
    """Git over SSH on 2222 is a convenience; a recipe's public port is the same on both sides."""
    draft = draft_of(
        tool,
        {"web": {"image": "o/app:1.0", "ports": ["127.0.0.1:${PORT}:80", "2222:22"]}},
        ("web", 80),
    )

    tool.clean_services(draft)

    assert draft.services["web"]["ports"] == ["127.0.0.1:${PORT}:80"]
    assert draft.public_ports == [] and draft.left_out_ssh == ["web"]


def test_another_port_mapped_to_a_different_host_port_is_still_a_reason_to_skip(
    tool: ModuleType,
) -> None:
    """A recipe's public port is the same on both sides, and a service whose port moved needs it."""
    draft = draft_of(
        tool,
        {"web": {"image": "o/app:1.0", "ports": ["127.0.0.1:${PORT}:80", "9000:9001"]}},
        ("web", 80),
    )

    with pytest.raises(tool.Skip) as caught:
        tool.clean_services(draft)

    assert caught.value.code == "extra-port"


@pytest.mark.parametrize("port", ["53:53/udp", "80:8080", "443:8443", "22:22", "5432:5432"])
def test_a_port_the_server_itself_uses_is_still_a_reason_to_skip(
    tool: ModuleType, port: str
) -> None:
    """A recipe that takes the host's DNS, web, SSH or database port would not start beside them."""
    draft = draft_of(
        tool,
        {"web": {"image": "o/app:1.0", "ports": ["127.0.0.1:${PORT}:80", port]}},
        ("web", 80),
    )

    with pytest.raises(tool.Skip) as caught:
        tool.clean_services(draft)

    assert caught.value.code == "extra-port"


def test_a_port_with_a_variable_or_a_range_is_not_declared(tool: ModuleType) -> None:
    """`public_ports` names a number; a template's variable or range is for a person to read."""
    for odd in (
        "${MEDIA_PORT}:${MEDIA_PORT}",
        "50000-50100:50000-50100/udp",
        "127.0.0.1:9000:9000",
    ):
        draft = draft_of(tool, {"web": {"image": "o/app:1.0", "ports": [odd]}}, ("web", 80))

        with pytest.raises(tool.Skip) as caught:
            tool.clean_services(draft)

        assert caught.value.code == "extra-port", odd


# -- the long tail: a lower floor of stars, licences a person read, repositories a person found -----


def test_the_floor_of_stars_is_the_catalogue_s_unless_a_run_lowers_it(tool: ModuleType) -> None:
    """1,000 stars is the rule; a run that looks for the long tail lowers it, and only the stars."""
    small = facts(stars=300)

    assert tool.not_eligible(small).code == "stars"
    assert tool.not_eligible(small, min_stars=150) is None
    assert tool.not_eligible(facts(stars=300, archived=True), min_stars=150).code == "archived"
    stale = facts(stars=300, released="2020-01-01T00:00:00Z")
    assert tool.not_eligible(stale, min_stars=150).code == "stale"


def test_a_licence_a_person_read_makes_a_project_eligible_or_names_why_it_is_not(
    tool: ModuleType,
) -> None:
    """GitHub says NOASSERTION for terms of a project's own; the reading is what the importer trusts."""
    unknown = facts(license="NOASSERTION", repository="O/App")
    readings = {
        "o/app": {
            "license": "LicenseRef-App-Community",
            "accept": True,
            "name": "App Community License",
            "evidence": "LICENSE of o/app, read 2026-10-30: use for your own organisation is free",
        }
    }

    accepted = tool.with_licence(unknown, readings)

    assert tool.not_eligible(accepted) is None
    assert accepted["license"] == "LicenseRef-App-Community"
    assert accepted["licence_name"] == "App Community License"
    refused = tool.with_licence(
        unknown,
        {"o/app": {"license": "LicenseRef-App", "accept": False, "evidence": "personal use only"}},
    )
    verdict = tool.not_eligible(refused)
    assert verdict is not None and verdict.code == "licence" and "personal use only" in str(verdict)
    assert tool.not_eligible(tool.with_licence(unknown, {})).code == "licence"
    assert tool.with_licence(None, readings) is None


def test_a_repository_a_person_found_wins_over_what_the_template_says(
    tool: ModuleType, tmp_path: Path
) -> None:
    """Templates that name no repository are not left out for it."""
    template = tmp_path / "demo.yaml"
    template.write_text("# documentation: https://example.com/docs\nservices: {}\n")
    source = tool.Source(key="demo", name="demo", coolify=template)

    assert tool.find_repository(source) is None
    assert tool.find_repository(source, {"demo": "owner/demo"}) == "owner/demo"
    assert tool.find_repository(source, {"other": "owner/other"}) is None


def test_a_recipe_of_a_source_available_project_says_so_and_cites_the_reading(
    tool: ModuleType,
) -> None:
    """The label is in the description and the evidence is in the header, for a person to check."""
    draft = draft_of(
        tool,
        {"web": {"image": "o/app:1.0@sha256:" + "a" * 64, "ports": ["127.0.0.1:${PORT}:3000"]}},
        ("web", 3000),
    )
    draft.origin = "Dokploy"
    draft.template = "blueprints/app"
    source = tool.Source(key="app", name="app")
    reading = tool.with_licence(
        facts(
            license="NOASSERTION",
            repository="o/app",
            description="Does a thing.",
            homepage="",
            html_url="https://github.com/o/app",
        ),
        {
            "o/app": {
                "license": "LicenseRef-App-Community",
                "accept": True,
                "name": "App Community License",
                "evidence": "LICENSE of o/app, read 2026-10-30",
            }
        },
    )
    health = {"path": "/", "expect": "200", "timeout": 60}

    text = tool.build_text(draft, source, reading, ["amd64"], logo="logo.svg", health=health)

    assert "# Licence: LICENSE of o/app, read 2026-10-30" in text
    assert "license: LicenseRef-App-Community" in text
    assert "Source-available (App Community License), for your own use. Does a thing." in text
