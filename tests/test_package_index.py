# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`wasm.core.package_index`: the version the source an
installation upgrades from can actually install.

No test opens a socket: :func:`package_index.fetch` is replaced by a table of
URLs, and the SSRF guard's resolver by a dictionary.
"""

from __future__ import annotations

import gzip
import lzma
import time
import urllib.error
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

import pytest

from wasm.core import package_index
from wasm.core.package_index import AptSource
from wasm.core.runner import FakeRunner

OBS = "https://download.opensuse.org/repositories/home:/Perkybeet"

PACKAGES = """\
Package: wasm-extras
Version: 9.9.9-1
Architecture: all

Package: wasm
Version: 2.2.0-1
Architecture: all
Depends: python3,
 python3-click

Package: wasm
Version: 2.3.0-1
Architecture: all
"""

REPOMD = b"""<?xml version="1.0" encoding="UTF-8"?>
<repomd xmlns="http://linux.duke.edu/metadata/repo" xmlns:rpm="http://linux.duke.edu/metadata/rpm">
  <revision>1</revision>
  <data type="filelists"><location href="repodata/abc-filelists.xml.gz"/></data>
  <data type="primary">
    <checksum type="sha256">abc</checksum>
    <location href="repodata/abc-primary.xml.gz"/>
  </data>
</repomd>
"""

PRIMARY = b"""<?xml version="1.0" encoding="UTF-8"?>
<metadata xmlns="http://linux.duke.edu/metadata/common" packages="2">
  <package type="rpm">
    <name>wasm-cli</name><arch>noarch</arch>
    <version epoch="0" ver="2.2.0" rel="1.1"/>
  </package>
  <package type="rpm">
    <name>wasm-cli</name><arch>noarch</arch>
    <version epoch="0" ver="2.3.0" rel="1.1"/>
  </package>
  <package type="rpm">
    <name>other</name><arch>noarch</arch>
    <version epoch="0" ver="9.0.0" rel="1"/>
  </package>
</metadata>
"""


def serve(monkeypatch: pytest.MonkeyPatch, table: dict[str, bytes]) -> list[str]:
    """
    Answer :func:`package_index.fetch` from a table; anything else is a 404.

    Args:
        monkeypatch: Patching helper.
        table: URL to body.

    Returns:
        Every URL asked for, in order.
    """
    asked: list[str] = []

    def fetch(url: str, **kwargs: object) -> bytes:
        asked.append(url)
        if url not in table:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        return table[url]

    monkeypatch.setattr(package_index, "fetch", fetch)
    return asked


# -- versions


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.2.0-1", "2.2.0"),
        ("1:2.2.0-1", "2.2.0"),
        ("2.2.0-lp156.1.1", "2.2.0"),
        ("2.2.0", "2.2.0"),
    ],
)
def test_upstream_version_drops_epoch_and_revision(raw: str, expected: str) -> None:
    assert package_index.upstream_version(raw) == expected


def test_newest_compares_numerically() -> None:
    assert package_index.newest(["2.9.0", "2.10.0", None, "garbage"]) == "2.10.0"


# -- apt sources


def test_one_line_sources_are_read_with_options_and_comments() -> None:
    text = (
        "# the distribution\n"
        "deb http://archive.ubuntu.com/ubuntu noble main universe\n"
        "deb-src http://archive.ubuntu.com/ubuntu noble main\n"
        f"deb [signed-by=/usr/share/keyrings/wasm.gpg arch=amd64] {OBS}/xUbuntu_24.04/ /  # wasm\n"
    )

    assert package_index.parse_one_line_sources(text) == [
        AptSource("http://archive.ubuntu.com/ubuntu", "noble", ("main", "universe")),
        AptSource(f"{OBS}/xUbuntu_24.04/", "/"),
    ]


def test_deb822_sources_are_read_and_disabled_stanzas_skipped() -> None:
    text = (
        "Types: deb deb-src\n"
        f"URIs: {OBS}/Debian_12/\n"
        "Suites: ./\n"
        "Signed-By: /usr/share/keyrings/wasm.gpg\n"
        "\n"
        "Types: deb\n"
        "URIs: http://deb.debian.org/debian\n"
        "Suites: bookworm bookworm-updates\n"
        "Components: main\n"
        "Enabled: no\n"
        "\n"
        "Types: deb-src\n"
        "URIs: http://deb.debian.org/debian\n"
        "Suites: bookworm\n"
    )

    assert package_index.parse_deb822_sources(text) == [AptSource(f"{OBS}/Debian_12/", "./")]


def test_apt_sources_reads_every_file(tmp_path: Path) -> None:
    main = tmp_path / "sources.list"
    main.write_text("deb http://archive.ubuntu.com/ubuntu noble main\n")
    directory = tmp_path / "sources.list.d"
    directory.mkdir()
    (directory / "wasm.list").write_text(f"deb {OBS}/xUbuntu_24.04/ /\n")
    (directory / "ubuntu.sources").write_text(
        "Types: deb\nURIs: http://security.ubuntu.com/ubuntu\nSuites: noble-security\n"
        "Components: main\n"
    )
    (directory / "ignored.save").write_text(f"deb {OBS}/elsewhere/ /\n")

    uris = [source.uri for source in package_index.apt_sources(main, directory)]

    assert uris == [
        "http://archive.ubuntu.com/ubuntu",
        f"{OBS}/xUbuntu_24.04/",
        "http://security.ubuntu.com/ubuntu",
    ]


def test_flat_and_pool_repositories_name_their_indexes() -> None:
    flat = AptSource(f"{OBS}/xUbuntu_24.04/", "./")
    pool = AptSource("http://mirror.example.com/wasm", "stable", ("main",))

    assert flat.index_urls("amd64") == [
        [
            f"{OBS}/xUbuntu_24.04/Packages.gz",
            f"{OBS}/xUbuntu_24.04/Packages.xz",
            f"{OBS}/xUbuntu_24.04/Packages",
        ]
    ]
    # Plain http is read over https: only a version number is taken from it.
    assert pool.index_urls("arm64")[0][0] == (
        "https://mirror.example.com/wasm/dists/stable/main/binary-arm64/Packages.gz"
    )
    assert pool.index_urls("arm64")[1][0].endswith("/binary-all/Packages.gz")


def test_packages_index_gives_the_newest_wasm() -> None:
    assert package_index.packages_version(PACKAGES) == "2.3.0"
    assert package_index.packages_version("Package: other\nVersion: 1.0\n") is None


def test_apt_policy_gives_candidate_and_origins() -> None:
    output = (
        "wasm:\n"
        "  Installed: 2.2.0-1\n"
        "  Candidate: 2.2.0-1\n"
        "  Version table:\n"
        f" *** 2.2.0-1 500\n        500 {OBS}/xUbuntu_24.04  Packages\n"
    )

    candidate, table = package_index.parse_apt_policy(output)

    assert candidate == "2.2.0"
    assert f"{OBS}/xUbuntu_24.04" in table
    assert package_index.parse_apt_policy("wasm:\n  Candidate: (none)\n")[0] is None


def _apt_runner(candidate: str = "2.2.0-1") -> FakeRunner:
    runner = FakeRunner()
    runner.script(
        ["apt-cache", "policy", "wasm"],
        stdout=f"wasm:\n  Installed: 2.2.0-1\n  Candidate: {candidate}\n  Version table:\n",
    )
    runner.script(["dpkg", "--print-architecture"], stdout="amd64\n")
    return runner


def test_apt_reads_the_repository_index_not_the_stale_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local lists are as old as the last `apt update`; the repository is not."""
    asked = serve(
        monkeypatch, {f"{OBS}/xUbuntu_24.04/Packages.gz": gzip.compress(PACKAGES.encode())}
    )
    sources = [
        AptSource("http://archive.ubuntu.com/ubuntu", "noble", ("main",)),
        AptSource(f"{OBS}/xUbuntu_24.04/", "/"),
    ]

    runner = _apt_runner()
    assert package_index.apt_latest(runner, sources=sources) == "2.3.0"
    # Only the repository that serves WASM is read, and apt update never runs.
    assert all("archive.ubuntu.com" not in url for url in asked)
    assert not runner.ran("apt-get") and not runner.ran("apt", "update")


def test_apt_falls_back_through_compressions(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {f"{OBS}/Debian_12/Packages.xz": lzma.compress(PACKAGES.encode())})

    assert (
        package_index.apt_latest(_apt_runner(), sources=[AptSource(f"{OBS}/Debian_12/", "./")])
        == "2.3.0"
    )


def test_apt_finds_a_mirror_through_apt_cache_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repository not named after the OBS project still serves wasm if apt says so."""
    mirror = "https://mirror.example.com/wasm/"
    serve(monkeypatch, {f"{mirror}Packages": PACKAGES.encode()})
    runner = FakeRunner()
    runner.script(
        ["apt-cache", "policy", "wasm"],
        stdout=(
            "wasm:\n  Installed: 2.2.0-1\n  Candidate: 2.2.0-1\n  Version table:\n"
            " *** 2.2.0-1 500\n        500 https://mirror.example.com/wasm  Packages\n"
        ),
    )

    assert package_index.apt_latest(runner, sources=[AptSource(mirror, "/")]) == "2.3.0"


def test_apt_offline_falls_back_to_the_apt_cache_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, {})

    assert (
        package_index.apt_latest(
            _apt_runner("2.2.5-1"), sources=[AptSource(f"{OBS}/xUbuntu_24.04/", "/")]
        )
        == "2.2.5"
    )


# -- rpm repositories


FEDORA_REPO = f"""\
[home_Perkybeet]
name=home:Perkybeet (Fedora_42)
type=rpm-md
baseurl={OBS}/Fedora_42/
gpgcheck=1
enabled=1

[fedora]
name=Fedora $releasever
metalink=https://mirrors.fedoraproject.org/metalink?repo=fedora-$releasever
enabled=1

[home_Perkybeet_disabled]
name=home:Perkybeet (old)
baseurl={OBS}/Fedora_40/
enabled=0

[templated]
name=Perkybeet templated
baseurl={OBS}/Fedora_$releasever/
"""


def test_rpm_repo_files_name_the_enabled_obs_repositories() -> None:
    assert package_index.parse_rpm_repo_file(FEDORA_REPO) == [f"{OBS}/Fedora_42/"]
    assert package_index.parse_rpm_repo_file("not an ini file [") == []


def test_rpm_repositories_reads_yum_and_zypp_directories(tmp_path: Path) -> None:
    yum = tmp_path / "yum.repos.d"
    zypp = tmp_path / "zypp" / "repos.d"
    yum.mkdir()
    zypp.mkdir(parents=True)
    (yum / "home:Perkybeet.repo").write_text(FEDORA_REPO)
    (zypp / "wasm.repo").write_text(
        f"[wasm]\nenabled=1\nautorefresh=1\nbaseurl={OBS}/openSUSE_Tumbleweed/\ntype=rpm-md\n"
    )

    assert package_index.rpm_repositories([yum, zypp]) == [
        f"{OBS}/Fedora_42/",
        f"{OBS}/openSUSE_Tumbleweed/",
    ]


def test_repomd_and_primary_give_the_newest_wasm_cli() -> None:
    assert package_index.repomd_primary_href(REPOMD) == "repodata/abc-primary.xml.gz"
    assert package_index.primary_version(PRIMARY) == "2.3.0"


def test_rpm_reads_the_repository_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(
        monkeypatch,
        {
            f"{OBS}/Fedora_42/repodata/repomd.xml": REPOMD,
            f"{OBS}/Fedora_42/repodata/abc-primary.xml.gz": gzip.compress(PRIMARY),
        },
    )
    runner = FakeRunner()

    assert package_index.rpm_latest(runner, "dnf", repositories=[f"{OBS}/Fedora_42/"]) == "2.3.0"
    assert runner.calls == []


@pytest.mark.parametrize(
    ("manager", "argv", "output"),
    [
        (
            "dnf",
            ("dnf", "--cacheonly", "info", "--available", "wasm-cli"),
            "Name         : wasm-cli\nVersion      : 2.2.5\nRelease      : 1.1\n",
        ),
        (
            "zypper",
            ("zypper", "--no-refresh", "--non-interactive", "info", "wasm-cli"),
            "Name           : wasm-cli\nVersion        : 2.2.5-lp156.1.1\n",
        ),
    ],
)
def test_rpm_falls_back_to_the_cached_metadata(
    monkeypatch: pytest.MonkeyPatch, manager: str, argv: tuple[str, ...], output: str
) -> None:
    """zstd metadata, or no route to the repository: the manager's cache, never a refresh."""
    serve(
        monkeypatch,
        {
            f"{OBS}/Fedora_42/repodata/repomd.xml": REPOMD,
            f"{OBS}/Fedora_42/repodata/abc-primary.xml.gz": b"\x28\xb5\x2f\xfd zstd",
        },
    )
    runner = FakeRunner().script(list(argv), stdout=output)

    assert package_index.rpm_latest(runner, manager, repositories=[f"{OBS}/Fedora_42/"]) == "2.2.5"
    assert runner.calls == [argv]


def test_metadata_with_a_dtd_is_refused() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><repomd>&a;</repomd>'

    with pytest.raises(ValueError, match="DTD"):
        package_index.repomd_primary_href(bomb)


def test_a_utf16_encoded_dtd_is_also_refused() -> None:
    """
    A byte search for ``<!DOCTYPE`` never matches a UTF-16 document: the
    marker's ASCII bytes are split by the encoding's null bytes. The DTD
    must still be refused once expat itself decodes the document.
    """
    bomb = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE x [<!ENTITY a "aaaa">]><repomd>&a;</repomd>'
    ).encode("utf-16")
    assert b"<!DOCTYPE" not in bomb

    with pytest.raises(ValueError, match="DTD"):
        package_index.repomd_primary_href(bomb)


def test_malformed_xml_still_raises_a_parse_error() -> None:
    with pytest.raises(ElementTree.ParseError):
        package_index.repomd_primary_href(b"<repomd><data></repomd>")


def test_an_index_that_inflates_past_the_limit_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(package_index, "MAX_INDEX_BYTES", 1024)

    with pytest.raises(ValueError, match="inflates"):
        package_index.decompress(gzip.compress(b"x" * 4096))


# -- the network guard


@pytest.fixture
def resolve(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, str]], None]:
    """Answer DNS from a dictionary instead of a socket."""

    def install(table: dict[str, str]) -> None:
        def lookup(host: str) -> tuple[str, ...]:
            if host[0].isdigit():
                return (host,)
            return (table[host],) if host in table else ()

        monkeypatch.setattr("wasm.core.notifier._resolve_host", lookup)

    return install


def test_only_https_to_public_hosts_is_fetched(resolve) -> None:
    resolve({"download.opensuse.org": "195.135.223.226", "metadata.internal": "169.254.169.254"})

    package_index.require_public_https(f"{OBS}/xUbuntu_24.04/Packages.gz")
    with pytest.raises(ValueError, match="only https"):
        package_index.require_public_https("http://download.opensuse.org/Packages")
    with pytest.raises(ValueError, match="private"):
        package_index.require_public_https("https://metadata.internal/latest")
    with pytest.raises(ValueError, match="private"):
        package_index.require_public_https("https://127.0.0.1/Packages")
    with pytest.raises(ValueError, match="did not resolve"):
        package_index.require_public_https("https://nowhere.example/Packages")


def test_a_redirect_to_a_private_host_is_refused(resolve) -> None:
    """download.opensuse.org redirects to mirrors; every hop is checked again."""
    import urllib.request

    resolve({"mirror.internal": "10.0.0.5"})
    handler = package_index._PublicHttpsRedirects()
    request = urllib.request.Request(f"{OBS}/Packages.gz")

    with pytest.raises(ValueError, match="private"):
        handler.redirect_request(
            request, None, 302, "Found", {}, "https://mirror.internal/Packages.gz"
        )  # type: ignore[arg-type]


class _TricklingResponse:
    """
    Answers one byte per ``.read()`` call, forever.

    Stands in for a response whose server sends data slower than any single
    socket operation ever times out at - the case a per-operation
    ``timeout=`` does not bound on its own.
    """

    def read(self, _size: int) -> bytes:
        return b"x"


class _FiniteResponse:
    """Answers from a fixed list of chunks, then signals end of stream."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def read(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def test_read_until_refuses_a_response_still_trickling_past_its_deadline() -> None:
    """The wall clock stops it, even though no single .read() call ever times out."""
    deadline = time.monotonic() + 0.05

    with pytest.raises(TimeoutError, match="not complete"):
        package_index._read_until(_TricklingResponse(), deadline)


def test_read_until_returns_a_finished_body_before_the_deadline() -> None:
    deadline = time.monotonic() + 5

    body = package_index._read_until(_FiniteResponse([b"ab", b"cd", b"ef"]), deadline)

    assert body == b"abcdef"


def test_read_until_stops_once_past_the_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    It stops as soon as the total crosses the limit, leaving the exact bound
    to the caller (:func:`package_index.fetch` raises past it) rather than
    reading a body already over budget forever.
    """
    monkeypatch.setattr(package_index, "MAX_INDEX_BYTES", 4)
    unread = _FiniteResponse([b"ab", b"cd", b"ef", b"gh", b"never read"])
    deadline = time.monotonic() + 5

    body = package_index._read_until(unread, deadline)

    assert body == b"abcdef"
    assert unread._chunks == [b"gh", b"never read"]


def test_an_unreadable_source_is_none_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {package_index.PYPI_JSON: b"not json"})

    assert package_index.pypi_latest() is None
    assert package_index.github_latest() is None
