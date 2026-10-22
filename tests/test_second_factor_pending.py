# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When an account must enrol a second factor before anything else (``mfa_pending``).

Until 4.0 every account was held at the enrolment screen, on every server: a person who
administers their own machine could not do anything until they scanned a code. The first
sign-in is now a path of a few clicks, and the second factor is a step that can wait while
nobody but the machine's own operator can reach the sign-in form:

    mfa_pending = no second factor yet
                  and (a framework asks for one, or the console is published)

What is pinned here is the four combinations, and that what keeps the console from being
published without one is untouched: ``noust console expose`` still refuses while any account
signs in with a password alone, however the pending flag reads.
"""

# The console's account harness is imported rather than replicated, so there stays one fake
# sign-in.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import AuthPolicy
from noust.core.accounts.policy import PROFILE_ENS_MEDIUM
from noust.core.console_exposure_store import (
    STATE_ACTIVE,
    STATE_PENDING,
    ConsoleExposureStore,
    ExposureState,
    console_published,
)
from noust.core.store import NoustStore
from noust.managers.console_exposure import second_factor_blockers
from tests.test_web_accounts import (  # noqa: F401 - fixtures used by name
    build,
    cheap_hashes,
    make_account,
    sign_in,
    store,
)


def publish(store: NoustStore, state: str = STATE_ACTIVE) -> None:
    """Record that the console is published on a public name."""
    ConsoleExposureStore(store).save(
        ExposureState(domain="console.example.com", port=8080, state=state)
    )


def pending(client: Any, username: str = "maria") -> bool:
    """Sign in as an account that has no second factor and say whether it is held."""
    body = sign_in(client, username, None)
    held = client.get("/api/apps").json().get("error") == "mfa_required"
    assert body["mfa_required"] is held
    assert client.get("/api/auth/session").json()["mfa_required"] is held
    return held


def test_on_a_server_nobody_reaches_a_new_account_is_not_held_at_the_enrolment(
    sandbox: Path, store: NoustStore
) -> None:
    client = build(sandbox)
    make_account("maria", "admin", mfa=False)

    assert pending(client) is False


def test_under_a_framework_that_asks_for_one_it_is(sandbox: Path, store: NoustStore) -> None:
    client = build(sandbox, AuthPolicy(profile=PROFILE_ENS_MEDIUM))
    make_account("maria", "admin", mfa=False)

    assert pending(client) is True


def test_with_the_console_published_it_is(sandbox: Path, store: NoustStore) -> None:
    client = build(sandbox)
    make_account("maria", "admin", mfa=False)
    publish(store)

    assert pending(client) is True


def test_an_account_that_has_one_is_never_held(sandbox: Path, store: NoustStore) -> None:
    client = build(sandbox, AuthPolicy(profile=PROFILE_ENS_MEDIUM))
    codes = make_account("maria", "admin")
    publish(store)

    body = sign_in(client, "maria", codes[0])

    assert body["mfa_required"] is False
    assert client.get("/api/apps").json().get("error") != "mfa_required"


def test_a_publication_that_did_not_finish_counts_as_published(
    sandbox: Path, store: NoustStore
) -> None:
    # The site may already answer: an unfinished exposure is not a reason to ask for less.
    client = build(sandbox)
    make_account("maria", "admin", mfa=False)
    publish(store, STATE_PENDING)

    assert pending(client) is True


def test_unexposing_the_console_lets_the_flag_go_back(sandbox: Path, store: NoustStore) -> None:
    client = build(sandbox)
    make_account("maria", "admin", mfa=False)
    publish(store)
    assert console_published(store) is True

    ConsoleExposureStore(store).clear()

    assert console_published(store) is False
    assert pending(client) is False


@pytest.mark.parametrize(
    "framework",
    ["nis2", "iso-27001", "soc2-security", "cis-ig1", "cis-ig2", "ens-media", "ens-alta"],
)
def test_the_frameworks_that_name_authentication_ask_for_it(
    sandbox: Path, store: NoustStore, framework: str
) -> None:
    client = build(sandbox, AuthPolicy(frameworks=(framework,)))
    make_account("maria", "admin", mfa=False)

    assert pending(client) is True


def test_a_framework_that_says_nothing_about_it_does_not(sandbox: Path, store: NoustStore) -> None:
    client = build(sandbox, AuthPolicy(frameworks=("gdpr-32",)))
    make_account("maria", "admin", mfa=False)

    assert pending(client) is False


def test_a_console_bound_beyond_loopback_counts_as_published(
    sandbox: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core import console_exposure_store
    from noust.core.config import Config

    real = Config.get
    monkeypatch.setattr(
        console_exposure_store.Config,
        "get",
        lambda self, key, default=None: (
            "0.0.0.0"  # noqa: S104 - the address under test, not one this code binds
            if key == "web.host"
            else real(self, key, default)
        ),
    )

    assert console_published(store) is True


def test_a_console_on_loopback_and_never_exposed_is_not_published(store: NoustStore) -> None:
    assert console_published(store) is False


def test_the_guard_of_noust_console_expose_does_not_depend_on_the_flag(
    sandbox: Path, store: NoustStore
) -> None:
    # Standard profile, console not published: the account is not held, and it still blocks.
    build(sandbox)
    make_account("maria", "admin", mfa=False)

    (blocker,) = second_factor_blockers()

    assert "maria" in blocker and "password alone" in blocker


def test_the_guard_is_quiet_once_every_account_has_a_second_factor(
    sandbox: Path, store: NoustStore
) -> None:
    build(sandbox)
    make_account("maria", "admin")

    assert second_factor_blockers() == []
