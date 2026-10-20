# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""scripts/recipe_draft.py: the lookups behind writing a recipe, without the network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from noust.recipes.logos import validate_svg

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "recipe_draft.py"


@pytest.fixture(scope="module")
def draft() -> ModuleType:
    """The script, loaded as a module."""
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("recipe_draft", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_draft"] = module
    spec.loader.exec_module(module)
    return module


def test_release_tags_are_numbers_newest_first_and_never_a_pre_release(draft: ModuleType) -> None:
    """A registry lists everything; a recipe pins a release."""
    listed = [
        "latest",
        "1.9.0",
        "1.10.0",
        "1.10.0-rc1",
        "v1.2",
        "1.10.1-alpine",
        "1.10.1",
        "nightly",
        "sha-abc123",
        "2.0.0-beta.1",
        "1.10.0",
    ]

    assert draft.release_tags(listed, limit=4) == ["1.10.1", "1.10.1-alpine", "1.10.0", "1.9.0"]


def test_every_tag_can_be_asked_for_when_a_project_names_releases_oddly(draft: ModuleType) -> None:
    """--all: the registry's tags as they are, by name."""
    assert draft.release_tags(["b", "a", "latest"], limit=2, everything=True) == ["latest", "b"]


def test_pinning_adds_the_digest_of_each_image_and_leaves_variable_images_alone(
    draft: ModuleType,
) -> None:
    """The line keeps its indentation and its comment; the tag is what is resolved."""
    lines = [
        "compose: |",
        "  services:",
        "    app:",
        "      image: ghcr.io/o/app:1.2.3 # the release",
        "    db:",
        "      image: postgres:17.11-alpine@sha256:" + "0" * 64,
        "    other:",
        "      image: ${IMAGE}:1",
    ]
    asked: list[str] = []

    def resolve(reference: str) -> str:
        asked.append(reference)
        return "sha256:" + "a" * 64

    pinned, archs = draft.pin_lines(
        lines, resolve=resolve, platforms=lambda ref, digest: {"amd64", "arm64"}
    )

    assert asked == ["ghcr.io/o/app:1.2.3", "postgres:17.11-alpine"]
    assert pinned[3] == "      image: ghcr.io/o/app:1.2.3@sha256:" + "a" * 64 + " # the release"
    assert pinned[5] == "      image: postgres:17.11-alpine@sha256:" + "a" * 64
    assert pinned[7] == lines[7]
    assert set(archs) == {"ghcr.io/o/app:1.2.3", "postgres:17.11-alpine"}


def test_an_image_without_a_tag_is_refused_rather_than_pinned_to_latest(draft: ModuleType) -> None:
    """The registry would answer for latest; a recipe never pins that."""
    with pytest.raises(RuntimeError, match="has no tag"):
        draft.pin_lines(
            ["      image: nginx"],
            resolve=lambda r: "sha256:" + "a" * 64,
            platforms=lambda r, d: set(),
        )


def test_the_architectures_a_recipe_claims_are_what_every_image_is_published_for(
    draft: ModuleType,
) -> None:
    """The intersection, in the order the recipes write them."""
    shared = draft.common_architectures(
        {"a": {"amd64", "arm64", "arm"}, "b": {"arm64", "amd64", "386"}, "c": {"amd64", "arm64"}}
    )

    assert shared == ["amd64", "arm64"]
    assert draft.common_architectures({}) == []


@pytest.mark.parametrize(
    ("title", "letters"),
    [
        ("Uptime Kuma", "UK"),
        ("n8n", "N8"),
        ("Vaultwarden", "VA"),
        ("Paperless-ngx", "PN"),
        ("", "??"),
    ],
)
def test_a_monogram_is_two_letters(draft: ModuleType, title: str, letters: str) -> None:
    """Initials of the first two words, or the first two letters of one."""
    assert draft.monogram(title) == letters


def test_a_drawn_logo_passes_the_policy_that_serves_it(draft: ModuleType) -> None:
    """No script, no style, no link; a title with markup is stripped, not trusted."""
    svg = draft.logo_svg('Evil <script>"& Co')

    assert validate_svg("demo", svg) == svg
    assert "<script" not in svg and '"&' not in svg


def test_a_tag_the_catalogue_already_pins_keeps_its_digest(
    draft: ModuleType, tmp_path: Path
) -> None:
    """Two recipes on postgres:17 are one image; a tag two recipes pin differently is not trusted."""
    one = "a" * 64
    other = "b" * 64
    (tmp_path / "one.yaml").write_text(
        f"compose: |\n  services:\n    db:\n      image: postgres:17-alpine@sha256:{one}\n"
        f"    x:\n      image: o/app:1@sha256:{one}\n"
    )
    (tmp_path / "two.yaml").write_text(
        f"      image: o/app:1@sha256:{other}\n      image: valkey/valkey:9@sha256:{other}\n"
    )

    assert draft.known_digests(tmp_path) == {
        "postgres:17-alpine": f"sha256:{one}",
        "valkey/valkey:9": f"sha256:{other}",
    }


def test_an_image_s_configuration_is_told_in_the_words_a_recipe_needs(draft: ModuleType) -> None:
    """The account, the command, the ports in numeric order and the volumes: what to mount and where."""
    said = draft.describe_config(
        {
            "User": "1000:1000",
            "Entrypoint": ["/sbin/tini", "--"],
            "Cmd": ["node", "server.js"],
            "ExposedPorts": {"9000/tcp": {}, "80/tcp": {}, "9000/udp": {}},
            "Volumes": {"/var/lib/x": {}, "/data": {}},
            "WorkingDir": "/app",
            "Healthcheck": {"Test": ["CMD", "true"]},
        }
    )

    assert said.splitlines() == [
        "user: 1000:1000",
        "entrypoint: ['/sbin/tini', '--']",
        "command: ['node', 'server.js']",
        "ports: 80/tcp, 9000/tcp, 9000/udp",
        "volumes: /data, /var/lib/x",
        "working directory: /app",
        "health check: yes",
    ]
    assert "user: root" in draft.describe_config({})
    assert "ports: none" in draft.describe_config({})


def test_pinning_offline_asks_no_registry_and_keeps_the_architectures_line(
    draft: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registry that answers 429 must not stop a recipe whose images are already pinned."""
    digest = "sha256:" + "c" * 64
    recipes = tmp_path / "src" / "noust" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "other.yaml").write_text(f"      image: ghcr.io/o/app:1.2.3@{digest}\n")
    monkeypatch.setattr(draft, "ROOT", tmp_path)
    monkeypatch.setattr(
        draft.recipe_pins,
        "resolve_digest",
        lambda reference: (_ for _ in ()).throw(AssertionError("the registry was asked")),
    )
    monkeypatch.setattr(
        draft.recipe_pins,
        "resolve_platforms",
        lambda reference, found: (_ for _ in ()).throw(AssertionError("the registry was asked")),
    )
    source = tmp_path / "draft.yaml"
    source.write_text(
        'architectures: ["amd64", "arm64"]\nservices:\n  app:\n    image: ghcr.io/o/app:1.2.3\n'
    )
    target = tmp_path / "pinned.yaml"
    target.write_text('architectures: ["amd64"]\n')

    assert draft.main(["pin", str(source), "--to", str(target), "--offline"]) == 0

    text = target.read_text()
    assert f"image: ghcr.io/o/app:1.2.3@{digest}" in text
    assert text.startswith('architectures: ["amd64"]\n')
    assert draft.main(["pin", str(source), "--to", str(target), "--offline"]) == 0
    source.write_text("services:\n  app:\n    image: ghcr.io/o/unknown:9\n")
    assert draft.main(["pin", str(source), "--to", str(target), "--offline"]) == 1
