# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The part of the live tests of the API's clients that can be proved without a server.

``tests/integration/live_api.py`` starts the console server and issues the tokens the Terraform provider and
the Python SDK are tested with. The server and the sign-in are what the live runs prove; what is checked here
is the arithmetic the sign-in depends on, and that a client of the live tests is never handed the master
token.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "tests" / "integration"


@pytest.fixture(scope="module")
def live_api() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "live_api_under_test", INTEGRATION / "live_api.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["live_api_under_test"] = module
    spec.loader.exec_module(module)
    return module


class TestTheAuthenticatorCode:
    # RFC 6238, appendix B: the SHA-1 secret "12345678901234567890", whose eight digits at these times are
    # 94287082, 07081804 and 14050471; six digits are the last six.
    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    @pytest.mark.parametrize(
        ("at", "code"),
        [(59, "287082"), (1111111109, "081804"), (1111111111, "050471")],
    )
    def test_it_is_the_one_rfc_6238_computes(
        self, live_api: ModuleType, at: int, code: str
    ) -> None:
        assert live_api.totp(self.SECRET, at=at) == code

    def test_it_changes_every_thirty_seconds(self, live_api: ModuleType) -> None:
        assert live_api.totp(self.SECRET, at=60) == live_api.totp(self.SECRET, at=89)
        assert live_api.totp(self.SECRET, at=59) != live_api.totp(self.SECRET, at=60)


class TestTheProfiles:
    def test_the_read_only_profile_cannot_change_anything(self, live_api: ModuleType) -> None:
        for permission in live_api.READ_ONLY_PERMISSIONS:
            assert permission.endswith(".read"), permission

    def test_every_profile_is_described(self, live_api: ModuleType) -> None:
        assert set(live_api.PROFILES) == {"provider", "nosudo", "read"}

    def test_the_tokens_are_issued_by_an_account_and_never_by_the_master_token(self) -> None:
        source = (INTEGRATION / "live_api.py").read_text(encoding="utf-8")
        mint = source[source.index("def mint_tokens") : source.index("@dataclass")]

        # The master token is used once, to make the account; every token is issued signed in as it.
        assert mint.count("token=master_token") == 1
        assert '"/api/auth/tokens"' in mint
        assert "allow_elevated" in mint

    def test_the_runner_never_passes_the_master_token_to_a_client(self) -> None:
        runner = (INTEGRATION / "terraform_run.py").read_text(encoding="utf-8")
        live = runner[runner.index("class Live") : runner.index("def provider_permissions")]

        assert "master" not in live
        assert "NOUST_TOKEN_NOSUDO" in live and "NOUST_TOKEN_READ" in live
