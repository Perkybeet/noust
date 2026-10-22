# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The minimal applications of ``examples/``, read the way a deploy begins (checklist N-20).

``tests/integration/examples_run.py`` builds and runs every one for real, on a clean machine with
systemd. What is pinned here is cheaper and runs everywhere: detection names the type each folder is
for, every folder says what it is in the sentence the harness looks for, the README lists exactly the
folders there are, the Node.js frameworks pin what they use and carry a lock file and the Node they
need, and nothing in a folder is a link, a credential or a build product.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from noust.core.secret_detection import name_looks_secret
from noust.deployers.registry import DeployerRegistry, available_types

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

#: Folder -> the type Noust detects it as. One example per type Noust deploys, but ``auto``.
TYPES = {
    "static": "static",
    "nodejs": "nodejs",
    "python": "python",
    "php": "php-fpm",
    "ruby": "ruby",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "dotnet": "dotnet",
    "elixir": "elixir",
    "deno": "deno",
    "nextjs": "nextjs",
    "nuxt": "nuxt",
    "sveltekit": "sveltekit",
    "astro": "astro",
    "remix": "remix",
    "angular": "angular",
    "vite": "vite",
    "monorepo": "monorepo",
    "docker-compose": "docker-compose",
    "container": "container",
}

#: The Node.js frameworks, which pin their dependencies and ask for the Node they need.
FRAMEWORKS = ("nextjs", "nuxt", "sveltekit", "astro", "remix", "angular", "vite")

#: Where the build leaves what the harness would not find in the source.
BUILD_PRODUCTS = {
    "node_modules",
    ".next",
    ".nuxt",
    ".output",
    ".svelte-kit",
    "dist",
    "build",
    "target",
    "_build",
    "bin",
    "obj",
    ".venv",
    "vendor",
    ".angular",
    ".react-router",
}

EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")

#: ``NAME = "value"``, ``NAME: value`` and ``"NAME": "value"``: the pairs a credential would be written as.
ASSIGNMENT = re.compile(
    r"""[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?\s*[:=]\s*[\"']?([^\s\"',{}\[\]]+)"""
)


def folders() -> list[Path]:
    return sorted(path for path in EXAMPLES.iterdir() if path.is_dir())


def files_of(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*") if path.is_file() or path.is_symlink())


def test_there_is_an_example_for_every_type_noust_deploys() -> None:
    types = {item["type"] for item in available_types()} - {"auto"}

    assert set(TYPES.values()) == types


def test_the_folders_are_the_ones_this_test_knows() -> None:
    assert {path.name for path in folders()} == set(TYPES)


@pytest.mark.parametrize(("folder", "expected"), sorted(TYPES.items()))
def test_detection_names_the_type_each_folder_is_for(folder: str, expected: str) -> None:
    assert DeployerRegistry.detect(EXAMPLES / folder) == expected


@pytest.mark.parametrize("folder", sorted(TYPES))
def test_every_example_says_what_it_is_in_the_sentence_the_harness_asks_for(folder: str) -> None:
    sentence = f"Hello from Noust ({folder}"
    texts = [
        path.read_text(encoding="utf-8", errors="ignore")
        for path in files_of(EXAMPLES / folder)
        if path.name != "package-lock.json"
    ]

    assert any(sentence in text for text in texts)


@pytest.mark.parametrize("folder", sorted(TYPES))
def test_nothing_in_an_example_is_a_link_a_build_product_or_a_credential(folder: str) -> None:
    for path in files_of(EXAMPLES / folder):
        relative = path.relative_to(EXAMPLES / folder)
        assert not path.is_symlink(), f"{relative} is a link"
        assert not BUILD_PRODUCTS & set(relative.parts[:-1]), f"{relative} is a build product"
        assert not path.name.startswith(".env"), f"{relative} is an environment file"
        assert path.stat().st_size < 400 * 1024, f"{relative} is far too large for an example"
        if path.name == "package-lock.json" or path.suffix in {".png", ".ico", ".svg"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "PRIVATE KEY-----" not in text, f"{relative} holds a private key"
        for number, line in enumerate(text.splitlines(), 1):
            for name, value in ASSIGNMENT.findall(line):
                assert not (name_looks_secret(name) and value), f"{relative}:{number} sets {name}"


@pytest.mark.parametrize("folder", FRAMEWORKS)
def test_a_framework_pins_what_it_uses_and_carries_its_lock_file_and_node(folder: str) -> None:
    package = json.loads((EXAMPLES / folder / "package.json").read_text(encoding="utf-8"))
    dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}

    assert dependencies
    for name, version in dependencies.items():
        assert EXACT_VERSION.fullmatch(version), f"{name} is {version}, not an exact version"
    lock = json.loads((EXAMPLES / folder / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["lockfileVersion"] >= 2
    assert set(dependencies) <= set(
        lock["packages"][""].get("dependencies", {})
        | lock["packages"][""].get("devDependencies", {})
    )
    assert (EXAMPLES / folder / ".nvmrc").read_text().strip() == "22"
    assert package["private"] is True


def test_a_server_with_no_dependencies_still_carries_a_lock_file() -> None:
    """Noust installs a Node.js project with ``npm ci``, which refuses to run without one."""
    package = json.loads((EXAMPLES / "nodejs" / "package.json").read_text(encoding="utf-8"))

    assert "dependencies" not in package
    lock = json.loads((EXAMPLES / "nodejs" / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["lockfileVersion"] >= 2


def test_the_monorepo_carries_the_lock_file_pnpm_installs_from() -> None:
    assert (EXAMPLES / "monorepo" / "pnpm-lock.yaml").is_file()


def test_the_readme_lists_exactly_the_folders_there_are() -> None:
    text = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| \[`([a-z-]+)/`\]\(", text, re.MULTILINE))

    assert listed == set(TYPES)
    for folder, expected in TYPES.items():
        assert re.search(rf"\[`{folder}/`\]\({folder}\) \| `{re.escape(expected)}` \|", text), (
            folder
        )


def test_the_guides_point_at_the_examples() -> None:
    for page in ("docs/first-application.md", "docs/frameworks.md"):
        assert "examples/README.md" in (ROOT / page).read_text(encoding="utf-8"), page


def test_the_examples_are_a_page_of_the_documentation_site() -> None:
    assert "source: examples/README.md" in (ROOT / "docs" / "site.yaml").read_text(encoding="utf-8")
