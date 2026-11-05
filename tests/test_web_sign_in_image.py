# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The picture on the sign-in screen over HTTP: ``/api/auth/sign-in``.

The routes are a thin layer over the manager and the validator, which have their own tests; what
is pinned here is what the layer owes. Reading is public, because the screen is drawn before there
is a session, and the picture is cacheable for good only under its own digest, never one that is not
the current picture's. Changing it asks for the sign-in policy's permission and for sudo mode, takes a
body as large as a file of the limit and not larger, and is on the audit trail with the person who did
it. A central never forwards it to a node and a fleet token never reaches it.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import paths
from noust.core.store import NoustStore
from noust.validators import sign_in_image as rules
from noust.web import server
from noust.web.auth import CSRF_HEADER_NAME, fleet_refusal
from noust.web.permissions import PUBLIC, Permission
from noust.web.permissions.registry import permission_for_route
from noust.web.server import create_app, get_token_manager
from tests.support import sign_in_images as make
from tests.support.teams_support import World, build_world, make_person
from tests.support.web_auth import make_config

APPEARANCE = "/api/auth/sign-in/appearance"
IMAGE = "/api/auth/sign-in/image"


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    monkeypatch.setattr(paths, "STATE_DIR", tmp_path / "state-dir")
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


@pytest.fixture
def officer(world: World) -> dict[str, str]:
    """Someone who holds ``security.manage`` (and may read the audit trail, to see what was done)."""
    return make_person(
        world.teams, "sofia-the-officer", "security", permissions=["security.manage", "audit.read"]
    ).headers


def put(world: World, who: dict[str, str], body: dict[str, Any]) -> Any:
    return world.client.put(APPEARANCE, json=body, headers=who)


def set_picture(world: World, who: dict[str, str], body: dict[str, Any]) -> Any:
    """Set it and say what the server answered if it did not take it."""
    response = put(world, who, body)
    assert response.status_code == 200, response.text
    return response


class TestNobodyNeedsASessionToSeeIt:
    def test_with_no_picture_the_answer_is_null_and_the_limits(self, world: World) -> None:
        response = world.client.get(APPEARANCE)

        assert response.status_code == 200
        assert response.json() == {
            "image": None,
            "limits": {
                "bytes": 1024 * 1024,
                "side": 4096,
                "alt_chars": 120,
                "types": ["image/png", "image/jpeg", "image/webp"],
            },
        }
        assert response.headers["cache-control"] == "no-store"

    def test_with_no_picture_the_picture_is_a_404_in_the_error_contract(self, world: World) -> None:
        for suffix in ("", "?v=" + "0" * 64):
            response = world.client.get(IMAGE + suffix)

            assert response.status_code == 404
            assert response.json()["error"] == "not_found"

    def test_a_picture_is_described_to_anyone_and_served_to_anyone(
        self, world: World, officer: dict[str, str]
    ) -> None:
        raw = make.png(300, 100, extra=(make.png_chunk(b"tEXt", b"Author\x00Someone"),))
        set_picture(world, officer, {"image": encoded(raw), "fit": "contain", "alt": "Acme"})

        described = world.client.get(APPEARANCE)  # no credential at all
        image = described.json()["image"]

        assert described.status_code == 200
        assert (image["fit"], image["alt"], image["width"], image["height"]) == (
            "contain",
            "Acme",
            300,
            100,
        )
        assert image["content_type"] == "image/png"
        assert image["url"] == f"{IMAGE}?v={image['sha256']}"
        served = world.client.get(image["url"])
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"
        assert hashlib.sha256(served.content).hexdigest() == image["sha256"]
        assert b"Someone" not in served.content and len(served.content) == image["size"]


class TestHowThePictureIsServed:
    @pytest.fixture
    def chosen(self, world: World, officer: dict[str, str]) -> dict[str, Any]:
        raw = make.jpeg(640, 480)
        response = put(world, officer, {"image": encoded(raw), "fit": "cover", "alt": ""})
        assert response.status_code == 200, response.text
        return dict(response.json()["image"])

    def test_under_its_own_digest_it_is_cacheable_for_a_year_and_cannot_be_sniffed_into_anything(
        self, world: World, chosen: dict[str, Any]
    ) -> None:
        response = world.client.get(chosen["url"])

        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["etag"] == f'"{chosen["sha256"]}"'
        assert response.headers["content-disposition"] == "inline"
        # The console's own policy is on it, like on every response it gives.
        assert "default-src 'self'" in response.headers["content-security-policy"]

    def test_the_type_is_the_one_judged_from_its_bytes_never_one_the_uploader_said(
        self, world: World, officer: dict[str, str]
    ) -> None:
        # The body has no field for a type or a name: a webp is image/webp whatever it is called.
        response = put(world, officer, {"image": encoded(make.webp(40, 30)), "fit": "cover"})

        assert response.json()["image"]["content_type"] == "image/webp"
        assert world.client.get(response.json()["image"]["url"]).headers["content-type"] == (
            "image/webp"
        )

    def test_without_a_digest_it_is_served_but_never_cached(
        self, world: World, chosen: dict[str, Any]
    ) -> None:
        response = world.client.get(IMAGE)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"

    def test_a_digest_that_is_not_the_current_pictures_is_a_404_never_the_current_picture(
        self, world: World, chosen: dict[str, Any]
    ) -> None:
        for digest in ("0" * 64, "f" * 64, "abc", "", "../../etc/passwd", "A" * 64, "x" * 65):
            response = world.client.get(IMAGE, params={"v": digest})

            assert response.status_code == 404, digest
            assert response.headers["cache-control"] == "no-store", digest

    def test_after_a_replacement_the_old_address_is_gone(
        self, world: World, officer: dict[str, str], chosen: dict[str, Any]
    ) -> None:
        put(world, officer, {"image": encoded(make.png(9, 9)), "fit": "cover"})

        assert world.client.get(chosen["url"]).status_code == 404

    def test_a_client_that_has_it_is_told_so(self, world: World, chosen: dict[str, Any]) -> None:
        response = world.client.get(
            chosen["url"], headers={"If-None-Match": f'"{chosen["sha256"]}"'}
        )

        assert response.status_code == 304 and response.content == b""

    def test_what_is_in_the_directory_is_not_served_if_it_no_longer_hashes_to_its_digest(
        self, world: World, chosen: dict[str, Any]
    ) -> None:
        (paths.sign_in_image_dir() / f"{chosen['sha256']}.jpg").write_bytes(
            b"<svg onload=alert(1)>"
        )

        assert world.client.get(chosen["url"]).status_code == 404
        # And the screen is told there is nothing to draw, so it draws its own.
        assert world.client.get(APPEARANCE).json()["image"] is None


class TestWhoMayChangeIt:
    def test_the_sign_in_policy_permission_is_what_it_asks_and_the_reads_are_public(self) -> None:
        assert permission_for_route("GET", APPEARANCE) == PUBLIC
        assert permission_for_route("GET", IMAGE) == PUBLIC
        assert permission_for_route("PUT", APPEARANCE) == Permission.SECURITY_MANAGE
        assert permission_for_route("DELETE", APPEARANCE) == Permission.SECURITY_MANAGE

    def test_a_security_officer_sets_and_removes_it(
        self, world: World, officer: dict[str, str]
    ) -> None:
        body = {"image": encoded(make.png()), "fit": "cover", "alt": ""}

        assert put(world, officer, body).status_code == 200
        removed = world.client.delete(APPEARANCE, headers=officer)

        assert removed.status_code == 200 and removed.json()["image"] is None

    def test_nobody_without_the_permission_changes_it(self, world: World) -> None:
        body = {"image": encoded(make.png()), "fit": "cover", "alt": ""}

        for who in ("alice", "dana", "omar", "aud"):
            denied = put(world, world.as_(who), body)
            denied_delete = world.client.delete(APPEARANCE, headers=world.as_(who))

            assert denied.status_code == 403 and denied.json()["error"] == "permission_denied", who
            assert denied_delete.status_code == 403, who
        assert world.client.get(APPEARANCE).json()["image"] is None

    def test_nobody_anonymous_changes_it(self, world: World) -> None:
        body = {"image": encoded(make.png()), "fit": "cover", "alt": ""}

        assert world.client.put(APPEARANCE, json=body).status_code == 401
        assert world.client.delete(APPEARANCE).status_code == 401

    def test_a_central_never_reaches_it_with_a_fleet_token(self) -> None:
        assert fleet_refusal("PUT", APPEARANCE) is not None
        assert fleet_refusal("DELETE", APPEARANCE) is not None
        assert fleet_refusal("GET", APPEARANCE) is not None

    def test_a_person_of_a_team_does_not_see_it_as_theirs(self, world: World) -> None:
        # It is the server's, whatever team one is in: the classification says so, so a team member is
        # refused the permission and not the other way round.
        from noust.web.permissions.scopes import INSTANCE, rule_for

        for method in ("PUT", "DELETE", "GET"):
            rule = rule_for(method, APPEARANCE)
            assert rule is not None and rule.scoping == INSTANCE


class TestSudoMode:
    @pytest.fixture
    def client(self, sandbox: Path, runner: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setattr(paths, "STATE_DIR", sandbox / "state-dir")
        return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))

    @staticmethod
    def sign_in(client: TestClient) -> str:
        master = get_token_manager().generate_master_token()
        response = client.post("/api/auth/login", json={"token": master})
        assert response.status_code == 200, response.text
        client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
        return master

    def test_setting_it_asks_for_sudo_mode_first(self, client: TestClient) -> None:
        self.sign_in(client)

        response = client.put(
            APPEARANCE, json={"image": encoded(make.png()), "fit": "cover", "alt": ""}
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_removing_it_asks_for_sudo_mode_too(self, client: TestClient) -> None:
        self.sign_in(client)

        response = client.delete(APPEARANCE)

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_in_sudo_mode_it_is_set_then_removed(self, client: TestClient) -> None:
        master = self.sign_in(client)
        assert client.post("/api/auth/elevate", json={"token": master}).status_code == 200

        put_response = client.put(
            APPEARANCE, json={"image": encoded(make.png()), "fit": "cover", "alt": ""}
        )
        gone = client.delete(APPEARANCE)

        assert put_response.status_code == 200 and put_response.json()["image"] is not None
        assert gone.status_code == 200 and gone.json()["image"] is None

    def test_the_description_of_the_operation_says_it_asks_for_it(self, client: TestClient) -> None:
        schema = client.get("/api/openapi.json")
        if schema.status_code != 200:  # the document is for whoever signs in
            self.sign_in(client)
            schema = client.get("/api/openapi.json")
        operations = schema.json()["paths"][APPEARANCE]

        assert operations["put"]["x-noust-requires-elevation"] is True
        assert operations["delete"]["x-noust-requires-elevation"] is True
        assert operations["put"]["x-noust-permission"] == "security.manage"
        assert operations["get"]["x-noust-permission"] == "public"


class TestWhatIsSent:
    def test_a_refusal_is_a_400_with_a_code_the_console_translates_and_the_field(
        self, world: World, officer: dict[str, str]
    ) -> None:
        response = put(world, officer, {"image": encoded(b"<svg onload=alert(1)>"), "fit": "cover"})

        assert response.status_code == 400
        body = response.json()
        assert "image" in body["fields"]
        assert body["coded"]["code"] == "sign_in_image.svg_refused"
        assert world.client.get(APPEARANCE).json()["image"] is None

    def test_each_thing_that_is_wrong_is_named(self, world: World, officer: dict[str, str]) -> None:
        png = encoded(make.png())
        wrong = [
            ({"image": "!!!", "fit": "cover"}, "sign_in_image.not_base64", "image"),
            ({"image": png, "fit": "stretch"}, "sign_in_image.bad_fit", "fit"),
            ({"image": png, "fit": "cover", "alt": "a" * 121}, "sign_in_image.alt_too_long", "alt"),
            ({"image": png, "fit": "cover", "alt": "a\nb"}, "sign_in_image.alt_control", "alt"),
            (
                {"image": encoded(make.png(5000, 10)), "fit": "cover"},
                "sign_in_image.too_many_pixels",
                "image",
            ),
            (
                {"image": encoded(make.webp_extended(flags=0x02)), "fit": "cover"},
                "sign_in_image.animated",
                "image",
            ),
            ({"fit": "cover"}, "sign_in_image.no_image_yet", "image"),
        ]
        for body, code, field in wrong:
            response = put(world, officer, body)

            assert response.status_code == 400, response.text
            assert response.json()["coded"]["code"] == code
            assert field in response.json()["fields"], (code, response.json())

    def test_the_fit_and_the_description_change_without_sending_the_picture_again(
        self, world: World, officer: dict[str, str]
    ) -> None:
        first = put(world, officer, {"image": encoded(make.png()), "fit": "cover"}).json()["image"]

        changed = put(world, officer, {"fit": "contain", "alt": "Acme"})

        assert changed.status_code == 200
        image = changed.json()["image"]
        assert image["sha256"] == first["sha256"] and image["url"] == first["url"]
        assert (image["fit"], image["alt"]) == ("contain", "Acme")

    def test_an_alt_left_out_says_the_picture_is_decoration(
        self, world: World, officer: dict[str, str]
    ) -> None:
        response = put(world, officer, {"image": encoded(make.png()), "fit": "cover"})

        assert response.json()["image"]["alt"] == ""

    def test_a_body_that_is_not_the_shape_is_refused_before_anything_changes(
        self, world: World, officer: dict[str, str]
    ) -> None:
        for body in ({"image": 5}, {"fit": ["cover"]}, {"alt": 7}, []):
            response = put(world, officer, body)  # type: ignore[arg-type]
            assert response.status_code in (400, 422)
        assert world.client.get(APPEARANCE).json()["image"] is None


class TestTheSizeOfTheBody:
    def test_a_file_of_exactly_the_limit_is_taken_though_its_body_is_over_the_apis_mebibyte(
        self, world: World, officer: dict[str, str]
    ) -> None:
        base = make.png()
        exact = make.png(
            extra=(make.png_chunk(b"IDAT", b"\x00" * (rules.MAX_IMAGE_BYTES - len(base) - 12)),)
        )
        assert len(exact) == rules.MAX_IMAGE_BYTES
        body = {"image": encoded(exact), "fit": "cover"}

        response = put(world, officer, body)

        assert (
            len(encoded(exact)) > server.MAX_BODY_BYTES
            if hasattr(server, "MAX_BODY_BYTES")
            else True
        )
        assert response.status_code == 200, response.text

    def test_a_body_larger_than_a_file_of_the_limit_could_make_is_refused_before_it_is_read(
        self, world: World, officer: dict[str, str]
    ) -> None:
        response = put(
            world, officer, {"image": "A" * (rules.MAX_UPLOAD_CHARS + 20_000), "fit": "cover"}
        )

        assert response.status_code == 413
        assert response.json()["error"] == "payload_too_large"

    def test_no_other_route_gets_the_larger_body(
        self, world: World, officer: dict[str, str]
    ) -> None:
        big = {"image": "A" * (1024 * 1024 + 10), "fit": "cover"}

        response = world.client.post("/api/auth/login", json=big)

        assert response.status_code == 413

    def test_a_file_one_byte_over_the_limit_is_refused_by_the_validator(
        self, world: World, officer: dict[str, str]
    ) -> None:
        over = make.PNG_SIGNATURE + b"\x00" * rules.MAX_IMAGE_BYTES
        response = put(world, officer, {"image": encoded(over), "fit": "cover"})

        assert response.status_code == 400
        assert response.json()["coded"]["code"] == "sign_in_image.too_large"


class TestTheCacheRule:
    DIGEST = "ab" * 32

    @pytest.mark.parametrize(
        "path, status, query, expected",
        [
            (IMAGE, 200, f"v={'ab' * 32}", "public, max-age=31536000, immutable"),
            (IMAGE, 304, f"v={'ab' * 32}", "public, max-age=31536000, immutable"),
            (IMAGE, 404, f"v={'ab' * 32}", "no-store"),
            (IMAGE, 200, "", "no-store"),
            (IMAGE, 200, "v=abc", "no-store"),
            (IMAGE, 200, f"v={'ab' * 32}&x=1", "no-store"),
            (IMAGE, 200, f"v={'AB' * 32}", "no-store"),
            (APPEARANCE, 200, f"v={'ab' * 32}", "no-store"),
            ("/assets/app-1a2b3c.js", 200, "", "public, max-age=31536000, immutable"),
            ("/api/apps", 200, "", "no-store"),
        ],
    )
    def test_only_the_picture_under_its_own_digest_joins_the_hashed_files(
        self, path: str, status: int, query: str, expected: str
    ) -> None:
        assert server._cache_control_for(path, status, query) == expected


class TestTheAuditTrail:
    def test_setting_and_removing_are_on_it_with_the_person_who_did_them(
        self, world: World, officer: dict[str, str]
    ) -> None:
        set_picture(
            world, officer, {"image": encoded(make.png()), "fit": "contain", "alt": "Acme Corp"}
        )
        world.client.delete(APPEARANCE, headers=officer)

        entries = world.client.get("/api/audit", params={"limit": 50}, headers=officer).json()[
            "items"
        ]
        found = {
            entry["action"]: entry
            for entry in entries
            if entry["action"].startswith("auth.sign_in_image")
        }

        assert set(found) == {"auth.sign_in_image.set", "auth.sign_in_image.remove"}
        assert "sofia-the-officer" in found["auth.sign_in_image.set"]["actor"]
        assert "sofia-the-officer" in found["auth.sign_in_image.remove"]["actor"]
        assert "Acme Corp" not in str(entries)
