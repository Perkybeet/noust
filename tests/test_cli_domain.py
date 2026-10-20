# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``noust domain``.

The command decides nothing: :mod:`noust.deployers.domains` does, and is
covered in ``tests/test_domains.py``. Pinned here is the translation: the
arguments that reach it, ``--json`` on either side of ``list``, the DNS
warning before a name is added, certbot's words when an order fails, and a
refusal becoming exit code 1 with the operation's own words.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.cli.commands import domain as domain_module
from noust.core.exceptions import DependencyError, DomainConflictError, DomainError
from noust.core.logger import Logger
from noust.core.store import DomainRecord
from noust.deployers.domains import DnsCheck, DomainChange

APP = "example.com"
PRIMARY = DomainRecord(id=1, app_id=1, domain=APP, kind="primary", created_at="2026-09-25")
ALIAS = DomainRecord(
    id=2, app_id=1, domain="shop.example.com", kind="alias", created_at="t1", port=9000
)
HERE = DnsCheck("shop.example.com", ("203.0.113.5",), ("203.0.113.5",), True)
ELSEWHERE = DnsCheck("shop.example.com", ("203.0.113.5",), ("198.51.100.7",), False)


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Collect what the command reports to the operator."""
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    """Stub the operations, recording how they were called."""
    recorded: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    change = DomainChange(app=APP, domains=(PRIMARY, ALIAS), tls=False)

    def stub(name: str, result: Any) -> Any:
        def fake(*args: Any, **kwargs: Any) -> Any:
            recorded.append((name, args, kwargs))
            return result

        return fake

    monkeypatch.setattr(domain_module, "list_domains", stub("list", [PRIMARY, ALIAS]))
    monkeypatch.setattr(domain_module, "add_domain", stub("add", change))
    monkeypatch.setattr(domain_module, "remove_domain", stub("remove", change))
    monkeypatch.setattr(domain_module, "check_dns", stub("dns", HERE))
    monkeypatch.setattr(domain_module, "set_canonical", stub("canonical", change))
    monkeypatch.setattr(domain_module, "set_domain_port", stub("port", change))
    monkeypatch.setattr(domain_module, "issue_certificate", stub("issue", change))
    monkeypatch.setattr(domain_module, "reduce_certificate", stub("reduce", change))
    return recorded


def invoke(args: list[str]) -> Result:
    """Run the real root group."""
    return CliRunner().invoke(root_cli, args)


@pytest.mark.parametrize(
    "args", [["domain", "list", APP, "--json"], ["--json", "domain", "list", APP]]
)
def test_list_prints_json_on_either_side_of_the_command(calls: list[Any], args: list[str]) -> None:
    result = invoke(args)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "app": APP,
        "items": [
            {
                "domain": APP,
                "kind": "primary",
                "port": None,
                "created_at": "2026-09-25",
                "temporary": False,
                "enabled": True,
            },
            {
                "domain": "shop.example.com",
                "kind": "alias",
                "port": 9000,
                "created_at": "t1",
                "temporary": False,
                "enabled": True,
            },
        ],
    }
    assert calls == [("list", (APP,), {})]


def test_list_shows_a_table_for_a_human(
    calls: list[Any], log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tables: list[tuple[list[Any], list[Any]]] = []
    monkeypatch.setattr(
        Logger, "table", lambda self, headers, rows, justify=None: tables.append((headers, rows))
    )

    result = invoke(["domain", "list", APP])

    assert result.exit_code == 0, result.output
    assert [row[:2] for row in tables[0][1]] == [[APP, "primary"], ["shop.example.com", "alias"]]


@pytest.mark.parametrize(
    ("extra", "kind", "issue_cert"),
    [
        ([], "alias", True),
        (["--kind", "redirect"], "redirect", True),
        (["--no-cert"], "alias", False),
    ],
)
def test_add_reaches_the_operation_with_what_was_asked(
    calls: list[Any], log: list[str], extra: list[str], kind: str, issue_cert: bool
) -> None:
    result = invoke(["domain", "add", APP, "shop.example.com", *extra])

    assert result.exit_code == 0, result.output
    (add,) = [call for call in calls if call[0] == "add"]
    assert add[1] == (APP, "shop.example.com", kind)
    assert add[2]["issue_cert"] is issue_cert


def test_add_warns_when_the_name_does_not_resolve_here(
    calls: list[Any], log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(domain_module, "check_dns", lambda name: ELSEWHERE)

    result = invoke(["domain", "add", APP, "shop.example.com"])

    assert result.exit_code == 0, result.output
    text = "\n".join(log)
    assert "does not resolve to this server" in text
    assert "198.51.100.7" in text
    assert "203.0.113.5" in text


def test_add_goes_ahead_when_the_addresses_cannot_be_listed(
    calls: list[Any], log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_psutil(name: str) -> DnsCheck:
        raise DependencyError("psutil is needed to list this server's addresses")

    monkeypatch.setattr(domain_module, "check_dns", no_psutil)

    result = invoke(["domain", "add", APP, "shop.example.com"])

    assert result.exit_code == 0, result.output
    assert [call[0] for call in calls] == ["add"]


def test_a_failed_order_shows_certbot_verbatim_and_how_to_retry(
    calls: list[Any], log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    change = DomainChange(
        app=APP,
        domains=(PRIMARY, ALIAS),
        tls=True,
        certificate_error="Challenge failed for domain shop.example.com",
    )
    monkeypatch.setattr(domain_module, "add_domain", lambda *a, **k: change)

    result = invoke(["domain", "add", APP, "shop.example.com"])

    assert result.exit_code == 0, result.output
    text = "\n".join(log)
    assert "Challenge failed for domain shop.example.com" in text
    assert f"noust domain add {APP} shop.example.com" in text


def test_remove_reaches_the_operation(calls: list[Any], log: list[str]) -> None:
    result = invoke(["domain", "remove", APP, "shop.example.com"])

    assert result.exit_code == 0, result.output
    assert [call[:2] for call in calls] == [("remove", (APP, "shop.example.com"))]


def test_removing_from_a_tls_site_says_nothing_was_revoked(
    log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    change = DomainChange(app=APP, domains=(PRIMARY,), tls=True)
    monkeypatch.setattr(domain_module, "remove_domain", lambda *a, **k: change)

    result = invoke(["domain", "remove", APP, "shop.example.com"])

    assert result.exit_code == 0, result.output
    assert any("not revoked" in line for line in log)


def test_a_refusal_exits_1_with_the_operations_words(
    log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise DomainError(
            "example.com is the primary domain and cannot be removed",
            details="Delete the application to stop serving it: wasm delete example.com",
        )

    monkeypatch.setattr(domain_module, "remove_domain", refuse)

    assert main(["domain", "remove", APP, APP]) == 1
    assert any("cannot be removed" in line for line in log)
    assert any("wasm delete example.com" in line for line in log)


def test_add_passes_the_force_the_port_and_the_dns_choice_through(
    calls: list[Any], log: list[str]
) -> None:
    result = invoke(
        ["domain", "add", APP, "admin.example.com", "--force", "--port", "9000", "--no-dns"]
    )

    assert result.exit_code == 0, result.output
    (add,) = [call for call in calls if call[0] == "add"]
    assert add[2]["force"] is True
    assert add[2]["port"] == 9000
    assert add[2]["dns"] is False


def test_add_by_default_forces_nothing_and_points_the_dns(calls: list[Any], log: list[str]) -> None:
    invoke(["domain", "add", APP, "shop.example.com"])

    (add,) = [call for call in calls if call[0] == "add"]
    assert (add[2]["force"], add[2]["port"], add[2]["dns"]) == (False, None, True)


def test_a_name_another_site_answers_on_is_refused_and_says_how_to_go_ahead(
    log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise DomainConflictError(
            "shop.example.com is already served by the site legacy",
            details="Add it anyway with: noust domain add example.com shop.example.com --force",
        )

    monkeypatch.setattr(domain_module, "add_domain", refuse)
    monkeypatch.setattr(domain_module, "check_dns", lambda name: HERE)

    assert main(["domain", "add", APP, "shop.example.com"]) == 1
    assert any("--force" in line for line in log)


def test_a_wildcard_is_not_resolved_and_a_failed_order_points_at_the_dns_challenge(
    calls: list[Any], log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    change = DomainChange(
        app=APP,
        domains=(PRIMARY,),
        tls=True,
        certificate_error="The DNS-01 challenge needs a DNS account",
    )
    monkeypatch.setattr(domain_module, "add_domain", lambda *a, **k: change)
    asked: list[str] = []
    monkeypatch.setattr(domain_module, "check_dns", lambda name: asked.append(name) or HERE)

    result = invoke(["domain", "add", APP, "*.example.com"])

    assert result.exit_code == 0, result.output
    assert asked == []
    assert any(f"noust cert use {APP} --acme --dns ACCOUNT" in line for line in log)


def test_a_server_behind_nat_is_told_to_declare_its_address_not_that_the_name_is_wrong(
    calls: list[Any], log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = DnsCheck("shop.example.com", (), ("203.0.113.5",), False, verdict="unknown")
    monkeypatch.setattr(domain_module, "check_dns", lambda name: unknown)

    result = invoke(["domain", "add", APP, "shop.example.com"])

    assert result.exit_code == 0, result.output
    text = "\n".join(log)
    assert "does not resolve to this server" not in text
    assert "dns.public_addresses" in text


def test_the_table_shows_the_port_of_a_name_that_has_one(
    calls: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    tables: list[tuple[list[Any], list[Any]]] = []
    monkeypatch.setattr(
        Logger, "table", lambda self, headers, rows, justify=None: tables.append((headers, rows))
    )

    invoke(["domain", "list", APP])

    assert tables[0][0] == ["Domain", "Kind", "Port", "Added"]
    assert [row[2] for row in tables[0][1]] == ["-", "9000"]


def test_removing_orders_the_certificate_again_and_says_so(
    log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    change = DomainChange(app=APP, domains=(PRIMARY,), tls=True, certificate_reduced=True)
    monkeypatch.setattr(domain_module, "remove_domain", lambda *a, **k: change)

    result = invoke(["domain", "remove", APP, "shop.example.com"])

    assert result.exit_code == 0, result.output
    text = "\n".join(log)
    assert "ordered again without the name" in text
    assert "not revoked" not in text


def test_a_certificate_that_could_not_be_ordered_again_shows_certbot_and_how_to_retry(
    log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    change = DomainChange(
        app=APP, domains=(PRIMARY,), tls=True, certificate_error="too many certificates"
    )
    monkeypatch.setattr(domain_module, "remove_domain", lambda *a, **k: change)

    result = invoke(["domain", "remove", APP, "shop.example.com"])

    assert result.exit_code == 0, result.output
    text = "\n".join(log)
    assert "too many certificates" in text
    assert f"noust domain certificate {APP} --reduce" in text


@pytest.mark.parametrize("spelling", ["www", "apex", "none"])
def test_canonical_reaches_the_operation(calls: list[Any], log: list[str], spelling: str) -> None:
    result = invoke(["domain", "canonical", APP, spelling])

    assert result.exit_code == 0, result.output
    (call,) = [c for c in calls if c[0] == "canonical"]
    assert call[1] == (APP, spelling)
    assert call[2]["issue_cert"] is True


def test_canonical_can_leave_the_certificate_alone(calls: list[Any], log: list[str]) -> None:
    invoke(["domain", "canonical", APP, "www", "--no-cert"])

    (call,) = [c for c in calls if c[0] == "canonical"]
    assert call[2]["issue_cert"] is False


def test_canonical_refuses_a_spelling_that_is_not_one(calls: list[Any]) -> None:
    result = invoke(["domain", "canonical", APP, "both"])

    assert result.exit_code == 2
    assert calls == []


def test_a_port_is_chosen_and_none_puts_the_applications_own_back(
    calls: list[Any], log: list[str]
) -> None:
    chosen = invoke(["domain", "port", APP, "admin.example.com", "9000"])
    cleared = invoke(["domain", "port", APP, "admin.example.com", "none"])

    assert chosen.exit_code == 0, chosen.output
    assert cleared.exit_code == 0, cleared.output
    ports = [c for c in calls if c[0] == "port"]
    assert [c[1] for c in ports] == [
        (APP, "admin.example.com", 9000),
        (APP, "admin.example.com", None),
    ]


@pytest.mark.parametrize("bad", ["http", "0", "70000"])
def test_something_that_is_not_a_port_never_reaches_the_operation(
    calls: list[Any], log: list[str], bad: str
) -> None:
    code = main(["domain", "port", APP, "admin.example.com", bad])

    assert code != 0
    assert [c for c in calls if c[0] == "port"] == []


def test_the_certificate_is_extended_or_reduced_on_request(
    calls: list[Any], log: list[str]
) -> None:
    extended = invoke(["domain", "certificate", APP])
    reduced = invoke(["domain", "certificate", APP, "--reduce"])

    assert extended.exit_code == 0, extended.output
    assert reduced.exit_code == 0, reduced.output
    assert [c[0] for c in calls] == ["issue", "reduce"]
    assert any("names nothing that was removed" in line for line in log)


def test_a_rehearsal_orders_no_certificate(calls: list[Any]) -> None:
    result = invoke(["--dry-run", "domain", "certificate", APP, "--reduce"])

    assert result.exit_code == 0, result.output
    assert calls == []
