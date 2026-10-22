# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Noust ships that comes from another project is credited and carries its licence.

The catalog has recipes derived in part from Coolify's Compose templates (Apache-2.0). The
licence asks for its text and the attribution to travel with every copy: the header of each
recipe names its template, NOTICE lists them, LICENSE-APACHE-2.0 is the licence, and the Debian
copyright file and the RPM spec carry both. A recipe added or removed without them fails here.

The console is built from npm packages (MIT, ISC), whose licences ask for their copyright line and
their text to travel with it: the build writes them to ``third-party-licenses.txt`` beside the
console (``panel/scripts/bundle-licenses.mjs``), NOTICE names that file, and the packages ship it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MARK = "Derived in part from Coolify's template"


def derived_recipes() -> set[str]:
    """
    Returns:
        The recipes whose header says they are derived from a Coolify template, as repository paths.
    """
    recipes = REPO / "src" / "noust" / "recipes"
    return {
        f"src/noust/recipes/{path.name}"
        for path in recipes.glob("*.yaml")
        if MARK in path.read_text(encoding="utf-8")
    }


def test_the_apache_licence_text_ships() -> None:
    text = (REPO / "LICENSE-APACHE-2.0").read_text(encoding="utf-8")

    assert text.lstrip().startswith("Apache License")
    assert "Version 2.0, January 2004" in text


def test_notice_lists_exactly_the_derived_recipes() -> None:
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\s+(src/noust/recipes/\S+\.yaml)$", notice, re.MULTILINE))

    assert listed == derived_recipes()
    assert "Andras Bacsai" in notice
    assert "LICENSE-APACHE-2.0" in notice


def test_the_debian_copyright_names_them_under_their_licence() -> None:
    copyright_text = (REPO / "obs" / "debian.copyright").read_text(encoding="utf-8")
    paragraph = next(
        p for p in copyright_text.split("\n\n") if p.startswith("Files:") and MARK.split()[0] in p
    )
    listed = set(re.findall(r"^\s+(src/noust/recipes/\S+\.yaml)$", paragraph, re.MULTILINE))

    assert listed == derived_recipes()
    assert re.search(r"^License: Apache-2\.0$", paragraph, re.MULTILINE)
    assert re.search(
        r"^License: Apache-2\.0\n", copyright_text.split(paragraph, 1)[1], re.MULTILINE
    )


def test_the_packages_and_the_sdist_carry_the_notice() -> None:
    spec = (REPO / "rpm" / "noust.spec").read_text(encoding="utf-8")
    manifest = (REPO / "MANIFEST.in").read_text(encoding="utf-8")

    assert re.search(r"^%license .*\bLICENSE-APACHE-2\.0\b.*\bNOTICE\b", spec, re.MULTILINE)
    assert "include LICENSE-APACHE-2.0" in manifest
    assert "include NOTICE" in manifest


def test_the_fonts_and_the_copied_files_are_declared_too() -> None:
    copyright_text = (REPO / "obs" / "debian.copyright").read_text(encoding="utf-8")
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    ofl = (REPO / "LICENSE-OFL-1.1").read_text(encoding="utf-8")
    spec = (REPO / "rpm" / "noust.spec").read_text(encoding="utf-8")

    assert "Files: src/noust/web/static/assets/*.woff2" in copyright_text
    assert "Files: src/noust/recipes/assets/plausible/clickhouse/*" in copyright_text
    for license_name in ("Expat", "OFL-1.1", "Apache-2.0"):
        # Each short name a Files paragraph uses has its own License paragraph with the text.
        assert re.search(rf"^License: {re.escape(license_name)}\n ", copyright_text, re.MULTILINE)
    assert "JetBrains Mono" in ofl and "Mona Sans" in ofl and "SIL OPEN FONT LICENSE" in ofl
    for name in ("Plausible", "JetBrains Mono", "Mona Sans", "Dokploy"):
        assert name in notice
    assert re.search(r"^%license .*\bLICENSE-OFL-1\.1\b", spec, re.MULTILINE)


# The console's npm packages: the build writes their notice, NOTICE points to it, the packages ship it.

STATIC = REPO / "src" / "noust" / "web" / "static"
LICENSES_MODULE = REPO / "panel" / "scripts" / "bundle-licenses.mjs"


def licenses_file_name() -> str:
    """
    Returns:
        The name the build gives the notice of the console's packages, read from the module that
        writes it, so a rename there fails here instead of leaving NOTICE pointing at nothing.
    """
    found = re.search(r'LICENSES_FILE\s*=\s*"([^"]+)"', LICENSES_MODULE.read_text(encoding="utf-8"))
    assert found is not None, "bundle-licenses.mjs no longer says what file it writes"
    return found.group(1)


def test_notice_points_to_the_licences_of_the_consoles_packages() -> None:
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\s+(src/noust/web/static/[^*\s]+\.txt)$", notice, re.MULTILINE))

    assert listed == {f"src/noust/web/static/{licenses_file_name()}"}


def test_the_build_writes_that_notice_and_the_wheel_and_the_sdist_carry_it() -> None:
    config = (REPO / "panel" / "vite.config.ts").read_text(encoding="utf-8")
    manifest = (REPO / "MANIFEST.in").read_text(encoding="utf-8")
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")

    # The plugin is registered, not only defined: a build without it ships no notice.
    assert re.search(r"^\s+thirdPartyLicenses\(\),$", config, re.MULTILINE)
    assert "LICENSES_FILE" in config
    # Any file of the build travels: neither the include nor the glob is by extension.
    assert "recursive-include src/noust/web/static *" in manifest
    assert '"web/static/**/*"' in pyproject


def test_the_committed_console_carries_the_licence_of_each_package_it_is_built_from() -> None:
    notice = STATIC / licenses_file_name()
    if not notice.exists():
        pytest.skip(
            "the committed build is from before the notice; the closing lane's build commit adds it"
        )
    text = notice.read_text(encoding="utf-8")
    dependencies = json.loads((REPO / "panel" / "package.json").read_text(encoding="utf-8"))[
        "dependencies"
    ]

    # Every package the console imports at run time is a heading with its licence under it.
    headings = dict(re.findall(r"^(\S+) \d\S*\nLicense: (\S+)$", text, re.MULTILINE))
    assert sorted(set(dependencies) - set(headings)) == []
    assert "unknown" not in headings.values()
    assert "Copyright (c) 2024-2026 Yago López Prado" in text
