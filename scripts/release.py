#!/usr/bin/env python3
"""
Propagate the project version from pyproject.toml to every packaging file.

The version used to live as a hand-edited literal in six places, kept in step
by a checklist in CONTRIBUTING.md. Checklists fail, and this one already caused
corrective releases. Here ``[project].version`` in pyproject.toml is the single
source of truth and everything else is derived from it: the noust packages,
the transitional wasm/wasm-cli packages that carry users of the old name over,
and the container image.

Usage:
    scripts/release.py --check          Verify every file agrees. Used by CI.
    scripts/release.py 1.0.0            Set the version everywhere.
    scripts/release.py 1.0.0 -m "..."   Set it and write changelog entries.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - only on 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent

MAINTAINER = "Yago Lopez Prado"
MAINTAINER_EMAIL = "yago.lopez.adeje@gmail.com"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

#: The Debian source name of the product, and the one it had before 3.0.0.
PACKAGE = "noust"
FORMER_PACKAGE = "wasm"

#: Written as the first changelog bullet of the first release under the new
#: name, which is the one whose previous Debian entry still carries the old one.
RENAME_ENTRY = (
    "Renamed from wasm: the package, the command and the paths are noust now; "
    "wasm remains a command alias for the whole 3.x series"
)

#: The transitional packages' changelogs say the same thing every release.
TRANSITIONAL_ENTRY = (
    "WASM is now Noust: this package only installs noust {version} and can be removed"
)

TRANSITIONAL_DIR = "packaging/transitional/wasm"


@dataclass(frozen=True)
class Site:
    """
    One place where the version literal appears.

    Attributes:
        path: File relative to the repository root.
        pattern: Regex capturing the version in group ``group``.
        template: Replacement string, with ``{version}`` substituted.
        label: Human-readable name for error messages.
        group: The group of ``pattern`` holding the version; the others are
            kept by ``template`` through back-references.
    """

    path: str
    pattern: str
    template: str
    label: str
    group: int = 1

    def read(self) -> str | None:
        """
        Return the version currently recorded in this file.

        Returns:
            The version string, or None when the file or pattern is missing.
        """
        file = ROOT / self.path
        if not file.exists():
            return None
        match = re.search(self.pattern, file.read_text(encoding="utf-8"), re.MULTILINE)
        return match.group(self.group) if match else None

    def write(self, version: str) -> bool:
        """
        Rewrite this file to carry the given version.

        Args:
            version: The version to record.

        Returns:
            True when the file changed.
        """
        file = ROOT / self.path
        text = file.read_text(encoding="utf-8")
        updated, count = re.subn(
            self.pattern, self.template.format(version=version), text, count=1, flags=re.MULTILINE
        )
        if count == 0:
            raise SystemExit(f"Could not find the version pattern in {self.path}")
        if updated == text:
            return False
        file.write_text(updated, encoding="utf-8")
        return True


# Templates use \g<N>, never \N: "\1" followed by a version starting with a
# digit reads as group 11 (see tests/test_release_script.py).
SITES: tuple[Site, ...] = (
    Site(
        path="setup.py",
        pattern=r'^(\s*)version="([^"]+)"',
        template=r'\g<1>version="{version}"',
        label="setup.py",
        group=2,
    ),
    Site(
        path="src/noust/__init__.py",
        pattern=r'^_FALLBACK_VERSION = "([^"]+)"',
        template='_FALLBACK_VERSION = "{version}"',
        label="package fallback",
    ),
    Site(
        path="rpm/noust.spec",
        pattern=r"^Version:(\s+)(\S+)",
        template=r"Version:\g<1>{version}",
        label="RPM spec",
        group=2,
    ),
    Site(
        path="obs/noust.dsc",
        pattern=r"^Version: (\S+)-\d+",
        template="Version: {version}-1",
        label="Debian source control",
    ),
    Site(
        path="obs/noust.dsc",
        pattern=r"^ (\w{32}) (\d+) noust-(\S+)\.tar\.gz",
        template=r" \g<1> \g<2> noust-{version}.tar.gz",
        label="Debian source tarball name",
        group=3,
    ),
    Site(
        path=f"{TRANSITIONAL_DIR}/wasm.spec",
        pattern=r"^Version:(\s+)(\S+)",
        template=r"Version:\g<1>{version}",
        label="transitional RPM spec",
        group=2,
    ),
    Site(
        path=f"{TRANSITIONAL_DIR}/wasm.dsc",
        pattern=r"^Version: (\S+)-\d+",
        template="Version: {version}-1",
        label="transitional Debian source control",
    ),
    Site(
        path=f"{TRANSITIONAL_DIR}/wasm.dsc",
        pattern=r"^ (\w{32}) (\d+) wasm-(\S+)\.tar\.gz",
        template=r" \g<1> \g<2> wasm-{version}.tar.gz",
        label="transitional Debian source tarball name",
        group=3,
    ),
    Site(
        path="packaging/transitional/wasm-cli/pyproject.toml",
        pattern=r'^version = "([^"]+)"',
        template='version = "{version}"',
        label="transitional PyPI package",
    ),
    Site(
        path="packaging/transitional/wasm-cli/pyproject.toml",
        pattern=r'^(\s*)"noust==([^"]+)"',
        template=r'\g<1>"noust=={version}"',
        label="transitional PyPI dependency",
        group=2,
    ),
    Site(
        path="packaging/container/Dockerfile",
        pattern=r"^ARG NOUST_VERSION=(\S+)",
        template="ARG NOUST_VERSION={version}",
        label="container image",
    ),
    Site(
        path="packaging/container/compose.yaml",
        pattern=r"^(\s*)image: ghcr\.io/perkybeet/noust:(\S+)",
        template=r"\g<1>image: ghcr.io/perkybeet/noust:{version}",
        label="container compose example",
        group=2,
    ),
)

#: Debian changelogs, each with its top entry at the current version.
DEBIAN_CHANGELOGS: tuple[str, ...] = (
    "obs/debian.changelog",
    f"{TRANSITIONAL_DIR}/debian.changelog",
)

#: RPM specs, each with a %changelog entry for the current version.
RPM_SPECS: tuple[str, ...] = ("rpm/noust.spec", f"{TRANSITIONAL_DIR}/wasm.spec")

DEBIAN_ENTRY = re.compile(r"^(\S+) \((\S+?)-\d+\)")


def source_of_truth() -> str:
    """
    Return the version declared in pyproject.toml.

    Returns:
        The canonical project version.
    """
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def _top_debian_entry(path: str) -> tuple[str, str] | None:
    """
    Read the package name and upstream version of a Debian changelog's top entry.

    Args:
        path: The changelog, relative to the repository root.

    Returns:
        ``(package, version)``, or None when the file is missing or unparseable.
    """
    file = ROOT / path
    if not file.exists():
        return None
    lines = file.read_text(encoding="utf-8").splitlines()
    match = DEBIAN_ENTRY.match(lines[0]) if lines else None
    return (match.group(1), match.group(2)) if match else None


def check() -> int:
    """
    Verify that every packaging file agrees with pyproject.toml.

    Returns:
        Process exit code: 0 when consistent, 1 otherwise.
    """
    expected = source_of_truth()
    problems: list[str] = []

    for site in SITES:
        found = site.read()
        if found is None:
            problems.append(f"{site.label} ({site.path}): version not found")
        elif found != expected:
            problems.append(f"{site.label} ({site.path}): {found}, expected {expected}")

    for path in DEBIAN_CHANGELOGS:
        top = _top_debian_entry(path)
        if top is None:
            problems.append(f"{path}: top entry is missing or not parseable")
        elif top[1] != expected:
            problems.append(f"{path}: top entry is {top[1]}, expected {expected}")

    for path in RPM_SPECS:
        spec = ROOT / path
        if not spec.exists():
            problems.append(f"{path}: missing")
        elif f"- {expected}-1" not in spec.read_text(encoding="utf-8"):
            problems.append(f"{path}: no %changelog entry for {expected}")

    tag = _current_tag()
    if tag and tag.lstrip("v") != expected:
        problems.append(f"git tag {tag} does not match pyproject version {expected}")

    if problems:
        print(f"Version inconsistency (pyproject.toml says {expected}):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"All packaging files agree on version {expected}")
    return 0


def _current_tag() -> str | None:
    """
    Return the tag pointing at HEAD, if any.

    Returns:
        The tag name, or None when HEAD is not tagged or git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "describe", "--exact-match", "--tags", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _prepend_debian_changelog(path: str, package: str, version: str, entries: list[str]) -> None:
    """
    Add a Debian changelog entry at the top of a file.

    Args:
        path: The changelog, relative to the repository root.
        package: The source package name the entry is for.
        version: The version being released.
        entries: Bullet lines describing the release.
    """
    changelog = ROOT / path
    stamp = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    body = "\n".join(f"  * {entry}" for entry in entries)
    block = (
        f"{package} ({version}-1) unstable; urgency=medium\n\n"
        f"{body}\n\n"
        f" -- {MAINTAINER} <{MAINTAINER_EMAIL}>  {stamp}\n\n"
    )
    changelog.write_text(block + changelog.read_text(encoding="utf-8"), encoding="utf-8")


def _prepend_rpm_changelog(path: str, version: str, entries: list[str]) -> None:
    """
    Add an RPM %changelog entry directly below the %changelog directive.

    Args:
        path: The spec, relative to the repository root.
        version: The version being released.
        entries: Bullet lines describing the release.
    """
    spec = ROOT / path
    text = spec.read_text(encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%a %b %d %Y")
    body = "\n".join(f"- {entry}" for entry in entries)
    block = f"%changelog\n* {stamp} {MAINTAINER} <{MAINTAINER_EMAIL}> - {version}-1\n{body}\n"
    spec.write_text(text.replace("%changelog\n", block, 1), encoding="utf-8")


def _write_changelogs(version: str, entries: list[str]) -> None:
    """
    Write this release's entries into every changelog.

    The product's changelogs get the operator's bullets, preceded by
    :data:`RENAME_ENTRY` on the first release after the rename; the
    transitional packages' get :data:`TRANSITIONAL_ENTRY`.

    Args:
        version: The version being released.
        entries: The operator's bullet lines.
    """
    top = _top_debian_entry("obs/debian.changelog")
    if top is not None and top[0] == FORMER_PACKAGE:
        entries = [RENAME_ENTRY, *entries]
    _prepend_debian_changelog("obs/debian.changelog", PACKAGE, version, entries)
    _prepend_rpm_changelog("rpm/noust.spec", version, entries)

    transitional = [TRANSITIONAL_ENTRY.format(version=version)]
    _prepend_debian_changelog(
        f"{TRANSITIONAL_DIR}/debian.changelog", FORMER_PACKAGE, version, transitional
    )
    _prepend_rpm_changelog(f"{TRANSITIONAL_DIR}/wasm.spec", version, transitional)


def bump(version: str, entries: list[str]) -> int:
    """
    Set the version everywhere and optionally write changelog entries.

    Args:
        version: The new version.
        entries: Bullet lines for the changelogs. Empty to skip them.

    Returns:
        Process exit code.
    """
    if not SEMVER.match(version):
        print(f"Version must look like X.Y.Z, got {version!r}")
        return 1

    pyproject = ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^version = "[^"]+"', f'version = "{version}"', text, count=1, flags=re.MULTILINE
    )
    if count == 0:
        print("Could not find the version field in pyproject.toml")
        return 1
    pyproject.write_text(updated, encoding="utf-8")

    for site in SITES:
        if site.write(version):
            print(f"  updated {site.label}")

    if entries:
        _write_changelogs(version, entries)
        print("  updated changelogs")

    print(f"\nVersion set to {version}. Now:")
    print(f"  git commit -am 'v{version}: <summary>'")
    print(f"  git tag -a v{version} -m 'Release v{version}'")
    print(f"  git push && git push origin v{version}")
    return check()


def main(argv: list[str] | None = None) -> int:
    """
    Command line entry point.

    Args:
        argv: Arguments, defaulting to sys.argv.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("version", nargs="?", help="new version, as X.Y.Z")
    parser.add_argument("--check", action="store_true", help="verify consistency without writing")
    parser.add_argument(
        "-m",
        "--message",
        action="append",
        default=[],
        help="changelog bullet, repeatable",
    )
    args = parser.parse_args(argv)

    if args.check or not args.version:
        return check()
    # The man pages carry the version in their header and CI checks them, so a bump
    # that leaves them behind fails the release gate. After bump(), here and not in it: regenerating
    # runs the real CLI against the real tree, which a test of bump() must not.
    status = bump(args.version, args.message)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_man.py")], check=True)
    print("  updated man pages")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
