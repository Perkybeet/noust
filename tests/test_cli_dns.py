# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``noust dns``.

The managers decide (``tests/test_dns_accounts.py``, ``tests/test_dns_records.py``).
Pinned here is the translation and what the operator is told: a credential never
travels as an argument and never appears in the output, a conflict names the record
and the exact command that replaces it, a plan changes nothing, the two certbot hooks
read the order from certbot's environment, and a refusal is exit code 1 with the
operation's own words.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.cli.commands import dns as dns_cli
from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.logger import Logger
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.deployers.domains import DnsCheck
from noust.managers.dns.accounts import DnsAccounts, secret_name
from noust.managers.dns.challenge import DnsChallenge
from noust.managers.dns.records import ManagedDns
from tests.dns_support import Cloudflare, FakeProvider, zones

TOKEN = "cf-token-" + "x" * 31
PUBLIC = "8.8.4.4"
APP = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(App(domain=APP, app_path=f"/var/www/apps/{APP}"))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "state" / "secrets")


@pytest.fixture
def cloudflare() -> Cloudflare:
    return Cloudflare()


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider("example.com")


@pytest.fixture
def wired(
    store: NoustStore,
    secrets: SecretStore,
    cloudflare: Cloudflare,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point the command at a temporary store and a provider that lives in memory."""

    def accounts() -> DnsAccounts:
        return DnsAccounts(store=store, secrets=secrets, opener=cloudflare)

    def managed() -> ManagedDns:
        return ManagedDns(accounts=accounts(), store=store, label="srv")

    monkeypatch.setattr(dns_cli, "DnsAccounts", accounts)
    monkeypatch.setattr(dns_cli, "ManagedDns", managed)
    monkeypatch.setattr(DnsAccounts, "provider", lambda self, name: provider)
    monkeypatch.setattr(dns_cli, "server_addresses", lambda: [PUBLIC])
    DnsStore(store).save_account(DnsAccount(name="cf", kind="cloudflare", automatic=True))


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def invoke(args: list[str], input_text: str | None = None) -> Result:
    return CliRunner().invoke(root_cli, args, input=input_text)


# -- accounts ---------------------------------------------------------------------


def test_a_token_read_from_standard_input_is_kept_and_never_printed(
    wired: None,
    store: NoustStore,
    secrets: SecretStore,
    cloudflare: Cloudflare,
    log: list[str],
) -> None:
    cloudflare.say(zones("example.com"))

    result = invoke(
        ["dns", "provider", "add", "main", "--kind", "cloudflare", "--token-stdin"],
        input_text=TOKEN + "\n",
    )

    assert result.exit_code == 0, result.output
    assert secrets.read(secret_name("main")) == TOKEN
    assert TOKEN not in result.output
    assert not any(TOKEN in line for line in log)
    assert DnsStore(store).get_account("main") is not None


def test_a_credential_in_an_argument_is_refused_and_says_where_it_goes(wired: None) -> None:
    result = invoke(
        ["dns", "provider", "add", "main", "--kind", "cloudflare", "--set", f"token={TOKEN}"]
    )

    assert result.exit_code == 2
    assert "does not go in an argument" in result.output
    assert TOKEN not in result.output


def test_a_pair_without_a_value_is_a_usage_error(wired: None) -> None:
    result = invoke(["dns", "provider", "add", "main", "--kind", "cloudflare", "--set", "account"])

    assert result.exit_code == 2
    assert "name=value" in result.output


def test_every_field_can_come_from_a_json_object_on_standard_input(
    wired: None, secrets: SecretStore, cloudflare: Cloudflare
) -> None:
    cloudflare.say(zones("example.com"))

    result = invoke(
        ["dns", "provider", "add", "main", "--kind", "cloudflare", "--credentials-stdin"],
        input_text=json.dumps({"token": TOKEN}),
    )

    assert result.exit_code == 0, result.output
    assert secrets.read(secret_name("main")) == TOKEN


def test_standard_input_that_is_not_json_is_a_usage_error(wired: None) -> None:
    result = invoke(
        ["dns", "provider", "add", "main", "--kind", "cloudflare", "--credentials-stdin"],
        input_text="not json",
    )

    assert result.exit_code == 2
    assert "JSON" in result.output


def test_adding_an_account_without_a_credential_is_a_refusal(wired: None, log: list[str]) -> None:
    assert main(["dns", "provider", "add", "main", "--kind", "cloudflare"]) == 1


def test_a_rehearsal_adds_nothing(wired: None, store: NoustStore) -> None:
    result = invoke(
        ["--dry-run", "dns", "provider", "add", "main", "--kind", "cloudflare", "--token-stdin"],
        input_text=TOKEN + "\n",
    )

    assert result.exit_code == 0, result.output
    assert DnsStore(store).get_account("main") is None


def test_the_accounts_are_listed_without_their_credentials(wired: None) -> None:
    result = invoke(["dns", "provider", "list", "--json"])

    assert result.exit_code == 0, result.output
    (account,) = json.loads(result.output)["accounts"]
    assert account["name"] == "cf" and account["kind"] == "cloudflare"
    assert account["automatic"] is True
    assert "token" not in result.output


def test_verifying_an_account_prints_the_zones_it_manages(wired: None, log: list[str]) -> None:
    # The command's help promises the zones; they were logged as sub-steps, which are shown with
    # --verbose only, so `verify` said "works" and nothing else (found against a real provider).
    result = invoke(["dns", "provider", "verify", "cf"])

    assert result.exit_code == 0, result.output
    assert any("cf works" in line for line in log)
    assert any("example.com" in line for line in log)


def test_verifying_an_account_can_print_the_zones_as_json(wired: None) -> None:
    result = invoke(["dns", "provider", "verify", "cf", "--json"])

    assert result.exit_code == 0, result.output
    answer = json.loads(result.output)
    assert answer["account"] == "cf" and [zone["name"] for zone in answer["zones"]] == [
        "example.com"
    ]


def test_the_kinds_say_which_ones_do_records_and_which_only_the_challenge(wired: None) -> None:
    result = invoke(["dns", "provider", "kinds", "--json"])

    kinds = json.loads(result.output)
    assert kinds["cloudflare"]["native"] is True
    assert any(not spec["native"] for spec in kinds.values())


def test_changing_nothing_about_an_account_is_a_usage_error(wired: None) -> None:
    result = invoke(["dns", "provider", "set", "cf"])

    assert result.exit_code == 2


def test_an_account_can_be_made_to_ask_and_to_wait_longer(wired: None, store: NoustStore) -> None:
    result = invoke(["dns", "provider", "set", "cf", "--ask", "--propagation", "45"])

    assert result.exit_code == 0, result.output
    account = DnsStore(store).get_account("cf")
    assert account is not None
    assert account.automatic is False
    assert account.settings["propagation_seconds"] == 45


def test_removing_an_account_asks_unless_told_not_to(wired: None, store: NoustStore) -> None:
    declined = invoke(["dns", "provider", "remove", "cf"], input_text="n\n")
    assert declined.exit_code == 0
    assert DnsStore(store).get_account("cf") is not None

    confirmed = invoke(["dns", "provider", "remove", "cf", "--yes"])
    assert confirmed.exit_code == 0, confirmed.output
    assert DnsStore(store).get_account("cf") is None


# -- records ----------------------------------------------------------------------


def test_a_plan_says_what_would_be_done_and_changes_nothing(
    wired: None, provider: FakeProvider
) -> None:
    result = invoke(["dns", "ensure", APP, "api.example.com", "--plan", "--json"])

    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)
    assert (plan["hostname"], plan["content"]) == ("api.example.com", PUBLIC)
    assert provider.calls == []


def test_a_rehearsal_is_a_plan(wired: None, provider: FakeProvider) -> None:
    result = invoke(["--dry-run", "dns", "ensure", APP, "api.example.com"])

    assert result.exit_code == 0, result.output
    assert provider.calls == []


def test_ensuring_creates_the_record_and_remembers_it_is_ours(
    wired: None, provider: FakeProvider, store: NoustStore
) -> None:
    result = invoke(["dns", "ensure", APP, "api.example.com", "--json"])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["outcome"] == "created"
    assert provider.holds("example.com", "api.example.com", "A", PUBLIC)
    listing = json.loads(invoke(["dns", "records", "--json"]).output)
    assert [r["hostname"] for r in listing["records"]] == ["api.example.com"]
    assert listing["records"][0]["applications"] == [APP]


def test_a_record_in_the_way_is_a_conflict_that_names_it_and_the_command_that_replaces_it(
    wired: None, provider: FakeProvider, log: list[str]
) -> None:
    other = provider.seed("example.com", "api.example.com", "A", "9.9.9.9")

    result = invoke(["dns", "ensure", APP, "api.example.com"])

    assert result.exit_code == 1
    assert provider.holds("example.com", "api.example.com", "A", "9.9.9.9")
    said = " ".join(log)
    assert "9.9.9.9" in said
    assert f"--replace-record {other.id} --replace-current 9.9.9.9" in said


def test_the_confirmation_of_exactly_that_record_replaces_it(
    wired: None, provider: FakeProvider
) -> None:
    other = provider.seed("example.com", "api.example.com", "A", "9.9.9.9")

    result = invoke(
        [
            "dns",
            "ensure",
            APP,
            "api.example.com",
            "--replace-record",
            other.id,
            "--replace-current",
            "9.9.9.9",
        ]
    )

    assert result.exit_code == 0, result.output
    assert provider.holds("example.com", "api.example.com", "A", PUBLIC)


def test_without_a_public_address_it_says_how_to_declare_one(
    wired: None, monkeypatch: pytest.MonkeyPatch, log: list[str]
) -> None:
    monkeypatch.setattr(dns_cli, "server_addresses", lambda: [])

    assert main(["dns", "ensure", APP, "api.example.com"]) == 1
    assert any("dns.public_addresses" in line for line in log)


def test_releasing_deletes_what_noust_made_and_keep_deletes_nothing(
    wired: None, provider: FakeProvider
) -> None:
    invoke(["dns", "ensure", APP, "api.example.com"])

    kept = invoke(["dns", "release", APP, "--keep", "--json"])
    assert kept.exit_code == 0, kept.output
    assert provider.holds("example.com", "api.example.com", "A", PUBLIC)

    invoke(["dns", "ensure", APP, "api.example.com"])
    released = invoke(["dns", "release", APP, "api.example.com", "--json"])
    assert released.exit_code == 0, released.output
    assert not provider.holds("example.com", "api.example.com", "A", PUBLIC)


def test_releasing_with_nothing_to_release_says_so(wired: None, log: list[str]) -> None:
    result = invoke(["dns", "release", APP])

    assert result.exit_code == 0, result.output
    assert any("Nothing to release" in line for line in log)


def test_a_sweep_with_nothing_to_sweep_says_so(wired: None, log: list[str]) -> None:
    result = invoke(["dns", "sweep"])

    assert result.exit_code == 0, result.output
    assert any("Nothing to sweep" in line for line in log)


# -- the check --------------------------------------------------------------------


def test_the_check_asks_the_resolvers_it_was_given_and_prints_the_verdict(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: dict[str, Any] = {}

    def fake_check(name: str, resolvers: Any = None) -> DnsCheck:
        asked.update(name=name, resolvers=resolvers)
        return DnsCheck(
            domain=name,
            expected_addresses=[PUBLIC],
            resolved_addresses=["104.16.0.1"],
            points_here=True,
            verdict="cdn",
            cdn="Cloudflare",
            resolver="1.1.1.1",
        )

    monkeypatch.setattr(dns_cli, "check_dns", fake_check)

    result = invoke(["dns", "check", "shop.example.com", "--resolver", "1.1.1.1", "--json"])

    assert result.exit_code == 0, result.output
    assert asked == {"name": "shop.example.com", "resolvers": ("1.1.1.1",)}
    body = json.loads(result.output)
    assert (body["verdict"], body["cdn"], body["resolver"]) == ("cdn", "Cloudflare", "1.1.1.1")


def test_a_name_behind_nat_says_how_to_declare_the_address(
    wired: None, monkeypatch: pytest.MonkeyPatch, log: list[str]
) -> None:
    monkeypatch.setattr(
        dns_cli,
        "check_dns",
        lambda name, resolvers=None: DnsCheck(
            domain=name,
            expected_addresses=[],
            resolved_addresses=["8.8.4.4"],
            points_here=False,
            verdict="unknown",
        ),
    )

    result = invoke(["dns", "check", "shop.example.com"])

    assert result.exit_code == 0, result.output
    assert any("dns.public_addresses" in line for line in log)


# -- certbot's hooks --------------------------------------------------------------


def test_the_hooks_read_the_order_from_certbots_environment_and_not_from_arguments(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str, str, str]] = []

    def present(self: DnsChallenge, account: str, domain: str, validation: str) -> str:
        seen.append(("present", account, domain, validation))
        return domain

    def cleanup(self: DnsChallenge, account: str, domain: str, validation: str) -> int:
        seen.append(("cleanup", account, domain, validation))
        return 1

    monkeypatch.setattr(DnsChallenge, "present", present)
    monkeypatch.setattr(DnsChallenge, "cleanup", cleanup)
    environment = {"CERTBOT_DOMAIN": "example.com", "CERTBOT_VALIDATION": "A" * 43}

    first = CliRunner().invoke(
        root_cli, ["dns", "challenge", "auth", "--account", "cf"], env=environment
    )
    second = CliRunner().invoke(
        root_cli, ["dns", "challenge", "cleanup", "--account", "cf"], env=environment
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert seen == [
        ("present", "cf", "example.com", "A" * 43),
        ("cleanup", "cf", "example.com", "A" * 43),
    ]


def test_the_hooks_refuse_to_run_outside_certbot(
    wired: None, monkeypatch: pytest.MonkeyPatch, log: list[str]
) -> None:
    monkeypatch.delenv("CERTBOT_DOMAIN", raising=False)
    monkeypatch.delenv("CERTBOT_VALIDATION", raising=False)

    assert main(["dns", "challenge", "auth", "--account", "cf"]) == 1
    assert any("CERTBOT_DOMAIN" in line for line in log)


def test_the_hooks_are_not_advertised(wired: None) -> None:
    result = invoke(["dns", "--help"])

    commands = [line.split()[0] for line in result.output.splitlines() if line.startswith("  ")]
    assert "challenge" not in commands
    assert "ensure" in commands


def test_the_kinds_say_what_each_one_does(wired: None) -> None:
    kinds = json.loads(invoke(["dns", "provider", "kinds", "--json"]).output)

    assert kinds["cloudflare"]["tunnels"] is True
    assert kinds["route53"]["native"] is True and kinds["route53"]["comments"] is False
    assert kinds["hetzner"]["comments"] is True and kinds["hetzner"]["tunnels"] is False
    assert kinds["route53"]["fields"] == ["access_key_id", "secret_access_key"]
    table = invoke(["dns", "provider", "kinds"]).output
    assert "records, challenge, tunnels" in table
    assert "challenge (certbot plugin)" in table


def test_a_route53_key_is_a_name_in_an_argument_and_a_secret_on_standard_input(
    wired: None,
    store: NoustStore,
    secrets: SecretStore,
    monkeypatch: pytest.MonkeyPatch,
    log: list[str],
) -> None:
    from tests.dns_wires import ACCESS_KEY, SECRET_KEY, Route53Wire

    wire = Route53Wire("example.com")
    monkeypatch.setattr(
        dns_cli, "DnsAccounts", lambda: DnsAccounts(store=store, secrets=secrets, opener=wire)
    )

    result = invoke(
        [
            "dns",
            "provider",
            "add",
            "aws",
            "--kind",
            "route53",
            "--set",
            f"access_key_id={ACCESS_KEY}",
            "--token-stdin",
        ],
        input_text=SECRET_KEY + "\n",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(secrets.read(secret_name("aws")))["secret_access_key"] == SECRET_KEY
    assert SECRET_KEY not in result.output and not any(SECRET_KEY in line for line in log)
    assert "noust cert use" in "".join(log) + result.output


def test_the_secret_half_of_a_route53_key_does_not_go_in_an_argument(wired: None) -> None:
    from tests.dns_wires import SECRET_KEY

    result = invoke(
        [
            "dns",
            "provider",
            "add",
            "aws",
            "--kind",
            "route53",
            "--set",
            f"secret_access_key={SECRET_KEY}",
        ]
    )

    assert result.exit_code == 2
    assert "does not go in an argument" in result.output
    assert SECRET_KEY not in result.output
