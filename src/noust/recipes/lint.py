# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The recipe lint: every finding at once, for CI and for people who write recipes.

Loading a recipe stops at the first thing wrong with it, which is the right
behaviour for an installer and a poor one for an author with six mistakes. This
reads the same documents and reports all of them, and holds the rule that makes
the catalogue worth trusting: **no image of a Compose recipe is allowed to move.**
Coolify's catalogue has a floating tag or none in 194 of 359 templates and no
digest anywhere; here an image is a tag that names a release *and* the digest
the registry served for it, or the lint fails.

The checks reuse the loader's own (:func:`noust.recipes.composefile.parse_image`
and :func:`noust.recipes.schema.parse_recipe`), so the lint and the catalogue
cannot disagree about what a floating tag is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from noust.recipes.assets import set_asset_root
from noust.recipes.catalog import catalogue_files
from noust.recipes.composefile import parse_image
from noust.recipes.model import RecipeError, set_location
from noust.recipes.schema import parse_recipe
from noust.recipes.sources import RecipeFile, folder_files

#: Severities, as the lint reports them.
ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """
    One thing wrong with a recipe.

    Attributes:
        recipe: The recipe's name.
        rule: A short name that does not change: ``floating-image``,
            ``unpinned-digest``, ``image-variable`` or ``schema``.
        severity: ``error`` fails CI; ``warning`` is listed.
        message: What is wrong, naming the service and the reference.
    """

    recipe: str
    rule: str
    severity: str
    message: str

    def render(self) -> str:
        """
        Format the finding as one line.

        Returns:
            ``recipe: severity [rule] message``.
        """
        return f"{self.recipe}: {self.severity} [{self.rule}] {self.message}"


def lint_images(name: str, data: dict[str, Any]) -> list[Finding]:
    """
    Check every image of a Compose recipe's document.

    Args:
        name: The recipe.
        data: Its YAML, loaded.

    Returns:
        One finding per image that moves, has no tag or lacks the digest the
        recipe promises. An empty list for a native recipe.
    """
    # A recipe listed as unavailable carries no document: it is not deployed.
    if data.get("kind") != "compose" or data.get("available") is False:
        return []
    verified = data.get("digest_verified", True) is not False
    try:
        document = yaml.safe_load(str(data.get("compose") or ""))
    except yaml.YAMLError as exc:
        return [Finding(name, "schema", ERROR, f"the Compose document is not YAML: {exc}")]
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        return [Finding(name, "schema", ERROR, "the Compose document has no services")]
    findings: list[Finding] = []
    for service, spec in services.items():
        reference = spec.get("image") if isinstance(spec, dict) else None
        if reference is None:
            findings.append(Finding(name, "no-image", ERROR, f"service {service} has no image"))
            continue
        if isinstance(reference, str) and "${" in reference:
            findings.append(
                Finding(
                    name,
                    "image-variable",
                    ERROR,
                    f"service {service}: {reference!r} is a variable, so it can name anything",
                )
            )
            continue
        try:
            parse_image(name, str(service), reference, digest_verified=verified)
        except RecipeError as exc:
            reason = str(exc).split("is not valid: ", 1)[-1]
            rule = "unpinned-digest" if "no digest" in reason else "floating-image"
            findings.append(Finding(name, rule, ERROR, reason))
    if not verified:
        findings.append(
            Finding(
                name,
                "digest-unverified",
                WARNING,
                "pinned by tag only: digest_verified is false, so the image can change under "
                "its tag",
            )
        )
    return findings


def lint_file(entry: RecipeFile) -> list[Finding]:
    """
    Lint one recipe file.

    Args:
        entry: The file, as the catalogue found it.

    Returns:
        Every finding: the images first, then whatever the schema refuses.
    """
    set_asset_root(entry.name, None if entry.origin == "shipped" else entry.root)
    set_location(entry.name, None if entry.origin == "shipped" else entry.location)
    try:
        data = yaml.safe_load(entry.read())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [Finding(entry.name, "schema", ERROR, f"cannot be read as YAML: {exc}")]
    if not isinstance(data, dict):
        return [Finding(entry.name, "schema", ERROR, "the file must hold a mapping")]
    findings = lint_images(entry.name, data)
    try:
        parse_recipe(entry.name, data)
    except RecipeError as exc:
        message = str(exc).split("is not valid: ", 1)[-1]
        # The loader stops at the first image it refuses, which the lint has
        # already reported (with the others): not twice.
        about_images = any(word in message for word in ("image", "tag", "digest"))
        if not (about_images and any(item.severity == ERROR for item in findings)):
            findings.append(Finding(entry.name, "schema", ERROR, message))
    return findings


def lint_catalogue(directory: Path | None = None) -> list[Finding]:
    """
    Lint the shipped recipes, or the recipes of a folder.

    Args:
        directory: A folder laid out as the catalogue is (``<name>.yaml`` and
            ``assets/<name>/``), read as it is: ownership is not judged, so a
            checkout can be linted. The package's own recipes when None.

    Returns:
        Every finding, by recipe name.
    """
    findings: list[Finding] = []
    if directory is None:
        entries = [entry for entry in catalogue_files()[0].values() if entry.origin == "shipped"]
    else:
        files, problems = folder_files(directory, "operator", taken={}, trust_owner=True)
        entries = list(files.values())
        findings.extend(
            Finding(problem.name, "file", ERROR, f"{problem.location}: {problem.error}")
            for problem in problems
        )
    for entry in sorted(entries, key=lambda item: item.name):
        findings.extend(lint_file(entry))
    return findings
