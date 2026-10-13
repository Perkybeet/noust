# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ownership of DNS records: when Noust may create, adopt, replace and delete one.

A record deleted that Noust did not make is an outage somebody else caused, so the
tests are the rules: only a record created here, still carrying its marker and its
content, is deleted; one that was there is adopted and left alone; a different record
in the way is a conflict until the operator confirms exactly it; a record shared by two
applications goes with the last; and an address that cannot be public is never
published. The provider is an in-memory account (``tests/dns_support.py``).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.applock import AppBusyError, app_lock
from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.exceptions import DomainConflictError, ValidationError
from noust.core.store import App, NoustStore
from noust.managers.dns import records as records_module
from noust.managers.dns.accounts import DnsAccounts
from noust.managers.dns.provider import DnsProviderError
from noust.managers.dns.records import (
    DnsRecordConflict,
    Ensured,
    ManagedDns,
    Release,
    ownership_comment,
)
from tests.dns_support import FakeProvider

PUBLIC = "8.8.4.4"
OTHER = "9.9.9.9"
MARKER = "managed-by: noust srv/"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for name in ("shop.example.com", "blog.example.com"):
        instance.create_app(App(domain=name, app_path=f"/var/www/apps/{name}"))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider("example.com")


@pytest.fixture
def dns(store: NoustStore, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch) -> ManagedDns:
    DnsStore(store).save_account(DnsAccount(name="cf", kind="cloudflare", automatic=True))
    monkeypatch.setattr(DnsAccounts, "provider", lambda self, name: provider)
    return ManagedDns(accounts=DnsAccounts(store=store), store=store, label="srv")


def hostname_count(provider: FakeProvider) -> int:
    return len(provider.records["zone-example.com"])


# -- creating ---------------------------------------------------------------------


def test_a_record_is_created_with_the_marker_and_remembered_as_ours(
    dns: ManagedDns, provider: FakeProvider, store: NoustStore
) -> None:
    result = dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")

    assert result.outcome is Ensured.CREATED
    (remote,) = provider.records["zone-example.com"]
    assert (remote.type, remote.name, remote.content) == ("A", "api.example.com", PUBLIC)
    assert remote.comment == ownership_comment(result.record, "srv")
    assert remote.comment is not None and remote.comment.startswith(MARKER)
    assert result.record.owned is True
    assert DnsStore(store).refs(result.record.id or 0) == ["shop.example.com"]


def test_an_ipv6_address_makes_an_aaaa_record_and_a_name_a_cname(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    dns.ensure("api.example.com", "2606:4700:4700::1111", app_domain="shop.example.com")
    dns.ensure(
        "go.example.com",
        "abc.cfargotunnel.com",
        app_domain="shop.example.com",
        record_type="CNAME",
        proxied=True,
    )

    kinds = sorted((r.type, r.name) for r in provider.records["zone-example.com"])
    assert kinds == [("AAAA", "api.example.com"), ("CNAME", "go.example.com")]
    cname = next(r for r in provider.records["zone-example.com"] if r.type == "CNAME")
    assert cname.proxied is True


@pytest.mark.parametrize(
    "address",
    ["10.0.0.5", "192.168.1.9", "127.0.0.1", "100.64.3.2", "169.254.1.1", "224.0.0.1", "fd00::1"],
)
def test_an_address_that_cannot_be_public_is_never_published(
    dns: ManagedDns, provider: FakeProvider, address: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        dns.ensure("api.example.com", address, app_domain="shop.example.com")

    assert raised.value.field == "content"
    assert "public" in raised.value.message
    assert provider.calls == []


def test_a_name_that_is_not_one_and_a_family_that_does_not_match_are_refused(
    dns: ManagedDns,
) -> None:
    with pytest.raises(ValidationError):
        dns.ensure("not a host", PUBLIC, app_domain="shop.example.com")
    with pytest.raises(ValidationError):
        dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com", record_type="AAAA")
    with pytest.raises(ValidationError):
        dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com", record_type="TXT")


def test_no_account_holding_the_zone_is_said_plainly(dns: ManagedDns) -> None:
    with pytest.raises(ValidationError) as raised:
        dns.ensure("api.elsewhere.org", PUBLIC, app_domain="shop.example.com")

    assert "No DNS account holds the zone" in raised.value.message


def test_a_wildcard_name_belongs_to_the_zone_of_its_base(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    result = dns.ensure("*.example.com", PUBLIC, app_domain="shop.example.com")

    assert result.hostname == "*.example.com"
    assert provider.holds("example.com", "*.example.com", "A", PUBLIC)


# -- adopting and replacing -------------------------------------------------------


def test_a_record_that_was_already_right_is_adopted_and_never_ours(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    provider.seed("example.com", "api.example.com", "A", PUBLIC)

    result = dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")

    assert result.outcome is Ensured.ADOPTED
    assert result.record.owned is False
    assert provider.calls == []  # nothing was written


def test_a_different_record_in_the_way_is_a_conflict_until_confirmed(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    stale = provider.seed("example.com", "api.example.com", "A", OTHER)

    with pytest.raises(DnsRecordConflict) as raised:
        dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")

    assert isinstance(raised.value, DomainConflictError)
    assert raised.value.record_id == stale.id
    assert raised.value.current == OTHER
    assert provider.calls == []
    assert dns.plan("api.example.com", PUBLIC).action == "conflict"


def test_replacing_changes_only_the_content_and_does_not_make_the_record_ours(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    stale = provider.seed("example.com", "api.example.com", "A", OTHER, comment="mine, by hand")

    result = dns.ensure(
        "api.example.com",
        PUBLIC,
        app_domain="shop.example.com",
        replace_record=stale.id,
        replace_current=OTHER,
    )

    assert result.outcome is Ensured.REPLACED
    assert result.record.owned is False
    (remote,) = provider.records["zone-example.com"]
    assert (remote.content, remote.comment) == (PUBLIC, "mine, by hand")
    assert provider.calls == [("update", f"api.example.com {PUBLIC}")]


def test_a_confirmation_for_a_record_that_changed_meanwhile_no_longer_applies(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    stale = provider.seed("example.com", "api.example.com", "A", "1.1.1.1")

    with pytest.raises(DnsRecordConflict):
        dns.ensure(
            "api.example.com",
            PUBLIC,
            app_domain="shop.example.com",
            replace_record=stale.id,
            replace_current=OTHER,  # what the operator saw; it is something else now
        )
    with pytest.raises(DnsRecordConflict):
        dns.ensure(
            "api.example.com",
            PUBLIC,
            app_domain="shop.example.com",
            replace_record="rec-other",
            replace_current="1.1.1.1",
        )
    assert provider.calls == []


def test_several_records_at_one_name_are_never_touched(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    provider.seed("example.com", "api.example.com", "A", OTHER)
    provider.seed("example.com", "api.example.com", "A", "1.1.1.1")

    with pytest.raises(DnsProviderError) as raised:
        dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")

    assert "Several" in raised.value.message
    assert provider.calls == []


def test_ensuring_twice_is_the_same_record_used_by_two_applications(
    dns: ManagedDns, provider: FakeProvider, store: NoustStore
) -> None:
    first = dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")
    second = dns.ensure("api.example.com", PUBLIC, app_domain="blog.example.com")

    assert second.outcome is Ensured.EXISTS
    assert hostname_count(provider) == 1
    assert DnsStore(store).refs(first.record.id or 0) == ["blog.example.com", "shop.example.com"]


# -- releasing --------------------------------------------------------------------


def test_the_last_user_letting_go_deletes_the_record_we_made(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")
    dns.ensure("api.example.com", PUBLIC, app_domain="blog.example.com")

    first = dns.release("shop.example.com")
    assert [r.outcome for r in first] == [Release.STILL_USED]
    assert hostname_count(provider) == 1

    last = dns.release("blog.example.com")
    assert [r.outcome for r in last] == [Release.DELETED]
    assert hostname_count(provider) == 0


def test_an_adopted_record_is_forgotten_but_never_deleted(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    provider.seed("example.com", "api.example.com", "A", PUBLIC)
    dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")

    results = dns.release("shop.example.com")

    assert [r.outcome for r in results] == [Release.NOT_OWNED]
    assert hostname_count(provider) == 1
    assert not any(call[0] == "delete" for call in provider.calls)
    assert dns.records.list_records() == []


@pytest.mark.parametrize("what", ["content", "comment", "type"])
def test_a_record_somebody_edited_is_left_as_it_is(
    dns: ManagedDns, provider: FakeProvider, what: str
) -> None:
    result = dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")
    (remote,) = provider.records["zone-example.com"]
    edited = {
        "content": remote.__class__(**{**remote.__dict__, "content": OTHER}),
        "comment": remote.__class__(**{**remote.__dict__, "comment": "kept by a person"}),
        "type": remote.__class__(**{**remote.__dict__, "type": "AAAA"}),
    }[what]
    provider.records["zone-example.com"][0] = edited
    assert result.record.owned

    results = dns.release("shop.example.com")

    assert [r.outcome for r in results] == [Release.CHANGED_EXTERNALLY]
    assert hostname_count(provider) == 1
    assert not any(call[0] == "delete" for call in provider.calls)


def test_a_record_already_gone_is_forgotten(dns: ManagedDns, provider: FakeProvider) -> None:
    dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")
    provider.records["zone-example.com"].clear()

    assert [r.outcome for r in dns.release("shop.example.com")] == [Release.ALREADY_GONE]
    assert dns.records.list_records() == []


def test_a_name_another_application_still_answers_on_is_not_released(
    dns: ManagedDns, provider: FakeProvider, store: NoustStore
) -> None:
    dns.ensure("blog.example.com", PUBLIC, app_domain="shop.example.com")

    results = dns.release("shop.example.com")  # blog.example.com is an application

    assert [r.outcome for r in results] == [Release.STILL_USED]
    assert hostname_count(provider) == 1


def test_a_provider_failure_is_reported_and_the_row_kept_for_the_sweep(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")
    provider.fail_on = "delete"

    results = dns.release("shop.example.com")

    assert [r.outcome for r in results] == [Release.FAILED]
    assert len(dns.records.list_records()) == 1


def test_releasing_one_name_leaves_the_applications_other_records(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    dns.ensure("a.example.com", PUBLIC, app_domain="shop.example.com")
    dns.ensure("b.example.com", PUBLIC, app_domain="shop.example.com")

    dns.release("shop.example.com", hostname="a.example.com")

    assert not provider.holds("example.com", "a.example.com", "A", PUBLIC)
    assert provider.holds("example.com", "b.example.com", "A", PUBLIC)


# -- the sweep --------------------------------------------------------------------


def test_the_sweep_releases_what_nobody_has_used_for_an_hour_and_only_that(
    dns: ManagedDns, provider: FakeProvider, store: NoustStore
) -> None:
    old = dns.ensure("old.example.com", PUBLIC, app_domain="shop.example.com").record
    young = dns.ensure("young.example.com", PUBLIC, app_domain="shop.example.com").record
    used = dns.ensure("used.example.com", PUBLIC, app_domain="shop.example.com").record
    records = DnsStore(store)
    for record in (old, young):
        records.remove_ref(record.id or 0, "shop.example.com")
    with store._transaction() as cursor:
        cursor.execute(
            "UPDATE dns_records SET unreferenced_since = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), old.id),
        )

    results = dns.sweep()

    assert [(r.hostname, r.outcome) for r in results] == [("old.example.com", Release.DELETED)]
    assert provider.holds("example.com", "young.example.com", "A", PUBLIC)
    assert provider.holds("example.com", "used.example.com", "A", PUBLIC)
    assert used.id is not None


def test_a_record_used_only_by_a_deleted_application_is_swept(
    dns: ManagedDns, provider: FakeProvider, store: NoustStore
) -> None:
    record = dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com").record
    store.delete_app("shop.example.com")

    assert dns.sweep() == []  # its reference is dropped now; the grace period starts
    with store._transaction() as cursor:
        cursor.execute(
            "UPDATE dns_records SET unreferenced_since = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), record.id),
        )

    assert [r.outcome for r in dns.sweep()] == [Release.DELETED]


# -- one change at a time ---------------------------------------------------------


def test_a_change_to_a_name_waits_for_the_one_in_progress_and_then_gives_up(
    dns: ManagedDns, provider: FakeProvider
) -> None:
    held = threading.Event()
    done = threading.Event()

    def hold() -> None:
        with app_lock("dns-record.api.example.com", "DNS change"):
            held.set()
            done.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    ticks = iter(range(0, 1000))
    busy = ManagedDns(
        accounts=dns.accounts,
        label="srv",
        sleep=lambda seconds: None,
        monotonic=lambda: float(next(ticks)),
    )
    try:
        with pytest.raises(AppBusyError):
            busy.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")
    finally:
        done.set()
        holder.join(5)
    assert provider.calls == []


# -- for a domain -----------------------------------------------------------------


def test_adding_a_domain_points_it_at_every_public_address_of_the_server(
    dns: ManagedDns, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        records_module, "server_addresses", lambda: [PUBLIC, "2606:4700:4700::1111"]
    )

    entries = dns.ensure_for_domain("shop.example.com", "api.example.com")

    assert [(e["type"], e["outcome"]) for e in entries] == [("A", "created"), ("AAAA", "created")]
    assert provider.holds("example.com", "api.example.com", "AAAA", "2606:4700:4700::1111")


def test_an_account_that_asks_first_is_only_asked_what_it_would_do(
    dns: ManagedDns, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    DnsStore(dns.records.store).save_account(
        DnsAccount(name="cf", kind="cloudflare", automatic=False)
    )
    monkeypatch.setattr(records_module, "server_addresses", lambda: [PUBLIC])

    (entry,) = dns.ensure_for_domain("shop.example.com", "api.example.com")

    assert (entry["outcome"], entry["action"]) == ("pending", "create")
    assert provider.calls == []


def test_a_name_in_no_account_zone_and_a_conflict_are_reported_not_raised(
    dns: ManagedDns, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records_module, "server_addresses", lambda: [PUBLIC])
    provider.seed("example.com", "api.example.com", "A", OTHER)

    assert dns.ensure_for_domain("shop.example.com", "x.elsewhere.org") == []
    (entry,) = dns.ensure_for_domain("shop.example.com", "api.example.com")

    assert entry["outcome"] == "conflict"
    assert entry["current"] == OTHER


def test_a_server_with_no_public_address_says_how_to_declare_one(
    dns: ManagedDns, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records_module, "server_addresses", lambda: [])

    (entry,) = dns.ensure_for_domain("shop.example.com", "api.example.com")

    assert entry["outcome"] == "failed"
    assert "dns.public_addresses" in entry["detail"]


def test_the_declared_addresses_win_over_the_interfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Declared:
        def get(self, key: str, default: Any = None) -> Any:
            return [PUBLIC] if key == "dns.public_addresses" else default

    monkeypatch.setattr(records_module, "Config", Declared)
    monkeypatch.setattr(records_module, "public_addresses", lambda: ["1.2.3.4"])

    assert records_module.server_addresses() == [PUBLIC]
