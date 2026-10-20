# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A name taking a certificate of its own, and giving it back, without the service stopping.

Every test runs the real store, the real nginx manager writing into a temporary tree, the real
certificate manager and the real deployers; only the processes are faked, and certbot is a
:class:`~tests.certbot_sim.CertbotSim` that remembers what it ordered, so a test can follow a
certificate from the order to the site that is written with it. Pinned:

- the certificate is ordered before the site changes, and a refusal changes nothing;
- the web server's refusal of the new site puts the choice back;
- the shared certificate stops naming the name once the site is right, and a failure of that
  housekeeping is reported without undoing the change;
- giving it back expands the shared certificate first, and deletes the lineage last;
- a site the operator wrote is never rewritten;
- removing a name retires only its lineage and its block.
"""

from __future__ import annotations

from dataclasses import replace
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
from noust.core.exceptions import (
    CertificateError,
    DomainError,
    NoustError,
    ValidationError,
)
from noust.core.runner import set_runner
from noust.core.store import App, NoustStore
from noust.deployers import domains, name_certificates
from noust.deployers.base import BaseDeployer
from noust.deployers.registry import get_deployer
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import NGINX_BACKEND
from tests.certbot_sim import CertbotSim

PRIMARY = "example.com"
SHOP = "shop.example.com"
ADMIN = "admin.example.com"
OLD = "old.example.com"
RELEASE = "20260925-120000-abcdef0"

NGINX_REJECTS = "nginx: [emerg] duplicate listen options for 0.0.0.0:443 in /etc/nginx/x:12\n"


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store in the test's directory, installed as the process-wide singleton."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def letsencrypt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """certbot's directory, in the test's own tree."""
    directory = tmp_path / "letsencrypt"
    monkeypatch.setattr(CertManager, "LETSENCRYPT_DIR", directory)
    monkeypatch.setattr(CertManager, "LIVE_DIR", directory / "live")
    return directory


@pytest.fixture
def sim(letsencrypt: Path) -> Any:
    """A certbot that remembers, installed as the process-wide runner."""
    fake = CertbotSim(letsencrypt / "live")
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def web(tmp_path: Path, sim: CertbotSim, store: NoustStore) -> NginxManager:
    """An nginx manager writing into a temporary configuration tree."""
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


@pytest.fixture
def certs(sim: CertbotSim, store: NoustStore, letsencrypt: Path) -> CertManager:
    """A certificate manager that asks the simulated certbot."""
    manager = CertManager()
    manager.config = SimpleNamespace(ssl_email="")  # type: ignore[assignment]
    return manager


class Machine:
    """Deploys an application into the temporary tree and wires its deployer."""

    def __init__(
        self,
        tmp_path: Path,
        store: NoustStore,
        web: NginxManager,
        certs: CertManager,
        sim: CertbotSim,
    ) -> None:
        self.tmp_path = tmp_path
        self.store = store
        self.web = web
        self.certs = certs
        self.sim = sim

    def wire(self, deployer: BaseDeployer) -> BaseDeployer:
        """Point a deployer at this machine's web server and certificates."""
        deployer._webserver_manager = lambda: self.web  # type: ignore[method-assign]
        deployer.cert_manager = self.certs
        return deployer

    def deployer(self, app_type: str, verbose: bool = False) -> Any:
        """Stand-in for the registry's get_deployer, wiring what it returns."""
        deployer = get_deployer(app_type, verbose=verbose)
        if isinstance(deployer, BaseDeployer):
            self.wire(deployer)
        return deployer

    def deploy(self, domain: str = PRIMARY, *, app_type: str = "nodejs", tls: bool = True) -> App:
        """Put an application with a few names on disk and in the store, and write its site."""
        root = self.tmp_path / "apps" / domain
        root.mkdir(parents=True)
        (root / "package.json").write_text('{"name": "app"}')
        app = self.store.create_app(
            App(
                domain=domain,
                app_type=app_type,
                source=str(root),
                app_path=str(root),
                port=3000,
                layout="inplace",
                ssl_enabled=tls,
            )
        )
        self.store.add_domain(domain, f"shop.{domain}", "alias")
        self.store.add_domain(domain, f"admin.{domain}", "alias")
        self.store.add_domain(domain, f"old.{domain}", "redirect")
        if tls:
            self.sim.have(domain, [domain, f"shop.{domain}", f"admin.{domain}", f"old.{domain}"])
        deployer = self.deployer(app_type)
        deployer.configure(domain, str(root), port=app.port, app_path=root)
        deployer.refresh_site(with_ssl=tls)
        self.sim.calls.clear()
        return app

    def config(self, domain: str = PRIMARY) -> str:
        """Read an application's site."""
        return self.web.config_path(domain).read_text()

    def modes(self, domain: str = PRIMARY) -> dict[str, str]:
        """Each name's certificate mode."""
        return {r.domain: r.certificate for r in self.store.list_domains(domain)}

    def reloads(self) -> int:
        """How many times nginx was reloaded since the last deploy."""
        return sum(1 for call in self.sim.calls if call == ("systemctl", "reload", "nginx"))

    def order_names(self, lineage: str) -> list[list[str]]:
        """The names asked for in each order of a lineage."""
        return [list(names) for name, names in self.sim.orders if name == lineage]


@pytest.fixture
def machine(
    tmp_path: Path,
    store: NoustStore,
    web: NginxManager,
    certs: CertManager,
    sim: CertbotSim,
    monkeypatch: pytest.MonkeyPatch,
) -> Machine:
    """A machine whose deployers the domains module builds through the registry."""
    fake = Machine(tmp_path, store, web, certs, sim)
    monkeypatch.setattr(domains, "get_deployer", fake.deployer)
    return fake


def blocks_for(config: str, name: str) -> list[str]:
    """The nginx server blocks that name a host in their ``server_name``."""
    import re

    found = []
    for block in re.split(r"(?m)^(?=server \{)", config)[1:]:
        for line in re.findall(r"(?m)^\s*server_name\s+(.+?);", block):
            if name in line.split():
                found.append(block)
                break
    return found


class TestTakingACertificateOfItsOwn:
    def test_the_certificate_is_ordered_for_the_name_alone_then_the_site_and_the_shared_one_follow(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()

        change = name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        assert (change.certificate, change.changed, change.ordered) == ("own", True, True)
        assert machine.modes()[ADMIN] == "own"
        assert machine.order_names(ADMIN) == [[ADMIN]]
        # The shared lineage was ordered again without it: the renewal of the others must not
        # depend on a name that has its own.
        assert machine.order_names(PRIMARY) == [[PRIMARY, SHOP, OLD]]
        assert change.shared_reduced is True
        # The order came before the site changed: the first reload is after the first order.
        commands = [call for call in sim.calls if call[:2] == ("certbot", "certonly")]
        assert commands[0][commands[0].index("--cert-name") + 1] == ADMIN

    def test_the_site_has_a_block_of_its_own_for_the_name_presenting_its_lineage(
        self, machine: Machine, letsencrypt: Path
    ) -> None:
        machine.deploy()

        name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        config = machine.config()
        own = blocks_for(config, ADMIN)
        assert len(own) == 2
        assert all(
            f"{letsencrypt}/live/{ADMIN}/fullchain.pem" in b or "listen 80" in b for b in own
        )
        shared = blocks_for(config, SHOP)
        assert all(ADMIN not in b.split("server_name")[1].split(";")[0] for b in shared)
        assert machine.reloads() >= 1

    def test_it_is_audited(self, machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
        machine.deploy()
        recorded: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            name_certificates.audit,
            "record",
            lambda event, **kw: recorded.append((event, kw["details"])),
        )

        name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        assert recorded[0][0] == "domains.certificate"
        assert recorded[0][1]["name"] == ADMIN and recorded[0][1]["certificate"] == "own"

    def test_a_refused_order_changes_nothing_and_carries_certbots_words(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        before = machine.config()
        sim.refuse[ADMIN] = "Some challenges have failed.\nrate limit reached"

        with pytest.raises(CertificateError) as caught:
            name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        assert "rate limit reached" in (caught.value.output or "")
        assert machine.modes()[ADMIN] == "shared"
        assert machine.config() == before
        assert machine.reloads() == 0
        assert CertificateStore().get_settings(ADMIN).is_default
        # The shared certificate was not touched either.
        assert machine.order_names(PRIMARY) == []

    def test_a_refusal_by_the_web_server_puts_the_choice_back(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        before = machine.config()
        sim.script(["nginx", "-t"], stderr=NGINX_REJECTS, exit_code=1)

        with pytest.raises(ValidationError):
            name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        assert machine.modes()[ADMIN] == "shared"
        assert machine.config() == before
        # The shared certificate still names the name, so it is still served and verifies.
        assert machine.order_names(PRIMARY) == []

    def test_a_failure_to_shrink_the_shared_certificate_is_reported_and_undoes_nothing(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        sim.refuse[PRIMARY] = "too many requests for example.com"

        change = name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        assert machine.modes()[ADMIN] == "own"
        assert change.shared_reduced is False
        assert any("too many requests" in w for w in change.warnings)
        assert any("--reduce" in w for w in change.warnings)
        assert len(blocks_for(machine.config(), ADMIN)) == 2

    def test_a_redirect_can_hold_one_too(self, machine: Machine, letsencrypt: Path) -> None:
        machine.deploy()

        name_certificates.set_certificate(PRIMARY, OLD, "own")

        assert machine.modes()[OLD] == "own"
        assert f"{letsencrypt}/live/{OLD}/fullchain.pem" in machine.config()

    def test_asking_again_is_a_retry_and_orders_nothing_it_has(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        sim.calls.clear()
        sim.orders.clear()

        change = name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        assert change.changed is False
        assert sim.orders == []

    def test_a_site_without_tls_only_keeps_the_choice(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy(tls=False)

        change = name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        assert change.tls is False and change.ordered is False
        assert machine.modes()[ADMIN] == "own"
        assert sim.certonly_calls() == []
        assert "ssl_certificate" not in machine.config()

    def test_the_first_certificate_then_orders_the_names_lineage_too(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        """`noust cert create` on a site that had none: the name's own is part of it."""
        machine.deploy(tls=False)
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        machine.certs.obtain(PRIMARY, nginx=True)

        assert machine.order_names(PRIMARY) == [[PRIMARY, SHOP, OLD]]
        assert machine.order_names(ADMIN) == [[ADMIN]]

    def test_dns_01_is_what_the_name_is_asked_to_be_proven_with(
        self, machine: Machine, sim: CertbotSim, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machine.deploy()
        monkeypatch.setattr(name_certificates, "check_choice", lambda *a, **k: None)
        monkeypatch.setattr(
            CertManager,
            "_dns_args",
            lambda self, account: ["--manual", "--manual-auth-hook", account],
        )

        name_certificates.set_certificate(
            PRIMARY, ADMIN, "own", challenge="dns-01", dns_provider="cf"
        )

        (order,) = [c for c in sim.certonly_calls() if c[c.index("--cert-name") + 1] == ADMIN]
        assert "--manual" in order and "cf" in order
        stored = CertificateStore().get_settings(ADMIN)
        assert (stored.challenge, stored.dns_provider) == (CHALLENGE_DNS, "cf")

    def test_by_default_it_is_proven_the_way_the_application_is(
        self, machine: Machine, sim: CertbotSim, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machine.deploy()
        CertificateStore().set_settings(
            CertificateSettings(
                domain=PRIMARY, source=SOURCE_ACME, challenge=CHALLENGE_DNS, dns_provider="cf"
            )
        )
        monkeypatch.setattr(name_certificates, "check_choice", lambda *a, **k: None)
        monkeypatch.setattr(
            CertManager,
            "_dns_args",
            lambda self, account: ["--manual", "--manual-auth-hook", account],
        )

        name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        stored = CertificateStore().get_settings(ADMIN)
        assert (stored.challenge, stored.dns_provider) == (CHALLENGE_DNS, "cf")

    def test_changing_how_it_is_proven_orders_it_again_once(
        self, machine: Machine, sim: CertbotSim, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        sim.orders.clear()
        sim.calls.clear()
        monkeypatch.setattr(name_certificates, "check_choice", lambda *a, **k: None)
        monkeypatch.setattr(CertManager, "_dns_args", lambda self, account: ["--manual"])

        name_certificates.set_certificate(
            PRIMARY, ADMIN, "own", challenge="dns-01", dns_provider="cf"
        )

        (order,) = sim.certonly_calls()
        assert "--force-renewal" in order and "--manual" in order

    def test_a_wildcard_needs_dns(self, machine: Machine, store: NoustStore) -> None:
        machine.deploy()
        store.add_domain(PRIMARY, "*.example.com", "alias")

        with pytest.raises(ValidationError) as caught:
            name_certificates.set_certificate(PRIMARY, "*.example.com", "own")

        assert "DNS" in caught.value.message

    def test_a_wildcard_proven_by_dns_has_the_lineage_no_host_can_have(
        self, machine: Machine, sim: CertbotSim, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machine.deploy()
        store.add_domain(PRIMARY, "*.example.com", "alias")
        monkeypatch.setattr(name_certificates, "check_choice", lambda *a, **k: None)
        monkeypatch.setattr(CertManager, "_dns_args", lambda self, account: ["--manual"])

        name_certificates.set_certificate(
            PRIMARY, "*.example.com", "own", challenge="dns-01", dns_provider="cf"
        )

        assert "_wildcard.example.com" in sim.lineages
        assert machine.order_names("_wildcard.example.com") == [["*.example.com"]]
        assert "live/_wildcard.example.com/fullchain.pem" in machine.config()


class TestWhatIsRefusedBeforeAnythingIsOrdered:
    def test_the_primary_is_the_lineage_everything_hangs_from(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()

        with pytest.raises(DomainError) as caught:
            name_certificates.set_certificate(PRIMARY, PRIMARY, "own")

        assert "primary" in caught.value.message
        assert sim.certonly_calls() == []

    def test_a_name_that_is_not_the_applications(self, machine: Machine, sim: CertbotSim) -> None:
        machine.deploy()

        with pytest.raises(DomainError):
            name_certificates.set_certificate(PRIMARY, "ghost.example.com", "own")

        assert sim.certonly_calls() == []

    def test_a_value_that_is_not_a_mode(self, machine: Machine, sim: CertbotSim) -> None:
        machine.deploy()

        with pytest.raises(ValidationError):
            name_certificates.set_certificate(PRIMARY, ADMIN, "private")

    def test_a_monorepo_orders_its_certificate_in_its_deploy(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        app = machine.deploy("mono.example.com", app_type="nodejs")
        row = machine.store.get_app(app.domain)
        assert row is not None
        row.app_type = "monorepo"
        machine.store.update_app(row)

        with pytest.raises(NoustError) as caught:
            name_certificates.set_certificate("mono.example.com", "admin.mono.example.com", "own")

        assert "monorepo" in caught.value.message
        assert sim.certonly_calls() == []

    def test_the_canonical_pair_is_served_together(
        self, machine: Machine, sim: CertbotSim, store: NoustStore
    ) -> None:
        from noust.managers.site_policy import SitePolicyManager

        machine.deploy()
        store.add_domain(PRIMARY, f"www.{PRIMARY}", "redirect")
        manager = SitePolicyManager()
        manager.save(PRIMARY, replace(manager.get(PRIMARY), canonical="www"), actor=None)

        with pytest.raises(DomainError) as caught:
            name_certificates.set_certificate(PRIMARY, f"www.{PRIMARY}", "own")

        assert "canonical" in caught.value.message
        assert sim.certonly_calls() == []


class TestAnOperatorsSite:
    def test_it_is_never_rewritten_and_the_answer_says_what_it_has_to_present(
        self, machine: Machine, sim: CertbotSim, letsencrypt: Path
    ) -> None:
        machine.deploy()
        path = machine.web.config_path(PRIMARY)
        text = path.read_text().replace("# Generated by Noust", "# written by hand")
        path.write_text(text)
        before = path.read_text()
        sim.calls.clear()

        change = name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        assert path.read_text() == before
        assert change.operator_site is True
        assert machine.modes()[ADMIN] == "own"
        assert machine.order_names(ADMIN) == [[ADMIN]]
        # Their site may still present the shared certificate for the name: it is not shrunk.
        assert machine.order_names(PRIMARY) == []
        assert any(f"{letsencrypt}/live/{ADMIN}/fullchain.pem" in w for w in change.warnings)
        assert machine.reloads() == 0


class TestGivingItBack:
    def test_the_shared_certificate_learns_the_name_first_and_the_lineage_goes_last(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        sim.calls.clear()
        sim.orders.clear()

        change = name_certificates.set_certificate(PRIMARY, ADMIN, "shared")

        assert (change.certificate, change.changed, change.lineage_removed) == (
            "shared",
            True,
            True,
        )
        assert machine.modes()[ADMIN] == "shared"
        (order,) = sim.orders
        assert order[0] == PRIMARY and ADMIN in order[1] and SHOP in order[1]
        assert ADMIN not in sim.lineages
        assert not (sim.live / ADMIN).exists()
        verbs = [call[1] for call in sim.calls if call[0] == "certbot"]
        assert verbs.index("certonly") < verbs.index("delete")
        assert CertificateStore().get_settings(ADMIN).is_default
        config = machine.config()
        assert len(blocks_for(config, ADMIN)) == 2
        assert f"live/{ADMIN}/" not in config

    def test_nothing_is_revoked(self, machine: Machine, sim: CertbotSim) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        name_certificates.set_certificate(PRIMARY, ADMIN, "shared")

        assert not any(call[:2] == ("certbot", "revoke") for call in sim.calls)

    def test_a_refusal_to_expand_the_shared_certificate_leaves_the_name_with_its_own(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        before = machine.config()
        sim.refuse[PRIMARY] = "Challenge failed for example.com"

        with pytest.raises(CertificateError):
            name_certificates.set_certificate(PRIMARY, ADMIN, "shared")

        assert machine.modes()[ADMIN] == "own"
        assert machine.config() == before
        assert ADMIN in sim.lineages

    def test_a_refusal_by_the_web_server_leaves_the_name_with_its_own(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        before = machine.config()
        sim.script(["nginx", "-t"], stderr=NGINX_REJECTS, exit_code=1)

        with pytest.raises(ValidationError):
            name_certificates.set_certificate(PRIMARY, ADMIN, "shared")

        assert machine.modes()[ADMIN] == "own"
        assert machine.config() == before
        assert ADMIN in sim.lineages

    def test_a_lineage_that_will_not_go_is_said_and_harmless(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        # Only the deletion is refused: the order for the shared certificate must still work.
        original = sim._delete

        def refuse_delete(args: tuple[str, ...], result: Any) -> Any:
            return replace(result, exit_code=1, stderr="cannot delete admin.example.com")

        sim._delete = refuse_delete  # type: ignore[method-assign]

        change = name_certificates.set_certificate(PRIMARY, ADMIN, "shared")

        sim._delete = original  # type: ignore[method-assign]
        assert machine.modes()[ADMIN] == "shared"
        assert change.lineage_removed is False
        assert any("cannot delete admin.example.com" in w for w in change.warnings)
        assert any("noust cert delete" in w for w in change.warnings)

    def test_giving_back_what_was_never_taken_is_nothing(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()

        change = name_certificates.set_certificate(PRIMARY, SHOP, "shared")

        assert change.changed is False
        assert sim.certonly_calls() == []

    def test_another_names_trouble_does_not_keep_this_one_from_coming_back(
        self, machine: Machine, sim: CertbotSim, store: NoustStore
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        name_certificates.set_certificate(PRIMARY, OLD, "own")
        sim.refuse[OLD] = "DNS problem: NXDOMAIN looking up A for old.example.com"
        # Its lineage is there, so the shared order does not need it: only the shared one is asked.
        change = name_certificates.set_certificate(PRIMARY, ADMIN, "shared")

        assert change.certificate == "shared"
        assert machine.modes()[OLD] == "own"


class TestAddingAndRemoving:
    def test_a_name_added_with_a_certificate_of_its_own_is_covered_by_it_alone(
        self, machine: Machine, sim: CertbotSim, letsencrypt: Path
    ) -> None:
        machine.deploy()

        change = domains.add_domain(PRIMARY, "api.example.com", "alias", certificate="own")

        assert machine.modes()["api.example.com"] == "own"
        assert change.certificate_issued is True
        assert machine.order_names("api.example.com") == [["api.example.com"]]
        for names in machine.order_names(PRIMARY):
            assert "api.example.com" not in names
        assert f"{letsencrypt}/live/api.example.com/fullchain.pem" in machine.config()

    def test_a_failed_order_keeps_the_name_and_serves_it_with_the_shared_one_until_retried(
        self, machine: Machine, sim: CertbotSim, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machine.deploy()
        sim.refuse["api.example.com"] = "Challenge failed for api.example.com"
        monkeypatch.setattr(
            domains, "check_dns", lambda *a, **kw: SimpleNamespace(points_here=True)
        )

        change = domains.add_domain(PRIMARY, "api.example.com", "alias", certificate="own")

        assert change.certificate_issued is False
        assert "Challenge failed for api.example.com" in (change.certificate_error or "")
        assert machine.modes()["api.example.com"] == "own"
        assert "live/api.example.com" not in machine.config()
        assert "api.example.com" in machine.config()
        sim.refuse.clear()

        again = domains.add_domain(PRIMARY, "api.example.com", "alias", certificate="own")

        assert again.certificate_issued is True
        assert "live/api.example.com/fullchain.pem" in machine.config()

    def test_the_primary_cannot_be_added_with_one(self, machine: Machine) -> None:
        machine.deploy()

        with pytest.raises(NoustError):
            domains.add_domain(PRIMARY, PRIMARY, "primary", certificate="own")  # type: ignore[arg-type]

    def test_removing_a_name_with_its_own_retires_its_lineage_and_block_only(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        sim.calls.clear()
        sim.orders.clear()

        change = domains.remove_domain(PRIMARY, ADMIN)

        assert change.lineage_removed is True
        assert change.certificate_reduced is False
        assert ADMIN not in sim.lineages
        assert sim.orders == []
        assert ADMIN not in machine.config()
        assert PRIMARY in sim.lineages
        assert not any(call[:2] == ("certbot", "revoke") for call in sim.calls)

    def test_removing_a_name_that_shares_leaves_the_others_lineages_alone(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        sim.calls.clear()
        sim.orders.clear()

        change = domains.remove_domain(PRIMARY, SHOP)

        assert change.certificate_reduced is True
        assert ADMIN in sim.lineages
        assert machine.order_names(PRIMARY) == [[PRIMARY, OLD]]
        assert len(blocks_for(machine.config(), ADMIN)) == 2

    def test_a_job_can_leave_the_lineage_to_run_later_and_delete_it_then(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        sim.calls.clear()

        change = domains.remove_domain(PRIMARY, ADMIN, reduce_cert=False)

        assert change.lineage_pending == ADMIN
        assert ADMIN in sim.lineages
        assert domains.release_certificate(PRIMARY, ADMIN) is True
        assert ADMIN not in sim.lineages

    def test_a_lineage_that_still_serves_a_name_is_never_deleted(
        self, machine: Machine, sim: CertbotSim, certs: CertManager
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        with pytest.raises(CertificateError) as caught:
            certs.delete_name(ADMIN)

        assert "certificate of its own" in caught.value.message
        assert ADMIN in sim.lineages

    def test_a_removal_the_web_server_refuses_brings_the_name_back_as_it_was(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        sim.script(["nginx", "-t"], stderr=NGINX_REJECTS, exit_code=1)

        with pytest.raises(ValidationError):
            domains.remove_domain(PRIMARY, ADMIN)

        assert machine.modes()[ADMIN] == "own"
        assert ADMIN in sim.lineages

    def test_switching_a_name_off_keeps_its_certificate(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        domains.disable_domain(PRIMARY, ADMIN)

        assert ADMIN not in machine.config()
        assert ADMIN in sim.lineages
        assert machine.modes()[ADMIN] == "own"

    def test_switching_it_on_orders_what_is_missing(
        self, machine: Machine, sim: CertbotSim
    ) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")
        domains.disable_domain(PRIMARY, ADMIN)
        sim.lineages.pop(ADMIN)
        for child in (sim.live / ADMIN).iterdir():
            child.unlink()
        (sim.live / ADMIN).rmdir()

        domains.enable_domain(PRIMARY, ADMIN)

        assert ADMIN in sim.lineages
        assert len(blocks_for(machine.config(), ADMIN)) == 2


class TestWhichCertificateServesEachName:
    def test_each_name_says_which_certificate_serves_it(self, machine: Machine) -> None:
        machine.deploy()
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        found = {b.name: b for b in name_certificates.bindings(PRIMARY)}

        assert found[PRIMARY].served_by == PRIMARY and found[PRIMARY].certificate == "shared"
        assert found[SHOP].served_by == PRIMARY
        assert found[ADMIN].served_by == ADMIN and found[ADMIN].certificate == "own"
        assert found[ADMIN].pending is False

    def test_a_name_whose_certificate_is_not_there_yet_is_served_by_the_shared_one(
        self, machine: Machine, store: NoustStore
    ) -> None:
        machine.deploy(tls=False)
        name_certificates.set_certificate(PRIMARY, ADMIN, "own")

        found = {b.name: b for b in name_certificates.bindings(PRIMARY)}

        assert found[ADMIN].certificate == "own"
        assert found[ADMIN].pending is True
        assert found[ADMIN].served_by == PRIMARY

    def test_an_uploaded_certificate_serves_the_shared_names(
        self, machine: Machine, store: NoustStore
    ) -> None:
        machine.deploy()
        CertificateStore().set_settings(
            CertificateSettings(domain=PRIMARY, source="custom", name="origin")
        )

        found = {b.name: b for b in name_certificates.bindings(PRIMARY)}

        assert found[SHOP].served_by == "origin" and found[SHOP].source == "custom"

    def test_every_application_is_listed(self, machine: Machine, store: NoustStore) -> None:
        machine.deploy()
        store.create_app(App(domain="other.example.org", app_type="static"))

        names = {b.name for b in name_certificates.all_bindings()}

        assert {PRIMARY, ADMIN, "other.example.org"} <= names
