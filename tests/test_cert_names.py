# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The certificate manager and a name that holds a certificate of its own.

What is pinned, with a real store and a fake certbot:

- The shared lineage never names a name with a lineage of its own, whoever asks for it.
- A name's lineage is ordered under its own name, proven the way its own settings say
  (HTTP-01 or DNS-01), and an order that fails carries certbot's words and changes nothing.
- Every order that covers an application also covers the names that hold their own.
- A renewal asked for an application renews every lineage it has, and one that cannot be
  renewed does not keep the others from it.
- Deleting a name's lineage revokes nothing and forgets its settings.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.certificate_store import (
    CHALLENGE_DNS,
    SOURCE_ACME,
    CertificateSettings,
    CertificateStore,
)
from noust.core.exceptions import CertificateError
from noust.core.runner import FakeRunner
from noust.core.store import App, DomainKind, NoustStore
from noust.managers.cert_manager import CertManager

PRIMARY = "example.com"
ADMIN = "admin.example.com"
SHOP = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store of the test's own, installed as the process-wide one."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain=PRIMARY, app_type="nodejs", port=3000))
    instance.add_domain(PRIMARY, SHOP, DomainKind.ALIAS.value)
    instance.add_domain(PRIMARY, ADMIN, DomainKind.ALIAS.value)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def certs(runner: FakeRunner, tmp_path: Path, store: NoustStore) -> CertManager:
    """A certificate manager over the test's store, whose letsencrypt tree is disposable."""
    manager = CertManager()
    manager.LETSENCRYPT_DIR = tmp_path / "letsencrypt"
    manager.LIVE_DIR = manager.LETSENCRYPT_DIR / "live"
    manager.config = SimpleNamespace(ssl_email="")  # type: ignore[assignment]
    runner.script(["certbot", "plugins"], stdout="* nginx\n* apache\n")
    return manager


def listing(*entries: tuple[str, str]) -> str:
    """What ``certbot certificates`` prints for some ``(lineage, "names")`` pairs."""
    text = "Found the following certs:\n"
    for lineage, names in entries:
        text += (
            f"  Certificate Name: {lineage}\n"
            f"    Domains: {names}\n"
            "    Expiry Date: 2026-11-30 10:00:00+00:00 (VALID: 60 days)\n"
            f"    Certificate Path: /etc/letsencrypt/live/{lineage}/fullchain.pem\n"
            f"    Private Key Path: /etc/letsencrypt/live/{lineage}/privkey.pem\n"
        )
    return text


def put_lineage(manager: CertManager, lineage: str) -> None:
    """Create the files of a live lineage."""
    live = manager.LIVE_DIR / lineage
    live.mkdir(parents=True, exist_ok=True)
    for name in ("fullchain.pem", "privkey.pem", "cert.pem", "chain.pem"):
        (live / name).write_text("-----BEGIN CERTIFICATE-----\n")


def orders(runner: FakeRunner) -> list[tuple[str, ...]]:
    """The ``certbot certonly`` commands run so far."""
    return [call for call in runner.calls if call[:2] == ("certbot", "certonly")]


def names_of(order: tuple[str, ...]) -> list[str]:
    """The ``-d`` arguments of a certbot command."""
    return [order[i + 1] for i, arg in enumerate(order) if arg == "-d"]


def cert_name_of(order: tuple[str, ...]) -> str:
    """The lineage a certbot command is pinned to."""
    return order[order.index("--cert-name") + 1]


class TestWhatTheSharedLineageCovers:
    def test_a_name_with_a_lineage_of_its_own_is_not_in_the_shared_one(
        self, certs: CertManager, store: NoustStore
    ) -> None:
        store.set_domain_certificate(PRIMARY, ADMIN, "own")

        assert certs.certificate_domains(PRIMARY) == [PRIMARY, SHOP]

    def test_an_explicit_extra_name_is_still_covered(
        self, certs: CertManager, store: NoustStore
    ) -> None:
        """How a name goes back to the shared lineage: ordered before the record changes."""
        store.set_domain_certificate(PRIMARY, ADMIN, "own")

        assert certs.certificate_domains(PRIMARY, [ADMIN]) == [PRIMARY, ADMIN, SHOP]


class TestOrderingANamesOwnCertificate:
    def test_it_is_ordered_under_its_own_name_for_that_name_alone(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        runner.script(["certbot", "certificates"], stdout=listing())

        assert certs.obtain_name(ADMIN, nginx=True) is True

        (order,) = orders(runner)
        assert cert_name_of(order) == ADMIN
        assert names_of(order) == [ADMIN]
        assert "--nginx" in order

    def test_http_01_is_the_default(self, certs: CertManager, runner: FakeRunner) -> None:
        runner.script(["certbot", "certificates"], stdout=listing())

        certs.obtain_name(ADMIN, nginx=True)

        (order,) = orders(runner)
        assert "--manual" not in order

    def test_dns_01_is_what_the_names_settings_say(
        self, certs: CertManager, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hooks are the ones certbot keeps in this lineage's renewal configuration."""
        CertificateStore().set_settings(
            CertificateSettings(
                domain=ADMIN, source=SOURCE_ACME, challenge=CHALLENGE_DNS, dns_provider="cf"
            )
        )
        monkeypatch.setattr(
            CertManager,
            "_dns_args",
            lambda self, account: ["--manual", "--manual-auth-hook", f"auth {account}"],
        )
        runner.script(["certbot", "certificates"], stdout=listing())

        certs.obtain_name(ADMIN)

        (order,) = orders(runner)
        assert order[order.index("--manual-auth-hook") + 1] == "auth cf"
        assert cert_name_of(order) == ADMIN
        assert "--nginx" not in order

    def test_the_manager_reads_the_names_own_settings_and_not_the_applications(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        """Copying the application's choice into the name is the switch's job, not an order's."""
        CertificateStore().set_settings(
            CertificateSettings(
                domain=PRIMARY, source=SOURCE_ACME, challenge=CHALLENGE_DNS, dns_provider="cf"
            )
        )
        runner.script(["certbot", "certificates"], stdout=listing())

        certs.obtain_name(ADMIN, nginx=True)

        (order,) = orders(runner)
        assert "--manual" not in order

    def test_a_certificate_that_is_already_there_is_left_alone(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        put_lineage(certs, ADMIN)
        runner.script(["certbot", "certificates"], stdout=listing((ADMIN, ADMIN)))

        assert certs.obtain_name(ADMIN, nginx=True) is True

        assert orders(runner) == []

    def test_one_that_does_not_cover_the_name_is_ordered_again_in_place(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        put_lineage(certs, ADMIN)
        runner.script(["certbot", "certificates"], stdout=listing((ADMIN, "other.example.com")))

        certs.obtain_name(ADMIN, nginx=True)

        (order,) = orders(runner)
        assert cert_name_of(order) == ADMIN
        assert "--expand" in order

    def test_force_orders_again_although_it_is_covered(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        """How a name changes the way it is proven: certbot keeps it in the renewal file."""
        put_lineage(certs, ADMIN)
        runner.script(["certbot", "certificates"], stdout=listing((ADMIN, ADMIN)))

        certs.obtain_name(ADMIN, nginx=True, force=True)

        (order,) = orders(runner)
        assert "--force-renewal" in order

    def test_a_failed_order_carries_certbots_words_and_writes_nothing(
        self, certs: CertManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        runner.script(["certbot", "certificates"], stdout=listing())
        runner.script(
            ["certbot", "certonly"],
            stderr="Some challenges have failed.\nrate limited",
            exit_code=1,
        )

        with pytest.raises(CertificateError) as caught:
            certs.obtain_name(ADMIN, nginx=True)

        assert "rate limited" in (caught.value.output or "")
        assert ADMIN in caught.value.message
        assert not (certs.LIVE_DIR / ADMIN).exists()

    def test_a_wildcard_cannot_be_proven_over_http(self, certs: CertManager) -> None:
        with pytest.raises(CertificateError) as caught:
            certs.obtain_name(f"*.{PRIMARY}", nginx=True)

        assert "DNS" in caught.value.message
        assert "--dns" in (caught.value.details or "")

    def test_a_wildcard_is_kept_in_the_lineage_no_host_can_have(
        self, certs: CertManager, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        CertificateStore().set_settings(
            CertificateSettings(
                domain=f"*.{PRIMARY}",
                source=SOURCE_ACME,
                challenge=CHALLENGE_DNS,
                dns_provider="cf",
            )
        )
        monkeypatch.setattr(CertManager, "_dns_args", lambda self, account: ["--manual"])
        runner.script(["certbot", "certificates"], stdout=listing())

        certs.obtain_name(f"*.{PRIMARY}")

        (order,) = orders(runner)
        assert cert_name_of(order) == "_wildcard.example.com"
        assert names_of(order) == ["*.example.com"]

    def test_the_web_servers_reload_hook_is_in_place_for_it_too(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        runner.script(["certbot", "certificates"], stdout=listing())

        certs.obtain_name(ADMIN, nginx=True)

        hook = certs.LETSENCRYPT_DIR / "renewal-hooks/deploy/noust-reload"
        assert hook.exists()

    def test_a_name_that_is_not_one_is_refused_before_certbot_is_asked(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        for bad in ("../x", "a;b.com", "", "a b.com"):
            with pytest.raises(CertificateError):
                certs.obtain_name(bad)
        assert orders(runner) == []


class TestEveryOrderCoversTheNamesWithTheirOwn:
    def test_the_shared_order_leaves_the_name_out_and_the_name_is_ordered_after_it(
        self, certs: CertManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        runner.script(["certbot", "certificates"], stdout=listing())

        assert certs.obtain(PRIMARY, nginx=True) is True

        shared, own = orders(runner)
        assert (cert_name_of(shared), names_of(shared)) == (PRIMARY, [PRIMARY, SHOP])
        assert (cert_name_of(own), names_of(own)) == (ADMIN, [ADMIN])

    def test_nothing_is_ordered_for_a_name_whose_lineage_is_there(
        self, certs: CertManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        put_lineage(certs, PRIMARY)
        put_lineage(certs, ADMIN)
        runner.script(
            ["certbot", "certificates"],
            stdout=listing((PRIMARY, f"{PRIMARY} {SHOP}"), (ADMIN, ADMIN)),
        )

        certs.obtain(PRIMARY, nginx=True)

        assert orders(runner) == []

    def test_a_name_that_cannot_be_ordered_does_not_undo_the_shared_certificate(
        self, certs: CertManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        runner.script(["certbot", "certificates"], stdout=listing())
        runner.script(["certbot", "certonly", "--cert-name", ADMIN], stderr="boom", exit_code=1)

        with pytest.raises(CertificateError) as caught:
            certs.obtain(PRIMARY, nginx=True)

        assert ADMIN in caught.value.message
        assert [cert_name_of(order) for order in orders(runner)] == [PRIMARY, ADMIN]

    def test_a_rehearsal_orders_nothing_for_real_either(
        self, certs: CertManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        runner.script(["certbot", "certificates"], stdout=listing())

        certs.obtain(PRIMARY, nginx=True, dry_run=True)

        assert all("--dry-run" in order for order in orders(runner))
        assert len(orders(runner)) == 2


class TestRenewal:
    def test_an_application_is_renewed_with_every_lineage_it_has(
        self, certs: CertManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        put_lineage(certs, PRIMARY)
        put_lineage(certs, ADMIN)

        assert certs.renew(PRIMARY, force=True) is True

        renewals = [call for call in runner.calls if call[:2] == ("certbot", "renew")]
        assert [call[call.index("--cert-name") + 1] for call in renewals] == [PRIMARY, ADMIN]
        assert all("--force-renewal" in call for call in renewals)
        assert all("--no-random-sleep-on-renew" in call for call in renewals)

    def test_a_lineage_that_is_not_on_disk_is_not_asked_for(
        self, certs: CertManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        put_lineage(certs, PRIMARY)

        certs.renew(PRIMARY)

        renewals = [call for call in runner.calls if call[:2] == ("certbot", "renew")]
        assert [call[call.index("--cert-name") + 1] for call in renewals] == [PRIMARY]

    def test_one_that_fails_does_not_keep_the_others_from_renewing_and_is_named(
        self, certs: CertManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        put_lineage(certs, PRIMARY)
        put_lineage(certs, ADMIN)
        runner.script(
            [
                "certbot",
                "renew",
                "--non-interactive",
                "--no-random-sleep-on-renew",
                "--cert-name",
                PRIMARY,
            ],
            stderr="the primary failed to renew",
            exit_code=1,
        )

        with pytest.raises(CertificateError) as caught:
            certs.renew(PRIMARY)

        renewals = [call for call in runner.calls if call[:2] == ("certbot", "renew")]
        assert [call[call.index("--cert-name") + 1] for call in renewals] == [PRIMARY, ADMIN]
        assert "the primary failed to renew" in (caught.value.output or "")

    def test_each_failure_is_named_with_its_own_words(
        self, certs: CertManager, runner: FakeRunner, store: NoustStore
    ) -> None:
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        put_lineage(certs, PRIMARY)
        put_lineage(certs, ADMIN)
        runner.script(["certbot", "renew"], stderr="no good", exit_code=1)

        with pytest.raises(CertificateError) as caught:
            certs.renew(PRIMARY)

        assert PRIMARY in caught.value.message and ADMIN in caught.value.message
        assert (caught.value.output or "").count("no good") == 2

    def test_a_name_that_is_not_an_application_is_renewed_alone(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        put_lineage(certs, "bare.example.org")

        certs.renew("bare.example.org")

        renewals = [call for call in runner.calls if call[:2] == ("certbot", "renew")]
        assert len(renewals) == 1

    def test_without_a_name_certbot_renews_what_is_due_as_it_always_did(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        certs.renew()

        (call,) = [c for c in runner.calls if c[:2] == ("certbot", "renew")]
        assert "--cert-name" not in call


class TestDeleting:
    def test_the_lineage_goes_without_being_revoked_and_its_settings_with_it(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        put_lineage(certs, ADMIN)
        CertificateStore().set_settings(
            CertificateSettings(
                domain=ADMIN, source=SOURCE_ACME, challenge=CHALLENGE_DNS, dns_provider="cf"
            )
        )

        assert certs.delete_name(ADMIN) is True

        assert ("certbot", "delete", "--cert-name", ADMIN, "--non-interactive") in runner.calls
        assert not any(call[:2] == ("certbot", "revoke") for call in runner.calls)
        assert CertificateStore().get_settings(ADMIN).is_default

    def test_one_that_is_already_gone_is_not_an_error(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        assert certs.delete_name(ADMIN) is True

        assert not any(call[:2] == ("certbot", "delete") for call in runner.calls)

    def test_certbots_refusal_is_reported_in_its_words(
        self, certs: CertManager, runner: FakeRunner
    ) -> None:
        put_lineage(certs, ADMIN)
        runner.script(["certbot", "delete"], stderr="cannot delete it", exit_code=1)

        with pytest.raises(CertificateError) as caught:
            certs.delete_name(ADMIN)

        assert "cannot delete it" in (caught.value.details or "")
