# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Client certificates over the API and the command line.

What a certificate is, and what the site's file says, is pinned in ``tests/test_client_certs.py``
(with the real ``openssl``). Here: that the team that owns an application makes, lists and revokes
its certificates and another team does not even see that they exist, that the private key travels
once and only to someone in sudo mode, that the requirement is the policy's own field, and that the
command line writes what it hands over readable by its owner alone and shows the password once.
"""

from __future__ import annotations

import base64
import json
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import HTTPException

from noust.cli.app import cli as root_cli
from noust.core.client_cert_store import ClientCertificate
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.deployers import access
from noust.managers.client_certs import IssuedClientCertificate
from noust.managers.site_policy import SitePolicy, validated
from noust.web.api.deps import require_elevated
from tests.teams_support import World, build_world

APP = "shop.acme.example"
SERIAL = "1A" + "0" * 30
KEY = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIA==\n-----END PRIVATE KEY-----\n"
PASSWORD = "correct-horse-battery-staple"


def record(name: str = "ana-laptop", serial: str = SERIAL, revoked: bool = False) -> Any:
    return ClientCertificate(
        id=1,
        app_domain=APP,
        name=name,
        serial=serial,
        fingerprint="AA:BB",
        not_before="2026-10-17T00:00:00+00:00",
        not_after="2027-10-17T00:00:00+00:00",
        issued_by="alice",
        issued_at="2026-10-17T00:00:00+00:00",
        revoked_at="2026-10-18T00:00:00+00:00" if revoked else None,
        revoked_by="alice" if revoked else None,
    )


def issued(name: str = "ana-laptop") -> IssuedClientCertificate:
    return IssuedClientCertificate(
        record=record(name),
        bundle=b"\x30\x82\x01\x00 not text \xff\xfe",
        password=PASSWORD,
        certificate="-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n",
        key=KEY,
        ca="-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----\n",
    )


def view(**changes: Any) -> access.AccessView:
    return access.AccessView(
        app=APP, policy=validated(SitePolicy(), changes), users=(), webserver="nginx"
    )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


@contextmanager
def outside_sudo_mode(world: World) -> Iterator[None]:
    """The console's own session, which has not confirmed it is the operator."""

    def refuse() -> None:
        raise HTTPException(status_code=403, detail={"error": "elevation_required"})

    world.client.app.dependency_overrides[require_elevated] = refuse  # type: ignore[attr-defined]
    try:
        yield
    finally:
        world.client.app.dependency_overrides.pop(require_elevated, None)  # type: ignore[attr-defined]


# -- the API -----------------------------------------------------------------------


def test_the_team_lists_its_certificates_revoked_ones_too(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "describe", lambda *a, **k: view(client_certs=True))
    monkeypatch.setattr(
        access,
        "client_certificates",
        lambda d: [record("ana"), record("old", "2B" + "0" * 30, True)],
    )
    monkeypatch.setattr(access, "client_authority", lambda d: "-----BEGIN CERTIFICATE-----")

    response = world.client.get(f"/api/apps/{APP}/client-certs", headers=world.as_("dana"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True and body["authority"] is True
    assert [c["name"] for c in body["certificates"]] == ["ana", "old"]
    assert [c["revoked"] for c in body["certificates"]] == [False, True]
    assert "key" not in json.dumps(body).lower().replace("key_pem", "")


def test_another_teams_application_has_no_certificates_to_ask_about(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    reached: list[str] = []
    for name in ("issue_client_certificate", "revoke_client_certificate", "client_certificates"):
        monkeypatch.setattr(access, name, lambda *a, _n=name, **k: reached.append(_n))

    for method, path, body in (
        ("get", f"/api/apps/{APP}/client-certs", None),
        ("get", f"/api/apps/{APP}/client-certs/ca", None),
        ("post", f"/api/apps/{APP}/client-certs", {"name": "ana"}),
        ("delete", f"/api/apps/{APP}/client-certs/{SERIAL}", None),
    ):
        kwargs: dict[str, Any] = {"headers": world.as_("bob")}
        if body is not None:
            kwargs["json"] = body
        response = getattr(world.client, method)(path, **kwargs)
        assert response.status_code == 404, (method, path, response.status_code)

    assert reached == []


def test_issuing_hands_over_the_bundle_and_the_key_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str, int]] = []

    def fake(domain: str, name: str, *, days: int = 365, **k: Any) -> IssuedClientCertificate:
        seen.append((domain, name, days))
        return issued(name)

    monkeypatch.setattr(access, "issue_client_certificate", fake)

    response = world.client.post(
        f"/api/apps/{APP}/client-certs",
        json={"name": "ana-laptop", "days": 90},
        headers=world.as_("alice"),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert seen == [(APP, "ana-laptop", 90)]
    assert base64.b64decode(body["bundle"]) == issued().bundle
    assert body["password"] == PASSWORD and body["key_pem"] == KEY
    assert body["certificate"]["serial"] == SERIAL and body["certificate"]["revoked"] is False


def test_issuing_needs_sudo_mode_because_a_private_key_is_in_the_answer(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[str] = []
    monkeypatch.setattr(
        access, "issue_client_certificate", lambda d, n, **k: made.append(n) or issued(n)
    )

    with outside_sudo_mode(world):
        made_one = world.client.post(
            f"/api/apps/{APP}/client-certs", json={"name": "ana"}, headers=world.as_("alice")
        )
        revoked = world.client.delete(
            f"/api/apps/{APP}/client-certs/{SERIAL}", headers=world.as_("alice")
        )
        listed = world.client.get(f"/api/apps/{APP}/client-certs", headers=world.as_("alice"))

    assert made_one.status_code == 403 and revoked.status_code == 403
    assert made == []
    assert listed.status_code == 200  # reading what exists needs no confirmation


def test_a_viewer_of_the_team_may_read_and_not_make(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[str] = []
    monkeypatch.setattr(
        access, "issue_client_certificate", lambda d, n, **k: made.append(n) or issued(n)
    )

    response = world.client.post(
        f"/api/apps/{APP}/client-certs", json={"name": "ana"}, headers=world.as_("dana")
    )

    assert response.status_code == 403 and made == []


@pytest.mark.parametrize("days", [0, 826, -3])
def test_a_validity_out_of_range_is_refused_before_anything_is_made(
    world: World, monkeypatch: pytest.MonkeyPatch, days: int
) -> None:
    made: list[str] = []
    monkeypatch.setattr(
        access, "issue_client_certificate", lambda d, n, **k: made.append(n) or issued(n)
    )

    response = world.client.post(
        f"/api/apps/{APP}/client-certs",
        json={"name": "ana", "days": days},
        headers=world.as_("alice"),
    )

    assert response.status_code == 422 and made == []


def test_a_name_the_manager_refuses_is_a_400_naming_the_field(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*a: Any, **k: Any) -> Any:
        raise ValidationError("'a b' cannot be the name of a certificate", field="name")

    monkeypatch.setattr(access, "issue_client_certificate", refuse)

    response = world.client.post(
        f"/api/apps/{APP}/client-certs", json={"name": "a b"}, headers=world.as_("alice")
    )

    assert response.status_code == 400, response.text
    assert "cannot be the name" in response.text


def test_revoking_passes_the_lockout_choice_through(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        access,
        "revoke_client_certificate",
        lambda d, serial, *, accept_lockout=False, **k: (
            calls.append((d, serial, accept_lockout)) or True
        ),
    )

    first = world.client.delete(
        f"/api/apps/{APP}/client-certs/{SERIAL}", headers=world.as_("alice")
    )
    second = world.client.delete(
        f"/api/apps/{APP}/client-certs/{SERIAL}?accept_lockout=true", headers=world.as_("alice")
    )

    assert first.status_code == 204 and second.status_code == 204
    assert calls == [(APP, SERIAL, False), (APP, SERIAL, True)]


def test_the_last_valid_certificate_is_refused_as_a_400_that_says_how_to_go_on(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*a: Any, **k: Any) -> Any:
        raise ValidationError(
            f"That is the last valid client certificate of {APP}",
            details="Make another first, or accept it.",
            field="serial",
        )

    monkeypatch.setattr(access, "revoke_client_certificate", refuse)

    response = world.client.delete(
        f"/api/apps/{APP}/client-certs/{SERIAL}", headers=world.as_("alice")
    )

    assert response.status_code == 400 and "last valid" in response.text


def test_the_authority_certificate_is_public_and_empty_before_the_first(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "client_authority", lambda d: None)

    empty = world.client.get(f"/api/apps/{APP}/client-certs/ca", headers=world.as_("dana"))
    monkeypatch.setattr(access, "client_authority", lambda d: "-----BEGIN CERTIFICATE-----\nX\n")
    made = world.client.get(f"/api/apps/{APP}/client-certs/ca", headers=world.as_("dana"))

    assert empty.status_code == 200 and empty.json() == {"certificate": None}
    assert made.json()["certificate"].startswith("-----BEGIN CERTIFICATE-----")


def test_the_requirement_is_the_policys_own_field(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def fake(domain: str, changes: dict[str, Any], **kwargs: Any) -> access.AccessView:
        seen.append(changes)
        return view(**changes)

    monkeypatch.setattr(access, "set_policy", fake)

    on = world.client.put(
        f"/api/apps/{APP}/policy", json={"client_certs": True}, headers=world.as_("alice")
    )
    world.client.put(f"/api/apps/{APP}/policy", json={"noindex": True}, headers=world.as_("alice"))

    assert on.status_code == 200, on.text
    assert on.json()["policy"]["client_certs"] is True
    assert seen[0] == {"client_certs": True}
    assert "client_certs" not in seen[1]


# -- the command line --------------------------------------------------------------


def invoke(*args: str) -> Any:
    return CliRunner().invoke(root_cli, ["app", "policy", *args])


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(domain: str, changes: dict[str, Any], **kwargs: Any) -> access.AccessView:
        calls.append(changes)
        return view(**changes)

    monkeypatch.setattr(access, "set_policy", fake)
    monkeypatch.setattr(access, "describe", lambda *a, **k: view())
    monkeypatch.setattr(access, "client_certificates", lambda d: [record()])
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    return calls


def test_the_flag_turns_the_requirement_on_and_off(asked: list[dict[str, Any]]) -> None:
    assert invoke("set", "shop.example.com", "--client-certs").exit_code == 0
    assert invoke("set", "shop.example.com", "--no-client-certs", "--accept-lockout").exit_code == 0

    assert asked == [{"client_certs": True}, {"client_certs": False}]


def test_the_policy_says_how_many_certificates_could_get_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access, "describe", lambda *a, **k: view(client_certs=True))
    monkeypatch.setattr(
        access, "client_certificates", lambda d: [record(), record("old", "2B" + "0" * 30, True)]
    )

    result = invoke("show", "shop.example.com")

    assert result.exit_code == 0, result.output
    assert "one of the 1 valid certificates" in result.output


def test_listing_shows_the_state_and_json_is_machine_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access, "describe", lambda *a, **k: view(client_certs=True))
    monkeypatch.setattr(
        access, "client_certificates", lambda d: [record(), record("old", "2B" + "0" * 30, True)]
    )

    plain = invoke("clients", "list", "shop.example.com")
    machine = invoke("clients", "list", "shop.example.com", "--json")

    assert plain.exit_code == 0 and "revoked" in plain.output and "valid" in plain.output
    body = json.loads(machine.output)
    assert body["enabled"] is True and [c["name"] for c in body["certificates"]] == [
        "ana-laptop",
        "old",
    ]


def test_issuing_writes_the_files_for_its_owner_alone_and_shows_the_password_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(access, "describe", lambda *a, **k: view())
    monkeypatch.setattr(access, "issue_client_certificate", lambda d, n, **k: issued(n))
    out = tmp_path / "ana"

    result = invoke("clients", "issue", "shop.example.com", "ana-laptop", "--out", str(out))

    assert result.exit_code == 0, result.output
    assert result.output.count(PASSWORD) == 1
    assert (out / "ana-laptop.p12").read_bytes() == issued().bundle
    assert (out / "ana-laptop.key").read_text() == KEY
    assert (out / "ca.crt").read_text().startswith("-----BEGIN CERTIFICATE-----")
    modes = {p.name: stat.S_IMODE(p.stat().st_mode) for p in out.iterdir()}
    assert modes["ana-laptop.p12"] == 0o600 and modes["ana-laptop.key"] == 0o600
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert "noust app policy set shop.example.com --client-certs" in result.output


def test_issuing_as_json_carries_the_password_and_the_paths_and_not_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(access, "issue_client_certificate", lambda d, n, **k: issued(n))

    result = invoke(
        "clients", "issue", "shop.example.com", "ana-laptop", "--out", str(tmp_path), "--json"
    )

    body = json.loads(result.output)
    assert body["password"] == PASSWORD
    assert body["files"]["bundle"].endswith("ana-laptop.p12")
    assert "PRIVATE KEY" not in result.output


def test_it_will_not_overwrite_a_key_and_makes_nothing_when_it_cannot_hand_it_over(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    made: list[str] = []
    monkeypatch.setattr(
        access, "issue_client_certificate", lambda d, n, **k: made.append(n) or issued(n)
    )
    (tmp_path / "ana-laptop.key").write_text("an older key")

    result = invoke("clients", "issue", "shop.example.com", "ana-laptop", "--out", str(tmp_path))

    assert result.exit_code == 2 and made == []
    assert (tmp_path / "ana-laptop.key").read_text() == "an older key"


def test_issuing_is_rehearsed_and_not_done(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    made: list[str] = []
    monkeypatch.setattr(
        access, "issue_client_certificate", lambda d, n, **k: made.append(n) or issued(n)
    )

    result = CliRunner().invoke(
        root_cli,
        ["--dry-run", "app", "policy", "clients", "issue", "shop.example.com", "ana", "--out",
         str(tmp_path / "x")],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert made == [] and not (tmp_path / "x").exists()


def test_revoking_asks_nothing_more_unless_it_is_the_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        access,
        "revoke_client_certificate",
        lambda d, serial, *, accept_lockout=False, **k: (
            calls.append((serial, accept_lockout)) or True
        ),
    )

    first = invoke("clients", "revoke", "shop.example.com", SERIAL.lower())
    second = invoke("clients", "revoke", "shop.example.com", SERIAL, "--accept-lockout")

    assert first.exit_code == 0 and second.exit_code == 0
    assert calls == [(SERIAL.lower(), False), (SERIAL, True)]


def test_the_authority_certificate_is_printed_and_its_absence_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access, "client_authority", lambda d: None)
    missing = invoke("clients", "ca", "shop.example.com")
    monkeypatch.setattr(access, "client_authority", lambda d: "-----BEGIN CERTIFICATE-----\nX\n")
    present = invoke("clients", "ca", "shop.example.com")

    assert missing.exit_code == 1 and "No authority yet" in missing.output
    assert present.exit_code == 0 and present.output.startswith("-----BEGIN CERTIFICATE-----")
