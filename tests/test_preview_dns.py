# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Previews check their DNS before a pull request needs it (checklist E-25).

The template was done (``{{pr_id}}.{{domain}}``); the part that was missing is the DNS: nothing
asked whether ``*.previews.example.com`` resolves to this server when previews are turned on, or
whether the preview about to be built does, and the failure showed up as a certbot error after a
build. What is pinned: the name that stands for the next preview is the one the application's own
template would give, the record that covers it is the wildcard one label above, a verdict is read
from the one DNS check every other feature uses, the answer says which record to add, and a preview
about to be built whose name points elsewhere is told in the job's log.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.exceptions import ValidationError
from noust.core.preview_store import PreviewOptions, PreviewStore
from noust.core.store import App, NoustStore, PreviewSettings
from noust.deployers.domains import DnsCheck
from noust.managers import preview_dns

SERVER = ("203.0.113.10",)


def answer(verdict: str, resolved: tuple[str, ...] = (), cdn: str | None = None) -> Any:
    def check(domain: str, **kwargs: Any) -> DnsCheck:
        return DnsCheck(
            domain=domain,
            expected_addresses=SERVER,
            resolved_addresses=resolved,
            points_here=verdict in ("here", "cdn"),
            verdict=verdict,
            cdn=cdn,
        )

    return check


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop", port=3000)
    )
    instance.save_preview_settings(
        PreviewSettings(
            app_domain="shop.example.com",
            base_domain="previews.example.com",
            max_previews=3,
            ttl_hours=168,
        )
    )
    yield instance
    NoustStore.reset_instance()


class TestTheName:
    def test_the_default_name_is_one_label_under_the_base(self, store: NoustStore) -> None:
        found = preview_dns.check_preview_dns("shop.example.com", check=answer("here", SERVER))

        assert found.probe.endswith(".previews.example.com")
        assert found.probe.count(".") == 3
        assert found.wildcard == "*.previews.example.com"

    def test_a_template_under_the_application_needs_that_wildcard(self, store: NoustStore) -> None:
        PreviewStore(store).save_options(
            PreviewOptions(app_domain="shop.example.com", domain_template="{{pr_id}}.{{domain}}")
        )

        found = preview_dns.check_preview_dns("shop.example.com", check=answer("here", SERVER))

        assert found.probe.endswith(".shop.example.com")
        assert found.wildcard == "*.shop.example.com"

    def test_the_probe_is_not_a_name_a_preview_will_ever_have(self, store: NoustStore) -> None:
        found = preview_dns.check_preview_dns("shop.example.com", check=answer("here", SERVER))

        assert "-0-" in found.probe or found.probe.startswith("0.")

    def test_previews_that_are_off_are_refused(self, store: NoustStore) -> None:
        store.delete_preview_settings("shop.example.com")

        with pytest.raises(ValidationError, match="Previews are off"):
            preview_dns.check_preview_dns("shop.example.com", check=answer("here"))

    def test_an_application_that_does_not_exist_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError):
            preview_dns.check_preview_dns("nope.example.com", check=answer("here"))


class TestTheVerdict:
    @pytest.mark.parametrize("verdict", ["here", "cdn", "unknown"])
    def test_a_name_that_reaches_the_server_is_fine(self, store: NoustStore, verdict: str) -> None:
        found = preview_dns.check_preview_dns(
            "shop.example.com", check=answer(verdict, SERVER, "cloudflare")
        )

        assert found.ok is True and found.advice is None

    def test_a_name_that_does_not_resolve_says_which_record_to_add(self, store: NoustStore) -> None:
        found = preview_dns.check_preview_dns("shop.example.com", check=answer("none"))

        assert found.ok is False
        assert found.advice is not None
        assert "*.previews.example.com" in found.advice
        assert SERVER[0] in found.advice

    def test_a_name_that_points_elsewhere_says_what_it_points_at(self, store: NoustStore) -> None:
        found = preview_dns.check_preview_dns(
            "shop.example.com", check=answer("elsewhere", ("198.51.100.4",))
        )

        assert found.ok is False
        assert found.advice is not None and "198.51.100.4" in found.advice

    def test_it_is_plain_data_for_the_api(self, store: NoustStore) -> None:
        found = preview_dns.check_preview_dns("shop.example.com", check=answer("none"))

        data = found.to_dict()
        assert data["wildcard"] == "*.previews.example.com"
        assert data["ok"] is False and data["verdict"] == "none"


class TestAPreviewAboutToBeBuilt:
    def test_a_name_that_points_elsewhere_is_told_before_the_build(self) -> None:
        said = preview_dns.problem_with(
            "pr-4-shop.previews.example.com", check=answer("elsewhere", ("198.51.100.4",))
        )

        assert said is not None
        assert "pr-4-shop.previews.example.com" in said and "198.51.100.4" in said

    def test_a_name_that_reaches_the_server_is_not(self) -> None:
        assert preview_dns.problem_with("a.previews.example.com", check=answer("here")) is None
        assert preview_dns.problem_with("a.previews.example.com", check=answer("cdn")) is None
        assert preview_dns.problem_with("a.previews.example.com", check=answer("unknown")) is None

    def test_a_check_that_cannot_be_made_is_not_a_problem(self) -> None:
        def broken(domain: str, **kwargs: Any) -> DnsCheck:
            raise OSError("no resolver")

        assert preview_dns.problem_with("a.previews.example.com", check=broken) is None

    def test_the_operator_can_turn_the_check_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            preview_dns, "Config", lambda: type("C", (), {"get": lambda s, k, d=None: False})()
        )

        assert preview_dns.problem_with("a.previews.example.com", check=answer("none")) is None


class TestTheCommand:
    @pytest.fixture
    def pointing(self, monkeypatch: pytest.MonkeyPatch):
        def set_answer(verdict: str, resolved: tuple[str, ...] = ()) -> None:
            monkeypatch.setattr(preview_dns, "_default_check", lambda: answer(verdict, resolved))

        return set_answer

    def test_dns_says_the_record_that_is_missing(self, store: NoustStore, pointing: Any) -> None:
        pointing("none")

        result = CliRunner().invoke(root_cli, ["preview", "dns", "shop.example.com"])

        assert result.exit_code == 0, result.output
        assert "*.previews.example.com" in result.output
        assert "does not resolve" in result.output or "no DNS record" in result.output

    def test_dns_says_when_all_is_well(self, store: NoustStore, pointing: Any) -> None:
        pointing("here", SERVER)

        result = CliRunner().invoke(root_cli, ["preview", "dns", "shop.example.com"])

        assert result.exit_code == 0, result.output
        assert "points at this server" in result.output

    def test_dns_in_json(self, store: NoustStore, pointing: Any) -> None:
        import json

        pointing("elsewhere", ("198.51.100.4",))

        result = CliRunner().invoke(root_cli, ["preview", "dns", "shop.example.com", "--json"])

        data = json.loads(result.output)
        assert data["ok"] is False and data["resolved_addresses"] == ["198.51.100.4"]
