# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One credential does not hold the whole server's share of plans (item 171).

The server makes four plans at once, each looking at the machine for up to a minute. A credential that asks for many
at the same time leaves nobody else one, so each holds at most two, and a request of another credential is still
served while one is at its share.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from noust.core.exceptions import ShareHeldError
from noust.web.api import plan_share, plans
from noust.web.api.plan_share import MAX_PER_CREDENTIAL, hold_a_share

CI = {"type": "api_token", "sid": "token:ci", "owner_account_id": 7}
OTHER = {"type": "api_token", "sid": "token:other", "owner_account_id": 8}


@pytest.fixture(autouse=True)
def nothing_held() -> Iterator[None]:
    plan_share._held.clear()
    yield
    plan_share._held.clear()


def hold(session: dict[str, Any]) -> Iterator[None]:
    held = hold_a_share(session)
    next(held)
    return held


class TestTheShareOfACredential:
    def test_it_is_half_of_what_the_server_makes(self) -> None:
        assert MAX_PER_CREDENTIAL == 2

    def test_a_third_plan_of_the_same_credential_is_told_to_wait(self) -> None:
        first, second = hold(CI), hold(CI)

        with pytest.raises(ShareHeldError) as refused:
            hold(CI)

        assert refused.value.limit == MAX_PER_CREDENTIAL
        assert refused.value.coded.code == "plans.credential_busy"
        assert refused.value.coded.hint
        first.close()
        second.close()

    def test_another_credential_is_served_while_one_holds_its_share(self) -> None:
        held = [hold(CI), hold(CI)]

        held.append(hold(OTHER))

        for item in held:
            item.close()

    def test_the_share_comes_back_when_the_request_is_over(self) -> None:
        first = hold(CI)
        hold(CI)
        first.close()

        hold(CI)

    def test_nothing_is_kept_of_a_credential_that_holds_nothing(self) -> None:
        first = hold(CI)
        first.close()

        assert plan_share._held == {}


def test_every_router_of_plans_holds_a_share() -> None:
    for router in (plans.router, plans.certs_router, plans.docker_router):
        assert any(dep.dependency is hold_a_share for dep in router.dependencies), router.prefix
