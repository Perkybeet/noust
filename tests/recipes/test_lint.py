# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The recipe lint (checklist D-14): no floating image gets in, every mistake is listed."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from noust.recipes.lint import ERROR, WARNING, lint_catalogue
from tests.recipes.factory import DIGEST, compose_document, compose_recipe
from tests.recipes.test_operator_recipes import LOGO

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "recipe_lint.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    """The CI script, loaded as a module."""
    spec = importlib.util.spec_from_file_location("recipe_lint", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_lint"] = module
    spec.loader.exec_module(module)
    return module


def write(directory: Path, name: str = "acme-wiki", **overrides: object) -> None:
    """Write a recipe and its logo into a folder."""
    document = compose_recipe(name=name, title="Acme", **overrides)
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
    assets = directory / "assets" / name
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "logo.svg").write_text(LOGO)


def two_services(first: str, second: str) -> str:
    """A Compose document with a web service and a database, each on the image given."""
    return (
        "services:\n"
        f"  web:\n    image: {first}\n    restart: unless-stopped\n"
        '    environment:\n      SECRET: "${SECRET}"\n'
        '    ports:\n      - "127.0.0.1:${PORT}:8080"\n'
        f"  db:\n    image: {second}\n    restart: unless-stopped\n"
        "    volumes:\n      - data:/data\n"
        "volumes:\n  data:\n"
    )


def test_the_recipes_the_package_ships_are_clean() -> None:
    findings = lint_catalogue()

    assert [finding.render() for finding in findings if finding.severity == ERROR] == []


def test_the_only_recipes_pinned_by_tag_alone_are_the_ones_that_say_so() -> None:
    """A warning is the honest form of a pin that could not be read; the list may only shrink."""
    warned = {finding.recipe for finding in lint_catalogue() if finding.severity == WARNING}

    assert warned <= set()


@pytest.mark.parametrize(
    ("reference", "rule"),
    [
        ("ghcr.io/o/app:latest@" + DIGEST, "floating-image"),
        ("ghcr.io/o/app:stable", "floating-image"),
        ("ghcr.io/o/app", "floating-image"),
        ("ghcr.io/o/app:1.2.3", "unpinned-digest"),
        ("${IMAGE}", "image-variable"),
    ],
)
def test_an_image_that_can_move_is_an_error(tmp_path: Path, reference: str, rule: str) -> None:
    document = compose_document(**{f"ghcr.io/o/app:1.2.3@{DIGEST}": reference})
    write(tmp_path, compose=document)

    findings = lint_catalogue(tmp_path)

    assert [(finding.rule, finding.severity) for finding in findings] == [(rule, ERROR)]
    assert "web" in findings[0].message


def test_every_bad_image_is_reported_not_only_the_first(tmp_path: Path) -> None:
    pinned = f"ghcr.io/o/app:1.2.3@{DIGEST}"
    write(tmp_path, compose=two_services("ghcr.io/o/app:latest", "postgres"))
    write(tmp_path, name="acme-blog", compose=two_services(pinned, pinned))

    findings = lint_catalogue(tmp_path)

    assert sorted((finding.recipe, finding.rule) for finding in findings) == [
        ("acme-wiki", "floating-image"),
        ("acme-wiki", "floating-image"),
    ]
    assert {finding.message.split(":")[0] for finding in findings} == {"service web", "service db"}


def test_a_tag_only_recipe_is_a_warning_that_can_fail_on_request(tmp_path: Path) -> None:
    document = compose_document(**{f"@{DIGEST}": ""})
    write(tmp_path, compose=document, digest_verified=False)

    findings = lint_catalogue(tmp_path)

    assert [(finding.rule, finding.severity) for finding in findings] == [
        ("digest-unverified", WARNING)
    ]


def test_a_native_recipe_has_no_image_to_lint(tmp_path: Path) -> None:
    from tests.recipes.factory import native_recipe

    document = native_recipe(name="acme-api", title="Acme API")
    (tmp_path / "acme-api.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
    (tmp_path / "assets" / "acme-api").mkdir(parents=True)
    (tmp_path / "assets" / "acme-api" / "logo.svg").write_text(LOGO)

    assert lint_catalogue(tmp_path) == []


def test_a_file_that_is_not_a_recipe_is_a_finding(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello")

    [finding] = lint_catalogue(tmp_path)

    assert finding.rule == "file" and "README.md" in finding.message


def test_a_checkout_the_group_can_write_is_still_linted(tmp_path: Path) -> None:
    write(tmp_path)
    tmp_path.chmod(0o775)
    (tmp_path / "acme-wiki.yaml").chmod(0o664)

    assert lint_catalogue(tmp_path) == []


def test_the_script_fails_on_an_error_and_passes_a_clean_folder(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    write(clean)
    bad = tmp_path / "bad"
    bad.mkdir()
    write(bad, compose=compose_document(**{f"1.2.3@{DIGEST}": "latest"}))

    assert script.main([str(clean)]) == 0
    assert script.main([str(bad)]) == 1
    assert "floating-image" in capsys.readouterr().out


def test_the_script_can_make_a_tag_only_pin_fail(script: ModuleType, tmp_path: Path) -> None:
    write(tmp_path, compose=compose_document(**{f"@{DIGEST}": ""}), digest_verified=False)

    assert script.main([str(tmp_path)]) == 0
    assert script.main([str(tmp_path), "--warnings-as-errors"]) == 1


def test_the_script_passes_on_the_shipped_catalogue(script: ModuleType) -> None:
    assert script.main([]) == 0
