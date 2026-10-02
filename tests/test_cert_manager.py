# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the certificate manager's issuance decisions.

Everything here is about one resource: the Let's Encrypt rate limit. It is
shared, it is per registered domain, and it refills over a week, so a bug that
places an order it did not need is not a wasted round trip - it is an outage
the operator cannot fix by retrying.

The two decisions that spend it are pinned:

- Whether to issue at all. A certificate that already covers the request is
  left alone, and a *failure to read* the current state is an error rather
  than an assumption that nothing is there.
- What to ask for. Expanding a certificate rewrites its whole SAN list, so the
  request has to carry the names the certificate already has.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import CertificateError
from noust.core.runner import FakeRunner
from noust.managers.cert_manager import CertManager

CERTBOT_OUTPUT = (
    "Found the following certs:\n"
    "  Certificate Name: shop.tld\n"
    "    Serial Number: 3f\n"
    "    Domains: shop.tld www.shop.tld\n"
    "    Expiry Date: 2026-11-30 10:00:00+00:00 (VALID: 60 days)\n"
    "    Certificate Path: /etc/letsencrypt/live/shop.tld/fullchain.pem\n"
    "    Private Key Path: /etc/letsencrypt/live/shop.tld/privkey.pem\n"
)


class _Store:
    """A store that records nothing, so issuance never touches SQLite."""

    def update_site_ssl(self, **_kwargs: Any) -> None:
        """Accept and discard a TLS state update."""

    def get_app(self, _domain: str) -> None:
        """
        Look up an application record.

        Args:
            _domain: Domain name.

        Returns:
            Always None: no test here has a deployed application.
        """
        return None

    def list_domains(self, _domain: str) -> list[Any]:
        """
        List the domains of an application.

        Args:
            _domain: Domain name.

        Returns:
            Always empty: no test here has a deployed application.
        """
        return []


@pytest.fixture
def certs(runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CertManager:
    """
    A certificate manager whose letsencrypt tree and store are disposable.

    Args:
        runner: The fake command runner, installed process-wide.
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The manager.
    """
    monkeypatch.setattr("noust.managers.cert_manager.get_store", lambda: _Store())
    manager = CertManager()
    manager.LETSENCRYPT_DIR = tmp_path / "letsencrypt"
    manager.LIVE_DIR = manager.LETSENCRYPT_DIR / "live"
    manager.config = SimpleNamespace(ssl_email="")  # type: ignore[assignment]
    return manager


def _put_certificate_on_disk(manager: CertManager, domain: str) -> None:
    """
    Create the files a live lineage has.

    Args:
        manager: The certificate manager.
        domain: Lineage name.
    """
    live = manager.LIVE_DIR / domain
    live.mkdir(parents=True, exist_ok=True)
    for name in ("fullchain.pem", "privkey.pem", "cert.pem", "chain.pem"):
        (live / name).write_text("-----BEGIN CERTIFICATE-----\n")


def _issued(runner: FakeRunner) -> list[tuple[str, ...]]:
    """
    Collect the issuance commands that were executed.

    Args:
        runner: The fake command runner.

    Returns:
        Every recorded ``certbot certonly`` call.
    """
    return [call for call in runner.calls if "certonly" in call]


def test_an_unreadable_certificate_list_never_triggers_an_order(
    certs: CertManager, runner: FakeRunner
) -> None:
    """
    The regression this test exists for.

    ``certbot certificates`` failing used to be parsed as "no certificate", so
    a certificate that was on disk and working was re-ordered on every run,
    burning the rate limit of a domain that was already served.
    """
    _put_certificate_on_disk(certs, "shop.tld")
    runner.script(["certbot", "certificates"], stderr="permission denied", exit_code=1)

    with pytest.raises(CertificateError) as raised:
        certs.obtain("shop.tld", email="ops@shop.tld")

    assert _issued(runner) == []
    assert "certbot certificates" in str(raised.value.details)


def test_an_unreadable_certificate_list_is_no_obstacle_when_nothing_is_on_disk(
    certs: CertManager, runner: FakeRunner
) -> None:
    """A first issuance must not be blocked by a certbot that lists nothing."""
    runner.script(["certbot", "certificates"], stderr="permission denied", exit_code=1)

    assert certs.obtain("shop.tld", email="ops@shop.tld") is True
    assert len(_issued(runner)) == 1


def test_an_empty_certificate_list_still_reads_as_an_answer(
    certs: CertManager, runner: FakeRunner
) -> None:
    """Certbot succeeding with nothing to report is a fact, not a failure."""
    runner.script(["certbot", "certificates"], stdout="No certificates found.\n")

    assert certs.list_certificates() == []
    assert certs._query_certificates() == []


def test_a_failed_query_is_hidden_from_the_read_only_callers(
    certs: CertManager, runner: FakeRunner
) -> None:
    """``wasm cert list`` and ``wasm health`` still get an empty list."""
    runner.script(["certbot", "certificates"], exit_code=1)

    assert certs.list_certificates() == []
    assert certs._query_certificates() is None


def test_list_certificates_reads_the_issuer_from_the_file(
    certs: CertManager, runner: FakeRunner
) -> None:
    """The issuer comes from the certificate itself, through openssl."""
    runner.script(["certbot", "certificates"], stdout=CERTBOT_OUTPUT)
    runner.script(
        ["openssl", "x509", "-noout", "-issuer", "-in"],
        stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n",
    )

    (certificate,) = certs.list_certificates()

    assert certificate.issuer == "C = US, O = Let's Encrypt, CN = R11"
    assert (
        "openssl",
        "x509",
        "-noout",
        "-issuer",
        "-in",
        "/etc/letsencrypt/live/shop.tld/fullchain.pem",
    ) in runner.calls


def test_list_certificates_tolerates_openssl_failing_to_read_the_issuer(
    certs: CertManager, runner: FakeRunner
) -> None:
    """A record is still useful without an issuer; the read failure is not fatal."""
    runner.script(["certbot", "certificates"], stdout=CERTBOT_OUTPUT)
    runner.script(
        ["openssl", "x509", "-noout", "-issuer", "-in"],
        stderr="unable to load certificate",
        exit_code=1,
    )

    (certificate,) = certs.list_certificates()

    assert certificate.issuer is None


def test_a_certificate_that_covers_the_request_is_left_alone(
    certs: CertManager, runner: FakeRunner
) -> None:
    """Running the same deploy twice must cost nothing at Let's Encrypt."""
    _put_certificate_on_disk(certs, "shop.tld")
    runner.script(["certbot", "certificates"], stdout=CERTBOT_OUTPUT)

    assert certs.obtain("shop.tld", email="ops@shop.tld", include_www=True) is True
    assert _issued(runner) == []


def test_expanding_keeps_the_domains_the_certificate_already_carries(
    certs: CertManager, runner: FakeRunner
) -> None:
    """
    An expansion must not silently drop the www alias.

    certbot rewrites the SAN list to exactly what ``-d`` says, so asking only
    for the new domain would produce a certificate the vhost's ServerAlias no
    longer matches.
    """
    _put_certificate_on_disk(certs, "shop.tld")
    runner.script(["certbot", "certificates"], stdout=CERTBOT_OUTPUT)

    certs.obtain("shop.tld", email="ops@shop.tld", additional_domains=["api.shop.tld"])

    (issued,) = _issued(runner)
    assert issued[:4] == ("certbot", "certonly", "--cert-name", "shop.tld")
    assert "--expand" in issued
    assert [issued[i + 1] for i, arg in enumerate(issued) if arg == "-d"] == [
        "shop.tld",
        "api.shop.tld",
        "www.shop.tld",
    ]


def test_www_is_added_only_where_it_can_resolve(certs: CertManager) -> None:
    """A www alias on a subdomain fails the whole ACME order."""
    assert certs.certificate_domains("shop.tld", include_www=True) == ["shop.tld", "www.shop.tld"]
    assert certs.certificate_domains("api.shop.tld", include_www=True) == ["api.shop.tld"]
    assert certs.certificate_domains("www.shop.tld", include_www=True) == ["www.shop.tld"]


def test_a_dry_run_issuance_does_not_consult_the_existing_certificate(
    certs: CertManager, runner: FakeRunner
) -> None:
    """A staging order is free, so the rehearsal always reaches certbot."""
    _put_certificate_on_disk(certs, "shop.tld")
    runner.script(["certbot", "certificates"], exit_code=1)

    assert certs.obtain("shop.tld", email="ops@shop.tld", dry_run=True) is True

    (issued,) = _issued(runner)
    assert "--dry-run" in issued


def test_renewal_names_the_lineage_and_nothing_else(certs: CertManager, runner: FakeRunner) -> None:
    """Renewing one site must not renew, or skip, the others."""
    certs.renew("Shop.TLD ", force=True)

    assert runner.calls == [
        (
            "certbot",
            "renew",
            "--non-interactive",
            "--cert-name",
            "shop.tld",
            "--force-renewal",
        )
    ]


def test_renewal_of_everything_asks_for_no_lineage(certs: CertManager, runner: FakeRunner) -> None:
    """Without a domain, certbot decides which certificates are due."""
    certs.renew()

    assert runner.calls == [("certbot", "renew", "--non-interactive")]


TWO_CERTIFICATES = CERTBOT_OUTPUT + (
    "  Certificate Name: blog.tld\n"
    "    Domains: blog.tld\n"
    "    Expiry Date: 2026-12-20 08:00:00+00:00 (VALID: 80 days)\n"
    "    Certificate Path: /etc/letsencrypt/live/blog.tld/fullchain.pem\n"
    "    Private Key Path: /etc/letsencrypt/live/blog.tld/privkey.pem\n"
)


def _renews_shop(runner: FakeRunner) -> None:
    """certbot lists both certificates; once ``renew`` ran, shop.tld's expiry moved."""
    runner.script(["certbot", "certificates"], stdout=TWO_CERTIFICATES)
    original = runner._lookup

    def lookup(argv: Any, user: str | None = None, env: Any = None) -> Any:
        if tuple(argv[:2]) == ("certbot", "renew"):
            renewed = TWO_CERTIFICATES.replace(
                "2026-11-30 10:00:00+00:00 (VALID: 60 days)",
                "2027-01-29 10:00:00+00:00 (VALID: 89 days)",
            )
            runner.script(["certbot", "certificates"], stdout=renewed)
        return original(argv, user, env)

    runner._lookup = lookup  # type: ignore[method-assign]


def test_renewing_everything_reports_each_certificate_renewed(
    certs: CertManager, runner: FakeRunner
) -> None:
    """The ones whose expiry moved, with their domains and new expiry; not the others."""
    _renews_shop(runner)

    renewed = certs.renew_and_report()

    assert renewed is not None
    assert [(c.name, c.domains, c.expiry) for c in renewed] == [
        ("shop.tld", ["shop.tld", "www.shop.tld"], "2027-01-29")
    ]
    assert ("certbot", "renew", "--non-interactive") in runner.calls


def test_a_renewal_with_nothing_due_reports_none_renewed(
    certs: CertManager, runner: FakeRunner
) -> None:
    runner.script(["certbot", "certificates"], stdout=TWO_CERTIFICATES)

    assert certs.renew_and_report() == []


def test_a_renewal_whose_certificates_cannot_be_listed_reports_it_cannot_tell(
    certs: CertManager, runner: FakeRunner
) -> None:
    """Unknown is not "none": the renewal itself still ran and succeeded."""
    runner.script(["certbot", "certificates"], stderr="locked", exit_code=1)

    assert certs.renew_and_report() is None
    assert ("certbot", "renew", "--non-interactive") in runner.calls


def test_the_renewal_job_returns_what_was_renewed(
    certs: CertManager, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fleet's job drawer reads this result off the node's job."""
    from noust.web import jobs

    _renews_shop(runner)
    monkeypatch.setattr("noust.managers.cert_manager.CertManager", lambda **kwargs: certs)
    logged: list[str] = []
    context = SimpleNamespace(
        set_metadata=lambda *a: None,
        update=lambda *a: None,
        log=lambda message, *a, **k: logged.append(message),
    )

    result = jobs.cert_renew_job(job_context=context)  # type: ignore[arg-type]

    assert result == {
        "domain": None,
        "status": "renewed",
        "renewed": [
            {"name": "shop.tld", "domains": ["shop.tld", "www.shop.tld"], "expiry": "2027-01-29"}
        ],
    }
    assert any("shop.tld" in line and "2027-01-29" in line for line in logged)


def test_a_failed_renewal_says_what_the_acme_server_reported(
    certs: CertManager, runner: FakeRunner
) -> None:
    """The one error an operator has to be able to act on."""
    runner.script(["certbot", "renew"], stderr="too many certificates", exit_code=1)

    with pytest.raises(CertificateError) as raised:
        certs.renew("shop.tld")

    assert "too many certificates" in str(raised.value.details)


@pytest.mark.parametrize("domain", ["../../etc/passwd", "shop tld", "a.tld; rm -rf /"])
def test_a_hostile_domain_never_reaches_certbot(
    certs: CertManager, runner: FakeRunner, domain: str
) -> None:
    """A lineage name becomes a directory under /etc/letsencrypt."""
    with pytest.raises(CertificateError):
        certs.obtain(domain, email="ops@shop.tld")

    assert runner.calls == []


# ---------------------------------------------------------------------------
# Self-signed material for the panel
# ---------------------------------------------------------------------------

#: What openssl prints when the key is requested on stdout: the key first,
#: then the certificate, nothing else on that stream.
SELF_SIGNED_STDOUT = (
    "-----BEGIN PRIVATE KEY-----\nMIIEvQfake\n-----END PRIVATE KEY-----\n"
    "-----BEGIN CERTIFICATE-----\nMIIDfake\n-----END CERTIFICATE-----\n"
)


#: What ``openssl x509 -text`` says about a certificate minted by 3.1: an
#: ECDSA key and the names it is valid for. Only what the reuse check reads.
MODERN_CERTIFICATE_TEXT = (
    "Certificate:\n    Data:\n        Subject Public Key Info:\n"
    "            Public Key Algorithm: id-ecPublicKey\n"
    "        X509v3 extensions:\n            X509v3 Subject Alternative Name:\n"
    "                DNS:panel.internal\nCertificate will not expire\n"
)

#: The same for a certificate 3.0 minted: RSA, no names.
LEGACY_CERTIFICATE_TEXT = (
    "Certificate:\n    Data:\n        Subject Public Key Info:\n"
    "            Public Key Algorithm: rsaEncryption\n"
    "        X509v3 extensions:\n            X509v3 Basic Constraints: critical\n"
    "Certificate will not expire\n"
)


@pytest.fixture(autouse=True)
def _known_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Give the certificate the same names whatever machine the tests run on.

    Args:
        monkeypatch: Patching helper, scoped to the test.
    """
    monkeypatch.setattr(
        "noust.central.setup.certificate_names",
        lambda hostname, environ=None: [f"DNS:{hostname}", "DNS:localhost", "IP:127.0.0.1"],
    )


def _tls_paths(tmp_path: Path) -> tuple[Path, Path]:
    """
    Build the destination paths a panel certificate would be written to.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        The certificate path and the key path, in a directory that does not
        exist yet.
    """
    base = tmp_path / "panel-tls"
    return base / "panel.crt", base / "panel.key"


def test_minting_builds_the_exact_openssl_argv(
    certs: CertManager, runner: FakeRunner, tmp_path: Path
) -> None:
    """The command that ends up running is the one under test."""
    runner.script(["openssl", "req"], stdout=SELF_SIGNED_STDOUT)
    cert_path, key_path = _tls_paths(tmp_path)

    assert certs.generate_self_signed("panel.internal", cert_path, key_path) is True
    assert runner.calls == [
        (
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-sha256",
            "-days",
            "825",
            "-nodes",
            "-subj",
            "/CN=panel.internal",
            "-addext",
            "subjectAltName=DNS:panel.internal,DNS:localhost,IP:127.0.0.1",
            "-addext",
            "basicConstraints=critical,CA:FALSE",
            "-addext",
            "keyUsage=critical,digitalSignature",
            "-addext",
            "extendedKeyUsage=serverAuth",
            "-keyout",
            "/dev/stdout",
        )
    ]


def test_the_minted_pair_is_owner_only_and_split_correctly(
    certs: CertManager, runner: FakeRunner, tmp_path: Path
) -> None:
    """
    The key is asked for on stdout so the files are created by the fs seam.

    Letting openssl write them itself would leave the certificate at the umask
    and put the writes outside the seam that makes --dry-run honest.
    """
    runner.script(["openssl", "req"], stdout=SELF_SIGNED_STDOUT)
    cert_path, key_path = _tls_paths(tmp_path)

    certs.generate_self_signed("panel.internal", cert_path, key_path)

    assert key_path.read_text() == (
        "-----BEGIN PRIVATE KEY-----\nMIIEvQfake\n-----END PRIVATE KEY-----\n"
    )
    assert cert_path.read_text() == (
        "-----BEGIN CERTIFICATE-----\nMIIDfake\n-----END CERTIFICATE-----\n"
    )
    assert (key_path.stat().st_mode & 0o777) == 0o600
    assert (cert_path.stat().st_mode & 0o777) == 0o600
    assert (cert_path.parent.stat().st_mode & 0o777) == 0o700
    # And nothing else - a temporary, a stray privkey.pem - was left behind.
    assert sorted(p.name for p in cert_path.parent.iterdir()) == ["panel.crt", "panel.key"]


def test_a_pair_still_valid_is_reused_not_reminted(
    certs: CertManager, runner: FakeRunner, tmp_path: Path
) -> None:
    """
    Restarting the panel must not churn the certificate fingerprint.

    The operator's browser stores an exception for the fingerprint it saw; a
    pair reminted on every start invalidates that exception on every start.
    """
    cert_path, key_path = _tls_paths(tmp_path)
    cert_path.parent.mkdir(parents=True)
    cert_path.write_text("cert")
    key_path.write_text("key")
    runner.script(["openssl", "x509"], stdout=MODERN_CERTIFICATE_TEXT)

    assert certs.generate_self_signed("panel.internal", cert_path, key_path) is False
    assert runner.calls == [
        ("openssl", "x509", "-in", str(cert_path), "-noout", "-text", "-checkend", "86400")
    ]
    assert cert_path.read_text() == "cert"


def test_a_pair_from_before_3_1_is_replaced_once_because_no_browser_can_trust_it(
    certs: CertManager, runner: FakeRunner, tmp_path: Path
) -> None:
    """
    RSA and no ``subjectAltName``: every current browser ignores the common
    name, so this pair cannot be trusted by any means (and WebAuthn is refused
    on it). Still valid is not enough to keep it.
    """
    cert_path, key_path = _tls_paths(tmp_path)
    cert_path.parent.mkdir(parents=True)
    cert_path.write_text("legacy cert")
    key_path.write_text("legacy key")
    runner.script(["openssl", "x509"], stdout=LEGACY_CERTIFICATE_TEXT)
    runner.script(["openssl", "req"], stdout=SELF_SIGNED_STDOUT)

    assert certs.generate_self_signed("panel.internal", cert_path, key_path) is True
    assert cert_path.read_text().startswith("-----BEGIN CERTIFICATE-----")


def test_the_names_are_the_machines_own_unless_the_caller_gives_them(
    certs: CertManager, runner: FakeRunner, tmp_path: Path
) -> None:
    cert_path, key_path = _tls_paths(tmp_path)
    runner.script(["openssl", "req"], stdout=SELF_SIGNED_STDOUT)

    certs.generate_self_signed(
        "panel.internal", cert_path, key_path, alt_names=["DNS:central.example.com", "IP:10.0.0.5"]
    )

    argv = runner.calls[-1]
    assert "subjectAltName=DNS:central.example.com,IP:10.0.0.5" in argv


@pytest.mark.parametrize(
    "hostile", ["DNS:a,DNS:b", "DNS:a;b", "otherName:1.2.3;UTF8:x", "DNS:a b", "a.example.com", ""]
)
def test_a_name_that_could_add_an_extension_never_reaches_openssl(
    certs: CertManager, runner: FakeRunner, tmp_path: Path, hostile: str
) -> None:
    cert_path, key_path = _tls_paths(tmp_path)

    with pytest.raises(CertificateError):
        certs.generate_self_signed("panel.internal", cert_path, key_path, alt_names=[hostile])

    assert runner.calls == []


def test_a_certificate_with_no_names_is_refused(
    certs: CertManager, runner: FakeRunner, tmp_path: Path
) -> None:
    cert_path, key_path = _tls_paths(tmp_path)

    with pytest.raises(CertificateError, match="at least one name"):
        certs.generate_self_signed("panel.internal", cert_path, key_path, alt_names=[])

    assert runner.calls == []


def test_an_expiring_pair_is_reminted(
    certs: CertManager, runner: FakeRunner, tmp_path: Path
) -> None:
    """A pair about to expire is replaced instead of served until it breaks."""
    cert_path, key_path = _tls_paths(tmp_path)
    cert_path.parent.mkdir(parents=True)
    cert_path.write_text("stale cert")
    key_path.write_text("stale key")
    runner.script(["openssl", "x509"], exit_code=1)
    runner.script(["openssl", "req"], stdout=SELF_SIGNED_STDOUT)

    assert certs.generate_self_signed("panel.internal", cert_path, key_path) is True
    assert cert_path.read_text().startswith("-----BEGIN CERTIFICATE-----")
    assert key_path.read_text().startswith("-----BEGIN PRIVATE KEY-----")


def test_a_failed_openssl_is_reported_verbatim(
    certs: CertManager, runner: FakeRunner, tmp_path: Path
) -> None:
    """The operator gets openssl's own words, not a paraphrase."""
    runner.script(["openssl", "req"], stderr="unable to load provider", exit_code=1)
    cert_path, key_path = _tls_paths(tmp_path)

    with pytest.raises(CertificateError) as raised:
        certs.generate_self_signed("panel.internal", cert_path, key_path)

    assert "unable to load provider" in str(raised.value.details)
    assert not cert_path.exists()
    assert not key_path.exists()


def test_success_without_pem_output_is_an_error(
    certs: CertManager, runner: FakeRunner, tmp_path: Path
) -> None:
    """An exit code of 0 with no key on stdout must not write empty files."""
    runner.script(["openssl", "req"], stdout="")
    cert_path, key_path = _tls_paths(tmp_path)

    with pytest.raises(CertificateError):
        certs.generate_self_signed("panel.internal", cert_path, key_path)

    assert not cert_path.exists()
    assert not key_path.exists()


@pytest.mark.parametrize("hostname", ["", "  ", "a/b", "host name", "x;y", "/CN=evil"])
def test_a_hostile_subject_never_reaches_openssl(
    certs: CertManager, runner: FakeRunner, tmp_path: Path, hostname: str
) -> None:
    """
    The name lands in ``-subj``, where ``/`` separates fields.

    Args:
        hostname: A spelling that must be refused before openssl runs.
    """
    cert_path, key_path = _tls_paths(tmp_path)

    with pytest.raises(CertificateError):
        certs.generate_self_signed(hostname, cert_path, key_path)

    assert runner.calls == []


def test_tls_is_recorded_only_once_the_certificate_files_exist(
    certs: CertManager, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A rehearsal reports success without issuing anything.

    ``--dry-run`` is enforced at the command runner, which the manager cannot
    see, so a successful-looking certbot call proves nothing on its own. A
    store that says a site serves TLS when it has no key makes every reader of
    that record lie, the vhost writer included.
    """
    recorded: list[dict[str, Any]] = []

    class _Recorder(_Store):
        """A store that keeps the TLS updates it is given."""

        def update_site_ssl(self, **kwargs: Any) -> None:
            """
            Record a TLS state update.

            Args:
                **kwargs: The domain and its certificate paths.
            """
            recorded.append(kwargs)

    monkeypatch.setattr("noust.managers.cert_manager.get_store", lambda: _Recorder())

    assert certs.obtain("shop.tld", email="ops@shop.tld") is True
    assert recorded == []

    _put_certificate_on_disk(certs, "shop.tld")
    runner.script(
        ["certbot", "certificates"],
        stdout=CERTBOT_OUTPUT.replace(" www.shop.tld", ""),
    )

    certs.obtain("shop.tld", email="ops@shop.tld", additional_domains=["api.shop.tld"])

    assert [update["ssl"] for update in recorded] == [True]
