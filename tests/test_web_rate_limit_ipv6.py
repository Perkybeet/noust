# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A rate limit and a lockout keyed on the exact IPv6 address are not a limit
(checklist L-37).

An IPv6 customer is routinely given a whole /64, 2^64 addresses, and can send
each request from a different one. Counting by exact address, the budget of
the anonymous limiter, the lockout of failed sign-ins and the webhook budget
were each fresh for every request, and the limiter's table, bounded to a
number of clients, was a way to push everyone else's history out of it. The
unit an attacker holds is the /64, so that is what is counted; the audit trail
and the session binding keep the exact address.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noust.web.auth import BruteForceProtection, RateLimiter, limiter_key
from noust.web.server import create_app
from tests.test_web_auth import make_config


class TestTheKey:
    def test_two_addresses_of_one_slash_64_are_one_client(self) -> None:
        a = limiter_key("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
        b = limiter_key("2001:db8:1:2::1")

        assert a == b == "2001:db8:1:2::/64"

    def test_another_slash_64_is_another_client(self) -> None:
        assert limiter_key("2001:db8:1:3::1") != limiter_key("2001:db8:1:2::1")

    @pytest.mark.parametrize(
        "identifier",
        [
            "203.0.113.7",
            "127.0.0.1",
            "::1",
            "unknown",
            "",
            "hooks:203.0.113.7",
            "sign-in:203.0.113.7:account:alice",
            "noust-token-ci",
        ],
    )
    def test_everything_else_is_counted_as_it_was(self, identifier: str) -> None:
        assert limiter_key(identifier) == identifier

    def test_an_ipv4_mapped_address_is_the_ipv4_one(self) -> None:
        assert limiter_key("::ffff:203.0.113.7") == "203.0.113.7"

    def test_a_scoped_link_local_address_is_still_one_client(self) -> None:
        assert limiter_key("fe80::1%eth0") == limiter_key("fe80::2") == "fe80::/64"


class TestTheRateLimiter:
    def test_rotating_inside_a_slash_64_does_not_buy_a_fresh_budget(self) -> None:
        limiter = RateLimiter(max_requests=3, window=60)

        allowed = [limiter.is_allowed(f"2001:db8:1:2::{n:x}") for n in range(1, 8)]

        assert allowed == [True, True, True, False, False, False, False]
        assert limiter.retry_after("2001:db8:1:2:ffff::9") > 0
        assert limiter.get_remaining("2001:db8:1:2::abcd") == 0
        assert limiter.tracked_clients() == 1

    def test_another_slash_64_has_its_own_budget(self) -> None:
        limiter = RateLimiter(max_requests=1, window=60)
        limiter.is_allowed("2001:db8:1:2::1")

        assert limiter.is_allowed("2001:db8:1:3::1")

    def test_a_flood_from_one_network_cannot_push_the_others_out_of_the_table(self) -> None:
        limiter = RateLimiter(max_requests=5, window=60, max_tracked=10)
        limiter.is_allowed("203.0.113.7")

        for n in range(500):
            limiter.is_allowed(f"2001:db8:9:9:{n:x}::1")

        assert limiter.get_remaining("203.0.113.7") == 4, "the other client's history survived"
        assert limiter.tracked_clients() == 2

    def test_reset_forgets_the_whole_network(self) -> None:
        limiter = RateLimiter(max_requests=1, window=60)
        limiter.is_allowed("2001:db8:1:2::1")

        limiter.reset("2001:db8:1:2::99")

        assert limiter.is_allowed("2001:db8:1:2::7")


class TestTheLockout:
    def test_failures_from_rotating_addresses_lock_the_network_out(self) -> None:
        brute = BruteForceProtection(max_attempts=3, lockout_duration=60)

        for n in range(1, 4):
            brute.record_failure(f"2001:db8:1:2::{n:x}")

        assert brute.is_locked("2001:db8:1:2:dead:beef::1")
        assert brute.get_lockout_remaining("2001:db8:1:2::ffff") > 0
        assert brute.get_attempts_remaining("2001:db8:1:2::ffff") == 0

    def test_another_network_is_not_locked(self) -> None:
        brute = BruteForceProtection(max_attempts=1, lockout_duration=60)
        brute.record_failure("2001:db8:1:2::1")

        assert not brute.is_locked("2001:db8:1:3::1")

    def test_a_success_clears_the_network(self) -> None:
        brute = BruteForceProtection(max_attempts=3, lockout_duration=60)
        brute.record_failure("2001:db8:1:2::1")

        brute.record_success("2001:db8:1:2::5")

        assert brute.get_attempts_remaining("2001:db8:1:2::1") == 3

    def test_ipv4_is_counted_as_before(self) -> None:
        brute = BruteForceProtection(max_attempts=2, lockout_duration=60)
        brute.record_failure("203.0.113.7")
        brute.record_failure("203.0.113.7")

        assert brute.is_locked("203.0.113.7") and not brute.is_locked("203.0.113.8")


class TestThroughTheConsole:
    """The middleware, with a client that picks a new source address for every request."""

    def attacker(self, sandbox: Path, **limits: int) -> tuple[TestClient, list[TestClient]]:
        app = create_app(make_config(sandbox, **limits))
        return (
            TestClient(app, client=("203.0.113.9", 50000)),
            [TestClient(app, client=(f"2001:db8:aa:bb::{n:x}", 50000)) for n in range(1, 8)],
        )

    def test_the_anonymous_budget_holds_across_a_slash_64(self, sandbox: Path) -> None:
        _, rotating = self.attacker(sandbox, rate_limit_requests=3)

        statuses = [client.get("/api/apps").status_code for client in rotating]

        assert statuses[:3] == [401, 401, 401]
        assert statuses[3:] == [429, 429, 429, 429]

    def test_a_refusal_names_when_to_come_back(self, sandbox: Path) -> None:
        _, rotating = self.attacker(sandbox, rate_limit_requests=1)
        rotating[0].get("/api/apps")

        refused = rotating[1].get("/api/apps")

        assert refused.status_code == 429
        assert int(refused.headers["Retry-After"]) >= 1

    def test_the_webhook_budget_holds_across_a_slash_64_too(self, sandbox: Path) -> None:
        _, rotating = self.attacker(sandbox, rate_limit_requests=2)

        statuses = [
            client.post("/hooks/deploy/example.com", content=b"{}").status_code
            for client in rotating
        ]

        assert 429 in statuses[2:]
        assert 429 not in statuses[:2]

    def test_an_ipv4_client_is_not_affected(self, sandbox: Path) -> None:
        single, _ = self.attacker(sandbox, rate_limit_requests=2)

        statuses = [single.get("/api/apps").status_code for _ in range(3)]

        assert statuses == [401, 401, 429]
