# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``scripts/sbom.py``: the CycloneDX SBOM attached to each release (ENS G16, op.pl.3).
"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "sbom_script", Path(__file__).parent.parent / "scripts/sbom.py"
)
assert SPEC and SPEC.loader
sbom = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sbom)

ROOT = Path(__file__).parent.parent


class TestTheSbom:
    def test_the_projects_requirements_are_read_without_a_toml_parser(self) -> None:
        requirements = sbom.project_requirements(ROOT / "pyproject.toml", ["web"])

        names = {sbom._requirement_name(r) for r in requirements}
        assert {"click", "jinja2", "pyyaml", "rich", "fastapi"} <= names

    def test_it_lists_the_python_packages_installed_with_their_purl(self, tmp_path: Path) -> None:
        wheel = tmp_path / "noust-3.1.0-py3-none-any.whl"
        wheel.write_bytes(b"wheel")

        document = sbom.build_sbom(wheel, "3.1.0", ["click>=8.0", "PyYAML>=6.0"], None)

        assert document["bomFormat"] == "CycloneDX" and document["specVersion"] == "1.5"
        root = document["metadata"]["component"]
        assert root["purl"] == "pkg:pypi/noust@3.1.0"
        assert root["hashes"][0]["alg"] == "SHA-256"
        purls = {component["purl"].split("@")[0] for component in document["components"]}
        assert {"pkg:pypi/click", "pkg:pypi/pyyaml"} <= purls
        assert document["dependencies"][0]["ref"] == "pkg:pypi/noust@3.1.0"

    def test_only_what_the_console_bundles_is_listed_from_npm(self, tmp_path: Path) -> None:
        digest = base64.b64encode(b"\x01" * 64).decode()
        lockfile = tmp_path / "package-lock.json"
        lockfile.write_text(
            json.dumps(
                {
                    "lockfileVersion": 3,
                    "packages": {
                        "": {"name": "panel"},
                        "node_modules/react": {
                            "version": "19.0.0",
                            "integrity": f"sha512-{digest}",
                            "license": "MIT",
                        },
                        "node_modules/@tanstack/react-router": {"version": "1.2.3"},
                        "node_modules/vite": {"version": "6.0.0", "dev": True},
                    },
                }
            )
        )

        components = sbom.npm_components(lockfile)

        assert [c["name"] for c in components] == ["@tanstack/react-router", "react"]
        assert components[0]["purl"] == "pkg:npm/%40tanstack/react-router@1.2.3"
        assert components[1]["hashes"][0] == {"alg": "SHA-512", "content": "01" * 64}

    def test_the_real_lockfile_reads(self) -> None:
        components = sbom.npm_components(ROOT / "panel" / "package-lock.json")

        assert components and all(c["purl"].startswith("pkg:npm/") for c in components)

    def test_main_writes_the_file(self, tmp_path: Path) -> None:
        output = tmp_path / "noust.cdx.json"

        assert sbom.main(["--extras", "web", "-o", str(output)]) == 0

        assert json.loads(output.read_text())["metadata"]["component"]["name"] == "noust"
