# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811

"""
The web application firewall: ModSecurity with the OWASP Core Rule Set, for the sites that ask.

A site's policy says ``waf`` and the rules to leave out; nginx has to have the module loaded and the
rules installed, one small site of Noust's own loads the rules once for everybody, and the rules
file names the packages' files itself because libmodsecurity reads ``Include`` and not
``IncludeOptional``. Pinned here: what each check says, that the shared rules are written with the
first site and taken away with the last, that a change nginx refuses leaves nothing behind, what the
site's file looks like, what is refused before it reaches nginx (a rule number is a number), and how
the audit log is read. Run against the real module and the real rules, the same files block an SQL
injection in ``block`` and let it through, written down, in ``detect``.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.deployers import access
from noust.managers.nginx_manager import NginxManager
from noust.managers.server.errors import UnsupportedHostError
from noust.managers.site_policy import SitePolicy, validated
from noust.managers.site_waf import MAX_EXCLUSIONS, checked_exclusions, checked_mode
from noust.managers.waf import (
    ANCHOR_NAME,
    AUDIT_LOG,
    PACKAGES,
    Waf,
    noisy_rules,
    parse_events,
)
from tests.conftest import OwnershipChange
from tests.test_domains import (  # noqa: F401 - fixtures of the domains harness
    Machine,
    certs,
    machine,
    store,
    web,
)

LOADED = "load_module modules/ngx_http_modsecurity_module.so;\nevents {}\nhttp {}\n"
COMMENTED = "# load_module modules/ngx_http_modsecurity_module.so;\nevents {}\nhttp {}\n"


def lay_out(
    root: Path,
    *,
    module: bool = True,
    rules: bool = True,
    setup: bool = True,
    logrotate: bool = True,
) -> None:
    """Put the packages' files where Debian and Ubuntu put them (and logrotate's directory)."""
    if logrotate:
        (root / "etc/logrotate.d").mkdir(parents=True, exist_ok=True)
    if module:
        (root / "usr/lib/nginx/modules").mkdir(parents=True, exist_ok=True)
        (root / "usr/lib/nginx/modules/ngx_http_modsecurity_module.so").write_text("")
    (root / "etc/nginx").mkdir(parents=True, exist_ok=True)
    (root / "etc/nginx/modsecurity.conf").write_text("SecRuleEngine DetectionOnly\n")
    if setup:
        (root / "etc/modsecurity/crs").mkdir(parents=True, exist_ok=True)
        (root / "etc/modsecurity/crs/crs-setup.conf").write_text("")
    if rules:
        (root / "usr/share/modsecurity-crs/rules").mkdir(parents=True, exist_ok=True)
        (root / "usr/share/modsecurity-crs/rules/REQUEST-901-INITIALIZATION.conf").write_text("")


@pytest.fixture
def packages(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    lay_out(root)
    return root


@pytest.fixture
def waf(web: NginxManager, packages: Path, runner: FakeRunner) -> Waf:
    runner.script(("nginx", "-T"), stdout=LOADED)
    return Waf(web, root=packages)


# -- what a policy may say --------------------------------------------------------


def test_a_mode_is_detect_block_or_nothing() -> None:
    assert checked_mode("detect") == "detect" and checked_mode("block") == "block"
    assert checked_mode("off") == "" and checked_mode("") == "" and checked_mode(None) == ""
    with pytest.raises(ValidationError) as raised:
        checked_mode("paranoid")
    assert raised.value.field == "waf"


def test_rule_numbers_are_numbers_and_only_numbers() -> None:
    assert checked_exclusions([942100, 941100, 942100]) == (941100, 942100)
    assert checked_exclusions(None) == () and checked_exclusions([]) == ()
    for bad in (
        ["942100; deny all"],
        ["942100"],
        [True],
        [0],
        [-1],
        [100_000_000],
        [1.5],
        "942100",
        {"a": 1},
        [942100, "x' ; return 200"],
    ):
        with pytest.raises(ValidationError) as raised:
            checked_exclusions(bad)
        assert raised.value.field == "waf_exclusions", bad
    with pytest.raises(ValidationError):
        checked_exclusions(list(range(1, MAX_EXCLUSIONS + 2)))


def test_the_policy_keeps_the_mode_and_the_rules_and_gives_them_back() -> None:
    policy = validated(SitePolicy(), {"waf": "block", "waf_exclusions": [942100]})

    assert policy.waf == "block" and policy.waf_exclusions == (942100,)
    assert policy.to_dict()["waf"] == "block" and policy.to_dict()["waf_exclusions"] == [942100]
    again = SitePolicy.from_document(json.dumps(policy.to_dict()))
    assert again == policy
    assert validated(policy, {"waf": "off"}).waf == ""
    assert not SitePolicy().waf and SitePolicy().is_default


# -- what the server has ----------------------------------------------------------


def test_a_server_with_the_module_loaded_and_the_rules_can_run_it(waf: Waf) -> None:
    support = waf.support()

    assert support.available and support.module_loaded and support.rules
    assert support.problem == "" and support.packages == PACKAGES


def test_a_module_that_is_not_there_says_how_to_get_it(
    web: NginxManager, tmp_path: Path, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-T"), stdout="events {}\nhttp {}\n")
    support = Waf(web, root=tmp_path / "bare").support()

    assert not support.available and not support.module_installed
    assert "no ModSecurity module" in support.problem
    assert "waf install" in support.hint


def test_a_module_on_disk_but_commented_out_is_installed_not_loaded(
    web: NginxManager, packages: Path, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-T"), stdout=COMMENTED)

    support = Waf(web, root=packages).support()

    assert not support.available and support.module_installed and not support.module_loaded
    assert "does not load it" in support.problem and "load_module" in support.hint


def test_the_rules_missing_are_named(web: NginxManager, tmp_path: Path, runner: FakeRunner) -> None:
    root = tmp_path / "norules"
    lay_out(root, rules=False)
    runner.script(("nginx", "-T"), stdout=LOADED)

    support = Waf(web, root=root).support()

    assert not support.available and not support.rules
    assert "Core Rule Set is not installed" in support.problem


def test_a_missing_setup_file_is_reported_not_guessed_around(
    web: NginxManager, tmp_path: Path, runner: FakeRunner
) -> None:
    root = tmp_path / "nosetup"
    lay_out(root, setup=False)
    runner.script(("nginx", "-T"), stdout=LOADED)

    support = Waf(web, root=root).support()

    assert not support.available and "setup file" in support.problem


def test_apache_is_not_driven(tmp_path: Path, runner: FakeRunner) -> None:
    from noust.managers.apache_manager import ApacheManager
    from noust.managers.webserver import APACHE_BACKEND

    manager = ApacheManager(
        backend=replace(
            APACHE_BACKEND, sites_available=tmp_path / "a", sites_enabled=tmp_path / "e"
        )
    )

    support = Waf(manager).support()

    assert not support.available and "nginx's here" in support.problem


# -- the shared rules -------------------------------------------------------------


def test_the_rules_are_written_once_and_name_the_packages_files_themselves(
    waf: Waf, packages: Path
) -> None:
    assert waf.ensure_anchor() is True

    text = waf.rules_file.read_text()
    assert f"Include {packages}/etc/nginx/modsecurity.conf" in text
    assert f"Include {packages}/etc/modsecurity/crs/crs-setup.conf" in text
    assert f"Include {packages}/usr/share/modsecurity-crs/rules/*.conf" in text
    assert "IncludeOptional" not in text  # libmodsecurity reads Include only
    assert "SecRuleEngine On" in text
    assert f"SecAuditLog {AUDIT_LOG}" in text and "SecAuditLogFormat JSON" in text
    # A password typed into a form must not end up in the log: no request body, no response body.
    assert "SecAuditLogParts ABFHKZ" in text and "SecResponseBodyAccess Off" in text
    anchor = waf.file.read_text()
    assert f"modsecurity_rules_file {waf.rules_file};" in anchor
    assert waf.link.is_symlink() and waf.anchored()
    assert waf.ensure_anchor() is False  # already there: nothing is written again


def test_nginx_refusing_the_rules_leaves_nothing_behind(
    waf: Waf, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        waf._web, "config_errors", lambda: "unknown directive modsecurity_rules_file"
    )

    with pytest.raises(ValidationError) as raised:
        waf.ensure_anchor()

    assert "unknown directive" in (raised.value.output or "")
    assert not waf.file.exists() and not waf.link.is_symlink() and not waf.rules_file.exists()


def test_the_rules_are_not_written_for_a_server_that_cannot_run_them(
    web: NginxManager, tmp_path: Path, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-T"), stdout="events {}\n")
    waf = Waf(web, root=tmp_path / "bare")

    with pytest.raises(ValidationError) as raised:
        waf.ensure_anchor()

    assert raised.value.field == "waf" and "no ModSecurity module" in raised.value.message
    assert not waf.file.exists()


def test_the_rules_go_with_the_last_site(waf: Waf) -> None:
    waf.ensure_anchor()

    assert waf.release_anchor() is True
    assert not waf.file.exists() and not waf.rules_file.exists() and not waf.link.is_symlink()
    assert waf.release_anchor() is False


def test_the_shared_site_is_not_listed_as_an_application(waf: Waf) -> None:
    waf.ensure_anchor()

    assert ANCHOR_NAME not in [site.domain for site in waf._web.list_sites()]


# -- the audit log ---------------------------------------------------------------


def _no_such_group(name: str) -> Any:
    raise KeyError(name)


def test_the_audit_log_is_not_readable_by_the_other_accounts_of_the_server(
    waf: Waf, packages: Path, ownership_changes: list[OwnershipChange]
) -> None:
    # It holds the headers of every request the rules flagged: the Cookie and the Authorization
    # of whoever sent one. A file anybody can read hands the session of a visitor to every account
    # on the machine (the application accounts, the build account), and ModSecurity creates it with
    # the default mask if Noust does not make it first. Found with the real module, which wrote
    # it 0644.
    waf.ensure_anchor()

    log = packages / AUDIT_LOG.lstrip("/")
    assert log.exists() and log.stat().st_mode & 0o007 == 0
    assert OwnershipChange(log, "root", "adm", 0o640) in ownership_changes


def test_a_log_made_world_readable_by_an_earlier_run_is_closed(
    waf: Waf, packages: Path, ownership_changes: list[OwnershipChange]
) -> None:
    log = packages / AUDIT_LOG.lstrip("/")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"transaction": {}}\n')
    log.chmod(0o644)

    waf.ensure_anchor()

    assert log.stat().st_mode & 0o007 == 0
    assert log.read_text() == '{"transaction": {}}\n'  # what was in it is kept


def test_a_server_without_the_adm_group_keeps_the_log_to_root(
    waf: Waf,
    packages: Path,
    ownership_changes: list[OwnershipChange],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("noust.managers.waf.grp.getgrnam", _no_such_group)

    waf.ensure_anchor()

    log = packages / AUDIT_LOG.lstrip("/")
    assert OwnershipChange(log, "root", "root", 0o640) in ownership_changes


def test_the_log_is_rotated_by_truncating_it_in_place(waf: Waf, packages: Path) -> None:
    # ModSecurity opens its audit log once and never again. The web server's rotation renames the
    # file and asks nginx to reopen its own logs, which does not reach the module: the firewall
    # went on writing to the renamed file for good (also after a reload) and `waf events` read the
    # new, empty one. Truncating in place keeps the open handle valid.
    waf.ensure_anchor()

    rule = (packages / "etc/logrotate.d/noust-waf").read_text()
    assert f"{AUDIT_LOG} {{" in rule
    assert "copytruncate" in rule and "create" not in rule and "postrotate" not in rule
    assert "compress" in rule and "rotate " in rule


def test_the_web_servers_own_rotation_does_not_also_take_the_audit_log() -> None:
    # logrotate refuses two rules for one file, and the second one is the one that is skipped.
    assert not fnmatch.fnmatch(AUDIT_LOG, "/var/log/nginx/*.log")


def test_the_rotation_rule_goes_with_the_last_site(waf: Waf, packages: Path) -> None:
    waf.ensure_anchor()
    rule = packages / "etc/logrotate.d/noust-waf"
    assert rule.exists()

    waf.release_anchor()

    assert not rule.exists()


def test_a_server_without_logrotate_is_not_given_a_rule_for_it(
    web: NginxManager, tmp_path: Path, runner: FakeRunner
) -> None:
    root = tmp_path / "nologrotate"
    lay_out(root, logrotate=False)
    runner.script(("nginx", "-T"), stdout=LOADED)
    waf = Waf(web, root=root)

    waf.ensure_anchor()

    assert waf.anchored() and not (root / "etc/logrotate.d").exists()


# -- installing --------------------------------------------------------------------


def test_the_packages_are_installed_through_the_package_manager(
    waf: Waf, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Waf, "_installable", lambda self: True)
    from noust.managers.server.host import Platform

    class Backend:
        def install(self, packages: Any, *, timeout: int) -> Any:
            return runner.run(
                ["apt-get", "install", "-y", *packages],
                env={"DEBIAN_FRONTEND": "noninteractive"},
                timeout=timeout,
            )

    monkeypatch.setattr(
        "noust.managers.waf.detect_platform", lambda *a, **k: Platform.__new__(Platform)
    )
    monkeypatch.setattr("noust.managers.waf.backend_for", lambda *a, **k: Backend())

    installed = waf.install()

    assert installed == list(PACKAGES)
    assert runner.calls[-1] == ("apt-get", "install", "-y", *PACKAGES)
    assert runner.envs[-1] == {"DEBIAN_FRONTEND": "noninteractive"}


def test_a_server_with_empty_package_lists_updates_them_before_installing_the_waf(
    waf: Waf, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.fs import RecordingFileSystem
    from noust.managers.server.pkg.apt import AptBackend
    from tests.server_support import EmptyAptLists, make_host, platform_for

    fresh_image = EmptyAptLists()
    monkeypatch.setattr(Waf, "_installable", lambda self: True)
    monkeypatch.setattr("noust.managers.waf.detect_platform", lambda *a, **k: platform_for("apt"))
    monkeypatch.setattr(
        "noust.managers.waf.backend_for",
        lambda platform, **k: AptBackend(
            platform, runner=fresh_image, fs=RecordingFileSystem(), host=make_host(tmp_path)
        ),
    )

    installed = waf.install()

    assert installed == list(PACKAGES)
    assert fresh_image.calls == [
        ("apt-get", "install", "-y", *PACKAGES),
        ("apt-get", "update"),
        ("apt-get", "install", "-y", *PACKAGES),
    ]


def test_installing_where_the_packages_are_not_known_is_refused(
    waf: Waf, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Waf, "_installable", lambda self: False)

    with pytest.raises(UnsupportedHostError):
        waf.install()


def test_a_package_manager_that_fails_is_an_error_with_its_words(
    waf: Waf, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Waf, "_installable", lambda self: True)

    class Backend:
        def install(self, packages: Any, *, timeout: int) -> Any:
            return runner.run(["apt-get", "install", "-y", *packages], timeout=timeout)

    monkeypatch.setattr("noust.managers.waf.detect_platform", lambda *a, **k: object())
    monkeypatch.setattr("noust.managers.waf.backend_for", lambda *a, **k: Backend())
    runner.script(["apt-get", "install"], exit_code=100, stderr="E: Unable to locate package")

    with pytest.raises(ValidationError) as raised:
        waf.install()

    assert "Unable to locate package" in (raised.value.output or "")


# -- the site ---------------------------------------------------------------------


@pytest.fixture
def served(
    machine: Machine, runner: FakeRunner, packages: Path, monkeypatch: pytest.MonkeyPatch
) -> Machine:
    runner.script(("nginx", "-T"), stdout=LOADED)
    real = Waf.__init__

    def init(self: Waf, manager: Any, root: Path = Path("/")) -> None:
        real(self, manager, packages)

    monkeypatch.setattr(Waf, "__init__", init)
    machine.deploy(app_type="nodejs", tls=True)
    return machine


def test_a_site_in_block_mode_turns_modsecurity_on_and_nothing_else(served: Machine) -> None:
    access.set_policy("example.com", {"waf": "block"})

    config = served.config()
    assert "modsecurity on;" in config
    assert "DetectionOnly" not in config and "SecRuleRemoveById" not in config
    assert "modsecurity_rules_file" not in config  # the rules are loaded once, elsewhere
    assert Waf(served.web, packages_root(served)).anchored()


def packages_root(machine: Machine) -> Path:
    return machine.tmp_path / "root"


def test_a_site_in_detect_mode_judges_and_refuses_nothing(served: Machine) -> None:
    access.set_policy("example.com", {"waf": "detect"})

    config = served.config()
    assert "modsecurity on;" in config
    assert "modsecurity_rules 'SecRuleEngine DetectionOnly';" in config


def test_the_rules_a_site_leaves_out_are_written_as_numbers(served: Machine) -> None:
    access.set_policy("example.com", {"waf": "block", "waf_exclusions": [942100, 941100]})

    assert "modsecurity_rules 'SecRuleRemoveById 941100 942100';" in served.config()


def test_a_site_without_the_waf_says_nothing_about_modsecurity(served: Machine) -> None:
    access.set_policy("example.com", {"noindex": True})

    assert "modsecurity" not in served.config().lower()


def test_the_site_is_still_accepted_by_nginx_after_a_change_back(served: Machine) -> None:
    access.set_policy("example.com", {"waf": "detect"})
    access.set_policy("example.com", {"waf": "block", "waf_exclusions": [920350]})
    access.set_policy("example.com", {"waf": "off"})

    assert "modsecurity" not in served.config().lower()
    assert not Waf(served.web, packages_root(served)).anchored()


def test_a_server_that_cannot_run_it_refuses_the_policy_and_changes_nothing(
    machine: Machine, runner: FakeRunner, tmp_path: Path
) -> None:
    runner.script(("nginx", "-T"), stdout="events {}\n")
    machine.deploy(app_type="nodejs", tls=True)
    before = machine.config()

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"waf": "block"})

    assert raised.value.field == "waf"
    assert machine.config() == before
    assert access.describe("example.com").policy.waf == ""


def test_apache_sites_are_refused_in_words(machine: Machine, runner: FakeRunner) -> None:
    app = machine.deploy(app_type="nodejs", tls=True)
    machine.store.update_app(replace(app, webserver="apache"))

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"waf": "block"})

    assert "nginx's here" in raised.value.message


def test_the_shared_rules_stay_while_another_site_uses_them_and_go_with_the_last(
    served: Machine,
) -> None:
    served.deploy("blog.example.com", app_type="nodejs", tls=True)
    waf = Waf(served.web, packages_root(served))

    access.set_policy("example.com", {"waf": "block"})
    access.set_policy("blog.example.com", {"waf": "detect"})
    assert waf.anchored()
    access.set_policy("example.com", {"waf": "off"})
    assert waf.anchored()  # the blog still uses them
    access.set_policy("blog.example.com", {"waf": "off"})
    assert not waf.anchored()


def test_a_reset_lets_go_of_the_rules(served: Machine) -> None:
    access.set_policy("example.com", {"waf": "block"})

    access.reset_policy("example.com")

    assert not Waf(served.web, packages_root(served)).anchored()
    assert "modsecurity" not in served.config().lower()


def test_a_refused_site_takes_back_the_rules_it_wrote(
    served: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    waf = Waf(served.web, packages_root(served))
    real = served.web.config_errors
    calls = {"n": 0}

    def refuse_the_second() -> str | None:
        calls["n"] += 1
        return real() if calls["n"] == 1 else "nginx: [emerg] unknown directive"

    monkeypatch.setattr(served.web, "config_errors", refuse_the_second)

    with pytest.raises(ValidationError):
        access.set_policy("example.com", {"waf": "block"})

    assert not waf.anchored()
    assert access.describe("example.com").policy.waf == ""


# -- the audit log ----------------------------------------------------------------


def transaction(
    host: str, uri: str, status: int, *rules: tuple[str, str], client: str = "203.0.113.9"
) -> str:
    return json.dumps(
        {
            "transaction": {
                "client_ip": client,
                "time_stamp": "Sat Oct 17 01:21:33 2026",
                "request": {"method": "GET", "uri": uri, "headers": {"Host": host}},
                "response": {"http_code": status},
                "messages": [
                    {"message": message, "details": {"ruleId": rule_id, "severity": "2"}}
                    for rule_id, message in rules
                ],
            }
        }
    )


LOG = "\n".join(
    [
        'me":"cut-off first line of a tail"}}',
        transaction(
            "example.com",
            "/?id=1' OR '1'='1",
            403,
            ("942100", "SQL Injection"),
            ("949110", "Anomaly Score Exceeded"),
        ),
        transaction(
            "other.example.org:443",
            "/?x=<script>",
            403,
            ("941100", "XSS"),
            ("949110", "Anomaly Score Exceeded"),
        ),
        "",
        "not json at all",
        transaction(
            "EXAMPLE.com",
            "/search?q=a+union",
            200,
            ("942100", "SQL Injection"),
            client="198.51.100.4",
        ),
        transaction("example.com", "/", 200),
    ]
)


def test_only_the_applications_own_requests_are_read_newest_first() -> None:
    events = parse_events(LOG, {"example.com"})

    assert [e.uri for e in events] == ["/", "/search?q=a+union", "/?id=1' OR '1'='1"]
    assert events[2].to_dict()["blocked"] is True and events[2].to_dict()["would_block"] is False
    assert events[1].to_dict()["blocked"] is False and events[1].client == "198.51.100.4"
    assert events[1].to_dict()["would_block"] is False  # it matched a rule but never scored out
    assert [(r[0], r[1]) for r in events[2].rules] == [
        ("942100", "SQL Injection"),
        ("949110", "Anomaly Score Exceeded"),
    ]


def test_a_request_the_detecting_site_let_through_but_block_mode_would_refuse_is_flagged() -> None:
    log = transaction(
        "example.com", "/?id=1' OR '1'='1", 200, ("942100", "SQL"), ("949110", "Anomaly Score")
    )

    (event,) = parse_events(log, {"example.com"})

    assert event.to_dict()["blocked"] is False and event.to_dict()["would_block"] is True


def test_the_limit_and_a_log_that_is_not_there(tmp_path: Path, waf: Waf) -> None:
    assert len(parse_events(LOG, {"example.com"}, limit=2)) == 2
    assert waf.events({"example.com"}, log=tmp_path / "nothing.log") == []


def test_the_log_is_read_from_its_tail(tmp_path: Path, waf: Waf) -> None:
    log = tmp_path / "audit.log"
    log.write_text(LOG)

    events = waf.events({"example.com"}, 10, log=log)

    assert len(events) == 3


def test_the_rules_that_matched_most_are_the_ones_to_look_at_without_the_scorer() -> None:
    events = parse_events(LOG, {"example.com", "other.example.org"})

    assert noisy_rules(events) == [
        ("942100", "SQL Injection", 2),
        ("941100", "XSS", 1),
    ]
