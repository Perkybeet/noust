# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The newest WASM version the source an installation upgrades from can install.

A GitHub release exists the moment its tag is pushed, but the packages a
server installs are built afterwards: PyPI takes a few minutes, the OBS
repositories behind apt, dnf and zypper fifteen to thirty or more. Telling an
operator to upgrade on the strength of the GitHub release alone sent them to a
package manager that had nothing new to install. So the version an update is
announced for is read from the same place the upgrade command reads it:

- apt: the ``Packages`` index of the repository serving ``wasm``, found in
  ``/etc/apt/sources.list`` and ``sources.list.d`` (one-line and deb822),
  fetched fresh because the local lists are only as new as the last
  ``apt update``. ``apt-cache policy`` is the fallback. ``apt update`` itself
  is never run: it changes the machine.
- dnf, yum, zypper: ``repodata/repomd.xml`` and the primary index it points
  at, for the repository serving ``wasm-cli`` in ``/etc/yum.repos.d`` or
  ``/etc/zypp/repos.d``. The package manager's cache-only ``info`` is the
  fallback.
- pip, pipx: the PyPI JSON API.
- a source checkout: the GitHub release, which is what ``git pull`` reaches.

Every request is HTTPS with a short deadline, reads at most
:data:`MAX_INDEX_BYTES`, and goes only to a host that resolves outside the
machine's own networks - every redirect hop included, since
download.opensuse.org redirects to mirrors. The repository URLs come from
root-owned files, but a request this process makes on every command is not
one to let reach a metadata service because a file said so.
"""

from __future__ import annotations

import bz2
import configparser
import http.client
import ipaddress
import json
import logging
import lzma
import re
import urllib.error
import urllib.request
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any
from urllib.parse import urlparse
from xml.etree import ElementTree

if TYPE_CHECKING:
    from wasm.core.runner import CommandRunner

logger = logging.getLogger(__name__)

#: Package names on each channel. The Debian package is ``wasm``
#: (obs/debian.control); the RPM and the PyPI distribution are ``wasm-cli``
#: (rpm/wasm.spec, pyproject.toml).
DEB_PACKAGE = "wasm"
RPM_PACKAGE = "wasm-cli"
PYPI_PACKAGE = "wasm-cli"

GITHUB_LATEST = "https://api.github.com/repos/Perkybeet/wasm/releases/latest"
PYPI_JSON = f"https://pypi.org/pypi/{PYPI_PACKAGE}/json"

#: The OBS project that builds the distribution packages. A repository whose
#: URL names it serves WASM whatever else it is called.
OBS_PROJECT = "perkybeet"

#: Seconds each request may take.
TIMEOUT = 3

#: The most an index may be, compressed or not. The OBS repositories hold one
#: package; this bounds what a misconfigured or hostile one can make this
#: process read or inflate.
MAX_INDEX_BYTES = 32 * 1024 * 1024

APT_SOURCES_LIST = Path("/etc/apt/sources.list")
APT_SOURCES_DIR = Path("/etc/apt/sources.list.d")
RPM_REPO_DIRS = (Path("/etc/yum.repos.d"), Path("/etc/zypp/repos.d"))

#: The errors reading a remote index can end in: OSError covers URLError,
#: HTTPError and timeouts; ValueError a refused URL, a body over the limit, bad
#: JSON or a compression it cannot decode; the others a truncated response,
#: malformed XML and a corrupt stream.
FETCH_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    ValueError,
    EOFError,
    http.client.HTTPException,
    ElementTree.ParseError,
    lzma.LZMAError,
    zlib.error,
)


# -- versions ---------------------------------------------------------------


def version_key(version: str | None) -> tuple[int, ...] | None:
    """
    Turn a dotted release number into something comparable.

    Args:
        version: A version such as ``2.2.0``.

    Returns:
        The tuple of its numbers, or None when it is not a plain dotted
        release (a pre-release, a missing value).
    """
    if not version:
        return None
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def is_newer(candidate: str | None, than: str | None) -> bool:
    """
    Report whether one release number is strictly greater than another.

    Args:
        candidate: The version that might be newer.
        than: The version to compare against.

    Returns:
        True only when both parse and ``candidate`` is greater.
    """
    left, right = version_key(candidate), version_key(than)
    return left is not None and right is not None and left > right


def newest(versions: Iterable[str | None]) -> str | None:
    """
    Pick the greatest release number.

    Args:
        versions: Candidates; unparsable ones are ignored.

    Returns:
        The greatest, or None when none parses.
    """
    best: str | None = None
    for version in versions:
        key = version_key(version)
        if key is None:
            continue
        best_key = version_key(best)
        if best_key is None or key > best_key:
            best = version
    return best


def upstream_version(version: str) -> str:
    """
    Strip a package version down to the release it packages.

    Args:
        version: A Debian (``1:2.2.0-1``) or RPM (``2.2.0-lp156.1.1``) version.

    Returns:
        The upstream part, ``2.2.0``.
    """
    version = version.strip()
    if ":" in version:
        version = version.split(":", 1)[1]
    return version.split("-", 1)[0]


# -- fetching ---------------------------------------------------------------


def require_public_https(url: str) -> None:
    """
    Refuse a URL that is not HTTPS or whose host is inside a private network.

    Uses the notifier's resolver and network list, the one SSRF guard in the
    codebase, rather than a second copy of either.

    Args:
        url: The URL about to be requested.

    Raises:
        ValueError: When the scheme is not https, there is no host, it does
            not resolve, or any address it resolves to is private or internal.
    """
    from wasm.core import notifier

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Refusing {url!r}: only https is fetched")
    host = parsed.hostname
    if not host:
        raise ValueError(f"Refusing {url!r}: no host")
    try:
        addresses = notifier._resolve_host(host)
    except OSError as exc:
        raise ValueError(f"Could not resolve {host!r}: {exc}") from exc
    if not addresses:
        raise ValueError(f"{host!r} did not resolve to any address")
    for raw in addresses:
        if notifier._is_forbidden(ipaddress.ip_address(raw)):
            raise ValueError(f"Refusing {host!r}: resolves to {raw}, a private address")


def as_https(url: str) -> str:
    """
    Read a repository configured over plain HTTP through HTTPS instead.

    apt and dnf verify what they install by signature, so an ``http://``
    repository is a legitimate configuration; this module only reads a version
    number and has no signature to check it against, so it asks the same host
    over TLS. OBS and PyPI serve both.

    Args:
        url: A repository URL.

    Returns:
        The URL with ``http://`` replaced by ``https://``; any other URL as is.
    """
    return "https://" + url[len("http://") :] if url.lower().startswith("http://") else url


class _PublicHttpsRedirects(urllib.request.HTTPRedirectHandler):
    """Re-applies :func:`require_public_https` to every redirect hop."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        require_public_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, *, accept: str | None = None) -> bytes:
    """
    Read a URL, within the limits this module promises.

    The one place this module touches the network, so a test replaces it
    instead of opening a socket.

    Args:
        url: The HTTPS URL to read.
        accept: An ``Accept`` header to send, if any.

    Returns:
        The response body, at most :data:`MAX_INDEX_BYTES`.

    Raises:
        ValueError: When the URL is refused or the body is over the limit.
        OSError: When the request fails or answers with an error status.
        http.client.HTTPException: When the response is malformed.
    """
    require_public_https(url)
    headers = {"User-Agent": "wasm-update-check"}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_PublicHttpsRedirects())
    with opener.open(request, timeout=TIMEOUT) as response:
        body = response.read(MAX_INDEX_BYTES + 1)
    if len(body) > MAX_INDEX_BYTES:
        raise ValueError(f"{url} is larger than {MAX_INDEX_BYTES} bytes")
    return bytes(body)


def decompress(data: bytes) -> bytes:
    """
    Undo the compression an index was served with, by its magic number.

    Args:
        data: The body as served.

    Returns:
        The index, inflated to at most :data:`MAX_INDEX_BYTES`.

    Raises:
        ValueError: When the stream is zstd, which the standard library of
            every supported Python cannot read, or inflates past the limit.
        zlib.error, lzma.LZMAError, OSError, EOFError: When it is corrupt.
    """
    limit = MAX_INDEX_BYTES + 1
    if data[:2] == b"\x1f\x8b":
        out = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data, limit)
    elif data[:6] == b"\xfd7zXZ\x00":
        out = lzma.LZMADecompressor().decompress(data, max_length=limit)
    elif data[:3] == b"BZh":
        out = bz2.BZ2Decompressor().decompress(data, max_length=limit)
    elif data[:4] == b"\x28\xb5\x2f\xfd":
        raise ValueError("The index is zstd-compressed, which cannot be read here")
    else:
        out = data
    if len(out) > MAX_INDEX_BYTES:
        raise ValueError(f"The index inflates past {MAX_INDEX_BYTES} bytes")
    return out


def _parse_xml(data: bytes) -> ElementTree.Element:
    """
    Parse repository metadata, refusing what only an attack would contain.

    Args:
        data: An XML document.

    Returns:
        Its root element.

    Raises:
        ValueError: When the document declares a DTD or entities, which no
            repomd or primary index does and which entity expansion attacks need.
        ElementTree.ParseError: When it is not XML.
    """
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("Repository metadata with a DTD is refused")
    return ElementTree.fromstring(data)  # noqa: S314 - DTDs refused above, size bounded


def _local(tag: str) -> str:
    """Drop the ``{namespace}`` from an element tag."""
    return tag.rsplit("}", 1)[-1]


# -- GitHub and PyPI --------------------------------------------------------


def github_latest() -> str | None:
    """
    Read the version of the latest GitHub release: what has been published.

    Returns:
        The release's tag without its ``v``, or None when it cannot be read.
    """
    try:
        data = json.loads(fetch(GITHUB_LATEST, accept="application/vnd.github.v3+json"))
    except FETCH_ERRORS as exc:
        logger.debug("Could not fetch the latest release: %s", exc)
        return None
    tag_name = data.get("tag_name") if isinstance(data, dict) else None
    if not isinstance(tag_name, str):
        logger.debug("The latest release carries no tag name: %r", data)
        return None
    return tag_name.lstrip("v")


def pypi_latest() -> str | None:
    """
    Read the version PyPI serves to ``pip install --upgrade``.

    Returns:
        ``info.version`` of the PyPI project, or None when it cannot be read.
    """
    try:
        data = json.loads(fetch(PYPI_JSON))
    except FETCH_ERRORS as exc:
        logger.debug("Could not read %s: %s", PYPI_JSON, exc)
        return None
    info = data.get("info") if isinstance(data, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return version if isinstance(version, str) else None


# -- apt --------------------------------------------------------------------


@dataclass(frozen=True)
class AptSource:
    """
    One ``deb`` entry of the apt sources.

    Attributes:
        uri: The repository's base URI.
        suite: The suite; one ending in ``/`` is a flat repository.
        components: The components, empty for a flat repository.
    """

    uri: str
    suite: str
    components: tuple[str, ...] = ()

    @property
    def is_flat(self) -> bool:
        """True for a flat repository (``deb URI /``), which is what OBS publishes."""
        return self.suite.endswith("/")

    def index_urls(self, arch: str) -> list[list[str]]:
        """
        The URLs of the ``Packages`` indexes this entry names.

        Args:
            arch: The Debian architecture (``amd64``) of this machine.

        Returns:
            One group per index, each listing the compressions to try in turn.
        """
        base = as_https(self.uri).rstrip("/")
        if self.is_flat:
            path = self.suite.strip("/").removeprefix("./").strip("/")
            directories = [f"{base}/{path}" if path and path != "." else base]
        else:
            directories = [
                f"{base}/dists/{self.suite}/{component}/binary-{architecture}"
                for component in self.components
                for architecture in dict.fromkeys((arch, "all"))
            ]
        return [
            [f"{directory}/Packages.gz", f"{directory}/Packages.xz", f"{directory}/Packages"]
            for directory in directories
        ]


_ONE_LINE = re.compile(r"^deb\s+(?:\[[^\]]*\]\s+)?(\S+)\s+(\S+)(?:\s+(.*))?$")


def parse_one_line_sources(text: str) -> list[AptSource]:
    """
    Read the ``deb`` lines of a one-line-style sources file.

    Args:
        text: The content of ``sources.list`` or a ``*.list`` file.

    Returns:
        Every binary entry, in order; ``deb-src`` and comments are skipped.
    """
    sources = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        match = _ONE_LINE.match(line)
        if not match:
            continue
        uri, suite, components = match.groups()
        sources.append(AptSource(uri, suite, tuple((components or "").split())))
    return sources


def _deb822_stanzas(text: str) -> list[dict[str, str]]:
    """
    Split an RFC 822-style document into its stanzas.

    Args:
        text: A deb822 ``*.sources`` file or a ``Packages`` index.

    Returns:
        One mapping per stanza, field names lower-cased, continuation lines
        joined with newlines.
    """
    stanzas: list[dict[str, str]] = []
    current: dict[str, str] = {}
    key: str | None = None
    for raw in text.splitlines():
        if raw.startswith("#"):
            continue
        if not raw.strip():
            if current:
                stanzas.append(current)
            current, key = {}, None
            continue
        if raw[0] in " \t":
            if key is not None:
                current[key] += "\n" + raw.strip()
            continue
        name, sep, value = raw.partition(":")
        if not sep:
            continue
        key = name.strip().lower()
        current[key] = value.strip()
    if current:
        stanzas.append(current)
    return stanzas


def parse_deb822_sources(text: str) -> list[AptSource]:
    """
    Read a deb822-style ``*.sources`` file.

    Args:
        text: The file's content.

    Returns:
        Every enabled binary entry, one per URI and suite.
    """
    sources = []
    for stanza in _deb822_stanzas(text):
        if "deb" not in stanza.get("types", "").split():
            continue
        if stanza.get("enabled", "yes").strip().lower() == "no":
            continue
        components = tuple(stanza.get("components", "").split())
        for uri in stanza.get("uris", "").split():
            for suite in stanza.get("suites", "").split():
                sources.append(AptSource(uri, suite, components))
    return sources


def apt_sources(
    sources_list: Path = APT_SOURCES_LIST, sources_dir: Path = APT_SOURCES_DIR
) -> list[AptSource]:
    """
    Read every configured apt source.

    Args:
        sources_list: The main one-line sources file.
        sources_dir: The directory of ``*.list`` and ``*.sources`` files.

    Returns:
        Every binary entry; a file that cannot be read is skipped and logged.
    """
    files: list[Path] = [sources_list]
    if sources_dir.is_dir():
        files += sorted(sources_dir.glob("*.list")) + sorted(sources_dir.glob("*.sources"))
    sources: list[AptSource] = []
    for path in files:
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            logger.debug("Could not read %s: %s", path, exc)
            continue
        parse = parse_deb822_sources if path.suffix == ".sources" else parse_one_line_sources
        sources += parse(text)
    return sources


def packages_version(text: str, package: str = DEB_PACKAGE) -> str | None:
    """
    Read a package's newest version from a ``Packages`` index.

    Args:
        text: The index.
        package: The binary package to look for.

    Returns:
        The upstream part of its greatest version, or None when absent.
    """
    return newest(
        upstream_version(stanza.get("version", ""))
        for stanza in _deb822_stanzas(text)
        if stanza.get("package") == package
    )


def parse_apt_policy(output: str) -> tuple[str | None, str]:
    """
    Read ``apt-cache policy`` for its candidate and the origins it lists.

    Args:
        output: The command's standard output.

    Returns:
        The candidate's upstream version (None for ``(none)`` or when
        missing), and the version table, whose URLs name the repositories
        that serve the package.
    """
    candidate: str | None = None
    lines = output.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("Candidate:"):
            value = stripped.split(":", 1)[1].strip()
            candidate = None if value in ("", "(none)") else upstream_version(value)
        elif stripped.startswith("Version table:"):
            return candidate, "\n".join(lines[index + 1 :])
    return candidate, ""


def apt_latest(runner: CommandRunner, *, sources: Sequence[AptSource] | None = None) -> str | None:
    """
    Read the newest ``wasm`` the configured apt repositories offer.

    Args:
        runner: The :class:`~wasm.core.runner.CommandRunner` for the local probes.
        sources: The apt sources; read from ``/etc/apt`` when omitted.

    Returns:
        The version, from the repositories themselves when they can be read,
        from ``apt-cache policy`` otherwise; None when neither answers.
    """
    policy = runner.run(["apt-cache", "policy", DEB_PACKAGE], timeout=5)
    candidate, table = parse_apt_policy(policy.stdout) if policy.success else (None, "")
    if sources is None:
        sources = apt_sources()
    serving = [
        source
        for source in sources
        if OBS_PROJECT in source.uri.lower() or (table and source.uri.rstrip("/") in table)
    ]
    arch_result = runner.run(["dpkg", "--print-architecture"], timeout=5)
    arch = arch_result.stdout.strip() if arch_result.success and arch_result.stdout.strip() else ""
    found: list[str | None] = []
    for source in dict.fromkeys(serving):
        for group in source.index_urls(arch or "amd64"):
            for url in group:
                try:
                    found.append(
                        packages_version(decompress(fetch(url)).decode("utf-8", "replace"))
                    )
                except FETCH_ERRORS as exc:
                    logger.debug("Could not read %s: %s", url, exc)
                    continue
                break
    online = newest(found)
    if online is not None:
        return online
    return candidate


# -- dnf, yum, zypper -------------------------------------------------------


def parse_rpm_repo_file(text: str) -> list[str]:
    """
    Read the base URLs of the enabled repositories that serve WASM.

    Args:
        text: A ``*.repo`` file, the same INI format for dnf, yum and zypper.

    Returns:
        The base URL of every enabled section whose id, name or URL names the
        OBS project. URLs with dnf variables (``$releasever``) are skipped:
        expanding them is the package manager's business.
    """
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        logger.debug("Could not parse a repository file: %s", exc)
        return []
    urls = []
    for section in parser.sections():
        if parser.get(section, "enabled", fallback="1").strip() in ("0", "false", "no"):
            continue
        name = parser.get(section, "name", fallback="")
        for url in parser.get(section, "baseurl", fallback="").split():
            if "$" in url:
                continue
            if OBS_PROJECT in f"{section} {name} {url}".lower():
                urls.append(url)
    return urls


def rpm_repositories(directories: Sequence[Path] = RPM_REPO_DIRS) -> list[str]:
    """
    Read the base URLs of every configured repository that serves WASM.

    Args:
        directories: Where the ``*.repo`` files live.

    Returns:
        The base URLs, deduplicated, in order.
    """
    urls: list[str] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.repo")):
            try:
                urls += parse_rpm_repo_file(path.read_text(errors="replace"))
            except OSError as exc:
                logger.debug("Could not read %s: %s", path, exc)
    return list(dict.fromkeys(urls))


def repomd_primary_href(data: bytes) -> str | None:
    """
    Find the primary index in a ``repomd.xml``.

    Args:
        data: The ``repomd.xml`` document.

    Returns:
        The primary index's location, relative to the repository base.
    """
    for element in _parse_xml(data).iter():
        if _local(element.tag) == "data" and element.get("type") == "primary":
            for child in element:
                if _local(child.tag) == "location":
                    return child.get("href")
    return None


def primary_version(data: bytes, package: str = RPM_PACKAGE) -> str | None:
    """
    Read a package's newest version from a ``primary.xml``.

    Args:
        data: The decompressed primary index.
        package: The package name.

    Returns:
        The greatest ``ver`` of the package, or None when absent.
    """
    versions: list[str | None] = []
    for element in _parse_xml(data).iter():
        if _local(element.tag) != "package":
            continue
        name = next((c.text for c in element if _local(c.tag) == "name"), None)
        if name != package:
            continue
        for child in element:
            if _local(child.tag) == "version":
                versions.append(child.get("ver"))
    return newest(versions)


def rpm_index_latest(base_url: str) -> str | None:
    """
    Read the newest ``wasm-cli`` in one rpm-md repository.

    Args:
        base_url: The repository base, where ``repodata/`` lives.

    Returns:
        The version, or None when the repository cannot be read.
    """
    base = as_https(base_url).rstrip("/")
    try:
        href = repomd_primary_href(fetch(f"{base}/repodata/repomd.xml"))
        if not href:
            logger.debug("%s names no primary index", base)
            return None
        return primary_version(decompress(fetch(f"{base}/{href.lstrip('/')}")))
    except FETCH_ERRORS as exc:
        logger.debug("Could not read the rpm repository %s: %s", base, exc)
        return None


def _info_version(output: str) -> str | None:
    """Read the greatest ``Version :`` of ``dnf info`` or ``zypper info`` output."""
    return newest(
        upstream_version(line.split(":", 1)[1])
        for line in output.splitlines()
        if line.split(":", 1)[0].strip() == "Version" and ":" in line
    )


def rpm_latest(
    runner: CommandRunner, manager: str, *, repositories: Sequence[str] | None = None
) -> str | None:
    """
    Read the newest ``wasm-cli`` the configured rpm repositories offer.

    Args:
        runner: The :class:`~wasm.core.runner.CommandRunner` for the fallback.
        manager: ``dnf``, ``yum`` or ``zypper``.
        repositories: Base URLs; read from the ``*.repo`` files when omitted.

    Returns:
        The version from the repositories themselves when they can be read,
        from the package manager's cached metadata otherwise; None when
        neither answers.
    """
    if repositories is None:
        repositories = rpm_repositories()
    online = newest(rpm_index_latest(url) for url in repositories)
    if online is not None:
        return online
    # Cache only: a refresh is slow and changes the machine's state, and this
    # runs on the way to whatever command the operator asked for.
    argv = {
        "dnf": ["dnf", "--cacheonly", "info", "--available", RPM_PACKAGE],
        "yum": ["yum", "--cacheonly", "info", "available", RPM_PACKAGE],
        "zypper": ["zypper", "--no-refresh", "--non-interactive", "info", RPM_PACKAGE],
    }.get(manager)
    if argv is None:
        return None
    result = runner.run(argv, timeout=5)
    return _info_version(result.stdout) if result.success else None


# -- dispatch ---------------------------------------------------------------


def installable_version(method: str, runner: CommandRunner) -> str | None:
    """
    Read the newest version the source this installation upgrades from offers.

    Args:
        method: The installation method (see
            :meth:`~wasm.core.update_checker.UpdateChecker._detect_installation_method`).
        runner: The :class:`~wasm.core.runner.CommandRunner` for local probes.

    Returns:
        The version, or None when it cannot be determined.
    """
    if method == "apt":
        return apt_latest(runner)
    if method in ("dnf", "yum", "zypper"):
        return rpm_latest(runner, method)
    if method == "source":
        return github_latest()
    # pip, pipx, and an installation nothing else claims: what the suggested
    # `pip install --upgrade wasm-cli` would install.
    return pypi_latest()
