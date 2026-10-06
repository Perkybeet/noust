# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""scripts/recipe_pins.py: the one way a pin is read, and compared, without the network."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from noust.recipes import get_recipe, list_recipes

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "recipe_pins.py"


@pytest.fixture(scope="module")
def pins() -> ModuleType:
    """The script, loaded as a module."""
    spec = importlib.util.spec_from_file_location("recipe_pins", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_pins"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def registries_publish_what_the_recipes_say(
    pins: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No network: an image is published for exactly the architectures its recipe names."""

    def platforms(reference: str, digest: str) -> set[str]:
        for recipe in list_recipes():
            for image in recipe.images:
                if f"{image.repository}:{image.tag}" == reference:
                    return set(recipe.architectures)
        raise AssertionError(reference)

    monkeypatch.setattr(pins, "resolve_platforms", platforms)


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("ghcr.io/o/app:1.2.3", ("ghcr.io", "o/app", "1.2.3")),
        ("ghcr.io/o/app:1.2.3@sha256:" + "a" * 64, ("ghcr.io", "o/app", "1.2.3")),
        ("o/app:1.2.3", ("docker.io", "o/app", "1.2.3")),
        ("nginx", ("docker.io", "library/nginx", "latest")),
        ("localhost:5000/x:1", ("localhost:5000", "x", "1")),
        ("quay.io/a/b/c:v1", ("quay.io", "a/b/c", "v1")),
    ],
)
def test_a_reference_is_split_into_registry_repository_and_tag(
    pins: ModuleType, reference: str, expected: tuple[str, str, str]
) -> None:
    """The way Docker reads it."""
    assert pins.split_reference(reference) == expected


def test_every_pin_of_a_recipe_that_holds_reports_no_problem(
    pins: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry serves the digest the recipe records."""
    recipe = get_recipe("vaultwarden")
    monkeypatch.setattr(pins, "resolve_digest", lambda reference: recipe.images[0].digest)

    report = pins.check_recipe("vaultwarden")

    assert report["problems"] == []
    assert report["images"][0]["registry"] == recipe.images[0].digest
    assert report["digest_verified"] is True


def test_a_digest_that_moved_a_registry_that_failed_and_a_missing_tag_are_all_problems(
    pins: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pin that does not hold is named, never skipped."""
    monkeypatch.setattr(pins, "resolve_digest", lambda reference: "sha256:" + "0" * 64)
    moved = pins.check_recipe("memos")
    assert (
        len(moved["problems"]) == 1 and "registry serves sha256:" + "0" * 64 in moved["problems"][0]
    )

    def broken(reference: str) -> str:
        raise RuntimeError(f"{reference}: the registry answered HTTP 429")

    monkeypatch.setattr(pins, "resolve_digest", broken)
    assert "HTTP 429" in pins.check_recipe("memos")["problems"][0]

    monkeypatch.setattr(pins, "github_tag_exists", lambda url, ref: None)
    assert "tag 1.23.17 not found" in pins.check_recipe("uptime-kuma")["problems"][0]


def test_the_architectures_a_recipe_names_are_the_ones_every_image_is_published_for(
    pins: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read from the registry by the pinned digest, and compared with the recipe."""
    recipe = get_recipe("vaultwarden")
    monkeypatch.setattr(pins, "resolve_digest", lambda reference: recipe.images[0].digest)

    report = pins.check_recipe("vaultwarden")
    assert report["architectures"] == sorted(recipe.architectures) and report["problems"] == []
    assert report["images"][0]["platforms"] == sorted(recipe.architectures)

    monkeypatch.setattr(pins, "resolve_platforms", lambda reference, digest: {"amd64"})
    problems = pins.check_recipe("vaultwarden")["problems"]
    assert len(problems) == 1 and "its images are all published for ['amd64']" in problems[0]

    def broken(reference: str, digest: str) -> set[str]:
        raise RuntimeError(f"{reference}: the registry answered HTTP 500")

    monkeypatch.setattr(pins, "resolve_platforms", broken)
    assert "HTTP 500" in pins.check_recipe("vaultwarden")["problems"][0]


def test_a_published_checksum_is_compared_with_what_the_archive_hashes_to(
    pins: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WordPress publishes a SHA-1 beside its archive."""
    monkeypatch.setattr(pins, "published_checksum", lambda url: "a" * 40)
    monkeypatch.setattr(pins, "archive_digest", lambda url, algorithm: "a" * 40)
    assert pins.check_recipe("wordpress")["problems"] == []

    monkeypatch.setattr(pins, "archive_digest", lambda url, algorithm: "b" * 40)
    problems = pins.check_recipe("wordpress")["problems"]
    assert len(problems) == 1 and "SHA1" in problems[0]


def test_check_exits_non_zero_and_writes_the_evidence(
    pins: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The exit code a CI job reads, and the findings per recipe."""
    monkeypatch.setattr(pins, "resolve_digest", lambda reference: "sha256:" + "0" * 64)

    code = pins.main(["check", "--evidence", str(tmp_path), "memos"])

    assert code == 1
    assert "memos: DIFFERS" in capsys.readouterr().out
    written = json.loads((tmp_path / "memos.json").read_text())
    assert written["recipe"] == "memos" and written["problems"]
    recipe = get_recipe("memos")
    monkeypatch.setattr(pins, "resolve_digest", lambda reference: recipe.images[0].digest)
    assert pins.main(["check", "memos"]) == 0


def test_the_npm_version_a_template_pins_must_exist(
    pins: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """n8n is a package.json that depends on one version of one package."""
    monkeypatch.setattr(pins, "npm_version_integrity", lambda package, version: "sha512-abc")
    held = pins.check_recipe("n8n")
    assert held["problems"] == []
    assert held["source"] == {"npm": {f"n8n@{get_recipe('n8n').version}": "sha512-abc"}}

    monkeypatch.setattr(pins, "npm_version_integrity", lambda package, version: None)
    assert pins.check_recipe("n8n")["problems"] == [f"npm has no n8n@{get_recipe('n8n').version}"]


def test_only_https_archives_are_read(pins: ModuleType) -> None:
    """No file:// and no http://."""
    with pytest.raises(ValueError, match="https"):
        pins.archive_digest("http://example.com/a.tar.gz")
    with pytest.raises(ValueError, match="https"):
        pins.published_checksum("file:///etc/passwd")
