"""
A site's regular expressions, matched against a request someone else chose.

``POST /api/sites/{d}/route`` takes a draft configuration and a request: both
are the caller's. Python's ``re`` holds the GIL while it backtracks, so
``location ~ ^/(a+)+$`` against forty a's and a ``!`` stopped every thread of
the console for longer than anyone would wait. A regular expression that could
take long is now matched in a separate process with a deadline, and one that
did not finish is reported as not evaluated.
"""

from __future__ import annotations

import time

import pytest

from noust.core.runner import SubprocessRunner, set_runner
from noust.managers.siteconf import parse, route, structure

EVIL = "a" * 40 + "!"


@pytest.fixture
def real_runner():
    set_runner(SubprocessRunner())
    yield
    set_runner(None)


def _route(config: str, *, host: str = "example.com", path: str = "/") -> object:
    return route(structure(parse(config, "nginx")), host=host, path=path, scheme="http", port=80)


@pytest.mark.allow_subprocess
def test_a_catastrophic_location_regex_finishes_and_is_not_evaluated(real_runner: None) -> None:
    config = (
        "server { listen 80; server_name example.com;\n"
        "  location / { return 200; }\n"
        "  location ~ ^/(a+)+$ { return 403; }\n}\n"
    )
    started = time.monotonic()
    result = _route(config, path="/" + EVIL)
    assert time.monotonic() - started < 10
    assert any(step.code == "regex_skipped" for step in result.trace)  # type: ignore[attr-defined]


@pytest.mark.allow_subprocess
def test_a_catastrophic_server_name_regex_finishes_and_is_not_evaluated(
    real_runner: None,
) -> None:
    config = "server { listen 80; server_name ~^(a+)+$; return 200; }\n"
    started = time.monotonic()
    result = _route(config, host=EVIL)
    assert time.monotonic() - started < 10
    assert any(step.code == "regex_skipped" for step in result.trace)  # type: ignore[attr-defined]


def test_a_simple_regex_is_still_matched_in_this_process() -> None:
    """The common ones (\\.php$, ^/api/(.*)$) need no process: routing stays instant."""
    config = (
        "server { listen 80; server_name example.com;\n"
        "  location / { return 200; }\n"
        "  location ~ \\.php$ { return 403; }\n"
        "  location ~* ^/api/(.*)$ { return 404; }\n}\n"
    )
    assert _route(config, path="/index.php").location_id is not None  # type: ignore[attr-defined]
    api = _route(config, path="/API/x")
    assert any(step.code == "location_regex" for step in api.trace)  # type: ignore[attr-defined]


def test_patterns_and_inputs_over_the_caps_are_not_evaluated() -> None:
    config = (
        "server { listen 80; server_name example.com;\n"
        "  location / { return 200; }\n"
        f"  location ~ ^/{'x' * 600}$ {{ return 403; }}\n}}\n"
    )
    result = _route(config, path="/" + "x" * 600)
    assert any(step.code == "regex_skipped" for step in result.trace)  # type: ignore[attr-defined]
    long_path = _route(
        "server { listen 80; location ~ \\.php$ { return 403; } }\n", path="/" + "a" * 5000
    )
    assert any(step.code == "regex_skipped" for step in long_path.trace)  # type: ignore[attr-defined]


def test_the_bounded_check_sends_nested_quantifiers_out_of_process() -> None:
    from noust.managers.siteconf.regex import is_cheap

    assert is_cheap(r"\.php$")
    assert is_cheap(r"^/api/(.*)$")
    assert is_cheap(r"\.(jpg|png|css)$")
    assert not is_cheap(r"^(a+)+$")
    assert not is_cheap(r"^(a|a)*$")
    assert not is_cheap(r".*.*=")
    assert not is_cheap(r"(a)\1")
    assert not is_cheap("a?" * 20 + "a" * 20)
