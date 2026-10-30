# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One adversarial suite per class of input that reaches the machine (checklist L-09).

Noust turns text a person typed, or a repository carried, into paths, argument
lists, unit files, web server configuration and SQL. The defence is not
a list of bad strings but an allowlist at the one place each class of text is
judged (``docs/SECURITY-MODEL.md`` lists the classes and the validator of each).
This file attacks every one of those validators with generated text and with the
payloads that have beaten validators before, and holds each to two properties:

- **It answers or it refuses cleanly.** A validator returns a value or raises
  its own documented error. Any other exception (a ``ValueError`` from a NUL
  byte, an ``OSError`` from a name too long) is a crash on hostile input, which
  the API turns into a 500 and nobody planned.
- **What it accepts is inert where it goes.** An accepted value has no
  newline, NUL byte or other control character, no path separator where it is a
  name, no leading dash where it is an argument, and nothing outside the alphabet
  its sink can carry.

The payloads include the ones that were once accepted: a trailing newline (the
``$`` of an anchored pattern matches before it, so ``re.match(r"^a$", "a\\n")``
matched), which let ``validate_app_name("shop\\n")`` through.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from noust.core import domain_certificates, outbound
from noust.core.exceptions import NoustError, SecurityError, ValidationError
from noust.managers import site_routes
from noust.recipes import logo_paint, logo_png, logo_sanitize, logos
from noust.validators import (
    container,
    domain,
    environment,
    health,
    names,
    object_names,
    port,
    source,
)

SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

#: Characters that have broken a validator somewhere, in any encoding layer.
NASTY = "\n\r\x00\t \x0b\x0c\x1b\x7f;|&$`'\"\\<>(){}[]*?!#~%=,:/.-_@aZ09\u00e9\u202e\u2028\ufeff"

#: Payloads that beat validators elsewhere, each in a form a person or a
#: repository could actually send.
PAYLOADS = [
    "evil\n",
    "evil\r\n",
    "evil\n\n",
    "\nevil",
    "ev\nil",
    "ev\x00il",
    "evil\x00",
    "../../etc/passwd",
    "..",
    ".",
    "a/../b",
    "/etc/passwd",
    "a\\b",
    "-rf",
    "--upload-pack=touch /tmp/pwn",
    "--output=/etc/passwd",
    "$(id)",
    "`id`",
    "${IFS}x",
    "a; rm -rf /",
    "a && b",
    "a | b",
    "a b",
    "ext::sh -c touch% /tmp/pwn",
    "ext::sh -c id.git",
    "fd::17/foo",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "\u202eevil",
    "\ufeffevil",
    "%2e%2e/",
    "%0d%0a",
    "\uff21dmin",
    "\uff45vil.example.com",
    "a" * 300,
    "",
    " ",
    "\t",
]

HOSTILE = st.one_of(
    st.text(max_size=100),
    st.text(alphabet=NASTY, max_size=60),
    st.builds(
        lambda payload, tail: payload + tail,
        st.sampled_from(PAYLOADS),
        st.text(alphabet="\n\r\x00 .git-/", max_size=3),
    ),
    st.builds(
        lambda head, payload: head + payload,
        st.text(alphabet="abc012-._", max_size=6),
        st.sampled_from(PAYLOADS),
    ),
)

CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def verdict(
    function: Callable[..., Any], value: object, *args: Any, **kwargs: Any
) -> tuple[bool, Any]:
    """
    Run a validator; return whether it accepted and what it returned.

    Any exception other than a Noust error is a defect and fails the test with
    the input that caused it.
    """
    try:
        return True, function(value, *args, **kwargs)
    except NoustError:
        return False, None
    except Exception as exc:
        pytest.fail(
            f"{function.__module__}.{function.__name__}({value!r}) raised "
            f"{type(exc).__name__}: {exc} instead of refusing"
        )


# ---------------------------------------------------------------------------
# Names that become paths, unit names and SQL
# ---------------------------------------------------------------------------

NAME_VALIDATORS = [
    (names.validate_service_name, names.MAX_SERVICE_NAME_LENGTH, "@"),
    (names.validate_app_name, names.MAX_APP_NAME_LENGTH, ""),
    (names.validate_database_name, names.MAX_DATABASE_NAME_LENGTH, ""),
    (names.validate_database_user, names.MAX_DATABASE_USER_LENGTH, ""),
    (names.validate_filename, names.MAX_FILENAME_LENGTH, ""),
]


class TestNamesThatBecomePathsUnitsAndSql:
    @pytest.mark.parametrize(("validator", "limit", "extra"), NAME_VALIDATORS)
    @pytest.mark.parametrize("payload", [p for p in PAYLOADS if p])
    def test_the_known_payloads_are_all_refused(
        self, validator: Callable[[str], str], limit: int, extra: str, payload: str
    ) -> None:
        accepted, _ = verdict(validator, payload)

        # A payload that is also a plausible name ("a b" is not; "evil" is): only
        # the dangerous ones are listed, so none may be accepted.
        assert not accepted, f"{validator.__name__} accepted {payload!r}"

    @pytest.mark.parametrize(("validator", "limit", "extra"), NAME_VALIDATORS)
    @SETTINGS
    @given(value=HOSTILE)
    def test_what_is_accepted_is_one_inert_path_segment(
        self, validator: Callable[[str], str], limit: int, extra: str, value: str
    ) -> None:
        accepted, result = verdict(validator, value)

        if not accepted:
            return
        assert result == value, "a validator returns the name it was given, unchanged"
        assert result.isascii() and not CONTROL.search(result)
        assert not any(char in result for char in "/\\ \t\n\r\x00:;|&$`'\"<>(){}[]*?!#~%=,")
        assert 0 < len(result) <= limit
        assert result not in {".", ".."}
        assert result.lower() not in names.RESERVED_NAMES
        assert not result.startswith("-")

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_database_identifier_is_what_can_be_written_unquoted_in_sql(self, value: str) -> None:
        for validator in (names.validate_database_name, names.validate_database_user):
            accepted, result = verdict(validator, value)
            if accepted:
                assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", result)

    def test_a_trailing_newline_was_once_accepted_and_is_not(self) -> None:
        for validator, *_ in NAME_VALIDATORS:
            for name in ("shop\n", "shop\r\n", "shop\n\n"):
                with pytest.raises(ValidationError):
                    validator(name)

    @pytest.mark.parametrize("value", [None, 0, 1.5, b"shop", ["shop"], {"a": 1}])
    def test_a_name_that_is_not_text_is_refused_not_crashed_on(self, value: object) -> None:
        for validator, *_ in NAME_VALIDATORS:
            accepted, _ = verdict(validator, value)
            assert not accepted


class TestPathContainment:
    @SETTINGS
    @given(candidate=HOSTILE)
    def test_nothing_resolves_outside_the_base(self, candidate: str, tmp_path: Path) -> None:
        base = tmp_path / "apps"
        base.mkdir(exist_ok=True)
        try:
            result = names.resolve_within(base, candidate)
        except (ValidationError, SecurityError):
            return
        except Exception as exc:
            pytest.fail(f"resolve_within({candidate!r}) raised {type(exc).__name__}: {exc}")
        resolved_base = base.resolve()
        assert resolved_base in result.parents, f"{candidate!r} escaped to {result}"

    def test_a_link_inside_the_base_that_points_out_is_not_followed_out(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "apps"
        base.mkdir()
        (tmp_path / "secret").mkdir()
        (base / "link").symlink_to(tmp_path / "secret")

        with pytest.raises(SecurityError):
            names.resolve_within(base, "link/file")


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


class TestDomains:
    @SETTINGS
    @given(value=HOSTILE)
    def test_what_is_accepted_is_a_hostname_nginx_certbot_and_a_directory_can_carry(
        self, value: str
    ) -> None:
        accepted, result = verdict(domain.validate_domain, value)

        if not accepted:
            return
        assert re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", result), repr(result)
        assert ".." not in result and len(result) <= 253
        assert all(0 < len(label) <= 63 for label in result.split(".")), repr(result)

    @pytest.mark.parametrize("payload", [p for p in PAYLOADS if p.strip()])
    def test_no_known_payload_survives_as_a_domain_with_anything_odd_in_it(
        self, payload: str
    ) -> None:
        accepted, result = verdict(domain.validate_domain, payload)

        if accepted:
            assert re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", result), repr(result)

    @pytest.mark.parametrize("value", [None, 0, b"x.com", ["x.com"]])
    def test_a_domain_that_is_not_text_is_refused_not_crashed_on(self, value: object) -> None:
        accepted, _ = verdict(domain.validate_domain, value)

        assert not accepted


class TestTheLineageANamesCertificateIsKeptIn:
    """The name of a certbot lineage: a directory of ``/etc/letsencrypt/live`` and a ``--cert-name``."""

    @SETTINGS
    @given(value=HOSTILE)
    def test_what_is_accepted_is_a_directory_name_and_an_argument_certbot_can_carry(
        self, value: str
    ) -> None:
        accepted, result = verdict(domain_certificates.lineage_name, value)

        if not accepted:
            return
        assert re.fullmatch(r"_?[a-z0-9]([a-z0-9._-]*[a-z0-9])?", result), repr(result)
        assert ".." not in result and "/" not in result and len(result) <= 270
        assert not result.startswith("-")

    @pytest.mark.parametrize("payload", [p for p in PAYLOADS if p.strip()])
    def test_no_known_payload_survives_as_a_lineage_with_anything_odd_in_it(
        self, payload: str
    ) -> None:
        accepted, result = verdict(domain_certificates.lineage_name, payload)

        if accepted:
            assert re.fullmatch(r"_?[a-z0-9]([a-z0-9._-]*[a-z0-9])?", result), repr(result)

    @pytest.mark.parametrize("value", [None, 0, b"x.com", ["x.com"]])
    def test_a_name_that_is_not_text_is_refused_not_crashed_on(self, value: object) -> None:
        accepted, _ = verdict(domain_certificates.lineage_name, value)

        assert not accepted

    @pytest.mark.parametrize("value", ["own ", "Own", "shared\n", "", "all", None, 0])
    def test_only_the_two_modes_are_modes(self, value: object) -> None:
        accepted, _ = verdict(domain_certificates.check_mode, value)

        assert not accepted


# ---------------------------------------------------------------------------
# Environment variables and unit values
# ---------------------------------------------------------------------------


def unescaped_quotes(text: str) -> int:
    """Count the double quotes in ``text`` that are not preceded by an odd run of backslashes."""
    count = 0
    for match in re.finditer(r"(\\*)\"", text):
        if len(match.group(1)) % 2 == 0:
            count += 1
    return count


class TestEnvironmentAndUnitValues:
    @SETTINGS
    @given(value=HOSTILE)
    def test_a_variable_name_is_a_posix_identifier_or_refused(self, value: str) -> None:
        accepted, result = verdict(environment.validate_env_name, value)

        if accepted:
            assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", result)

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_value_has_no_control_character_when_accepted(self, value: str) -> None:
        accepted, result = verdict(environment.validate_env_value, value)

        if accepted:
            assert not CONTROL.search(result) or all(
                char in "\t" for char in CONTROL.findall(result)
            ), repr(result)

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_unit_value_cannot_end_its_line_when_accepted(self, value: str) -> None:
        accepted, result = verdict(environment.validate_unit_value, value, field="ExecStart")

        if accepted:
            assert "\n" not in result and "\r" not in result and "\x00" not in result

    @SETTINGS
    @given(value=HOSTILE)
    def test_an_escaped_value_cannot_close_the_quote_it_sits_in(self, value: str) -> None:
        accepted, result = verdict(environment.validate_env_value, value)
        if not accepted:
            return

        escaped = environment.escape_systemd_value(result)

        assert unescaped_quotes(escaped) == 0, f"{result!r} -> {escaped!r}"
        assert "%" not in escaped.replace("%%", "")
        assert "\n" not in escaped and "\r" not in escaped


class TestTheUnitTemplateKeepsEveryValueOnItsOwnLine:
    """
    Whatever a validator accepts, the template renders one line for it.

    The test does not know which directives a unit has (hardening adds some); it
    counts lines: a value that brought a newline with it would add one.
    """

    def render(self, **overrides: Any) -> str:
        from noust.managers.service_manager import ServiceManager

        context: dict[str, Any] = {
            "name": "shop-example-com",
            "description": "Shop",
            "user": "www-data",
            "group": "www-data",
            "working_directory": "/var/www/apps/shop-example-com",
            "command": "/usr/bin/node server.js",
            "environment": {"PORT": "3000", "NODE_ENV": "production"},
            "environment_file": "/var/www/apps/shop-example-com/.env",
            "resource_limits": [],
        }
        context.update(overrides)
        return ServiceManager().jinja_env.get_template("app.service.j2").render(**context)

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_hostile_description_a_command_and_a_variable_add_no_line(self, value: str) -> None:
        baseline = self.render()
        accepted, text = verdict(environment.validate_unit_value, value, field="Description")
        if not accepted:
            return
        try:
            rendered = self.render(
                description=text,
                command="/usr/bin/node " + text,
                working_directory="/var/www/apps/" + text,
                environment={"PORT": "3000", "NODE_ENV": text},
            )
        except NoustError:
            return

        assert len(rendered.split("\n")) == len(baseline.split("\n")), (
            f"{text!r} changed the shape of the unit:\n{rendered}"
        )
        directives = {
            line.split("=", 1)[0] for line in rendered.splitlines() if re.match(r"^[A-Z]\w+=", line)
        }
        assert directives == {
            line.split("=", 1)[0] for line in baseline.splitlines() if re.match(r"^[A-Z]\w+=", line)
        }, f"{text!r} added a directive"


# ---------------------------------------------------------------------------
# Sources, refs, commits, archives
# ---------------------------------------------------------------------------


class TestSources:
    @SETTINGS
    @given(value=HOSTILE)
    def test_a_source_is_refused_cleanly_or_is_inert(self, value: str) -> None:
        accepted, result = verdict(source.validate_source, value)

        if not accepted:
            return
        kind, normalised = result
        assert kind in {"git", "archive", "local"}
        assert not CONTROL.search(normalised), repr(normalised)
        assert not normalised.startswith("-"), "git would read it as an option"
        if kind != "local":
            assert " " not in normalised and not normalised.startswith(("ext::", "fd::", "file:"))
            assert re.match(r"^(https?://|ssh://|git://|git@[\w.-]+:)", normalised), normalised
        else:
            assert normalised.startswith("/"), "a local source is an absolute path"

    @pytest.mark.parametrize("payload", [p for p in PAYLOADS if p.strip()])
    def test_no_known_payload_becomes_a_source(self, payload: str) -> None:
        accepted, result = verdict(source.validate_source, payload)

        if accepted:
            kind, normalised = result
            assert (kind == "local" and normalised.startswith("/")) or not CONTROL.search(
                normalised
            )
            assert not normalised.startswith(("-", "ext::", "fd::")), (payload, result)

    def test_a_source_of_only_spaces_is_not_the_servers_working_directory(self) -> None:
        for blank in (" ", "\t", "  \n"):
            accepted, _ = verdict(source.validate_source, blank)
            assert not accepted

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_git_ref_is_never_an_option_and_never_climbs(self, value: str) -> None:
        from noust.managers.source_manager import validate_git_ref, validate_tag_name

        for validator in (validate_git_ref, validate_tag_name):
            accepted, result = verdict(validator, value)
            if accepted:
                assert not result.startswith("-") and ".." not in result
                assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._/+-]*", result), repr(result)

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_commit_id_is_hexadecimal_and_nothing_else(self, value: str) -> None:
        from noust.managers.source_manager import validate_commit_id

        accepted, result = verdict(validate_commit_id, value)

        if accepted:
            assert re.fullmatch(r"[0-9a-f]{4,64}", result)

    @SETTINGS
    @given(value=HOSTILE)
    def test_an_archive_url_is_http_or_https_without_a_control_character(self, value: str) -> None:
        from noust.managers.source_manager import validate_archive_url

        accepted, result = verdict(validate_archive_url, value)

        if accepted:
            assert result.lower().startswith(("http://", "https://")) and not CONTROL.search(result)

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_git_remote_is_a_url_git_can_clone_and_not_a_helper_or_an_option(
        self, value: str
    ) -> None:
        from noust.managers.source_manager import validate_git_remote_url

        accepted, result = verdict(validate_git_remote_url, value)

        if accepted:
            assert not CONTROL.search(result) and not result.startswith(("-", "ext::", "fd::"))
            assert "::" not in result.split("/", 1)[0]


# ---------------------------------------------------------------------------
# Health check path, ports, containers
# ---------------------------------------------------------------------------


class TestHealthCheckPath:
    @SETTINGS
    @given(value=HOSTILE)
    def test_it_is_a_path_on_the_application_and_cannot_name_another_host(self, value: str) -> None:
        accepted, result = verdict(health.check_health_path, value)

        if accepted:
            assert result.startswith("/") and not result.startswith("//")
            assert all("\x21" <= char <= "\x7e" for char in result)


class TestSiteRoutes:
    """A route's path and target are written into a file the web server loads as root."""

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_path_is_slash_separated_plain_segments_or_refused(self, value: str) -> None:
        accepted, result = verdict(site_routes.normal_path, value)

        if accepted:
            assert result.startswith("/") and not result.startswith("//")
            assert re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)+", result)
            assert ".." not in result.split("/") and "." not in result.split("/")
            assert not CONTROL.search(result)
            assert not result.startswith("/.well-known")

    @SETTINGS
    @given(value=HOSTILE, keep=st.booleans())
    def test_a_redirect_target_cannot_end_its_directive_or_name_another_scheme(
        self, value: str, keep: bool
    ) -> None:
        accepted, result = verdict(
            lambda text: site_routes.route_from(
                {"path": "/x", "kind": "redirect", "to": text, "keep_path": keep}
            ),
            value,
        )

        if accepted:
            target = result.to
            assert re.fullmatch(
                r"(?:https?://[A-Za-z0-9.-]+(?::\d+)?)?(?:/[^\s\"'$;{}#\\]*)?(?:\?[^\s\"'$;{}#\\]*)?",
                target,
            )
            assert not target.startswith("//")
            assert not CONTROL.search(target)
            assert not (keep and "?" in target)

    @SETTINGS
    @given(
        value=st.one_of(HOSTILE, st.integers(), st.floats(allow_nan=True), st.none(), st.booleans())
    )
    def test_a_proxy_port_is_a_number_in_range_or_refused(self, value: object) -> None:
        accepted, result = verdict(
            lambda port_value: site_routes.route_from(
                {"path": "/x", "kind": "proxy", "port": port_value}
            ),
            value,
        )

        if accepted:
            assert isinstance(result.port, int) and not isinstance(result.port, bool)
            assert 1 <= result.port <= 65535

    @SETTINGS
    @given(name=HOSTILE, value=HOSTILE)
    def test_a_route_header_is_one_line_of_plain_text(self, name: str, value: str) -> None:
        accepted, result = verdict(
            lambda pair: site_routes.route_from({"path": "/x", "headers": {pair[0]: pair[1]}}),
            (name, value),
        )

        if accepted:
            for header, text in result.headers.items():
                assert re.fullmatch(r"[A-Za-z0-9-]{1,64}", header)
                assert not CONTROL.search(text) and not re.search(r'["\\$%]', text)


class TestPorts:
    @SETTINGS
    @given(
        value=st.one_of(HOSTILE, st.integers(), st.floats(allow_nan=True), st.none(), st.booleans())
    )
    def test_a_port_is_a_number_in_range_or_refused(self, value: object) -> None:
        accepted, result = verdict(
            port.validate_port, value, check_available=False, allow_privileged=True
        )

        if accepted:
            assert isinstance(result, int) and not isinstance(result, bool)
            assert 1 <= result <= 65535


class TestContainerReferences:
    @SETTINGS
    @given(value=HOSTILE)
    def test_an_image_reference_is_not_an_option_and_has_no_whitespace(self, value: str) -> None:
        accepted, result = verdict(container.check_image_reference, value)

        if accepted:
            assert not result.startswith("-") and not CONTROL.search(result) and " " not in result
            assert "://" not in result

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_digest_is_sha256_hex(self, value: str) -> None:
        accepted, result = verdict(container.check_digest, value)

        if accepted:
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", result)

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_container_port_is_in_range(self, value: object) -> None:
        accepted, result = verdict(container.check_container_port, value)

        if accepted:
            assert 1 <= result <= 65535


# ---------------------------------------------------------------------------
# Identifiers of things Noust wrote
# ---------------------------------------------------------------------------


class TestIdentifiersOfThingsNoustWrote:
    @SETTINGS
    @given(value=HOSTILE)
    def test_a_release_id_names_a_directory_noust_made(self, value: str) -> None:
        from noust.core.appdir.releases import is_release_id

        if is_release_id(value):
            assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]+(-\d+)?", value), repr(value)

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_backup_id_names_an_archive_noust_made(self, value: str) -> None:
        from noust.managers.backup_manager import BACKUP_ID_PATTERN

        if BACKUP_ID_PATTERN.fullmatch(value):
            assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*_\d{8}_\d{6}", value), repr(value)

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_backup_destination_name_becomes_an_rclone_remote_name(self, value: str) -> None:
        from noust.managers.backup_destinations import validate_destination_name

        accepted, result = verdict(validate_destination_name, value)

        if accepted:
            assert re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", result)


# ---------------------------------------------------------------------------
# The names an operator gives to objects of Noust: one table, one rule
# ---------------------------------------------------------------------------


class TestTheNamesOfObjects:
    @SETTINGS
    @given(value=HOSTILE, kind=st.sampled_from(sorted(object_names.KINDS)))
    def test_a_name_that_is_accepted_is_inert_where_every_kind_of_it_goes(
        self, value: str, kind: str
    ) -> None:
        accepted, result = verdict(object_names.check_name, kind, value)

        if accepted:
            rule = object_names.rule_for(kind)
            assert result == value and 1 <= len(result) <= rule.max_length
            # A letter first, then lower-case words: nothing a unit file, a path, a SQL
            # identifier, a URL segment or a header could be misled by.
            assert re.fullmatch(r"[a-z][a-z0-9_-]*", result), repr(result)
            assert not CONTROL.search(result)
            if rule.shape == "dashes":
                assert "_" not in result and "--" not in result and not result.endswith("-")
            if rule.shape == "underscores":
                assert "-" not in result and "__" not in result and not result.endswith("_")

    @SETTINGS
    @given(value=HOSTILE, room=st.integers(min_value=-3, max_value=40))
    def test_a_team_channel_name_leaves_room_for_the_team_prefix_of_its_key(
        self, value: str, room: int
    ) -> None:
        accepted, result = verdict(object_names.check_name, "team_channel", value, room)

        if accepted:
            assert 1 <= len(result) <= room

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_every_payload_that_beat_a_validator_elsewhere_is_refused_by_every_kind(
        self, payload: str
    ) -> None:
        for kind in object_names.KINDS:
            assert not object_names.is_name(kind, payload), (kind, payload)


# ---------------------------------------------------------------------------
# The group an operator names for an application's new account
# ---------------------------------------------------------------------------


class TestTheGroupOfANewAccount:
    @SETTINGS
    @given(value=HOSTILE)
    def test_a_group_name_reaches_usermod_as_one_inert_argument(self, value: str) -> None:
        accepted, result = verdict(names.validate_group_name, value)

        if accepted:
            assert result == value and 1 <= len(result) <= 32
            assert re.fullmatch(r"[a-z_][a-z0-9_-]*", result), repr(result)
            assert not result.startswith("-") and not CONTROL.search(result)

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_every_payload_that_beat_a_validator_elsewhere_is_refused(self, payload: str) -> None:
        if payload and re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", payload):
            return
        accepted, _ = verdict(names.validate_group_name, payload)
        assert not accepted, repr(payload)


# ---------------------------------------------------------------------------
# Replicas: the name, the host and the join code a person pastes
# ---------------------------------------------------------------------------


class TestReplicasAndTheirJoinCodes:
    @SETTINGS
    @given(value=HOSTILE)
    def test_a_replica_name_becomes_a_role_a_slot_a_pg_hba_line_and_a_file(
        self, value: str
    ) -> None:
        from noust.validators import replica

        accepted, result = verdict(replica.check_replica_name, value)

        if accepted:
            assert re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", result), repr(result)
            assert len(result) <= replica.MAX_NAME_LENGTH
            assert not CONTROL.search(result)

    @SETTINGS
    @given(value=HOSTILE)
    def test_the_host_a_replica_connects_to_reaches_libpq_and_a_passfile(self, value: str) -> None:
        from noust.validators import replica

        accepted, result = verdict(replica.check_replica_host, value)

        if accepted:
            assert result == value
            assert not CONTROL.search(result) and not any(c in result for c in " /;'\"$`")

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_join_code_is_refused_cleanly_or_is_what_encode_makes(self, value: str) -> None:
        from noust.managers.database import replication_token

        accepted, result = verdict(replication_token.parse, replication_token.PREFIX + value)

        if accepted:
            assert replication_token.encode(result) == (replication_token.PREFIX + value).strip()

    @SETTINGS
    @given(value=HOSTILE)
    def test_anything_after_the_prefix_is_refused_cleanly(self, value: str) -> None:
        from noust.managers.database import replication_token

        verdict(replication_token.parse, value)

    @SETTINGS
    @given(value=HOSTILE)
    def test_the_address_of_a_replica_is_a_network_or_nothing(self, value: str) -> None:
        import ipaddress

        from noust.managers.database import pg_hba

        try:
            result = pg_hba.replica_address(value)
        except ValueError:
            return
        network = ipaddress.ip_network(result)
        assert network.prefixlen > 0
        assert not CONTROL.search(result) and " " not in result

    @SETTINGS
    @given(value=HOSTILE)
    def test_an_account_written_into_pg_hba_needs_no_quoting(self, value: str) -> None:
        from noust.managers.database import pg_hba

        try:
            text = pg_hba.replication_block(value, "10.0.0.5")
        except ValueError:
            return
        assert text.count("\n") == 4, repr(text)
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value)


# ---------------------------------------------------------------------------
# What an operator chooses for an instance, and what a move of a database names
# ---------------------------------------------------------------------------


class TestWhatAnOperatorChoosesForAnInstance:
    @SETTINGS
    @given(value=st.lists(HOSTILE, max_size=6))
    def test_a_command_or_arguments_are_words_of_one_line_and_never_a_secret_flag(
        self, value: list[str]
    ) -> None:
        from noust.core.db_instance_store import check_words

        accepted, result = verdict(lambda words: check_words(words, field="args"), value)

        if accepted:
            assert all(word and not CONTROL.search(word) for word in result)
            assert all(
                word.split("=", 1)[0] not in ("--requirepass", "--masterauth") for word in result
            )

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_line_is_never_taken_for_words(self, value: str) -> None:
        from noust.core.db_instance_store import check_words

        accepted, _ = verdict(lambda text: check_words(text, field="args"), value)

        assert accepted is False

    @SETTINGS
    @given(value=HOSTILE)
    def test_an_image_the_operator_chose_is_pinned_and_is_neither_an_option_nor_two_images(
        self, value: str
    ) -> None:
        from noust.managers.database.instance_catalog import parse_custom_image

        accepted, result = verdict(parse_custom_image, value)

        if accepted:
            assert result.digest and re.fullmatch(
                r"sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128}", result.digest
            )
            assert not result.repository.startswith("-")
            # Lower case, but for a registry's host name.
            path = result.repository.partition("/")[2] or result.repository
            assert path == path.lower()
            assert not CONTROL.search(result.repository) and "@" not in result.repository
            assert " " not in result.repository and "://" not in result.repository

    @pytest.mark.parametrize(
        "reference",
        [
            "acme/pg:17",
            "acme/pg@sha256:" + "a" * 63,
            "acme/pg@sha256:" + "a" * 64 + "\n",
            "acme/pg@sha256:" + "a" * 64 + "@sha256:" + "b" * 64,
            "--privileged@sha256:" + "a" * 64,
            "https://acme/pg@sha256:" + "a" * 64,
            "ACME/pg@sha256:" + "a" * 64,
        ],
    )
    def test_the_references_that_have_beaten_other_parsers(self, reference: str) -> None:
        from noust.managers.database.instance_catalog import parse_custom_image

        with pytest.raises(ValidationError):
            parse_custom_image(reference)


class TestAnImageNoustPinsAtCreation:
    """An image given by registry and tag is read for its digest when the instance is made (4.1)."""

    @SETTINGS
    @given(value=HOSTILE)
    def test_what_is_accepted_is_a_repository_and_a_tag_that_are_neither_an_option_nor_a_shell(
        self, value: str
    ) -> None:
        from noust.managers.database.instance_catalog import parse_custom_image

        accepted, result = verdict(parse_custom_image, value, pin_later=True)

        if accepted:
            assert result.digest == "" or re.fullmatch(r"sha256:[0-9a-f]{64}", result.digest)
            # Without a digest the tag is what Noust reads one from: it is never missing.
            assert result.digest or result.tag
            assert not result.repository.startswith("-")
            assert not CONTROL.search(result.repository) and "@" not in result.repository
            assert " " not in result.repository and "://" not in result.repository
            assert result.tag is None or not CONTROL.search(result.tag)

    @pytest.mark.parametrize(
        "reference",
        [
            "acme/pg",
            "acme/pg:",
            "acme/pg:-v",
            "acme/pg:a b",
            "acme/pg:1\n",
            "acme/pg:1:2",
            "--privileged:1",
            "https://acme/pg:1",
            "ACME/pg:1",
            "acme/pg:1@sha256:" + "z" * 64,
        ],
    )
    def test_the_references_that_have_beaten_other_parsers(self, reference: str) -> None:
        from noust.managers.database.instance_catalog import parse_custom_image

        with pytest.raises(ValidationError):
            parse_custom_image(reference, pin_later=True)


class TestTheKeyOfADatabaseServer:
    """The key a default is chosen by: the host's engine, or an instance Noust runs (4.1)."""

    @SETTINGS
    @given(value=HOSTILE)
    def test_what_is_accepted_is_an_engine_and_at_most_the_name_of_an_instance(
        self, value: str
    ) -> None:
        from noust.validators.db_instance import check_server_key

        accepted, result = verdict(check_server_key, value)

        if accepted:
            assert result == value
            engine, _, name = result.partition("@")
            assert re.fullmatch(r"[a-z][a-z0-9]{1,23}", engine)
            assert "@" not in name and "." not in name and "/" not in name
            assert not CONTROL.search(result) and " " not in result

    @pytest.mark.parametrize(
        "key",
        [
            "",
            "mysql@",
            "@a",
            "mysql@a@b",
            "mysql@a.b",
            "mysql@../x",
            "mysql@a\n",
            "MySQL",
            "my sql",
        ],
    )
    def test_the_keys_that_have_beaten_other_parsers(self, key: str) -> None:
        from noust.validators.db_instance import check_server_key

        with pytest.raises(ValidationError):
            check_server_key(key)

    def test_a_value_that_is_not_text_is_refused_not_crashed_on(self) -> None:
        from noust.validators.db_instance import check_server_key

        for value in (None, 5, ["mysql"], b"mysql"):
            with pytest.raises(ValidationError):
                check_server_key(value)


class TestWhatAMoveOfADatabaseNames:
    @SETTINGS
    @given(value=HOSTILE)
    def test_a_database_name_becomes_a_file_a_path_and_a_statement(self, value: str) -> None:
        from noust.web.fleet_database_actions import _database_name

        accepted, result = verdict(lambda name: _database_name(name, "database"), value)

        if accepted:
            assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_$-]*", result)
            assert len(result) <= 64 and not CONTROL.search(result)

    @SETTINGS
    @given(value=HOSTILE)
    def test_an_engine_is_one_the_registry_knows_and_that_travels_as_a_dump(
        self, value: str
    ) -> None:
        from noust.web.fleet_database_actions import _engine_key

        accepted, result = verdict(lambda text: _engine_key(text, "engine"), value)

        if accepted:
            assert result.partition("@")[0] in ("postgresql", "mysql", "mongodb")
            assert not CONTROL.search(result) and " " not in result


# ---------------------------------------------------------------------------
# Where code may be deployed from: the entries of security.allowed_sources
# ---------------------------------------------------------------------------


class TestAnAllowedSourceIsComparedAsText:
    """An entry decides what is deployed as this server, and is compared against a URL as text."""

    @pytest.mark.parametrize("payload", [p for p in PAYLOADS if p.strip()])
    def test_the_known_payloads_are_never_stored_as_they_came(self, payload: str) -> None:
        from noust.validators import origins

        accepted, result = verdict(origins.check_allowed_source, payload)

        if accepted:
            assert result == payload.strip().lower() or result.startswith(
                payload.strip().split("/", 1)[0].lower()
            ), repr(result)
            assert not CONTROL.search(result) and " " not in result

    @SETTINGS
    @given(value=HOSTILE)
    def test_what_is_stored_is_one_clean_line_that_stores_as_itself(self, value: str) -> None:
        from noust.validators import origins

        accepted, result = verdict(origins.check_allowed_source, value)

        if accepted:
            assert not CONTROL.search(result) and result.isascii()
            assert not any(char in result for char in " ;|&$`'\"<>(){}[]\\,@:?#%")
            assert result == origins.check_allowed_source(result)
            assert len(result) <= origins.MAX_ENTRY_LENGTH

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_stored_entry_means_to_the_matcher_what_it_means_to_the_validator(
        self, value: str
    ) -> None:
        from noust.core.ens import origins as matcher
        from noust.validators import origins

        accepted, result = verdict(origins.parse_allowed_source, value)

        if accepted and result.keyword is None:
            host, path = matcher._pattern_parts(result.text)
            assert host == result.host
            assert path == tuple(part.lower() for part in result.path)

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_list_typed_with_brackets_never_stores_one(self, value: str) -> None:
        from noust.core.config import ConfigError, parse_list_text

        try:
            items = parse_list_text(value)
        except ConfigError:
            return
        assert all("[" not in item and "]" not in item for item in items)
        assert all(item == item.strip() and item for item in items)


# ---------------------------------------------------------------------------
# The runner's own refusal
# ---------------------------------------------------------------------------


class TestTheRunnerRefusesWhatCannotBeAnArgumentList:
    @pytest.mark.parametrize("value", ["ls; rm -rf /", b"ls", "", "echo $(id)"])
    def test_a_string_is_never_split_into_a_command(self, value: object) -> None:
        from noust.core.runner import _validate

        with pytest.raises(ValueError, match="not a string"):
            _validate(value)  # type: ignore[arg-type]

    @SETTINGS
    @given(args=st.lists(st.text(alphabet=NASTY, max_size=20), min_size=1, max_size=5))
    def test_a_nul_byte_never_reaches_an_exec(self, args: list[str]) -> None:
        from noust.core.runner import _validate

        try:
            result = _validate(args)
        except ValueError:
            assert any("\x00" in arg for arg in args)
            return
        assert all("\x00" not in arg for arg in result)

    def test_an_empty_argument_list_is_refused(self) -> None:
        from noust.core.runner import _validate

        with pytest.raises(ValueError, match="empty"):
            _validate([])


class TestRepositoriesAndNamesThatBecomePartsOfAnApiPath:
    @pytest.mark.parametrize("payload", [p for p in PAYLOADS if p])
    def test_no_known_payload_is_a_repository(self, payload: str) -> None:
        for value in (payload, f"octo/{payload}", f"{payload}/hello"):
            accepted, result = verdict(source.github_repository_parts, value)

            assert not accepted or all(
                part.strip(".") and "/" not in part and not CONTROL.search(part) for part in result
            ), (value, result)

    @SETTINGS
    @given(value=HOSTILE)
    def test_what_is_accepted_is_two_inert_names(self, value: str) -> None:
        accepted, result = verdict(source.github_repository_parts, value)

        if not accepted:
            return
        owner, repo = result
        assert f"{owner}/{repo}" == value, "the names are returned as they were given"
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner), owner
        assert re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo) and repo.strip("."), repo

    @SETTINGS
    @given(value=HOSTILE)
    def test_what_becomes_a_segment_is_one_and_moves_nothing(self, value: str) -> None:
        try:
            segment = outbound.path_segment(value)
        except outbound.UnsafePath:
            return
        assert "/" not in segment and not CONTROL.search(segment)
        assert segment not in {"", ".", ".."}
        assert outbound.api_path(f"/repos/{segment}/branches") == f"/repos/{segment}/branches"

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_path_is_sent_or_it_is_plain(self, value: str) -> None:
        for path in (value, f"/{value}", f"/repos/{value}/branches"):
            try:
                accepted = outbound.api_path(path)
            except outbound.UnsafePath:
                continue
            assert accepted == path
            assert path.startswith("/") and not path.startswith("//")
            assert not CONTROL.search(path) and " " not in path and "\\" not in path
            assert all(part not in {".", ".."} for part in path.split("?", 1)[0].split("/")), path


# ---------------------------------------------------------------------------
# What a push of the fleet's settings is signed with, and where it is set
# ---------------------------------------------------------------------------

SETTING_KINDS = ["channel", "alert_rule", "backup_destination", "registry", "variable"]


class TestTheSettingsOfTheFleet:
    @SETTINGS
    @given(value=HOSTILE)
    def test_a_scope_is_the_fleet_or_one_server_and_nothing_else(self, value: str) -> None:
        from noust.fleet.settings.kinds import check_scope

        accepted, result = verdict(check_scope, value)

        if accepted:
            assert result == value and not CONTROL.search(result)
            assert result == "fleet" or re.fullmatch(r"node:[a-z0-9][a-z0-9-]*", result), result

    @SETTINGS
    @given(value=HOSTILE, kind=st.sampled_from(SETTING_KINDS))
    def test_the_name_of_a_setting_is_inert_where_it_goes(self, value: str, kind: str) -> None:
        from noust.fleet.settings.kinds import kind_of

        accepted, result = verdict(kind_of(kind).check_name, value)

        # It is a segment of a path to the server's own route, an argument and a secret's name.
        if accepted:
            assert result == value and not CONTROL.search(result)
            assert not any(char in result for char in "/\\ \t\n\r\x00:;|&$`'\"<>(){}[]*?!#~%=,")

    @SETTINGS
    @given(value=HOSTILE)
    def test_what_a_node_reads_as_a_signature_is_exactly_one_and_forges_no_field(
        self, value: str
    ) -> None:
        from noust.fleet.settings.signature import SyncSignature

        accepted, parsed = verdict(SyncSignature.parse, value, "change:c0123456789ab")

        if accepted and parsed is not None:
            signature = parsed.signature
            assert signature.actor == value, "the header is read back as it was written"
            assert not CONTROL.search(value) and "\n" not in value
            assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,63}", signature.person)
            assert re.fullmatch(r"c[0-9a-f]{12}", signature.change)

    @pytest.mark.parametrize("payload", [p for p in PAYLOADS if p])
    def test_a_payload_in_the_person_or_the_change_is_refused(self, payload: str) -> None:
        from noust.fleet.settings.signature import SyncSignature

        for person, change in ((payload, "c0123456789ab"), ("ana", payload)):
            with pytest.raises(NoustError):
                SyncSignature(person=person, change=change)


# ---------------------------------------------------------------------------
# What a channel of the fleet is routed by
# ---------------------------------------------------------------------------


class TestTheRouteOfAChannelOfTheFleet:
    @SETTINGS
    @given(value=HOSTILE)
    def test_a_server_pattern_is_a_name_and_nothing_a_pattern_cannot_hold(self, value: str) -> None:
        from noust.fleet.settings.reach import check_route, servers_of

        channel = {"type": "slack", "route": {"servers": [value]}}
        accepted, _ = verdict(check_route, channel)

        # It is matched against a server's name with fnmatch and is never part of a command.
        if accepted:
            for entry in servers_of(channel):
                assert re.fullmatch(r"[A-Za-z0-9*?._:/@-]{1,253}", entry), entry
                assert not CONTROL.search(entry)

    @SETTINGS
    @given(value=HOSTILE, field=st.sampled_from(["apps", "tags", "min_severity", "servers"]))
    def test_a_route_that_is_accepted_is_decided_without_failing(
        self, value: str, field: str
    ) -> None:
        from noust.fleet.settings.reach import check_route, explain

        channel = {"type": "slack", "route": {field: value if field == "min_severity" else [value]}}
        accepted, _ = verdict(check_route, channel)

        # Where a route is accepted the decision on any server and any application is a verdict,
        # not a crash on a pattern nobody expected.
        if accepted:
            apps = [{"domain": value, "tags": [value]}]
            assert explain("channel", channel, "web-1", apps).reaches in (True, False)

    @pytest.mark.parametrize("payload", [p for p in PAYLOADS if p and p.strip()])
    def test_a_payload_in_a_list_of_a_route_is_refused_or_read_as_the_name_it_trims_to(
        self, payload: str
    ) -> None:
        from noust.core.notifications.routing import Route
        from noust.fleet.settings.reach import check_route, servers_of

        for field in ("servers", "apps", "tags"):
            channel = {"type": "slack", "route": {field: [payload]}}
            accepted, _ = verdict(check_route, channel)
            if not accepted:
                continue
            # A trailing newline is trimmed like any space around a name; nothing else is let in.
            read = (
                servers_of(channel)
                if field == "servers"
                else getattr(Route.from_mapping(channel["route"]), field)
            )
            for entry in read:
                assert re.fullmatch(r"[A-Za-z0-9*?._:/@-]{1,253}", entry), (field, entry)


# ---------------------------------------------------------------------------
# An event of a code host that a central forwards to a server
# ---------------------------------------------------------------------------


class TestTheEventsACentralForwards:
    """What a server acts on comes from a repository's text, through a central it trusts to deploy."""

    SHA = "a" * 40
    PUSH = {
        "forge": "github",
        "repository": "you/app",
        "clone_url": "https://github.com/you/app.git",
        "branch": "main",
        "head_sha": SHA,
    }

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_branch_that_is_accepted_is_inert_as_an_argument_and_as_a_path(
        self, value: str
    ) -> None:
        from noust.validators.forge_event import parse_forwarded_event

        accepted, event = verdict(parse_forwarded_event, "push", {**self.PUSH, "branch": value})

        if accepted:
            assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._/+@-]{0,254}", event.branch)
            assert ".." not in event.branch and "//" not in event.branch
            assert not event.branch.endswith(("/", ".", ".lock"))

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_repository_that_is_accepted_is_one_segment_pair_of_a_request_path(
        self, value: str
    ) -> None:
        from noust.validators.forge_event import parse_forwarded_event

        accepted, event = verdict(parse_forwarded_event, "push", {**self.PUSH, "repository": value})

        if accepted:
            owner, _, name = event.repository.partition("/")
            assert owner and name and "/" not in name
            assert owner.strip(".") and name.strip(".")
            assert not CONTROL.search(event.repository)

    @SETTINGS
    @given(value=HOSTILE)
    def test_a_commit_and_an_address_that_are_accepted_carry_nothing_but_their_alphabet(
        self, value: str
    ) -> None:
        from noust.validators.forge_event import parse_forwarded_event

        accepted, event = verdict(parse_forwarded_event, "push", {**self.PUSH, "head_sha": value})
        if accepted:
            assert re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", event.head_sha)
        accepted, event = verdict(parse_forwarded_event, "push", {**self.PUSH, "clone_url": value})
        if accepted:
            assert event.clone_url.startswith("https://") and "@" not in event.clone_url
            assert not CONTROL.search(event.clone_url) and " " not in event.clone_url

    @SETTINGS
    @given(value=HOSTILE, field=st.sampled_from(["title", "author", "sender", "head_message"]))
    def test_the_free_text_of_a_pull_request_has_no_control_character(
        self, value: str, field: str
    ) -> None:
        from noust.core.forge_events import PullRequestAction, PullRequestEvent, event_to_dict
        from noust.validators.forge_event import parse_forwarded_event

        base = event_to_dict(
            PullRequestEvent(
                forge=source_forge(),
                action=PullRequestAction.OPENED,
                repository="you/app",
                clone_url="https://github.com/you/app.git",
                number=1,
                title="t",
                branch="b",
                base_branch="main",
                head_sha=self.SHA,
            )
        )

        accepted, event = verdict(parse_forwarded_event, "pull_request", {**base, field: value})

        if accepted:
            text = getattr(event, field)
            # A commit message keeps its line breaks and a tab; nothing else that can end a line.
            assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text)
            if field != "head_message":
                assert "\n" not in text and "\r" not in text

    @pytest.mark.parametrize("payload", [p for p in PAYLOADS if p])
    def test_the_payloads_that_beat_other_validators_are_refused_in_every_name_of_an_event(
        self, payload: str
    ) -> None:
        from noust.validators.forge_event import parse_forwarded_event

        for field in ("branch", "repository", "head_sha"):
            accepted, _ = verdict(parse_forwarded_event, "push", {**self.PUSH, field: payload})
            assert not accepted, (field, payload)

    @SETTINGS
    @given(
        data=st.recursive(
            st.none() | st.booleans() | st.integers() | st.text(max_size=20),
            lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=8), c, max_size=4),
            max_leaves=12,
        )
    )
    def test_whatever_arrives_is_read_or_refused_and_never_crashes(self, data: Any) -> None:
        from noust.validators.forge_event import parse_forwarded_event

        for kind in ("push", "tag", "pull_request"):
            verdict(parse_forwarded_event, kind, data)


def source_forge() -> Any:
    """The code host the events of these tests come from."""
    from noust.core.forge_events import Forge

    return Forge.GITHUB


class TestTheRecordsOfAZoneOfNoustDns:
    """What a person writes in a zone Noust DNS serves, which becomes a zone file (4.1, item 104)."""

    @SETTINGS
    @given(value=HOSTILE)
    def test_what_is_accepted_as_a_zone_is_a_plain_domain(self, value: str) -> None:
        from noust.validators.dns_zone import check_zone_name

        accepted, result = verdict(check_zone_name, value)

        if accepted:
            assert re.fullmatch(
                r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", result
            )
            assert len(result) <= 253 and ".." not in result
            assert check_zone_name(result) == result

    @SETTINGS
    @given(value=HOSTILE)
    def test_what_is_accepted_as_the_name_of_a_record_is_a_relative_name(self, value: str) -> None:
        from noust.validators.dns_zone import check_record_name

        accepted, result = verdict(check_record_name, value, "example.com")

        if accepted:
            assert result == "@" or re.fullmatch(r"(\*|[a-z0-9_-]+)(\.[a-z0-9_-]+)*", result)
            assert not CONTROL.search(result) and " " not in result and "$" not in result
            assert check_record_name(result, "example.com") == result

    @SETTINGS
    @given(
        kind=st.sampled_from(["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SRV", "CAA", "PTR", "DS"]),
        value=HOSTILE,
    )
    def test_what_is_accepted_as_content_is_canonical_and_has_no_line_break(
        self, kind: str, value: str
    ) -> None:
        from noust.validators.dns_zone import check_record

        accepted, result = verdict(check_record, kind, value)

        if accepted:
            assert not CONTROL.search(result)
            assert check_record(kind, result) == result
            if kind != "TXT":
                assert "$" not in result and ";" not in result and "(" not in result

    @pytest.mark.parametrize("payload", [p for p in PAYLOADS if p.strip()])
    def test_the_payloads_that_beat_other_validators_are_refused_everywhere_a_zone_takes_text(
        self, payload: str
    ) -> None:
        from noust.validators.dns_zone import (
            check_contact,
            check_hostname,
            check_record,
            check_record_name,
            check_zone_name,
        )

        for function in (check_zone_name, check_hostname, check_contact):
            if function is check_hostname and payload == ".":
                continue  # the root is how a null MX says "no mail", and only a host name may be it
            accepted, _ = verdict(function, payload)
            assert not accepted, (function.__name__, payload)
        accepted, _ = verdict(check_record_name, payload, "example.com")
        assert not accepted, payload
        for kind in ("A", "AAAA", "CNAME", "MX", "NS", "SRV", "CAA", "PTR", "DS"):
            accepted, _ = verdict(check_record, kind, payload)
            assert not accepted, (kind, payload)

    @SETTINGS
    @given(
        data=st.recursive(
            st.none() | st.booleans() | st.integers() | st.text(max_size=20),
            lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=8), c, max_size=4),
            max_leaves=12,
        )
    )
    def test_whatever_arrives_is_judged_or_refused_and_never_crashes(self, data: Any) -> None:
        from noust.validators import dns_zone

        verdict(dns_zone.check_zone_name, data)
        verdict(dns_zone.check_hostname, data)
        verdict(dns_zone.check_nameservers, data)
        verdict(dns_zone.check_ttl, data)
        verdict(dns_zone.check_record_type, data)
        verdict(dns_zone.check_record_name, data, "example.com")
        verdict(dns_zone.check_record, "TXT", data)


# ---------------------------------------------------------------------------
# A logo a project drew, served to a browser from the console's own origin
# ---------------------------------------------------------------------------

SVG_HEAD = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 24 24"'
SVG_FRAGMENTS = [
    "<script>alert(1)</script>",
    '<script xlink:href="https://evil.example/x.js"/>',
    '<foreignObject width="9" height="9"><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>',
    '<image href="https://evil.example/x.png" width="9" height="9"/>',
    '<image xlink:href="data:image/png;base64,iVBORw0KGgo=" width="9" height="9"/>',
    '<use href="https://evil.example/sprite.svg#a"/>',
    '<use xlink:href="data:image/svg+xml;base64,PHN2Zy8+"/>',
    '<a xlink:href="javascript:alert(1)"><path d="M0 0h9v9z"/></a>',
    "<style>@import url(https://evil.example/x.css);path{fill:url(http://evil.example/p.svg#p)}</style>",
    '<path d="M0 0h9v9z" onload="alert(1)" onclick="alert(2)" onerror="alert(3)"/>',
    '<path d="M0 0h9v9z" style="fill:url(https://evil.example/a.svg#x);stroke:expression(alert(1))"/>',
    '<path d="M0 0h9v9z" fill="url(//evil.example/a.svg#x)" stroke="url(file:///etc/passwd)"/>',
    '<path d="M0 0h9v9z" fill="java&#115;cript:alert(1)"/>',
    '<path d="M0 0h9v9z" fill="url(#missing)" clip-path="url(#missing)"/>',
    '<set attributeName="href" to="javascript:alert(1)"/>',
    '<animate attributeName="fill" values="url(https://evil.example/a)" dur="1s"/>',
    '<mask id="m"><rect width="9" height="9"/></mask><path d="M0 0h9v9z" mask="url(#m)"/>',
    '<pattern id="p" width="2" height="2"><rect width="1" height="1"/></pattern><path d="M0 0h9v9z" fill="url(#p)"/>',
    '<text x="1" y="1">x</text>',
    '<path d="M0 0h9v9z"/>',
    '<g id="a"><use href="#a"/></g><use href="#a"/>',
    '<svg><path d="M0 0h9v9z"/></svg>',
    "<!-- a comment --><?xml-stylesheet href='https://evil.example/a.css'?>",
    '<path d="M0 0 L' + "1 " * 4000 + 'z"/>',
    '<path d="' + "M0 0 " * 10 + 'e1e999 z"/>',
    '<path d="M0 0a1 1 0 011 1" transform="matrix(1e308,0,0,1e308,0,0)"/>',
]
SVG_DOCTYPES = [
    "",
    '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">',
    '<!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>',
    '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>',
]
HOSTILE_SVG = st.builds(
    lambda doctype, attributes, fragments: doctype
    + SVG_HEAD
    + attributes
    + ">"
    + "".join(fragments)
    + "</svg>",
    st.sampled_from(SVG_DOCTYPES),
    st.sampled_from(
        [
            "",
            ' onload="alert(1)"',
            ' style="fill:url(https://evil.example/a)"',
            ' xml:base="https://evil.example/"',
        ]
    ),
    st.lists(st.sampled_from(SVG_FRAGMENTS), max_size=6),
)
#: What nothing served from the console's origin may contain, however the file got there.
ACTIVE_SVG = re.compile(
    r"<script|<foreignObject|<style|<image|<use|<a[ >]|<animate|<set[ />]|\bon[a-z]+=|href=|javascript:|data:|"
    r"expression\s*\(|@import|<!ENTITY|<!DOCTYPE|url\((?!#)",
    re.IGNORECASE,
)


class TestALogoThatIsServedFromTheConsolesOwnOrigin:
    @SETTINGS
    @given(
        text=st.one_of(HOSTILE_SVG, st.text(max_size=300), st.text(alphabet=NASTY, max_size=120))
    )
    def test_what_the_sanitizer_writes_runs_nothing_and_reaches_nowhere(self, text: str) -> None:
        accepted, result = verdict(logo_sanitize.sanitize_svg, "demo", text)

        if not accepted:
            return
        assert not ACTIVE_SVG.search(result), result
        assert logos.validate_svg("demo", result) == result
        assert len(result.encode()) <= logos.MAX_LOGO_BYTES

    @SETTINGS
    @given(text=st.one_of(HOSTILE_SVG, st.text(max_size=300)))
    def test_the_guard_accepts_nothing_the_sanitizer_would_have_removed(self, text: str) -> None:
        accepted, _ = verdict(logos.validate_svg, "demo", text)

        if accepted:
            assert not ACTIVE_SVG.search(text), text

    @SETTINGS
    @given(
        text=HOSTILE_SVG,
        scheme=st.sampled_from(["light", "dark"]),
        brand=st.one_of(st.none(), st.just("336699")),
    )
    def test_a_file_recoloured_for_a_theme_is_still_one_the_guard_accepts(
        self, text: str, scheme: str, brand: str | None
    ) -> None:
        accepted, clean = verdict(logo_sanitize.sanitize_svg, "demo", text)
        if not accepted:
            return
        painted = logo_paint.adapt_to_scheme("demo", clean, scheme, brand)

        assert logos.validate_svg("demo", painted) == painted
        assert not ACTIVE_SVG.search(painted)

    @pytest.mark.parametrize(
        "scheme", ["", "sepia", "LIGHT", "dark\n", "light dark", "../dark", "dark;x"]
    )
    def test_a_theme_is_light_or_dark_and_nothing_else(self, scheme: str) -> None:
        with pytest.raises(ValueError, match="scheme"):
            logo_paint.adapt_to_scheme("demo", f'{SVG_HEAD}><path d="M0 0h9v9z"/></svg>', scheme)

    @SETTINGS
    @given(data=st.binary(max_size=400))
    def test_bytes_that_are_not_a_picture_are_refused_cleanly(self, data: bytes) -> None:
        for function in (logo_png.sanitize_png, logo_png.validate_png):
            accepted, _ = verdict(function, "demo", data)
            assert not accepted

    @SETTINGS
    @given(
        position=st.integers(min_value=0, max_value=600),
        replacement=st.binary(min_size=0, max_size=12),
        kind=st.sampled_from([b"tEXt", b"iTXt", b"eXIf", b"iCCP", b"acTL", b"zzzz"]),
    )
    def test_a_picture_that_is_damaged_or_carries_something_is_refused_or_stripped_to_pixels(
        self, position: int, replacement: bytes, kind: bytes
    ) -> None:
        import struct
        import zlib

        def chunk(name: bytes, body: bytes) -> bytes:
            return (
                struct.pack(">I", len(body))
                + name
                + body
                + struct.pack(">I", zlib.crc32(name + body))
            )

        base = (
            logo_png.PNG_SIGNATURE
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 6, 0, 0, 0))
            + chunk(kind, b"<script>alert(1)</script>")
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x28\x5a\xa0\xff" * 256) * 256))
            + chunk(b"IEND", b"")
        )
        damaged = base[:position] + replacement + base[position + len(replacement) :]
        accepted, result = verdict(logo_png.sanitize_png, "demo", damaged)

        if accepted:
            assert b"script" not in result and logo_png.validate_png("demo", result) == result
            offset, kinds = len(logo_png.PNG_SIGNATURE), []
            while offset < len(result):
                (length,) = struct.unpack(">I", result[offset : offset + 4])
                kinds.append(result[offset + 4 : offset + 8])
                offset += 12 + length
            assert set(kinds) <= {b"IHDR", b"PLTE", b"tRNS", b"sRGB", b"IDAT", b"IEND"}
