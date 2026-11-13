# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The assistant under the security profile: a provider outside is a flow somebody declared
(item 171).

The assistant is off by default whatever the profile says. Under the ENS profile (op.ext.4: every
flow to an external service is authorised beforehand and documented) it is turned on only with a
model on this machine or the operator's network, or with a provider whose flow has a reason on the
record.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import outbound
from noust.core.assistant.settings import AssistantSettings
from noust.core.compliance import controls, frameworks
from noust.core.config import Config
from noust.core.ens.profile import assistant_needs_declared_flow
from noust.core.exceptions import ValidationError
from noust.core.fs import RealFileSystem, set_fs
from noust.core.store import NoustStore

PUBLIC = "203.0.113.9"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AssistantSettings]:
    target = tmp_path / "etc" / "config.yaml"
    target.parent.mkdir()
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", target)
    monkeypatch.setattr(
        outbound, "resolve_host", lambda host: ("127.0.0.1",) if host == "localhost" else (PUBLIC,)
    )
    set_fs(RealFileSystem())
    Config.reset_instance()
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    try:
        yield AssistantSettings(store)
    finally:
        store.close()
        NoustStore.reset_instance()
        Config.reset_instance()
        set_fs(None)


def outside(settings: AssistantSettings, **extra: object) -> None:
    settings.update(base_url="https://models.example.org/v1", model="m", actor="a", **extra)
    settings.record_probe({"reachable": True, "tools": True})


class TestTheControl:
    def test_it_is_a_control_of_its_own_and_ens_medium_asks_for_it(self) -> None:
        control = controls.get("assistant_flows")
        assert control.kind == controls.KIND_FLAG
        assert control.key == "security.assistant_flows"
        for name in ("ens-media", "ens-alta"):
            asked = frameworks.get(name).requirements.get("assistant_flows")
            assert asked is not None and asked.value is True
            assert any(c.id == "op.ext.4" for c in asked.clauses)

    def test_it_is_off_unless_the_operator_or_a_framework_turns_it_on(self) -> None:
        assert assistant_needs_declared_flow() is False
        Config().set("security.assistant_flows", True)
        assert assistant_needs_declared_flow() is True

    def test_the_ens_profile_turns_it_on(self) -> None:
        Config().set("security.profile", "ens-medium")
        assert assistant_needs_declared_flow() is True


class TestTurningItOn:
    def test_without_the_control_a_provider_outside_is_turned_on_as_ever(
        self, isolated: AssistantSettings
    ) -> None:
        outside(isolated)
        assert isolated.enable(actor="a").enabled is True

    def test_with_the_control_a_provider_outside_needs_its_flow_declared(
        self, isolated: AssistantSettings
    ) -> None:
        Config().set("security.assistant_flows", True)
        outside(isolated)
        with pytest.raises(ValidationError) as caught:
            isolated.enable(actor="a")
        assert caught.value.field == "flow_reason"
        assert "flow-reason" in caught.value.details
        isolated.update(
            flow_reason="Processing agreement of 2026-10-01 with the provider", actor="a"
        )
        assert isolated.enable(actor="a").enabled is True

    def test_a_reason_that_is_blank_is_not_one(self, isolated: AssistantSettings) -> None:
        Config().set("security.assistant_flows", True)
        outside(isolated, flow_reason="   ")
        with pytest.raises(ValidationError):
            isolated.enable(actor="a")

    def test_with_the_control_a_model_on_this_machine_needs_no_reason(
        self, isolated: AssistantSettings
    ) -> None:
        Config().set("security.assistant_flows", True)
        isolated.update(preset="ollama", model="llama3", actor="a", allow_private_host=True)
        isolated.record_probe({"reachable": True, "tools": True})
        assert isolated.enable(actor="a").enabled is True

    def test_the_reason_is_kept_and_a_credential_in_it_is_not(
        self, isolated: AssistantSettings
    ) -> None:
        outside(isolated, flow_reason="Contract, key Bearer abcdefghijklmnopqrstuvwxyz0123456789")
        reason = isolated.read().flow_reason
        assert "Contract" in reason
        assert "abcdefghijklmnopqrstuvwxyz0123456789" not in reason
