# Copyright (c) 2024-2026 Yago Lopez Prado
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

from noust.core.exceptions import NoustError, SecurityError, ValidationError
from noust.managers import site_routes
from noust.validators import container, domain, environment, health, names, port, source

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
        from noust.deployers.releases import is_release_id

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
