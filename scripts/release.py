#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Propagate the project version from pyproject.toml to every packaging file.

The version used to live as a hand-edited literal in six places, kept in step
by a checklist in CONTRIBUTING.md. Checklists fail, and this one already caused
corrective releases. Here ``[project].version`` in pyproject.toml is the single
source of truth and everything else is derived from it: the noust packages and
the container image.

SECURITY.md is held to the same rule: its table of supported versions must list
the release's major.minor line as supported, and ``--check`` fails when it does
not. A release of a new minor version adds its line, keeps the previous one
until the minor version after it is released, and retires the one before.

``.well-known/security.txt`` (RFC 9116) expires on purpose: each release renews
its ``Expires`` six months ahead, and ``--check`` fails once it has lapsed, when
a contact in it is not also listed in SECURITY.md, or when a scanner exception
in ``.trivyignore`` has no reason or has run out.

Usage:
    scripts/release.py --check          Verify every file agrees. Used by CI.
    scripts/release.py 1.0.0            Set the version everywhere.
    scripts/release.py 1.0.0 -m "..."   Set it and write changelog entries.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - only on 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent

MAINTAINER = "Yago Lopez Prado"
MAINTAINER_EMAIL = "yago.lopez.adeje@gmail.com"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

#: The Debian source name of the product.
PACKAGE = "noust"

#: The policy that says which versions get security fixes.
SECURITY_FILE = "SECURITY.md"

#: Where a researcher looks for how to reach the project (RFC 9116). It expires
#: by design: a contact nobody has vouched for lately is not one to trust, so
#: every release renews it and ``--check`` fails once it has lapsed.
SECURITY_TXT = ".well-known/security.txt"

#: How far ahead a release sets ``Expires``. The RFC asks for under a year.
SECURITY_TXT_VALIDITY_DAYS = 180

#: Where the scanners' exceptions are listed, each with the day it ends.
TRIVY_IGNORE = ".trivyignore"

#: The policy URL security.txt must carry, the one place the policy is read.
POLICY_URL = "https://github.com/Perkybeet/noust/blob/main/SECURITY.md"

#: One line of its table: ``| 3.3.x | Yes |``, or ``| 3.2.x | Yes, until 3.4.0 is
#: released |``. The row for everything older (``| 3.1.x and earlier ... |``) has
#: text after the ``x`` and is not one of these.
_LINE_ROW = re.compile(r"^\| (\d+)\.(\d+)\.x \| ([^|]*?) \|[ \t]*$", re.MULTILINE)
_EARLIER_ROW = re.compile(r"^(\| )(\d+)\.(\d+)(\.x and earlier)", re.MULTILINE)
_UNTIL = re.compile(r"Yes, until (\d+\.\d+\.\d+) is released")


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
DEBIAN_CHANGELOGS: tuple[str, ...] = ("obs/debian.changelog",)

#: What the console shows of each release ("what's new"), generated from the product's Debian
#: changelog so there is one place a release's summary is written (src/noust/core/changelog.py).
PACKAGED_CHANGELOG = "src/noust/changelog.json"


def _changelog_module():
    """
    Load ``noust.core.changelog`` without importing the package.

    The checks run before the project's dependencies are installed (CI runs ``--check`` first),
    and that module needs nothing but the standard library, so it is loaded from its file.

    Returns:
        The module.
    """
    name = "noust_changelog_data"
    if name in sys.modules:
        return sys.modules[name]
    # This script's own checkout, not ROOT: the tests point ROOT at a miniature repository.
    source = Path(__file__).resolve().parent.parent / "src/noust/core/changelog.py"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise RuntimeError("src/noust/core/changelog.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def packaged_changelog() -> str:
    """Returns: What ``src/noust/changelog.json`` must hold, from ``obs/debian.changelog``."""
    text = (ROOT / "obs/debian.changelog").read_text(encoding="utf-8")
    return _changelog_module().render_from_debian(text)


def sync_packaged_changelog() -> bool:
    """
    Write ``src/noust/changelog.json`` from the Debian changelog.

    Returns:
        True when the file changed.
    """
    path = ROOT / PACKAGED_CHANGELOG
    wanted = packaged_changelog()
    if path.exists() and path.read_text(encoding="utf-8") == wanted:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(wanted, encoding="utf-8")
    return True


#: RPM specs, each with a %changelog entry for the current version.
RPM_SPECS: tuple[str, ...] = ("rpm/noust.spec",)

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


def _numbers(version: str) -> tuple[int, int, int]:
    """
    Args:
        version: A version that starts ``X.Y.Z``; what follows (``rc1``) is
            not part of the comparison.

    Returns:
        Its three numbers, to compare versions the way people read them.

    Raises:
        ValueError: When it does not start with three numbers.
    """
    found = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if found is None:
        raise ValueError(f"{version!r} does not start with X.Y.Z")
    return int(found.group(1)), int(found.group(2)), int(found.group(3))


def security_problem(version: str) -> str | None:
    """
    Check that SECURITY.md lists a version's line as supported.

    Args:
        version: The version being released, ``X.Y.Z``.

    Returns:
        What is wrong, or None when the table lists ``X.Y.x`` as supported.
    """
    file = ROOT / SECURITY_FILE
    if not file.exists():
        return f"{SECURITY_FILE}: missing"
    try:
        major, minor, _ = _numbers(version)
    except ValueError as exc:
        return f"{SECURITY_FILE}: cannot tell which line {version!r} belongs to ({exc})"
    for row in _LINE_ROW.finditer(file.read_text(encoding="utf-8")):
        if (int(row.group(1)), int(row.group(2))) != (major, minor):
            continue
        note = row.group(3).strip()
        if not note.startswith("Yes"):
            return f"{SECURITY_FILE}: lists {major}.{minor}.x as not supported ({note!r})"
        until = _UNTIL.fullmatch(note)
        if until is not None and _numbers(version) >= _numbers(until.group(1)):
            return (
                f"{SECURITY_FILE}: says {major}.{minor}.x is supported until {until.group(1)} "
                f"is released, and {version} is not before it"
            )
        return None
    return (
        f"{SECURITY_FILE}: does not list {major}.{minor}.x as supported; add the row "
        f"'| {major}.{minor}.x | Yes |' to its table (scripts/release.py {version} does it)"
    )


def advance_security(text: str, version: str) -> str:
    """
    Bring SECURITY.md's table of supported versions to a new release.

    A release of a line the table already lists changes nothing. A release of
    a new line adds it as supported; the line that was the newest stays
    supported until the minor version after the new one is released; the lines
    that were still supported before it are retired, and the row for everything
    older moves up to cover them.

    Args:
        text: SECURITY.md as it is.
        version: The version being released, ``X.Y.Z``.

    Returns:
        The text with the table advanced, unchanged for a patch release.

    Raises:
        ValueError: When the table's rows are not one block, which is the only
            shape this knows how to rewrite without damaging what is around it.
    """
    major, minor, _ = _numbers(version)
    rows = list(_LINE_ROW.finditer(text))
    if any((int(row.group(1)), int(row.group(2))) == (major, minor) for row in rows):
        return text
    supported = [row for row in rows if row.group(3).strip().startswith("Yes")]
    others = [row.group(0) for row in rows if row not in supported]

    if rows:
        start, end = rows[0].start(), rows[-1].end()
        if "\n".join(row.group(0) for row in rows) != text[start:end]:
            raise ValueError(
                f"the rows of the supported-versions table in {SECURITY_FILE} are split"
            )
    else:
        raise ValueError(
            f"{SECURITY_FILE} has no supported-versions table to add {major}.{minor}.x to"
        )

    lines = [f"| {major}.{minor}.x | Yes |"]
    if supported:
        newest = supported[0]
        lines.append(
            f"| {newest.group(1)}.{newest.group(2)}.x | "
            f"Yes, until {major}.{minor + 1}.0 is released |"
        )
    lines.extend(others)
    advanced = text[:start] + "\n".join(lines) + text[end:]

    retired = [(int(row.group(1)), int(row.group(2))) for row in supported[1:]]
    if retired:
        top = max(retired)

        def move_up(row: re.Match[str]) -> str:
            current = (int(row.group(2)), int(row.group(3)))
            shown = max(current, top)
            return f"{row.group(1)}{shown[0]}.{shown[1]}{row.group(4)}"

        advanced = _EARLIER_ROW.sub(move_up, advanced, count=1)
    return advanced


def _advanced_security(version: str) -> str | None:
    """
    Work out SECURITY.md for a release, without writing it.

    Args:
        version: The version being released.

    Returns:
        The new text, or None when the file is missing or already right.

    Raises:
        ValueError: As :func:`advance_security`.
    """
    file = ROOT / SECURITY_FILE
    if not file.exists():
        return None
    text = file.read_text(encoding="utf-8")
    advanced = advance_security(text, version)
    return None if advanced == text else advanced


def _today() -> date:
    """
    Return today's date in UTC, the clock every expiry is judged by.

    Returns:
        The current date.
    """
    return datetime.now(timezone.utc).date()


_TXT_FIELD = re.compile(r"^([A-Za-z-]+):[ \t]*(.*?)[ \t]*$")
_TXT_EXPIRES = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_FINGERPRINT = re.compile(r"(?:[0-9A-Fa-f]{4}[ ]?){9}[0-9A-Fa-f]{4}")


def _txt_fields(text: str) -> dict[str, list[str]]:
    """
    Read the fields of a security.txt; comments and blank lines are not fields.

    Args:
        text: The file.

    Returns:
        Each field name (as written) to its values, in order.
    """
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        found = _TXT_FIELD.match(line)
        if found is not None:
            fields.setdefault(found.group(1), []).append(found.group(2))
    return fields


def security_txt_problems(today: date | None = None) -> list[str]:
    """
    Check ``.well-known/security.txt`` and that it agrees with SECURITY.md.

    Args:
        today: The date to judge the expiry by; today's by default.

    Returns:
        One sentence per problem, empty when the file is sound: present, the
        fields the policy depends on, a date that is neither past nor more
        than a year away, every contact also listed in SECURITY.md, and a
        fingerprint in SECURITY.md when a key is offered.
    """
    today = today or _today()
    file = ROOT / SECURITY_TXT
    if not file.exists():
        return [f"{SECURITY_TXT}: missing (RFC 9116; scripts/release.py renews it)"]
    fields = _txt_fields(file.read_text(encoding="utf-8"))
    problems: list[str] = []

    for name in ("Contact", "Expires", "Policy", "Canonical"):
        if not fields.get(name):
            problems.append(f"{SECURITY_TXT}: no {name} field")

    for expires in fields.get("Expires", [])[:1]:
        found = _TXT_EXPIRES.match(expires)
        if found is None:
            problems.append(
                f"{SECURITY_TXT}: Expires {expires!r} is not a date like 2027-01-31T00:00:00.000Z"
            )
        else:
            ends = date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
            if ends < today:
                problems.append(
                    f"{SECURITY_TXT}: expired on {ends.isoformat()}; "
                    "scripts/release.py renews it with every release"
                )
            elif ends > today + timedelta(days=366):
                problems.append(f"{SECURITY_TXT}: Expires is more than a year away (RFC 9116)")

    for policy in fields.get("Policy", []):
        if policy != POLICY_URL:
            problems.append(f"{SECURITY_TXT}: Policy is {policy!r}, expected {POLICY_URL}")

    policy_file = ROOT / SECURITY_FILE
    policy_text = policy_file.read_text(encoding="utf-8") if policy_file.exists() else ""
    if SECURITY_TXT not in policy_text:
        problems.append(f"{SECURITY_FILE}: does not point at {SECURITY_TXT}")
    for contact in fields.get("Contact", []):
        shown = contact.removeprefix("mailto:")
        if shown not in policy_text:
            problems.append(f"{SECURITY_TXT}: Contact {shown} is not listed in {SECURITY_FILE}")
    if fields.get("Encryption") and _FINGERPRINT.search(policy_text) is None:
        problems.append(
            f"{SECURITY_FILE}: {SECURITY_TXT} offers a key, so the policy must print its fingerprint"
        )
    return problems


def refreshed_security_txt(text: str, today: date | None = None) -> str:
    """
    Renew a security.txt's expiry; nothing else in it changes.

    Args:
        text: The file as it is.
        today: The release's date; today's by default.

    Returns:
        The text with ``Expires`` set :data:`SECURITY_TXT_VALIDITY_DAYS` ahead,
        added after the last ``Contact`` when it had none.
    """
    today = today or _today()
    ends = today + timedelta(days=SECURITY_TXT_VALIDITY_DAYS)
    line = f"Expires: {ends.isoformat()}T00:00:00.000Z"
    lines = text.splitlines()
    for index, current in enumerate(lines):
        if current.startswith("Expires:"):
            lines[index] = line
            break
    else:
        last_contact = max(
            (i for i, current in enumerate(lines) if current.startswith("Contact:")), default=-1
        )
        lines.insert(last_contact + 1, line)
    return "\n".join(lines) + "\n"


def _advanced_security_txt() -> str | None:
    """
    Work out security.txt for a release, without writing it.

    Returns:
        The renewed text, or None when there is no file to renew.
    """
    file = ROOT / SECURITY_TXT
    if not file.exists():
        return None
    return refreshed_security_txt(file.read_text(encoding="utf-8"), _today())


_EXPIRY_COMMENT = re.compile(r"^#\s*expires:\s*(\d{4}-\d{2}-\d{2})(?:\s+(\S.*))?$", re.IGNORECASE)


def trivyignore_problems(today: date | None = None) -> list[str]:
    """
    Check that every scanner exception says why, and has not run out.

    An exception is an advisory id preceded by a ``# expires: YYYY-MM-DD reason``
    comment. One that never ends is a finding hidden for good.

    Args:
        today: The date to judge by; today's by default.

    Returns:
        One sentence per entry that is expired, undated or unexplained.
    """
    today = today or _today()
    file = ROOT / TRIVY_IGNORE
    if not file.exists():
        return []
    problems: list[str] = []
    pending: re.Match[str] | None = None
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            found = _EXPIRY_COMMENT.match(line)
            if found is not None:
                pending = found
            continue
        if pending is None:
            problems.append(
                f"{TRIVY_IGNORE}: {line} has no '# expires: YYYY-MM-DD reason' comment before it"
            )
            continue
        if not (pending.group(2) or "").strip():
            problems.append(f"{TRIVY_IGNORE}: {line} gives no reason after its expires date")
        if date.fromisoformat(pending.group(1)) < today:
            problems.append(
                f"{TRIVY_IGNORE}: {line} expired on {pending.group(1)}; fix or renew it"
            )
        pending = None
    return problems


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

    problem = security_problem(expected)
    if problem is not None:
        problems.append(problem)
    problems.extend(security_txt_problems())
    problems.extend(trivyignore_problems())

    packaged = ROOT / PACKAGED_CHANGELOG
    if not packaged.exists() or packaged.read_text(encoding="utf-8") != packaged_changelog():
        problems.append(
            f"{PACKAGED_CHANGELOG}: out of date with obs/debian.changelog; "
            "run scripts/release.py --sync-changelog"
        )

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

    Args:
        version: The version being released.
        entries: The operator's bullet lines.
    """
    _prepend_debian_changelog("obs/debian.changelog", PACKAGE, version, entries)
    _prepend_rpm_changelog("rpm/noust.spec", version, entries)
    sync_packaged_changelog()


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

    # Worked out before anything is written: a table this cannot rewrite must
    # not leave a release half made.
    try:
        security = _advanced_security(version)
    except ValueError as exc:
        print(str(exc))
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

    if security is not None:
        (ROOT / SECURITY_FILE).write_text(security, encoding="utf-8")
        print(f"  updated {SECURITY_FILE}")

    renewed = _advanced_security_txt()
    if renewed is not None:
        (ROOT / SECURITY_TXT).write_text(renewed, encoding="utf-8")
        print(f"  renewed {SECURITY_TXT}")

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
        "--sync-changelog",
        action="store_true",
        help=f"regenerate {PACKAGED_CHANGELOG} from obs/debian.changelog",
    )
    parser.add_argument(
        "-m",
        "--message",
        action="append",
        default=[],
        help="changelog bullet, repeatable",
    )
    args = parser.parse_args(argv)

    if args.sync_changelog:
        print("updated" if sync_packaged_changelog() else "already current", PACKAGED_CHANGELOG)
        return 0
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
