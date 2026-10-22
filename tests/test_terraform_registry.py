# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Staging the Terraform provider for the registry (checklist J-14).

The registry needs a public repository of its own, a signing key and an account, which are the owner's.
``scripts/prepare_terraform_registry.py`` is what is not: it copies the provider into a directory, makes it
true as a repository of its own, and says what to do next. These tests hold it to that and to nothing more:
it writes only where it is told, it cannot create anything public (it runs no process), and what it stages
is what the registry reads.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "prepare_terraform_registry.py"
PROVIDER = ROOT / "terraform-provider-noust"


@pytest.fixture(scope="module")
def prepare() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prepare_terraform_registry", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["prepare_terraform_registry"] = module
    spec.loader.exec_module(module)
    return module


def fake_provider(root: Path) -> Path:
    """A provider directory with everything the registry needs, small."""
    files = {
        "main.go": "package main\n",
        "go.mod": "module github.com/Perkybeet/terraform-provider-noust\n",
        "go.sum": "",
        "LICENSE": "licence\n",
        "README.md": "see [the guide](../docs/terraform.md) and [the registry](../docs/terraform-registry.md)\n",
        ".goreleaser.yml": "version: 2\n",
        "terraform-registry-manifest.json": "{}\n",
        ".github/workflows/release.yml": "name: release\n",
        "docs/index.md": "# noust\n",
        "docs/resources/team.md": "# team\n",
        "docs/data-sources/team.md": "# team\n",
        "examples/provider/provider.tf": 'source = "perkybeet/noust"\n',
        "e2e/run.sh": "#!/bin/sh\n",
        "dist/binary": "x",
        "internal/provider/state.tfstate": "{}",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class TestTheProviderInTheRepository:
    def test_it_has_everything_the_registry_needs(self, prepare: ModuleType) -> None:
        """Run after ``go generate``: the generated reference is committed."""
        assert prepare.missing(PROVIDER) == []

    def test_its_module_path_and_registry_address_are_the_ones_the_script_rewrites(self) -> None:
        assert (
            "module github.com/Perkybeet/terraform-provider-noust"
            in (PROVIDER / "go.mod").read_text()
        )
        assert '"registry.terraform.io/perkybeet/noust"' in (PROVIDER / "main.go").read_text()

    def test_it_declares_protocol_6_for_the_registry(self) -> None:
        manifest = (PROVIDER / "terraform-registry-manifest.json").read_text(encoding="utf-8")

        assert '"protocol_versions": ["6.0"]' in manifest

    def test_the_release_signs_the_checksums_with_a_key_from_the_environment(self) -> None:
        text = (PROVIDER / ".goreleaser.yml").read_text(encoding="utf-8")

        assert "artifacts: checksum" in text
        assert "GPG_FINGERPRINT" in text
        assert "--detach-sign" in text
        assert "terraform-registry-manifest.json" in text

    def test_no_key_and_no_secret_is_in_the_directory(self) -> None:
        for path in PROVIDER.rglob("*"):
            if not path.is_file() or path.suffix in {".zip", ".png"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "BEGIN PGP PRIVATE KEY" not in text, path
            assert "BEGIN PRIVATE KEY" not in text, path
            assert not re.search(r"noust_tok_[A-Za-z0-9_-]{16,}", text), path


class TestStagingIt:
    def test_the_directory_is_copied_without_what_a_repository_of_its_own_does_not_carry(
        self, prepare: ModuleType, tmp_path: Path
    ) -> None:
        source = fake_provider(tmp_path / "source")
        out = tmp_path / "out"

        written = {str(path) for path in prepare.stage(out, source=source)}

        assert "main.go" in written and ".github/workflows/release.yml" in written
        assert not any(name.startswith(("e2e", "dist")) for name in written)
        assert not any(name.endswith(".tfstate") for name in written)
        assert (out / ".gitignore").is_file()
        assert not (out / "e2e").exists()

    def test_links_that_left_the_directory_become_absolute(
        self, prepare: ModuleType, tmp_path: Path
    ) -> None:
        source = fake_provider(tmp_path / "source")

        prepare.stage(tmp_path / "out", source=source)

        readme = (tmp_path / "out" / "README.md").read_text(encoding="utf-8")
        assert "](https://github.com/Perkybeet/noust/blob/main/docs/terraform.md)" in readme
        assert "](../" not in readme

    def test_another_owner_is_written_where_the_old_one_was(
        self, prepare: ModuleType, tmp_path: Path
    ) -> None:
        source = fake_provider(tmp_path / "source")

        prepare.stage(tmp_path / "out", owner="Noust", source=source)

        assert (
            "github.com/Noust/terraform-provider-noust" in (tmp_path / "out" / "go.mod").read_text()
        )
        assert (
            'source = "noust/noust"'
            in (tmp_path / "out" / "examples/provider/provider.tf").read_text()
        )

    def test_it_replaces_what_it_staged_before_and_nothing_else(
        self, prepare: ModuleType, tmp_path: Path
    ) -> None:
        source = fake_provider(tmp_path / "source")
        out = tmp_path / "out"
        out.mkdir()
        (out / "stale.txt").write_text("old")
        neighbour = tmp_path / "neighbour.txt"
        neighbour.write_text("mine")

        prepare.stage(out, source=source)

        assert not (out / "stale.txt").exists()
        assert neighbour.read_text() == "mine"

    def test_a_provider_that_lacks_something_is_not_staged(
        self, prepare: ModuleType, tmp_path: Path
    ) -> None:
        source = fake_provider(tmp_path / "source")
        (source / ".goreleaser.yml").unlink()
        (source / "docs/resources/team.md").unlink()

        with pytest.raises(prepare.NotReady) as error:
            prepare.stage(tmp_path / "out", source=source)

        assert ".goreleaser.yml" in str(error.value)
        assert "docs/resources/*.md" in str(error.value)
        assert not (tmp_path / "out").exists()

    def test_the_next_steps_are_the_owners_to_run_not_the_scripts(
        self, prepare: ModuleType
    ) -> None:
        text = prepare.next_steps(Path("/tmp/stage"), "Perkybeet")

        assert "Nothing was created and nothing was pushed" in text
        assert "gh repo create Perkybeet/terraform-provider-noust --public" in text
        assert "GPG_PRIVATE_KEY" in text and "PASSPHRASE" in text


class TestTheScriptCreatesNothingPublic:
    def test_it_runs_no_process_and_calls_no_service(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        for forbidden in (
            "import subprocess",
            "from subprocess",
            "os.system",
            "urllib",
            "requests",
            "httpx",
            "socket",
        ):
            assert forbidden not in source, forbidden
