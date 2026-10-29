# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The access to a code host that a central leases to a node: what the node keeps of it.

What is pinned: the token is a secret file sealed under the node's own key and never a column of
the store; a lease lives for the seconds the central said, counted on the node's own clock; it
covers exactly the repositories it was given, on the host it was given; a lease that ended is not
used and is found afterwards, so that the error of a clone can say whose it was; and a person or a
central cannot make the node keep a token it should not.
"""

from __future__ import annotations

import sqlite3

import pytest

from noust.core.exceptions import ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.integrations.github.leases import MAX_LIFETIME, SourceLeases

TOKEN = "ghs_Abc123_leased-token"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def leases(store: NoustStore, secrets: SecretStore, clock: Clock) -> SourceLeases:
    return SourceLeases(store, secrets, clock=clock)


def hold(leases: SourceLeases, **kw: object) -> object:
    values: dict[str, object] = {
        "central": "nas",
        "key": "github.com:77:0",
        "host": "github.com",
        "installation_id": 77,
        "repositories": ["you/app", "you/api"],
        "token": TOKEN,
        "expires_in": 3600,
    }
    values.update(kw)
    return leases.hold(**values)  # type: ignore[arg-type]


class TestKeepingATokenSealed:
    def test_the_token_is_a_sealed_secret_and_no_column_of_the_store(
        self, leases: SourceLeases, store: NoustStore, secrets: SecretStore
    ) -> None:
        held = hold(leases)

        rows = store._get_connection().execute("SELECT * FROM source_leases").fetchall()
        assert TOKEN not in " ".join(str(value) for row in rows for value in tuple(row))
        raw = secrets.read(held.token_name)  # type: ignore[attr-defined]
        assert raw is not None and TOKEN not in raw
        assert raw.startswith("noust-secret:v1:")

    def test_the_token_is_read_back_by_the_one_that_holds_it(self, leases: SourceLeases) -> None:
        held = hold(leases)

        assert leases.token_of(held) == TOKEN  # type: ignore[arg-type]

    def test_a_lease_listed_does_not_say_the_token(self, leases: SourceLeases) -> None:
        hold(leases)

        shown = [lease.to_dict() for lease in leases.list()]

        assert TOKEN not in str(shown)
        assert shown[0]["repositories"] == ["you/api", "you/app"]
        assert shown[0]["central"] == "nas"

    def test_holding_again_replaces_the_lease_and_its_secret(
        self, leases: SourceLeases, secrets: SecretStore
    ) -> None:
        first = hold(leases)
        second = hold(leases, token="ghs_second", repositories=["you/app"])

        assert len(leases.list()) == 1
        assert leases.token_of(second) == "ghs_second"  # type: ignore[arg-type]
        assert second.repositories == ("you/app",)  # type: ignore[attr-defined]
        assert first.token_name == second.token_name  # type: ignore[attr-defined]
        assert secrets.read(first.token_name) is not None  # type: ignore[attr-defined]


class TestHowLongItLives:
    def test_it_ends_the_seconds_the_central_said_after_it_arrived_on_the_nodes_clock(
        self, leases: SourceLeases, clock: Clock
    ) -> None:
        held = hold(leases, expires_in=1800)

        assert held.expires_at == clock.now + 1800  # type: ignore[attr-defined]
        clock.now += 1799
        assert leases.usable("github.com", "you/app") is not None
        clock.now += 2
        assert leases.usable("github.com", "you/app") is None

    @pytest.mark.parametrize("seconds", [0, -5, MAX_LIFETIME + 1, 10**9, True, "3600", None])
    def test_a_lifetime_that_is_not_a_short_positive_number_of_seconds_is_refused(
        self, leases: SourceLeases, seconds: object
    ) -> None:
        with pytest.raises(ValidationError):
            hold(leases, expires_in=seconds)

        assert leases.list() == []

    def test_an_ended_lease_is_found_afterwards_by_the_repository_it_covered(
        self, leases: SourceLeases, clock: Clock
    ) -> None:
        hold(leases)
        clock.now += 3601

        assert leases.usable("github.com", "you/app") is None
        found = leases.expired("github.com", "you/app")
        assert found is not None and found.central == "nas"
        assert leases.expired("github.com", "you/other") is None

    def test_a_lease_that_ended_long_ago_is_forgotten_with_its_secret(
        self, leases: SourceLeases, clock: Clock, secrets: SecretStore
    ) -> None:
        held = hold(leases)
        clock.now += 3600 + 2 * 86400

        assert leases.purge() == 1

        assert leases.list() == []
        assert secrets.read(held.token_name) is None  # type: ignore[attr-defined]

    def test_a_lease_that_ended_a_moment_ago_is_kept_to_explain_the_failure(
        self, leases: SourceLeases, clock: Clock
    ) -> None:
        hold(leases)
        clock.now += 3600 + 60

        assert leases.purge() == 0
        assert len(leases.list()) == 1


class TestWhatItCovers:
    def test_exactly_the_repositories_it_was_given_on_the_host_it_was_given(
        self, leases: SourceLeases
    ) -> None:
        hold(leases)

        assert leases.usable("github.com", "you/app") is not None
        assert leases.usable("github.com", "YOU/App") is not None
        assert leases.usable("github.com", "you/other") is None
        assert leases.usable("ghe.example.com", "you/app") is None

    def test_the_lease_that_lasts_longest_is_the_one_used(
        self, leases: SourceLeases, clock: Clock
    ) -> None:
        hold(leases, key="a", token="ghs_short", expires_in=600)
        hold(leases, key="b", token="ghs_long", expires_in=3000)

        found = leases.usable("github.com", "you/app")

        assert found is not None and leases.token_of(found) == "ghs_long"

    def test_two_centrals_leases_do_not_replace_each_other(self, leases: SourceLeases) -> None:
        hold(leases, central="nas")
        hold(leases, central="edge", token="ghs_edge")

        assert len(leases.list()) == 2

    def test_dropping_a_central_drops_its_leases_and_their_secrets_only(
        self, leases: SourceLeases, secrets: SecretStore
    ) -> None:
        mine = hold(leases, central="nas")
        other = hold(leases, central="edge", token="ghs_edge")

        assert leases.drop("nas") == 1

        assert [lease.central for lease in leases.list()] == ["edge"]
        assert secrets.read(mine.token_name) is None  # type: ignore[attr-defined]
        assert secrets.read(other.token_name) is not None  # type: ignore[attr-defined]

    def test_a_lease_of_a_key_can_be_dropped_alone(self, leases: SourceLeases) -> None:
        hold(leases, key="a")
        hold(leases, key="b", token="ghs_b")

        assert leases.drop("nas", "a") == 1

        assert [lease.key for lease in leases.list()] == ["b"]


class TestWhatIsRefused:
    @pytest.mark.parametrize(
        "token",
        ["", " ", "ghs_a b", "ghs_a\nb", "ghs_a\r\nX-Evil: 1", "ghs_é", "x" * 513, None, 5],
    )
    def test_a_token_that_is_not_one_is_refused(self, leases: SourceLeases, token: object) -> None:
        with pytest.raises(ValidationError):
            hold(leases, token=token)

        assert leases.list() == []

    @pytest.mark.parametrize(
        "repositories",
        [
            [],
            "you/app",
            ["you"],
            ["you/app\n"],
            ["../x"],
            ["you/.."],
            ["you/app", 5],
            [f"you/r{n}" for n in range(1001)],
        ],
    )
    def test_repositories_that_are_not_a_list_of_repositories_are_refused(
        self, leases: SourceLeases, repositories: object
    ) -> None:
        with pytest.raises(ValidationError):
            hold(leases, repositories=repositories)

    @pytest.mark.parametrize("host", ["", "https://github.com", "github.com/x", "a b", "x@y"])
    def test_a_host_that_is_not_a_host_is_refused(self, leases: SourceLeases, host: str) -> None:
        with pytest.raises(ValidationError):
            hold(leases, host=host)

    @pytest.mark.parametrize("installation", [0, -1, "7", True, None])
    def test_an_installation_that_is_not_a_positive_number_is_refused(
        self, leases: SourceLeases, installation: object
    ) -> None:
        with pytest.raises(ValidationError):
            hold(leases, installation_id=installation)

    @pytest.mark.parametrize("key", ["", "a/b", "a b", "a" * 81, "../x", "a\n"])
    def test_a_key_that_is_not_a_plain_name_is_refused(
        self, leases: SourceLeases, key: str
    ) -> None:
        with pytest.raises(ValidationError):
            hold(leases, key=key)

    @pytest.mark.parametrize("central", ["", "a b", "a/b", "A", "a" * 40, "a\n"])
    def test_a_central_that_is_not_a_name_is_refused(
        self, leases: SourceLeases, central: str
    ) -> None:
        with pytest.raises(ValidationError):
            hold(leases, central=central)

    def test_only_the_permission_to_read_contents_is_ever_held(self, leases: SourceLeases) -> None:
        held = hold(leases, permissions={"contents": "read"})
        assert held.permissions == {"contents": "read"}  # type: ignore[attr-defined]

        for wrong in ({"contents": "write"}, {"contents": "read", "pull_requests": "write"}, "x"):
            with pytest.raises(ValidationError):
                hold(leases, key="other", permissions=wrong)


class TestTheTableIsComplete:
    def test_a_store_has_the_table(self, store: NoustStore) -> None:
        columns = {
            row["name"]
            for row in store._get_connection().execute("PRAGMA table_info(source_leases)")
        }

        assert {"central", "lease_key", "host", "installation_id", "repos", "expires_at"} <= columns
        with pytest.raises(sqlite3.OperationalError):
            store._get_connection().execute("SELECT token FROM source_leases")
