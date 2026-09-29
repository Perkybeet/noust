"""
Tests for archive checksum verification.

An archive source URL may carry its expected checksum in the URL fragment
(never sent to the server), either pinned directly (``#sha256=<hex>``) or as
a URL to fetch it from (``#checksum=<https url>``). See
:func:`noust.managers.source_manager.split_archive_checksum` for the grammar
and :meth:`noust.managers.source_manager.SourceManager.download_archive` for
where it is enforced. No test here opens a real socket: every download goes
through a fake replacing ``_open_url``.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from noust.core.exceptions import SourceError
from noust.core.runner import FakeRunner
from noust.managers import source_manager as sm
from noust.managers.source_manager import ArchiveChecksum, SourceManager
from noust.validators.source import is_archive_url, validate_source

ARCHIVE_URL = "https://archives.example.test/app.tar.gz"
CHECKSUM_URL = "https://archives.example.test/app.tar.gz.sha256"

SHA256_HEX = "a" * 64
SHA1_HEX = "b" * 40
SHA512_HEX = "c" * 128


# Helpers --------------------------------------------------------------------


def _tar_gz_bytes(files: dict[str, bytes]) -> bytes:
    """
    Build a small gzipped tar in memory.

    Args:
        files: Member name to payload.

    Returns:
        The archive's raw bytes.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _responder(responses: dict[str, bytes], calls: list[str] | None = None):
    """
    Build a fake ``_open_url`` that answers by exact URL match.

    Args:
        responses: URL to the bytes it should answer with.
        calls: When given, every requested URL is appended to it.

    Returns:
        A callable with the same shape as ``source_manager._open_url``.
    """

    def _fake(url: str, timeout: int = 0) -> io.BytesIO:
        if calls is not None:
            calls.append(url)
        if url not in responses:
            raise AssertionError(f"unexpected URL requested: {url}")
        return io.BytesIO(responses[url])

    return _fake


@pytest.fixture
def manager(runner: FakeRunner) -> SourceManager:
    """
    Provide a source manager wired to the fake runner.

    Args:
        runner: The process-wide fake runner.

    Returns:
        The manager under test.
    """
    return SourceManager()


# split_archive_checksum: parsing --------------------------------------------


class TestSplitArchiveChecksum:
    """Tests for :func:`noust.managers.source_manager.split_archive_checksum`."""

    def test_no_fragment_is_unchanged(self) -> None:
        """A plain archive URL keeps working exactly as before this existed."""
        bare, checksum = sm.split_archive_checksum(ARCHIVE_URL)
        assert bare == ARCHIVE_URL
        assert checksum is None

    def test_pinned_sha256(self) -> None:
        """#sha256=<hex> pins the digest right there."""
        url = f"{ARCHIVE_URL}#sha256={SHA256_HEX}"
        bare, checksum = sm.split_archive_checksum(url)
        assert bare == ARCHIVE_URL
        assert checksum == ArchiveChecksum(algorithm="sha256", digest=SHA256_HEX, url=None)

    def test_pinned_sha256_uppercase_is_normalised(self) -> None:
        """A digest given in uppercase hex is folded to lowercase."""
        url = f"{ARCHIVE_URL}#sha256={SHA256_HEX.upper()}"
        _, checksum = sm.split_archive_checksum(url)
        assert checksum is not None
        assert checksum.digest == SHA256_HEX

    @pytest.mark.parametrize(
        ("suffix", "algorithm"),
        [
            (".sha1", "sha1"),
            (".sha256", "sha256"),
            (".sha512", "sha512"),
        ],
    )
    def test_checksum_url_infers_algorithm_from_suffix(self, suffix: str, algorithm: str) -> None:
        """The algorithm is read from the checksum file's own extension."""
        checksum_url = f"https://archives.example.test/app.tar.gz{suffix}"
        url = f"{ARCHIVE_URL}#checksum={checksum_url}"
        bare, checksum = sm.split_archive_checksum(url)
        assert bare == ARCHIVE_URL
        assert checksum == ArchiveChecksum(algorithm=algorithm, digest=None, url=checksum_url)

    def test_checksum_url_must_be_https(self) -> None:
        """A checksum fetched over plain http proves nothing."""
        url = f"{ARCHIVE_URL}#checksum=http://archives.example.test/app.tar.gz.sha256"
        with pytest.raises(SourceError):
            sm.split_archive_checksum(url)

    def test_checksum_url_without_known_suffix_is_refused(self) -> None:
        """An algorithm that cannot be inferred is refused, not guessed."""
        url = f"{ARCHIVE_URL}#checksum=https://archives.example.test/CHECKSUMS.txt"
        with pytest.raises(SourceError):
            sm.split_archive_checksum(url)

    def test_unknown_fragment_key_is_refused(self) -> None:
        """A fragment key other than sha256/checksum is refused, not ignored."""
        url = f"{ARCHIVE_URL}#md5={'a' * 32}"
        with pytest.raises(SourceError):
            sm.split_archive_checksum(url)

    def test_sha1_or_sha512_cannot_be_pinned_directly(self) -> None:
        """Only sha256 can be pinned right in the fragment."""
        url = f"{ARCHIVE_URL}#sha1={SHA1_HEX}"
        with pytest.raises(SourceError):
            sm.split_archive_checksum(url)

    def test_wrong_length_hex_is_refused(self) -> None:
        """A digest that is not exactly 64 characters is refused."""
        url = f"{ARCHIVE_URL}#sha256={'a' * 63}"
        with pytest.raises(SourceError):
            sm.split_archive_checksum(url)

    def test_non_hex_digest_is_refused(self) -> None:
        """A 64-character value that is not hex is still refused."""
        url = f"{ARCHIVE_URL}#sha256={'z' * 64}"
        with pytest.raises(SourceError):
            sm.split_archive_checksum(url)

    def test_empty_fragment_is_refused(self) -> None:
        """A bare '#' with nothing after it is refused."""
        with pytest.raises(SourceError):
            sm.split_archive_checksum(f"{ARCHIVE_URL}#")

    def test_empty_value_is_refused(self) -> None:
        """A key with no value ('#sha256=') is refused."""
        with pytest.raises(SourceError):
            sm.split_archive_checksum(f"{ARCHIVE_URL}#sha256=")


# is_archive_url / validate_source: fragments must not break detection ------


class TestFragmentAwareValidators:
    """Tests that the validators recognise an archive URL with a fragment."""

    def test_is_archive_url_with_pinned_sha256(self) -> None:
        assert is_archive_url(f"{ARCHIVE_URL}#sha256={SHA256_HEX}") is True

    def test_is_archive_url_with_checksum_url(self) -> None:
        assert is_archive_url(f"{ARCHIVE_URL}#checksum={CHECKSUM_URL}") is True

    def test_is_archive_url_without_fragment_still_works(self) -> None:
        assert is_archive_url(ARCHIVE_URL) is True

    def test_is_archive_url_false_when_extension_is_not_archive_even_with_fragment(self) -> None:
        assert is_archive_url(f"https://host/app.html#sha256={SHA256_HEX}") is False

    def test_validate_source_keeps_fragment_intact(self) -> None:
        url = f"{ARCHIVE_URL}#sha256={SHA256_HEX}"
        assert validate_source(url) == ("archive", url)

    def test_validate_source_keeps_checksum_url_fragment_intact(self) -> None:
        url = f"{ARCHIVE_URL}#checksum={CHECKSUM_URL}"
        assert validate_source(url) == ("archive", url)


# _fetch_checksum_digest: reading a checksum file ----------------------------


class TestFetchChecksumDigest:
    """Tests for :func:`noust.managers.source_manager._fetch_checksum_digest`."""

    def test_bare_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A checksum file that is only the digest, as WordPress publishes."""
        monkeypatch.setattr(sm, "_open_url", _responder({CHECKSUM_URL: SHA256_HEX.encode()}))
        assert sm._fetch_checksum_digest(CHECKSUM_URL, "sha256") == SHA256_HEX

    def test_sha256sum_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`sha256sum` output: '<hex>  filename'."""
        body = f"{SHA256_HEX}  app.tar.gz\n".encode()
        monkeypatch.setattr(sm, "_open_url", _responder({CHECKSUM_URL: body}))
        assert sm._fetch_checksum_digest(CHECKSUM_URL, "sha256") == SHA256_HEX

    def test_uppercase_digest_is_normalised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = SHA256_HEX.upper().encode()
        monkeypatch.setattr(sm, "_open_url", _responder({CHECKSUM_URL: body}))
        assert sm._fetch_checksum_digest(CHECKSUM_URL, "sha256") == SHA256_HEX

    def test_wrong_length_for_algorithm_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A sha1-length digest served for a sha256 fragment is refused."""
        monkeypatch.setattr(sm, "_open_url", _responder({CHECKSUM_URL: SHA1_HEX.encode()}))
        with pytest.raises(SourceError):
            sm._fetch_checksum_digest(CHECKSUM_URL, "sha256")

    def test_non_hex_content_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sm, "_open_url", _responder({CHECKSUM_URL: b"not-a-digest" * 6}))
        with pytest.raises(SourceError):
            sm._fetch_checksum_digest(CHECKSUM_URL, "sha256")

    def test_oversized_checksum_file_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        oversized = b"a" * (sm._CHECKSUM_MAX_BYTES + 1)
        monkeypatch.setattr(sm, "_open_url", _responder({CHECKSUM_URL: oversized}))
        with pytest.raises(SourceError):
            sm._fetch_checksum_digest(CHECKSUM_URL, "sha256")


# SourceManager.download_archive: end-to-end verification --------------------


class TestDownloadArchiveChecksum:
    """Tests for checksum verification inside download_archive."""

    def test_no_fragment_downloads_and_extracts_as_before(
        self, manager: SourceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A URL with no fragment verifies nothing, exactly as before."""
        payload = _tar_gz_bytes({"hello.txt": b"hi"})
        monkeypatch.setattr(sm, "_open_url", _responder({ARCHIVE_URL: payload}))
        destination = tmp_path / "app"

        assert manager.download_archive(ARCHIVE_URL, destination) is True

        assert (destination / "hello.txt").read_bytes() == b"hi"

    def test_pinned_sha256_matching_extracts(
        self, manager: SourceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A correct pinned digest lets the archive through."""
        payload = _tar_gz_bytes({"hello.txt": b"hi"})
        digest = hashlib.sha256(payload).hexdigest()
        monkeypatch.setattr(sm, "_open_url", _responder({ARCHIVE_URL: payload}))
        destination = tmp_path / "app"
        url = f"{ARCHIVE_URL}#sha256={digest}"

        assert manager.download_archive(url, destination) is True

        assert (destination / "hello.txt").read_bytes() == b"hi"

    def test_pinned_sha256_mismatch_extracts_nothing(
        self, manager: SourceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wrong pinned digest refuses the archive before extraction."""
        payload = _tar_gz_bytes({"hello.txt": b"hi"})
        wrong_digest = "0" * 64
        monkeypatch.setattr(sm, "_open_url", _responder({ARCHIVE_URL: payload}))
        destination = tmp_path / "app"
        url = f"{ARCHIVE_URL}#sha256={wrong_digest}"

        with pytest.raises(SourceError) as exc_info:
            manager.download_archive(url, destination)

        message = str(exc_info.value)
        assert f"Expected sha256: {wrong_digest}" in message
        assert f"Actual sha256:   {hashlib.sha256(payload).hexdigest()}" in message
        assert not list(destination.iterdir())

    def test_checksum_url_fetch_matching_extracts(
        self, manager: SourceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The digest is fetched from the checksum URL and verified."""
        payload = _tar_gz_bytes({"hello.txt": b"hi"})
        digest = hashlib.sha256(payload).hexdigest()
        checksum_body = f"{digest}  app.tar.gz\n".encode()
        monkeypatch.setattr(
            sm,
            "_open_url",
            _responder({ARCHIVE_URL: payload, CHECKSUM_URL: checksum_body}),
        )
        destination = tmp_path / "app"
        url = f"{ARCHIVE_URL}#checksum={CHECKSUM_URL}"

        assert manager.download_archive(url, destination) is True

        assert (destination / "hello.txt").read_bytes() == b"hi"

    def test_checksum_url_fetch_mismatch_extracts_nothing(
        self, manager: SourceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A checksum file naming a different digest refuses the archive."""
        payload = _tar_gz_bytes({"hello.txt": b"hi"})
        wrong_digest = "1" * 64
        checksum_body = f"{wrong_digest}  app.tar.gz\n".encode()
        monkeypatch.setattr(
            sm,
            "_open_url",
            _responder({ARCHIVE_URL: payload, CHECKSUM_URL: checksum_body}),
        )
        destination = tmp_path / "app"
        url = f"{ARCHIVE_URL}#checksum={CHECKSUM_URL}"

        with pytest.raises(SourceError) as exc_info:
            manager.download_archive(url, destination)

        message = str(exc_info.value)
        assert "Expected sha256:" in message
        assert "Actual sha256:" in message
        assert not list(destination.iterdir())

    def test_malformed_fragment_fails_before_any_download(
        self, manager: SourceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed fragment is refused before a connection is opened."""

        def _refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("a malformed fragment must not open a connection")

        monkeypatch.setattr(sm, "_open_url", _refuse)
        destination = tmp_path / "app"
        url = f"{ARCHIVE_URL}#md5=deadbeef"

        with pytest.raises(SourceError):
            manager.download_archive(url, destination)

        assert not destination.exists()

    def test_download_receives_the_url_without_fragment(
        self, manager: SourceManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The archive is fetched from the bare URL; the fragment never reaches the network."""
        payload = _tar_gz_bytes({"hello.txt": b"hi"})
        digest = hashlib.sha256(payload).hexdigest()
        calls: list[str] = []
        monkeypatch.setattr(sm, "_open_url", _responder({ARCHIVE_URL: payload}, calls=calls))
        destination = tmp_path / "app"
        url = f"{ARCHIVE_URL}#sha256={digest}"

        assert manager.download_archive(url, destination) is True

        assert calls == [ARCHIVE_URL]
        assert all("#" not in requested for requested in calls)

    def test_dry_run_verifies_nothing_and_opens_no_connection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rehearsal must not fetch the archive or its checksum."""
        from noust.core.fs import DryRunFileSystem

        def _refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("a rehearsal must not open a connection")

        monkeypatch.setattr(sm, "_open_url", _refuse)
        destination = tmp_path / "app"
        url = f"{ARCHIVE_URL}#sha256={SHA256_HEX}"

        assert SourceManager(fs=DryRunFileSystem()).download_archive(url, destination) is True

        assert not destination.exists()
