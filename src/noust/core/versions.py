# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Versions and the ranges a project declares them in.

A repository says which runtime it needs in four dialects: npm's semver ranges
(``engines.node``: ``>=18 <21``, ``^20.11``, ``20.x``), Composer's constraints
(``require.php``: ``^8.1``, ``>=8.1,<8.4``, ``8.3.*``), Python's PEP 440
specifiers (``requires-python``: ``>=3.10,<4``, ``~=3.11``) and a bare version
(``.nvmrc``, ``.python-version``). Deciding whether the runtime a server has
satisfies them, and which of the versions that exist is the newest that does,
is one question, so this module answers it once for all four (rule 3): the
runtime manager asks it, and so do the deployers that read the files.

Only what decides a deployment is modelled: ``major.minor.patch``. A
prerelease suffix is dropped, because nothing here installs a prerelease, and
build metadata is ignored as semver says.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from noust.core.exceptions import ValidationError

#: The dialects of a range.
Flavor = Literal["npm", "composer", "pep440"]

_NUMBER = r"(?:0|[1-9][0-9]*)"
_PART = rf"(?:{_NUMBER}|[xX*])"
_VERSION_TEXT = re.compile(
    rf"^[vV=]?\s*(?P<major>{_PART})(?:\.(?P<minor>{_PART})(?:\.(?P<patch>{_PART}))?)?"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


@dataclass(frozen=True, order=True)
class Version:
    """
    A ``major.minor.patch`` version.

    Attributes:
        major: The first number.
        minor: The second; 0 when the text had none.
        patch: The third; 0 when the text had none.
    """

    major: int
    minor: int = 0
    patch: int = 0

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_version(text: str) -> Version | None:
    """
    Read a complete or partial version.

    Args:
        text: ``22``, ``v22.11``, ``22.11.0`` or ``3.12.4-rc1``.

    Returns:
        The version, with missing parts as zero; None when the text is not a
        version (a wildcard, ``lts/iron``, ``latest``).
    """
    match = _VERSION_TEXT.match(text.strip())
    if match is None:
        return None
    parts = [match.group("major"), match.group("minor"), match.group("patch")]
    if any(part is not None and part in ("x", "X", "*") for part in parts):
        return None
    numbers = [int(part) if part is not None else 0 for part in parts]
    return Version(*numbers)


@dataclass(frozen=True)
class _Comparator:
    """One bound: ``op`` is one of ``<``, ``<=``, ``>``, ``>=``, ``=``."""

    op: str
    version: Version

    def allows(self, candidate: Version) -> bool:
        if self.op == "=":
            return candidate == self.version
        if self.op == ">":
            return candidate > self.version
        if self.op == ">=":
            return candidate >= self.version
        if self.op == "<":
            return candidate < self.version
        return candidate <= self.version


@dataclass(frozen=True)
class VersionRange:
    """
    A range: alternatives (``||``), each a set of bounds that must all hold.

    Attributes:
        alternatives: At least one set of bounds; an empty set allows every
            version, which is what ``*`` and an empty string mean.
        text: The range as it was written, for messages.
    """

    alternatives: tuple[tuple[_Comparator, ...], ...]
    text: str = ""

    def allows(self, version: Version) -> bool:
        """
        Tell whether a version is inside the range.

        Args:
            version: The candidate.

        Returns:
            True when it satisfies every bound of at least one alternative.
        """
        return any(all(bound.allows(version) for bound in group) for group in self.alternatives)

    def best(self, versions: Iterable[Version]) -> Version | None:
        """
        Pick the newest of some versions that the range allows.

        Args:
            versions: The candidates.

        Returns:
            The highest allowed one, or None when none is.
        """
        allowed = [version for version in versions if self.allows(version)]
        return max(allowed) if allowed else None

    @property
    def lower_bound(self) -> Version | None:
        """The lowest version any alternative starts at, None when one is open."""
        starts: list[Version] = []
        for group in self.alternatives:
            lows = [b.version for b in group if b.op in (">=", ">", "=")]
            if not lows:
                return None
            starts.append(min(lows))
        return min(starts) if starts else None


def _bad(text: str, flavor: Flavor, reason: str) -> ValidationError:
    names = {
        "npm": "an npm range",
        "composer": "a Composer constraint",
        "pep440": "a PEP 440 range",
    }
    return ValidationError(
        f"{text!r} is not {names[flavor]} Noust can read ({reason})",
        details="Name the runtime explicitly instead, in noust.yaml: build: {runtime: node@22}.",
    )


def _partial(text: str) -> tuple[list[int | None], str]:
    """
    Split a possibly partial version into its numbers.

    Args:
        text: ``1``, ``1.2``, ``1.2.x``, ``1.2.3``, ``*``.

    Returns:
        Up to three numbers, with None for a missing or wildcard part.
    """
    match = _VERSION_TEXT.match(text.strip())
    if match is None:
        raise ValueError(text)
    numbers: list[int | None] = []
    for name in ("major", "minor", "patch"):
        part = match.group(name)
        numbers.append(None if part is None or part in ("x", "X", "*") else int(part))
    # A wildcard hides everything after it: 1.x.3 is 1.x.x.
    for index, value in enumerate(numbers):
        if value is None:
            numbers[index:] = [None] * (3 - index)
            break
    return numbers, text


def _floor(numbers: list[int | None]) -> Version:
    return Version(*(value or 0 for value in numbers))


def _ceiling(numbers: list[int | None]) -> Version | None:
    """The first version above everything ``numbers`` matches, None for ``*``."""
    major, minor, patch = numbers
    if major is None:
        return None
    if minor is None:
        return Version(major + 1)
    if patch is None:
        return Version(major, minor + 1)
    return Version(major, minor, patch + 1)


def _caret(numbers: list[int | None]) -> list[_Comparator]:
    floor = _floor(numbers)
    major, minor, patch = numbers
    if major is None:
        return []
    if major > 0 or minor is None:
        top = Version(major + 1)
    elif minor > 0 or patch is None:
        top = Version(0, (minor or 0) + 1)
    else:
        top = Version(0, 0, (patch or 0) + 1)
    return [_Comparator(">=", floor), _Comparator("<", top)]


def _tilde(numbers: list[int | None]) -> list[_Comparator]:
    floor = _floor(numbers)
    major, minor, _patch = numbers
    if major is None:
        return []
    top = Version(major + 1) if minor is None else Version(major, minor + 1)
    return [_Comparator(">=", floor), _Comparator("<", top)]


def _primitive(token: str) -> list[_Comparator]:
    """
    Turn one token (``^1.2``, ``>=3``, ``2.x``, ``~4.1.0``) into bounds.

    Args:
        token: A comparator with no spaces.

    Returns:
        The bounds, empty for a wildcard.

    Raises:
        ValueError: The token is not a comparator.
    """
    # Every string matches: an operator, then whatever is left.
    match = re.match(r"^(\^|~>|~|>=|<=|>|<|=)?\s*(.*)$", token)
    op, rest = (match.group(1) or "", match.group(2)) if match else ("", token)
    if rest in ("", "*", "x", "X") and op in ("", "=", ">=", "<=", "^", "~", "~>"):
        return []
    numbers, _ = _partial(rest)
    if op == "^":
        return _caret(numbers)
    if op in ("~", "~>"):
        return _tilde(numbers)
    floor = _floor(numbers)
    ceiling = _ceiling(numbers)
    if op in ("", "="):
        if numbers[2] is not None:
            return [_Comparator("=", floor)]
        if ceiling is None:
            return []
        return [_Comparator(">=", floor), _Comparator("<", ceiling)]
    if op == ">=":
        return [_Comparator(">=", floor)]
    if op == "<":
        return [_Comparator("<", floor)]
    if op == ">":
        return (
            [_Comparator(">=", ceiling)] if ceiling is not None else [_Comparator("<", Version(0))]
        )
    # "<=": a partial upper bound includes everything under it.
    return [_Comparator("<", ceiling)] if ceiling is not None else []


def _npm_alternative(text: str) -> tuple[_Comparator, ...]:
    hyphen = re.match(r"^\s*(\S+)\s+-\s+(\S+)\s*$", text)
    if hyphen:
        low, _ = _partial(hyphen.group(1))
        high, _ = _partial(hyphen.group(2))
        span = [_Comparator(">=", _floor(low))]
        ceiling = _ceiling(high)
        if ceiling is not None:
            span.append(_Comparator("<", ceiling))
        return tuple(span)
    # "> 1.2" and ">= 1.2" are written with a space after the operator.
    joined = re.sub(r"(\^|~>|~|>=|<=|>|<|=)\s+", r"\1", text.strip())
    bounds: list[_Comparator] = []
    for token in joined.split():
        bounds.extend(_primitive(token))
    return tuple(bounds)


def _pep440_alternative(text: str) -> tuple[_Comparator, ...]:
    bounds: list[_Comparator] = []
    for token in (part.strip() for part in text.split(",")):
        if not token:
            continue
        match = re.match(r"^(===|==|!=|~=|>=|<=|>|<)\s*(.+)$", token)
        if match is None:
            raise ValueError(token)
        op, rest = match.groups()
        numbers, _ = _partial(rest.replace(".*", ".x"))
        floor = _floor(numbers)
        ceiling = _ceiling(numbers)
        if op in ("==", "==="):
            bounds.extend(_primitive(rest.replace(".*", ".x")))
        elif op == "~=":
            # ~=3.10 is >=3.10, ==3.*; ~=3.10.2 is >=3.10.2, ==3.10.*
            known = [value for value in numbers if value is not None]
            if len(known) < 2:
                raise ValueError(token)
            top = Version(known[0] + 1) if len(known) == 2 else Version(known[0], known[1] + 1)
            bounds.extend([_Comparator(">=", floor), _Comparator("<", top)])
        elif op == "!=":
            # A single excluded patch or minor cannot be expressed as one
            # bound; the supported dialect is "allowed ranges", so refuse it.
            raise ValueError(token)
        elif op == ">=":
            bounds.append(_Comparator(">=", floor))
        elif op == "<=":
            bounds.append(_Comparator("<", ceiling) if ceiling else _Comparator("<=", floor))
        elif op == ">":
            bounds.append(_Comparator(">=", ceiling) if ceiling else _Comparator(">", floor))
        else:
            bounds.append(_Comparator("<", floor))
    return tuple(bounds)


def parse_range(text: str, flavor: Flavor = "npm") -> VersionRange:
    """
    Read a range written in one of the dialects.

    Args:
        text: The range as the repository wrote it.
        flavor: ``npm`` for ``engines.node``, ``composer`` for ``require.php``,
            ``pep440`` for ``requires-python``.

    Returns:
        The range. An empty string, ``*`` and ``x`` allow every version.

    Raises:
        ValidationError: The text is not in that dialect, or uses a form this
            module does not model (``!=``, an unbalanced operator).
    """
    original = text
    stripped = text.strip()
    if flavor == "pep440":
        parts = [stripped]
    elif flavor == "composer":
        # Composer: "," is AND, "|" or "||" is OR, stability flags are noise.
        cleaned = re.sub(r"@\w+", "", stripped)
        cleaned = re.sub(r"\s*\|\|?\s*", "||", cleaned).replace(",", " ")
        parts = cleaned.split("||")
    else:
        parts = stripped.split("||")
    try:
        alternatives = tuple(
            _pep440_alternative(part) if flavor == "pep440" else _npm_alternative(part)
            for part in parts
        )
    except ValueError as exc:
        raise _bad(original, flavor, f"cannot read {str(exc)!r}") from exc
    return VersionRange(alternatives=alternatives or ((),), text=original)


#: ``lts``, ``lts/*``, ``lts/iron`` and their kin: the newest long-term release.
_LTS = re.compile(r"^lts(?:/(?:\*|[A-Za-z]+))?$", re.IGNORECASE)
#: What ``.nvmrc`` and friends write for "whatever is newest".
_LATEST = frozenset({"latest", "node", "current", "stable", "*", "x"})


def is_lts_alias(text: str) -> bool:
    """
    Tell whether a version file asks for the newest long-term release.

    Args:
        text: Content such as ``lts/*`` or ``lts/iron``.

    Returns:
        True for ``lts``, ``lts/*`` and ``lts/<codename>``.
    """
    return bool(_LTS.match(text.strip()))


def is_latest_alias(text: str) -> bool:
    """
    Tell whether a version file asks for the newest release, whichever it is.

    Args:
        text: Content such as ``node`` or ``latest``.

    Returns:
        True for the aliases that name no version.
    """
    return text.strip().lower() in _LATEST


#: The runtimes Noust can pin, and the spellings a project may use for them.
RUNTIME_LANGUAGES: tuple[str, ...] = ("node", "python", "php")
_LANGUAGE_ALIASES = {"node": "node", "nodejs": "node", "python": "python", "php": "php"}

#: What an explicit pin may say after the ``@``: a version of up to three
#: numbers, or one of the two aliases. A range is a way for a repository to say
#: what it tolerates; a pin is what the operator wants to run.
_PIN = re.compile(r"^(?:\d+(?:\.\d+){0,2}|lts|latest)$", re.IGNORECASE)

#: A bare version, as a pin or a version file writes it.
_BARE_VERSION = re.compile(r"^[vV]?\d+(?:\.\d+){0,2}$")

#: The dialect each language's own version files are written in.
LANGUAGE_FLAVOR: dict[str, Flavor] = {"node": "npm", "python": "pep440", "php": "composer"}


@dataclass(frozen=True)
class RuntimeRequest:
    """
    A runtime a project asks for.

    Attributes:
        language: ``node``, ``python`` or ``php``.
        spec: The version as written: ``22``, ``22.11.0``, ``lts``, or, for one
            read from a project file, a range such as ``>=18 <21``.
        explicit: True for ``build.runtime``, which is an order; False for a
            version file's, which is a hint the server's own runtime may
            already satisfy.
        source: Where it was read, for messages (``build.runtime``,
            ``.nvmrc``, ``engines.node``).
    """

    language: str
    spec: str
    explicit: bool = True
    source: str = "build.runtime"

    @property
    def text(self) -> str:
        """The request in the form ``noust.yaml`` writes it: ``node@22``."""
        return f"{self.language}@{self.spec}"

    @property
    def alias(self) -> Literal["lts", "latest"] | None:
        """``lts`` or ``latest`` when the request names no version, else None."""
        if is_latest_alias(self.spec):
            return "latest"
        if is_lts_alias(self.spec):
            return "lts"
        return None

    def version_range(self) -> VersionRange | None:
        """
        Say which versions satisfy the request.

        Returns:
            The range; None for an alias, which no installed version fails.

        Raises:
            ValidationError: The spec is not in the language's dialect.
        """
        if self.alias is not None:
            return None
        # A bare version is a pin in every dialect: 22 is 22.x, not "=22".
        if _BARE_VERSION.match(self.spec.strip()):
            return parse_range(self.spec.strip().lstrip("vV"), "npm")
        return parse_range(self.spec, LANGUAGE_FLAVOR[self.language])

    def allows(self, version: Version) -> bool:
        """
        Tell whether a version satisfies the request.

        Args:
            version: The candidate.

        Returns:
            True when it does; always for an alias.
        """
        wanted = self.version_range()
        return True if wanted is None else wanted.allows(version)

    @property
    def exact(self) -> Version | None:
        """The one version it names, when it names a full ``x.y.z``; else None."""
        if re.fullmatch(r"[vV]?\d+\.\d+\.\d+", self.spec.strip()):
            return parse_version(self.spec)
        return None


def parse_runtime(text: object, *, field: str = "build.runtime") -> RuntimeRequest:
    """
    Read an explicit runtime pin: ``node@22``, ``python@3.12``, ``php@8.3``.

    Args:
        text: What ``noust.yaml`` or the operator wrote.
        field: The field it came from, for the error.

    Returns:
        The request, explicit.

    Raises:
        ValidationError: Not a string of the form ``language@version``, an
            unknown language, or a version that is not one to pin.
    """
    if not isinstance(text, str) or "@" not in text:
        raise ValidationError(
            f"{field} must look like node@22",
            details=f"Name the language and the version to pin: {', '.join(RUNTIME_LANGUAGES)} "
            "followed by @ and a version (node@22, python@3.12, php@8.3).",
            field=field,
        )
    name, _, spec = text.strip().partition("@")
    language = _LANGUAGE_ALIASES.get(name.strip().lower())
    if language is None:
        raise ValidationError(
            f"{field} names {name!r}, a runtime Noust does not pin",
            details=f"The runtimes it pins are {', '.join(RUNTIME_LANGUAGES)}. A toolchain "
            "already on the server (Go, Ruby, Rust) is used through the install, build and "
            "start commands.",
            field=field,
        )
    spec = spec.strip()
    if not _PIN.match(spec):
        raise ValidationError(
            f"{field} pins {spec!r}, which is not a version",
            details="Use a major (22), a minor (22.11), a full version (22.11.0), lts or latest. "
            "A range is for a repository's own version files, not for a pin.",
            field=field,
        )
    return RuntimeRequest(language=language, spec=spec.lower() if spec[0].isalpha() else spec)


__all__ = [
    "LANGUAGE_FLAVOR",
    "RUNTIME_LANGUAGES",
    "Flavor",
    "RuntimeRequest",
    "Version",
    "VersionRange",
    "is_latest_alias",
    "is_lts_alias",
    "parse_range",
    "parse_runtime",
    "parse_version",
]
