# ruff: noqa: F811
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Authentication delegated to a service: nginx's ``auth_request`` in front of an application.

The service's address, its sign-in and the headers it sets land in a file nginx loads as root, so
most of what is pinned is what is refused. The rest is what a site looks like with it (an
internal location nginx alone can reach, the identity headers handed to the application so a
client cannot send its own, no authentication on the certificate authority's path over plain
HTTP), and that a service that does not answer is not put in front of a site without the
operator knowing: with it down, every visitor is refused.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.deployers import access
from noust.managers.site_forward_auth import (
    PRESETS,
    ForwardAuth,
    check_service,
    forward_auth_from,
)
from noust.managers.site_policy import SitePolicy, SitePolicyManager, locks_out, validated
from tests.test_domains import (  # noqa: F401 - fixtures of the domains harness
    Machine,
    certs,
    machine,
    store,
    web,
)

OAUTH = {
    "url": "http://127.0.0.1:4180/oauth2/auth",
    "sign_in": "/oauth2/start?rd=",
    "headers": ["X-Auth-Request-User", "X-Auth-Request-Email"],
}


# -- what is accepted -------------------------------------------------------------


def test_the_presets_are_valid_settings() -> None:
    for name, preset in PRESETS.items():
        settings = forward_auth_from({key: preset[key] for key in ("url", "sign_in", "headers")})
        assert settings is not None, name
        assert settings.header_pairs


def test_no_address_means_no_forward_auth() -> None:
    assert forward_auth_from(None) is None
    assert forward_auth_from({"url": ""}) is None
    assert forward_auth_from({}) is None


def test_the_variables_of_the_headers_are_the_ones_nginx_reads_the_service_answer_in() -> None:
    settings = forward_auth_from(OAUTH)

    assert settings is not None
    assert settings.header_pairs == [
        {
            "header": "X-Auth-Request-User",
            "source": "upstream_http_x_auth_request_user",
            "variable": "noust_fa_1",
        },
        {
            "header": "X-Auth-Request-Email",
            "source": "upstream_http_x_auth_request_email",
            "variable": "noust_fa_2",
        },
    ]
    assert settings.host_and_port == ("127.0.0.1", 4180)


@pytest.mark.parametrize(
    "url",
    [
        "127.0.0.1:4180/auth",
        "ftp://x.example/auth",
        "//x.example/auth",
        "http://x.example/auth?token=1",
        "http://x.example/a b",
        "http://x.example/a;b",
        'http://x.example/"',
        "http://x.example/$1",
        "http://x.example/#f",
        "http://exa mple.org/",
        "/oauth2/auth",
        5,
    ],
)
def test_an_address_that_could_end_its_directive_is_refused(url: Any) -> None:
    with pytest.raises(ValidationError) as raised:
        forward_auth_from({"url": url})

    assert raised.value.field == "forward_auth"


@pytest.mark.parametrize(
    "sign_in", ["x.example/start", "http://x.example/a b", "/a;b", '/a"b', "/a$b"]
)
def test_a_sign_in_that_could_end_its_directive_is_refused(sign_in: str) -> None:
    with pytest.raises(ValidationError):
        forward_auth_from({"url": "http://127.0.0.1:4180/a", "sign_in": sign_in})


@pytest.mark.parametrize(
    "header",
    ["", "bad name", "a;b", "9start", "Host", "Cookie", "Set-Cookie", "X-Forwarded-For", "x" * 70],
)
def test_a_header_that_cannot_be_handed_on_is_refused(header: str) -> None:
    with pytest.raises(ValidationError):
        forward_auth_from({"url": "http://127.0.0.1:4180/a", "headers": [header]})


def test_an_unknown_field_and_too_many_headers_are_refused() -> None:
    with pytest.raises(ValidationError):
        forward_auth_from({"url": "http://127.0.0.1:4180/a", "mode": "x"})
    with pytest.raises(ValidationError):
        forward_auth_from(
            {"url": "http://127.0.0.1:4180/a", "headers": [f"X-H{index}" for index in range(11)]}
        )


def test_the_policy_keeps_it_and_it_is_not_a_default() -> None:
    policy = validated(SitePolicy(), {"forward_auth": OAUTH})

    assert policy.forward_auth == ForwardAuth(
        url=OAUTH["url"], sign_in=OAUTH["sign_in"], headers=tuple(OAUTH["headers"])
    )
    assert not policy.is_default
    assert SitePolicy.from_document(__import__("json").dumps(policy.to_dict())) == policy
    assert validated(policy, {"forward_auth": None}).is_default


def test_it_counts_as_a_way_in_for_either() -> None:
    with pytest.raises(ValidationError):
        validated(SitePolicy(), {"access": "any", "forward_auth": OAUTH})
    policy = validated(
        SitePolicy(), {"access": "any", "forward_auth": OAUTH, "allow_ips": ["10.0.0.0/8"]}
    )

    assert policy.access == "any"
    # Signing in with the service is a way in, so a list that refuses the operator is no lockout.
    assert locks_out(policy, ["198.51.100.4"]) == []
    all_of_them = validated(policy, {"access": "all"})
    assert locks_out(all_of_them, ["198.51.100.4"]) == ["198.51.100.4"]


# -- asking the service -----------------------------------------------------------


def script_curl(runner: FakeRunner, **kwargs: Any) -> None:
    runner.script(("curl", "-sS"), **kwargs)


@pytest.mark.parametrize("status", ["200", "204", "401", "403", "302"])
def test_a_service_that_answers_a_visitor_with_no_session_is_up(
    runner: FakeRunner, status: str
) -> None:
    script_curl(runner, stdout=status)

    answer = check_service(forward_auth_from(OAUTH), runner)  # type: ignore[arg-type]

    assert answer.reachable and answer.status == int(status)
    (call,) = runner.calls
    assert call[-2:] == ("--", OAUTH["url"])  # never read as an option


@pytest.mark.parametrize(
    ("code", "words"),
    [
        (7, "nothing accepts connections"),
        (28, "did not answer in time"),
        (6, "does not resolve"),
        (60, "not trusted"),
    ],
)
def test_a_service_that_does_not_answer_says_why(runner: FakeRunner, code: int, words: str) -> None:
    script_curl(runner, stdout="000", exit_code=code)

    answer = check_service(forward_auth_from(OAUTH), runner)  # type: ignore[arg-type]

    assert not answer.reachable and words in answer.detail


def test_a_service_that_fails_is_not_up_for_nginx_either(runner: FakeRunner) -> None:
    script_curl(runner, stdout="502")

    answer = check_service(forward_auth_from(OAUTH), runner)  # type: ignore[arg-type]

    assert not answer.reachable and answer.status == 502


# -- the site ---------------------------------------------------------------------


@pytest.fixture
def served(machine: Machine, runner: FakeRunner) -> Machine:
    script_curl(runner, stdout="401")
    machine.deploy(app_type="nodejs", tls=True)
    return machine


def test_every_request_is_asked_about_and_only_nginx_can_ask(served: Machine) -> None:
    access.set_policy("example.com", {"forward_auth": OAUTH})

    config = served.config()
    assert "auth_request /_noust_auth;" in config
    location = config.split("location = /_noust_auth {")[1].split("\n    }")[0]
    assert "internal;" in location
    assert "proxy_pass http://127.0.0.1:4180/oauth2/auth;" in location
    assert "proxy_pass_request_body off;" in location
    assert 'proxy_set_header Content-Length "";' in location
    assert "proxy_set_header X-Original-URL $scheme://$host$request_uri;" in location
    assert "proxy_set_header Host $host;" in location


def test_a_visitor_who_is_not_signed_in_is_sent_to_the_service(served: Machine) -> None:
    access.set_policy("example.com", {"forward_auth": OAUTH})

    config = served.config()
    assert "error_page 401 = @noust_signin;" in config
    signin = config.split("location @noust_signin {")[1].split("}")[0]
    assert "internal;" in signin
    assert "return 302 /oauth2/start?rd=$scheme://$host$request_uri;" in signin


def test_without_a_sign_in_the_answer_stays_401(served: Machine) -> None:
    access.set_policy("example.com", {"forward_auth": {**OAUTH, "sign_in": ""}})

    assert "@noust_signin" not in served.config() and "error_page 401" not in served.config()


def test_who_the_visitor_is_reaches_the_application_and_cannot_be_forged(served: Machine) -> None:
    access.set_policy("example.com", {"forward_auth": OAUTH})

    config = served.config()
    assert "auth_request_set $noust_fa_1 $upstream_http_x_auth_request_user;" in config
    assert "auth_request_set $noust_fa_2 $upstream_http_x_auth_request_email;" in config
    root = config.split("proxy_pass http://127.0.0.1:3000;")[1].split("}")[0]
    # Set on every request, so a header the client sent with that name is replaced by it.
    assert "proxy_set_header X-Auth-Request-User $noust_fa_1;" in root
    assert "proxy_set_header X-Auth-Request-Email $noust_fa_2;" in root


def test_the_service_may_refresh_the_visitors_session_cookie(served: Machine) -> None:
    access.set_policy("example.com", {"forward_auth": OAUTH})

    config = served.config()
    assert "auth_request_set $noust_fa_cookie $upstream_http_set_cookie;" in config
    assert "add_header Set-Cookie $noust_fa_cookie always;" in config


def test_over_plain_http_the_certificate_authority_is_not_asked_to_sign_in(
    machine: Machine, runner: FakeRunner
) -> None:
    script_curl(runner, stdout="401")
    machine.deploy(app_type="nodejs")

    access.set_policy("example.com", {"forward_auth": OAUTH})

    challenge = machine.config().split("location /.well-known/acme-challenge/ {")[1].split("}")[0]
    assert "auth_request off;" in challenge


def test_an_https_service_is_called_with_the_servers_name(served: Machine) -> None:
    access.set_policy(
        "example.com", {"forward_auth": {"url": "https://auth.example.org/verify", "headers": []}}
    )

    location = served.config().split("location = /_noust_auth {")[1].split("\n    }")[0]
    assert "proxy_pass https://auth.example.org/verify;" in location
    assert "proxy_ssl_server_name on;" in location


def test_together_with_a_password_both_are_wanted_unless_either_is_enough(served: Machine) -> None:
    SitePolicyManager().records.set_user(
        "example.com", "ana", "$6$salt$" + "a" * 86, created_by="t"
    )

    access.set_policy("example.com", {"basic_auth": True, "forward_auth": OAUTH})
    assert "satisfy all;" in served.config()

    access.set_policy("example.com", {"access": "any"})
    assert "satisfy any;" in served.config()


def test_a_service_alone_needs_no_satisfy(served: Machine) -> None:
    access.set_policy("example.com", {"forward_auth": OAUTH})

    assert "satisfy" not in served.config()


def test_a_proxied_prefix_hands_the_identity_on_too(served: Machine) -> None:
    access.set_policy(
        "example.com",
        {"forward_auth": OAUTH, "routes": [{"path": "/api", "kind": "proxy", "port": 4000}]},
    )

    api = served.config().split("location ^~ /api/ {")[1].split("\n    }")[0]
    assert "proxy_set_header X-Auth-Request-User $noust_fa_1;" in api


def test_a_site_without_it_renders_nothing_of_it(served: Machine) -> None:
    access.set_policy("example.com", {"noindex": True})

    for text in ("auth_request", "_noust_auth", "noust_fa_", "noust_signin"):
        assert text not in served.config()


# -- the guards -------------------------------------------------------------------


def test_a_service_that_does_not_answer_is_refused_and_nothing_changes(
    machine: Machine, runner: FakeRunner
) -> None:
    script_curl(runner, stdout="000", exit_code=7)
    machine.deploy(app_type="nodejs", tls=True)
    before = machine.config()

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"forward_auth": OAUTH})

    assert raised.value.field == "forward_auth"
    assert "nothing accepts connections" in raised.value.message
    assert "every visitor is refused" in raised.value.details
    assert machine.config() == before
    assert SitePolicyManager().get("example.com").forward_auth is None


def test_the_operator_may_go_on_knowing_it_is_not_up_yet(
    machine: Machine, runner: FakeRunner
) -> None:
    script_curl(runner, stdout="000", exit_code=7)
    machine.deploy(app_type="nodejs", tls=True)

    access.set_policy("example.com", {"forward_auth": OAUTH}, accept_unreachable_auth=True)

    assert "auth_request /_noust_auth;" in machine.config()


def test_changing_something_else_does_not_ask_the_service_again(
    served: Machine, runner: FakeRunner
) -> None:
    access.set_policy("example.com", {"forward_auth": OAUTH})
    script_curl(runner, stdout="000", exit_code=7)

    access.set_policy("example.com", {"noindex": True})

    assert SitePolicyManager().get("example.com").forward_auth is not None


@pytest.mark.parametrize("port", [80, 443, 8080])
def test_the_service_cannot_be_the_web_server_or_the_console(served: Machine, port: int) -> None:
    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"forward_auth": {"url": f"http://127.0.0.1:{port}/auth"}})

    assert raised.value.field == "forward_auth" and str(port) in raised.value.message


def test_it_can_be_turned_off(served: Machine) -> None:
    access.set_policy("example.com", {"forward_auth": OAUTH})

    access.set_policy("example.com", {"forward_auth": None})

    assert "auth_request" not in served.config()
    assert SitePolicyManager().get("example.com").is_default
