# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for SSH hardening: effective configuration, keys, access proof, safe apply.

The fixes change how an operator reaches the server, so what is pinned here is
mostly what must *not* happen: a password login turned off without a key
proven to work, root refused while a central's tunnel logs in as root, a value
written where another file wins and left as if it had worked, a change kept
without a new session proving it, a change that stays applied when nobody
confirms it. Every sshd answer comes from :class:`FakeSshd`, which reads the
drop-in the code writes, so "the value changed" is observed, not assumed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from noust.core.exceptions import SecurityError
from noust.managers.server import security_pending, security_proof
from noust.managers.server.security import ServerSecurity
from noust.managers.server.security_access import AccessGuardError, prove_key_access
from noust.managers.server.security_accounts import HostAccounts
from noust.managers.server.security_keys import (
    fingerprint_of,
    key_bits,
    parse_file,
    parse_line,
    parse_new_key,
    strict_mode_problems,
)
from noust.managers.server.security_logins import LoginReader, parse_short_unix, parse_syslog
from noust.managers.server.security_pending import CONFIRM_WINDOW, ChangeLedger
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.server.security_proof import NEGATIVE_TTL, find_proof
from noust.managers.server.security_sockets import parse_established, parse_listeners
from noust.managers.server.security_ssh import SshSecurity
from noust.managers.server.security_sshd import (
    DROPIN,
    SshdDropIn,
    include_present,
    parse_effective,
    read_effective,
    winning_source,
)
from tests.server_security_support import (
    ALICE_FP,
    ALICE_KEY,
    CENTRAL_FP,
    CENTRAL_LINE,
    CLOUD_INIT_SUDOERS,
    DEBIAN_12_SUDOERS,
    ECDSA_FP,
    ECDSA_KEY,
    ED_FP,
    ED_KEY,
    NOW,
    OPENSUSE_SUDOERS,
    RHEL_9_SUDOERS,
    RSA_1024,
    RSA_1024_FP,
    RSA_3072,
    RSA_3072_FP,
    UBUNTU_2404_SUDOERS,
    FakeHost,
    FakeSshd,
    accepted,
)


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    return FakeHost(tmp_path / "root")


@pytest.fixture
def sshd(host: FakeHost) -> FakeSshd:
    return FakeSshd(host)


@pytest.fixture
def ledger(tmp_path: Path, sshd: FakeSshd, host: FakeHost) -> ChangeLedger:
    return ChangeLedger(
        tmp_path / "changes",
        runner=sshd,
        host=host.paths,
        clock=lambda: NOW,
        python="/usr/bin/python3",
    )


def _probe(sshd: FakeSshd, host: FakeHost) -> SecurityProbe:
    return SecurityProbe(runner=sshd, host=host.paths, clock=lambda: NOW)


def _security(sshd: FakeSshd, host: FakeHost, ledger: ChangeLedger) -> SshSecurity:
    return SshSecurity(_probe(sshd, host), ledger, actor="tester")


def _journal(sshd: FakeSshd, *lines: str) -> None:
    sshd.script(["journalctl"], stdout="\n".join(lines) + "\n")


# Effective configuration ------------------------------------------------------


class TestEffectiveConfiguration:
    def test_every_read_evaluates_match_blocks_for_a_remote_connection(self, sshd, host):
        read_effective(sshd, user="alice")

        assert sshd.calls[-1] == (
            "sshd",
            "-T",
            "-C",
            "user=alice,host=203.0.113.1,addr=203.0.113.1",
        )
        assert sshd.envs[-1] == {"LC_ALL": "C", "TERM": "dumb"}

    def test_repeated_keywords_keep_every_value(self):
        effective = parse_effective("port 22\nport 2222\npermitrootlogin without-password\n", "")

        assert effective.ports == [22, 2222]
        assert effective.first("permitrootlogin") == "without-password"

    def test_the_keyboard_interactive_keyword_is_the_one_this_sshd_prints(self):
        old = parse_effective("challengeresponseauthentication yes\n", "")
        new = parse_effective("kbdinteractiveauthentication no\n", "")

        assert old.kbd_keyword == "challengeresponseauthentication"
        assert new.kbd_keyword == "kbdinteractiveauthentication"

    def test_pam_keyboard_interactive_still_accepts_passwords(self):
        effective = parse_effective(
            "passwordauthentication no\nkbdinteractiveauthentication yes\nusepam yes\n", ""
        )

        assert effective.passwords_accepted

    def test_an_sshd_that_cannot_print_its_configuration_says_why_verbatim(self, host):
        runner = FakeSshd(host)
        runner.run = lambda *a, **k: type(  # type: ignore[method-assign]
            "R",
            (),
            {"success": False, "stdout": "", "stderr": "line 3: Bad option", "exit_code": 255},
        )()

        with pytest.raises(SecurityError) as caught:
            read_effective(runner)

        assert caught.value.output == "line 3: Bad option"


class TestDropIn:
    def test_the_drop_in_carries_the_marker_and_only_known_directives(self, host):
        dropin = SshdDropIn(host.paths)
        previous, text = dropin.merged({"passwordauthentication": "no"})

        assert previous is None
        assert "Generated by Noust" in text
        assert "PasswordAuthentication no\n" in text

    def test_a_value_that_is_not_one_word_is_refused(self, host):
        with pytest.raises(SecurityError):
            SshdDropIn(host.paths).merged({"passwordauthentication": "no\nPermitRootLogin yes"})

    def test_an_unknown_keyword_is_refused(self, host):
        with pytest.raises(SecurityError):
            SshdDropIn(host.paths).merged({"allowtcpforwarding": "no"})

    def test_a_file_noust_did_not_write_is_never_taken_over(self, host):
        host.write(DROPIN, "PasswordAuthentication yes\n")

        with pytest.raises(SecurityError, match="Noust did not write it"):
            SshdDropIn(host.paths).read()

    def test_a_symlinked_drop_in_is_refused(self, host, tmp_path):
        target = tmp_path / "elsewhere"
        target.write_text("x")
        host.paths.at(DROPIN).symlink_to(target)

        with pytest.raises(SecurityError, match="symbolic link"):
            SshdDropIn(host.paths).read()

    def test_the_include_is_found(self, host):
        assert include_present(host.paths)

        host.write("/etc/ssh/sshd_config", "PasswordAuthentication yes\n")
        assert not include_present(host.paths)

    def test_the_line_before_the_include_is_named_as_the_winner(self, host):
        host.write(
            "/etc/ssh/sshd_config",
            "PasswordAuthentication yes\nInclude /etc/ssh/sshd_config.d/*.conf\n",
        )
        host.write(DROPIN, "# Generated by Noust.\nPasswordAuthentication no\n")

        assert winning_source("PasswordAuthentication", host.paths) == (
            "/etc/ssh/sshd_config:1: PasswordAuthentication yes"
        )

    def test_a_cloud_init_drop_in_after_ours_does_not_win(self, host):
        host.write(DROPIN, "# Generated by Noust.\nPasswordAuthentication no\n")
        host.write("/etc/ssh/sshd_config.d/50-cloud-init.conf", "PasswordAuthentication yes\n")

        assert winning_source("passwordauthentication", host.paths) == (
            "/etc/ssh/sshd_config.d/00-noust.conf:2: PasswordAuthentication no"
        )


# Keys -------------------------------------------------------------------------


class TestKeys:
    @pytest.mark.parametrize(
        ("line", "fingerprint", "bits"),
        [
            (ED_KEY, ED_FP, 256),
            (RSA_3072, RSA_3072_FP, 3072),
            (RSA_1024, RSA_1024_FP, 1024),
            (ECDSA_KEY, ECDSA_FP, 256),
        ],
    )
    def test_fingerprints_and_sizes_match_ssh_keygen(self, line, fingerprint, bits):
        key_type, blob = line.split()[:2]

        assert fingerprint_of(blob) == fingerprint
        assert key_bits(key_type, blob) == bits

    def test_a_central_key_is_recognised_by_its_marker(self):
        key = parse_line(CENTRAL_LINE)

        assert key is not None
        assert key.kind == "central"
        assert key.fingerprint == CENTRAL_FP
        assert key.options[-1] == 'command="/usr/bin/false"'

    def test_the_cloud_image_root_line_logs_nobody_in(self):
        line = (
            'no-port-forwarding,command="echo \'Please login as the user \\"ubuntu\\" rather '
            'than the user \\"root\\".\';echo;sleep 10;exit 142" ' + ED_KEY
        )

        key = parse_line(line)

        assert key is not None and key.kind == "cloud_disabled"

    def test_weak_keys_say_why(self):
        key = parse_line(RSA_1024)

        assert key is not None and "1024 bits" in key.weak

    def test_comments_and_garbage_are_skipped(self):
        keys = parse_file(f"# a comment\n\nnot a key\n{ED_KEY}\nssh-ed25519 !!!notbase64\n")

        assert [key.fingerprint for key in keys] == [ED_FP]
        assert keys[0].line_number == 4

    def test_an_operator_key_with_options_is_refused(self):
        with pytest.raises(SecurityError):
            parse_new_key('from="10.0.0.1" ' + ED_KEY)

    def test_a_weak_rsa_key_is_refused(self):
        with pytest.raises(SecurityError, match="1024 bits"):
            parse_new_key(RSA_1024)

    def test_a_blob_that_does_not_hold_its_type_is_refused(self):
        _, blob = ECDSA_KEY.split()[:2]

        with pytest.raises(SecurityError, match="damaged"):
            parse_new_key(f"ssh-ed25519 {blob}")

    def test_the_comment_is_reduced_to_safe_characters(self):
        key = parse_new_key(ED_KEY.replace("yago@laptop", 'yago@laptop" command="x'))

        assert '"' not in key.comment

    def test_strict_modes_problems_come_with_their_fix(self, host):
        from noust.managers.server.security_accounts import Account

        me = Account("me", __import__("os").getuid(), 0, "/home/me", "/bin/bash")
        path = host.write("/home/me/.ssh/authorized_keys", ED_KEY + "\n")
        path.parent.chmod(0o777)

        problems = strict_mode_problems(host.paths, me, "/home/me/.ssh/authorized_keys")

        assert any("chmod go-w /home/me/.ssh" in problem for problem in problems)


def _sudo_host(
    host: FakeHost,
    sudoers: str,
    *,
    group: str = "sudo",
    members: str = "bob",
    dropins: dict[str, str] | None = None,
) -> HostAccounts:
    """A host whose only sudoers rules are the ones given; ``members`` are in ``group``."""
    host.paths.at("/etc/sudoers.d/90-alice").unlink()
    host.write("/etc/sudoers", sudoers)
    host.write("/etc/group", f"root:x:0:\n{group}:x:27:{members}\nalice:x:1000:\nbob:x:1001:\n")
    for name, text in (dropins or {}).items():
        host.write(f"/etc/sudoers.d/{name}", text)
    return HostAccounts(host.paths)


def _rule(accounts: HostAccounts, name: str):
    account = accounts.account(name)
    assert account is not None
    return accounts.sudo_rule(account)


def _usable(accounts: HostAccounts, name: str) -> bool:
    account = accounts.account(name)
    assert account is not None
    return accounts.sudo_usable(account)


# Accounts ---------------------------------------------------------------------


class TestAccounts:
    def test_admins_are_root_and_the_accounts_sudo_grants(self, host):
        accounts = HostAccounts(host.paths)

        assert [entry.name for entry in accounts.admins()] == ["root", "alice"]

    def test_a_nopasswd_rule_makes_sudo_usable_without_a_password(self, host):
        accounts = HostAccounts(host.paths)
        alice = accounts.account("alice")

        assert alice is not None
        assert accounts.password_state("alice") == "locked"
        assert accounts.sudo_usable(alice)

    def test_a_rule_that_wants_a_password_the_account_lacks_is_not_usable(self, host):
        host.write("/etc/sudoers.d/90-alice", "alice ALL=(ALL) ALL\n")
        accounts = HostAccounts(host.paths)
        alice = accounts.account("alice")

        assert alice is not None and not accounts.sudo_usable(alice)

    def test_files_sudo_skips_grant_nothing(self, host):
        host.write("/etc/sudoers.d/90-alice.dpkg-old", "bob ALL=(ALL) NOPASSWD:ALL\n")
        accounts = HostAccounts(host.paths)
        bob = accounts.account("bob")

        assert bob is not None and not accounts.sudo_rule(bob).granted

    def test_wheel_membership_alone_grants_nothing(self, host):
        host.write("/etc/group", "root:x:0:\nwheel:x:10:bob\nalice:x:1000:\nbob:x:1001:\n")
        host.write("/etc/sudoers", "root ALL=(ALL) ALL\n# %wheel ALL=(ALL) ALL\n")
        accounts = HostAccounts(host.paths)
        bob = accounts.account("bob")

        assert bob is not None and not accounts.sudo_rule(bob).granted
        assert "bob" in [entry.name for entry in accounts.admins()]

    def test_the_primary_group_and_the_listed_ones_both_count(self, host):
        host.write(
            "/etc/passwd", host.read("/etc/passwd") + "carol:x:1002:27::/home/carol:/bin/bash\n"
        )
        host.write(
            "/etc/group",
            "root:x:0:\nsudo:x:27:\nops:x:50:bob\ncarol:x:1002:\nalice:x:1000:\nbob:x:1001:\n",
        )
        accounts = HostAccounts(host.paths)
        carol, bob = accounts.account("carol"), accounts.account("bob")

        assert carol is not None and bob is not None
        assert accounts.groups_of(carol) == {"sudo"}
        assert accounts.groups_of(bob) == {"bob", "ops"}
        assert accounts.identity(carol).gids == {27}

    def test_a_primary_group_alone_is_enough_for_a_group_rule(self, host):
        host.write(
            "/etc/passwd", host.read("/etc/passwd") + "carol:x:1002:27::/home/carol:/bin/bash\n"
        )
        accounts = _sudo_host(host, "%sudo\tALL=(ALL:ALL) ALL\n", members="")

        assert _rule(accounts, "carol").granted

    @pytest.mark.parametrize(
        ("sudoers", "group"),
        [
            pytest.param(DEBIAN_12_SUDOERS, "sudo", id="debian-12"),
            pytest.param(UBUNTU_2404_SUDOERS, "sudo", id="ubuntu-24.04-sudo"),
            pytest.param(UBUNTU_2404_SUDOERS, "admin", id="ubuntu-24.04-admin"),
            pytest.param(RHEL_9_SUDOERS, "wheel", id="rhel-9"),
        ],
    )
    def test_the_stock_file_of_a_distribution_grants_its_admin_group(self, host, sudoers, group):
        accounts = _sudo_host(host, sudoers, group=group)

        rule = _rule(accounts, "bob")

        assert rule.granted and not rule.nopasswd
        assert rule.sources and rule.sources[0].startswith("/etc/sudoers:")
        assert _usable(accounts, "bob")
        assert not _rule(accounts, "alice").granted
        assert [entry.name for entry in accounts.admins()] == ["root", "bob"]

    def test_the_stock_rule_is_found_on_the_line_it_is_on(self, host):
        accounts = _sudo_host(host, DEBIAN_12_SUDOERS)
        line = DEBIAN_12_SUDOERS.splitlines().index("%sudo\tALL=(ALL:ALL) ALL") + 1

        assert _rule(accounts, "bob").sources == (f"/etc/sudoers:{line}",)

    def test_the_commented_nopasswd_line_of_red_hat_grants_nothing_more(self, host):
        accounts = _sudo_host(host, RHEL_9_SUDOERS, group="wheel")

        assert not _rule(accounts, "bob").nopasswd

    def test_cloud_inits_rule_makes_the_default_account_usable_without_a_password(self, host):
        host.write(
            "/etc/passwd", host.read("/etc/passwd") + "ubuntu:x:1002:1002::/home/ubuntu:/bin/bash\n"
        )
        host.write("/etc/shadow", host.read("/etc/shadow") + "ubuntu:!:19000:0:99999:7:::\n")
        accounts = _sudo_host(
            host,
            UBUNTU_2404_SUDOERS,
            members="",
            dropins={"90-cloud-init-users": CLOUD_INIT_SUDOERS},
        )

        rule = _rule(accounts, "ubuntu")

        assert rule.granted and rule.nopasswd
        assert rule.sources == ("/etc/sudoers.d/90-cloud-init-users:4",)
        assert _usable(accounts, "ubuntu")

    def test_opensuse_grants_everyone_and_asks_for_roots_password(self, host):
        accounts = _sudo_host(host, OPENSUSE_SUDOERS, group="wheel", members="")

        assert _rule(accounts, "bob").granted and _rule(accounts, "alice").granted
        assert _usable(accounts, "bob")
        # alice has a locked password; sudo asks for root's, which works.
        assert _usable(accounts, "alice")
        host.write("/etc/shadow", "root:!:1:0:99999:7:::\nbob:$6$salt$hash:1:0:99999:7:::\n")
        assert not _usable(HostAccounts(host.paths), "bob")

    @pytest.mark.parametrize(
        "rule",
        [
            "%sudo ALL = (ALL) ALL",
            "%sudo\tALL\t=\t(ALL:ALL)\tALL",
            "bob ALL=(ALL) ALL",
            "alice,  bob ALL=(ALL) ALL",
            "alice , bob\tALL=(ALL) ALL",
            "%#27 ALL=(ALL) ALL",
            "%#1001 ALL=(ALL) ALL",
            "#1001 ALL=(ALL) ALL",
            "bob ALL=(root) ALL",
            "bob ALL=(#0) ALL",
            "bob ALL=ALL",
            "bob ALL=(ALL) ALL # why",
            "bob ALL=(ALL) \\\n    ALL",
            "ALL ALL=(ALL) ALL",
            "bob myhost = (ALL) ALL",
            "bob myhost.example.com=(ALL) ALL",
            "User_Alias ADMINS = alice, bob\nADMINS ALL=(ALL) ALL",
            "User_Alias ADMINS = %sudo\nADMINS ALL=(ALL) ALL",
            "Runas_Alias OP = root, daemon\nbob ALL=(OP) ALL",
            "Host_Alias HERE = myhost\nbob HERE=(ALL) ALL",
            "Cmnd_Alias EVERYTHING = ALL\nbob ALL=(ALL) EVERYTHING",
            "bob ALL=(ALL) ALL, !/usr/bin/passwd",
            "bob ALL=(ALL) ALL : otherhost = /bin/ls",
            "ALL, !alice ALL=(ALL) ALL",
            "bob ALL=(www-data) /bin/ls, (root) ALL",
            "bob ALL=(ALL) CWD=/tmp ALL",
        ],
    )
    def test_the_ways_sudoers_lets_you_write_a_rule_that_grant_root(self, host, rule):
        host.write("/etc/hostname", "myhost.example.com\n")
        accounts = _sudo_host(host, f"root ALL=(ALL:ALL) ALL\n{rule}\n")

        assert _rule(accounts, "bob").granted

    @pytest.mark.parametrize(
        "rule",
        [
            "%wheel ALL=(ALL) ALL",
            "%sudoers ALL=(ALL) ALL",
            "%sudo ALL=(www-data) ALL",
            "bob ALL=(ALL) /usr/bin/systemctl restart nginx",
            "bob ALL=(www-data) NOPASSWD: ALL",
            "bob ALL=(ALL, !root) ALL",
            "bob ALL=(:wheel) ALL",
            "bob ALL=() ALL",
            "bob otherhost=(ALL) ALL",
            "ALL, !bob ALL=(ALL) ALL",
            "!bob ALL=(ALL) ALL",
            "+admins ALL=(ALL) ALL",
            "%:domain\\ admins ALL=(ALL) ALL",
            "bob ALL=(ALL) ALL\nbob ALL=(ALL) !ALL",
            "bob ALL=(ALL) NOTAFTER=20200101000000Z ALL",
            "bob ALL (ALL) ALL",
            "bob ALL=(ALL) ALL,",
            "bob ALL=(",
            "bob",
            "=",
            "User_Alias ADMINS = alice\nADMINS ALL=(ALL) ALL",
            "User_Alias A = B\nUser_Alias B = A\nA ALL=(ALL) ALL",
            "# bob ALL=(ALL) ALL",
            "Defaults bob ALL=(ALL) ALL",
        ],
    )
    def test_what_does_not_make_the_account_root_grants_nothing(self, host, rule):
        host.write("/etc/hostname", "myhost\n")
        accounts = _sudo_host(host, f"root ALL=(ALL:ALL) ALL\n{rule}\n", members="")

        assert not _rule(accounts, "bob").granted

    @pytest.mark.parametrize(
        "rule",
        [
            "bob ALL=(ALL) NOPASSWD:ALL",
            "bob ALL=(ALL) NOPASSWD: ALL",
            "bob ALL=(ALL) NOPASSWD : ALL",
            "bob ALL=(ALL) NOPASSWD: SETENV: ALL",
            "bob ALL=(ALL) SETENV: NOPASSWD: ALL",
            "bob ALL=(ALL:ALL)\tNOPASSWD:\tALL",
            "bob ALL = (ALL) NOPASSWD: ALL",
            "bob ALL=NOPASSWD: ALL",
            "bob ALL=(ALL) NOPASSWD: /bin/ls, ALL",
            "bob ALL=(ALL) NOPASSWD: ALL, PASSWD: /bin/ls",
            "%sudo ALL=(ALL:ALL) ALL\nbob ALL=(ALL) NOPASSWD: ALL",
            "Defaults:%sudo !authenticate\n%sudo ALL=(ALL:ALL) ALL",
            "Defaults\t!authenticate\nbob ALL=(ALL) ALL",
        ],
    )
    def test_nopasswd_is_found_however_it_is_written(self, host, rule):
        accounts = _sudo_host(host, f"root ALL=(ALL:ALL) ALL\n{rule}\n")

        assert _rule(accounts, "bob").nopasswd

    @pytest.mark.parametrize(
        "rule",
        [
            "bob ALL=(ALL) NOPASSWD: /bin/ls, PASSWD: ALL",
            "bob ALL=(ALL) NOPASSWD: /bin/ls\nbob ALL=(ALL) ALL",
            "bob ALL=(ALL) NOPASSWD: ALL\n%sudo ALL=(ALL:ALL) ALL",
            "Defaults:alice !authenticate\nbob ALL=(ALL) ALL",
            "Defaults:bob !authenticate\nDefaults:bob authenticate\nbob ALL=(ALL) ALL",
        ],
    )
    def test_the_last_rule_that_matches_decides_whether_a_password_is_asked(self, host, rule):
        accounts = _sudo_host(host, f"root ALL=(ALL:ALL) ALL\n{rule}\n")

        assert _rule(accounts, "bob").granted
        assert not _rule(accounts, "bob").nopasswd

    @pytest.mark.parametrize(
        ("defaults", "asks_root"),
        [
            ("Defaults\trootpw", True),
            ("Defaults targetpw   # root's password", True),
            ("Defaults runaspw", True),
            ("Defaults:bob rootpw", True),
            ("Defaults:%sudo rootpw", True),
            ("Defaults:alice rootpw", False),
            ("Defaults@othermachine rootpw", False),
            ("Defaults rootpw\nDefaults !rootpw", False),
            ("Defaults !visiblepw", False),
            ("Defaults>root rootpw", True),
        ],
    )
    def test_defaults_decide_whose_password_sudo_asks_for(self, host, defaults, asks_root):
        host.write("/etc/hostname", "myhost\n")
        _sudo_host(host, f"{defaults}\n%sudo\tALL=(ALL:ALL) ALL\n")
        # bob has a password; root's is locked.
        host.write("/etc/shadow", "root:!:1:0:99999:7:::\nbob:$6$salt$hash:1:0:99999:7:::\n")

        assert _usable(HostAccounts(host.paths), "bob") is (not asks_root)

    def test_includes_are_followed_where_they_stand(self, host):
        _sudo_host(
            host,
            "root ALL=(ALL:ALL) ALL\n"
            "#include /etc/sudoers.local\n"
            "@include sudoers.more\n"
            "#includedir /etc/sudoers.d\n"
            "#include /etc/sudoers\n",
            dropins={"10-ops": "%sudo ALL=(ALL) NOPASSWD: ALL\n"},
        )
        host.write("/etc/sudoers.local", "alice ALL=(ALL) ALL\n")
        host.write("/etc/sudoers.more", "# nothing here\n")
        accounts = HostAccounts(host.paths)

        assert _rule(accounts, "alice").sources == ("/etc/sudoers.local:1",)
        assert _rule(accounts, "bob").nopasswd

    def test_a_drop_in_with_a_dot_or_a_tilde_is_skipped_in_any_include(self, host):
        accounts = _sudo_host(
            host,
            "@includedir /etc/sudoers.d\n",
            dropins={"10-ops~": "bob ALL=(ALL) ALL\n", "20-ops.bak": "bob ALL=(ALL) ALL\n"},
        )

        assert not _rule(accounts, "bob").granted

    def test_empty_passwords_and_a_second_uid_0_are_found(self, host):
        host.write(
            "/etc/passwd",
            host.read("/etc/passwd") + "toor:x:0:0::/root:/bin/sh\n",  # type: ignore[operator]
        )
        host.write("/etc/shadow", "root::1:0:99999:7:::\n")
        accounts = HostAccounts(host.paths)

        assert accounts.empty_passwords() == ["root"]
        assert accounts.other_uid0() == ["toor"]


# Logins and sockets -------------------------------------------------------------


class TestLogins:
    def test_the_journal_line_gives_user_source_and_fingerprint(self):
        events = parse_short_unix(accepted("root", ED_FP, at=NOW - 60) + "\n")

        assert len(events) == 1
        event = events[0]
        assert (event.user, event.source, event.port, event.fingerprint, event.key_type) == (
            "root",
            "198.51.100.7",
            51234,
            ED_FP,
            "ED25519",
        )

    def test_openssh_9_8_logs_from_sshd_session(self, sshd, host):
        line = accepted("root", ED_FP, at=NOW - 60).replace("sshd[", "sshd-session[")
        _journal(sshd, line)

        history = LoginReader(sshd, host.paths, lambda: NOW).read()

        assert history.source == "journal"
        assert [event.fingerprint for event in history.events] == [ED_FP]
        call = sshd.calls_to("journalctl")[-1]
        assert "_COMM=sshd" in call and "_COMM=sshd-session" in call

    def test_without_a_journal_the_syslog_file_is_read(self, sshd, host):
        sshd.script(["journalctl"], stderr="No journal files were found.", exit_code=1)
        host.write(
            "/var/log/auth.log",
            "2026-09-29T11:00:00.000000+00:00 vps-1 sshd[1]: Accepted password for bob from "
            "192.0.2.4 port 4000 ssh2\n",
        )

        history = LoginReader(sshd, host.paths, lambda: NOW).read()

        assert history.source == "/var/log/auth.log"
        assert history.events[0].method == "password"

    def test_the_window_is_read_once_a_minute_for_every_probe(self, sshd, host):
        """The central's 30-day journal took 2.4 s to read, and every view read it again."""
        from noust.managers.server import security_logins

        security_logins.forget_shared()
        _journal(sshd, accepted("root", ED_FP, at=NOW - 60))

        first = LoginReader(sshd, host.paths, lambda: NOW).read()
        second = LoginReader(sshd, host.paths, lambda: NOW).read()

        assert len(sshd.calls_to("journalctl")) == 1
        assert [e.fingerprint for e in second.events] == [e.fingerprint for e in first.events]

        # What proves a change kept access is read fresh, always.
        LoginReader(sshd, host.paths, lambda: NOW).read(since=NOW - 120)
        assert len(sshd.calls_to("journalctl")) == 2

        # A change to SSH forgets the reading.
        security_logins.forget_shared()
        LoginReader(sshd, host.paths, lambda: NOW).read()
        assert len(sshd.calls_to("journalctl")) == 3

    def test_an_unreadable_history_is_not_kept(self, sshd, host):
        from noust.managers.server import security_logins

        security_logins.forget_shared()
        sshd.script(["journalctl"], stderr="No journal files were found.", exit_code=1)

        LoginReader(sshd, host.paths, lambda: NOW).read()
        LoginReader(sshd, host.paths, lambda: NOW).read()

        assert len(sshd.calls_to("journalctl")) == 2

    def test_old_style_syslog_dates_get_their_year(self):
        events = parse_syslog(
            "Sep 29 11:00:00 vps-1 sshd[1]: Accepted publickey for root from 192.0.2.4 port 1 "
            f"ssh2: ED25519 {ED_FP}\n",
            NOW,
        )

        assert events and abs(events[0].at - (NOW - 3600)) < 86400

    def test_listeners_and_sessions_parse_what_ss_prints(self):
        listeners = parse_listeners(
            'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=900,fd=3))\n'
            "tcp LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*\n"
            'tcp LISTEN 0 511 [::]:5432 [::]:* users:(("postgres",pid=7,fd=5))\n'
            "udp UNCONN 0 0 [::1]:323 [::]:*\n"
        )
        sessions = parse_established(
            '0      0      10.0.0.5:22   198.51.100.7:51234 users:(("sshd",pid=1,fd=4))\n'
        )

        assert [(item.address, item.port, item.exposure, item.process) for item in listeners] == [
            ("0.0.0.0", 22, "all", "sshd"),  # noqa: S104 - what ss prints, not a bind
            ("127.0.0.53", 53, "local", None),
            ("::", 5432, "all", "postgres"),
            ("::1", 323, "local", None),
        ]
        assert sessions[0].peer_address == "198.51.100.7" and sessions[0].peer_port == 51234


# Access proof -------------------------------------------------------------------


class TestAccessProof:
    def test_a_recent_key_login_of_root_is_proof(self, sshd, host):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))

        proof = prove_key_access(_probe(sshd, host))

        assert proof.proved
        assert proof.evidence[0].user == "root" and proof.evidence[0].fingerprint == ED_FP

    def test_a_key_in_the_file_that_never_logged_in_is_not_proof(self, sshd, host):
        proof = prove_key_access(_probe(sshd, host))

        assert not proof.proved
        assert any("during the last 30 days" in problem for problem in proof.problems)

    def test_a_login_with_a_key_that_was_since_removed_is_not_proof(self, sshd, host):
        _journal(sshd, accepted("root", RSA_3072_FP, at=NOW - 3600))

        assert not prove_key_access(_probe(sshd, host)).proved

    def test_root_does_not_count_when_the_fix_is_about_root(self, sshd, host):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))

        assert not prove_key_access(_probe(sshd, host), exclude_root=True).proved

    def test_another_admin_with_usable_sudo_counts_for_root_no(self, sshd, host):
        _journal(sshd, accepted("alice", ALICE_FP, at=NOW - 3600))

        proof = prove_key_access(_probe(sshd, host), exclude_root=True)

        assert proof.proved and proof.evidence[0].user == "alice"

    def test_an_admin_whose_sudo_wants_a_missing_password_does_not_count(self, sshd, host):
        host.write("/etc/sudoers.d/90-alice", "alice ALL=(ALL) ALL\n")
        _journal(sshd, accepted("alice", ALICE_FP, at=NOW - 3600))

        proof = prove_key_access(_probe(sshd, host), exclude_root=True)

        assert not proof.proved
        assert any("cannot use sudo" in problem for problem in proof.problems)

    def test_a_member_of_sudo_with_a_password_is_a_way_in_on_a_stock_debian(self, sshd, host):
        _sudo_host(host, DEBIAN_12_SUDOERS, members="alice")
        host.write("/etc/shadow", "root:!:1:0:99999:7:::\nalice:$6$salt$hash:1:0:99999:7:::\n")
        _journal(sshd, accepted("alice", ALICE_FP, at=NOW - 3600))

        proof = prove_key_access(_probe(sshd, host), exclude_root=True)

        assert proof.proved and proof.evidence[0].user == "alice"

    def test_a_member_of_sudo_without_a_password_still_does_not_count(self, sshd, host):
        _sudo_host(host, DEBIAN_12_SUDOERS, members="alice")
        _journal(sshd, accepted("alice", ALICE_FP, at=NOW - 3600))

        proof = prove_key_access(_probe(sshd, host), exclude_root=True)

        assert not proof.proved
        assert any(
            "would ask for a password it does not have" in problem for problem in proof.problems
        )

    def test_a_group_no_rule_reaches_is_named_so_a_gap_in_the_reader_can_be_found(self, sshd, host):
        _sudo_host(host, "root ALL=(ALL:ALL) ALL\n+admins ALL=(ALL) ALL\n", members="alice")
        _journal(sshd, accepted("alice", ALICE_FP, at=NOW - 3600))

        proof = prove_key_access(_probe(sshd, host), exclude_root=True)

        assert not proof.proved
        assert any(
            "alice can log in but cannot use sudo: it is in group sudo, but no sudoers rule "
            "Noust can read grants root to it or to that group" in problem
            for problem in proof.problems
        )

    def test_an_account_allowusers_refuses_does_not_count(self, sshd, host):
        sshd.per_user["alice"] = {"allowusers": "root"}
        _journal(sshd, accepted("alice", ALICE_FP, at=NOW - 3600))

        proof = prove_key_access(_probe(sshd, host), exclude_root=True)

        assert not proof.proved
        assert any("AllowUsers" in problem for problem in proof.problems)

    def test_the_central_key_never_counts(self, sshd, host):
        host.write("/root/.ssh/authorized_keys", CENTRAL_LINE + "\n")
        _journal(sshd, accepted("root", CENTRAL_FP, at=NOW - 60))

        assert not prove_key_access(_probe(sshd, host)).proved

    def test_an_unreadable_history_is_not_proof(self, sshd, host):
        sshd.script(["journalctl"], stderr="Failed to open journal", exit_code=1)

        proof = prove_key_access(_probe(sshd, host))

        assert not proof.proved
        assert "Failed to open journal" in proof.summary()


# The safe apply -------------------------------------------------------------------


class TestSafeApply:
    def test_disabling_passwords_without_proof_is_refused_and_guided(self, sshd, host, ledger):
        with pytest.raises(AccessGuardError) as caught:
            _security(sshd, host, ledger).apply("disable-passwords")

        assert "ssh-keygen -t ed25519" in caught.value.details
        assert "sshd -t" in caught.value.details
        assert host.read(DROPIN) is None
        assert not sshd.ran("systemd-run")

    def test_disabling_passwords_with_proof_follows_the_protocol(self, sshd, host, ledger):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))

        change = _security(sshd, host, ledger).apply("disable-passwords")

        assert change.status == "pending"
        assert change.expires_at == NOW + CONFIRM_WINDOW
        text = host.read(DROPIN) or ""
        assert "PasswordAuthentication no" in text and "KbdInteractiveAuthentication no" in text
        order = [call[:2] for call in sshd.calls]
        test_at = order.index(("sshd", "-t"))
        timer_at = next(i for i, call in enumerate(sshd.calls) if call[0] == "systemd-run")
        reload_at = sshd.calls.index(("systemctl", "reload", "ssh.service"))
        assert timer_at < test_at < reload_at
        assert ledger.load(change.id).undo == [["systemctl", "reload", "ssh.service"]]
        assert change.after == {
            "passwordauthentication": "no",
            "kbdinteractiveauthentication": "no",
        }

    def test_the_timer_runs_this_module_not_the_cli(self, sshd, host, ledger, tmp_path):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))

        change = _security(sshd, host, ledger).apply("disable-passwords")

        timer = next(call for call in sshd.calls if call[0] == "systemd-run")
        assert f"--unit=noust-security-revert-{change.id}" in timer
        assert f"--on-active={CONFIRM_WINDOW}" in timer
        assert timer[timer.index("--") + 1 :] == (
            "/usr/bin/python3",
            "-m",
            "noust.managers.server.security_pending",
            "revert",
            change.id,
            "--directory",
            str(tmp_path / "changes"),
        )

    def test_a_configuration_sshd_rejects_is_put_back(self, sshd, host, ledger):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))
        sshd.test_exit = 255
        sshd.test_output = "/etc/ssh/sshd_config line 3: Bad configuration option"

        with pytest.raises(SecurityError) as caught:
            _security(sshd, host, ledger).apply("disable-passwords")

        assert caught.value.output == "/etc/ssh/sshd_config line 3: Bad configuration option"
        assert host.read(DROPIN) is None
        assert not sshd.ran("systemctl", "reload")
        assert [change.status for change in ledger.changes()] == ["reverted"]

    def test_a_change_that_cannot_undo_itself_is_not_made(self, sshd, host, ledger):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))
        sshd.script(["systemd-run"], stderr="Failed to connect to bus", exit_code=1)

        with pytest.raises(SecurityError, match="could not arm the timer"):
            _security(sshd, host, ledger).apply("disable-passwords")

        assert host.read(DROPIN) is None
        assert not sshd.ran("systemctl", "reload")
        assert ledger.changes() == []
        assert not any(call[0] == "sshd" and call[1] == "-t" for call in sshd.calls)

    def test_a_value_another_file_wins_is_undone_and_the_winner_named(self, sshd, host, ledger):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))
        sshd.before_include = {"passwordauthentication": "yes"}
        host.write(
            "/etc/ssh/sshd_config",
            "PasswordAuthentication yes\nInclude /etc/ssh/sshd_config.d/*.conf\n",
        )

        with pytest.raises(SecurityError) as caught:
            _security(sshd, host, ledger).apply("disable-passwords")

        assert "/etc/ssh/sshd_config:1: PasswordAuthentication yes" in caught.value.details
        assert host.read(DROPIN) is None
        assert [change.status for change in ledger.changes()] == ["reverted"]

    def test_root_no_is_refused_while_the_central_logs_in_as_root(self, sshd, host, ledger):
        host.write("/root/.ssh/authorized_keys", ED_KEY + "\n" + CENTRAL_LINE + "\n")
        _journal(sshd, accepted("alice", ALICE_FP, at=NOW - 3600))

        with pytest.raises(AccessGuardError) as caught:
            _security(sshd, host, ledger).apply("root-no")

        assert "migrate-tunnel" in caught.value.details

    def test_root_no_with_another_admin_proven_is_applied(self, sshd, host, ledger):
        _journal(sshd, accepted("alice", ALICE_FP, at=NOW - 3600))

        change = _security(sshd, host, ledger).apply("root-no")

        assert change.after == {"permitrootlogin": "no"}

    def test_root_no_is_applied_for_a_member_of_sudo_on_a_stock_ubuntu(self, sshd, host, ledger):
        _sudo_host(host, UBUNTU_2404_SUDOERS, members="alice")
        host.write("/etc/shadow", "root:!:1:0:99999:7:::\nalice:$6$salt$hash:1:0:99999:7:::\n")
        _journal(sshd, accepted("alice", ALICE_FP, at=NOW - 3600))

        change = _security(sshd, host, ledger).apply("root-no")

        assert change.after == {"permitrootlogin": "no"}

    def test_one_change_at_a_time(self, sshd, host, ledger):
        security = _security(sshd, host, ledger)
        security.apply("verbose-logging")

        with pytest.raises(SecurityError, match="waiting for confirmation"):
            security.apply("sensible-defaults")

    def test_nothing_to_do_is_said_so(self, sshd, host, ledger):
        sshd.configured = {"permitemptypasswords": "no"}

        with pytest.raises(SecurityError, match="Nothing to do"):
            _security(sshd, host, ledger).apply("no-empty-passwords")

    def test_the_plan_shows_before_and_after(self, sshd, host, ledger):
        plan = _security(sshd, host, ledger).plan("sensible-defaults").to_dict()

        assert {"directive": "MaxAuthTries", "before": "6", "after": "4"} in plan["changes"]
        assert plan["allowed"] is True


# Confirm or revert -----------------------------------------------------------------


class TestConfirmOrRevert:
    def _applied(self, sshd, host, ledger):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))
        return _security(sshd, host, ledger).apply("disable-passwords")

    def test_confirming_without_a_new_login_is_refused(self, sshd, host, ledger):
        change = self._applied(sshd, host, ledger)

        with pytest.raises(AccessGuardError, match="No new SSH login"):
            _security(sshd, host, ledger).confirm(change.id)

        assert ledger.load(change.id).status == "pending"
        assert not sshd.ran("systemctl", "stop")

    def test_the_session_that_applied_it_does_not_count(self, sshd, host, ledger):
        change = self._applied(sshd, host, ledger)
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))

        with pytest.raises(AccessGuardError):
            _security(sshd, host, ledger).confirm(change.id)

    def test_the_central_reconnecting_does_not_confirm_an_sshd_change(self, sshd, host, ledger):
        host.write("/root/.ssh/authorized_keys", ED_KEY + "\n" + CENTRAL_LINE + "\n")
        change = self._applied(sshd, host, ledger)
        _journal(sshd, accepted("root", CENTRAL_FP, at=NOW + 30))

        with pytest.raises(AccessGuardError):
            _security(sshd, host, ledger).confirm(change.id)

    def test_a_new_login_confirms_and_stops_the_timer(self, sshd, host, ledger):
        change = self._applied(sshd, host, ledger)
        _journal(sshd, accepted("root", ED_FP, at=NOW + 30, port=60000))

        confirmed = _security(sshd, host, ledger).confirm(change.id)

        assert confirmed.status == "confirmed"
        assert "port 60000" in confirmed.resolution
        assert sshd.ran("systemctl", "stop", f"{change.unit}.timer")
        assert "PasswordAuthentication no" in (host.read(DROPIN) or "")

    def test_the_timer_reverts_without_any_confirmation(
        self, sshd, host, ledger, tmp_path, monkeypatch
    ):
        change = self._applied(sshd, host, ledger)
        # What systemd runs when the window closes: the module's own entry point.
        monkeypatch.setattr(
            security_pending,
            "ChangeLedger",
            lambda directory: ChangeLedger(
                directory, runner=sshd, host=host.paths, clock=lambda: NOW + CONFIRM_WINDOW
            ),
        )

        code = security_pending.main(
            ["revert", change.id, "--directory", str(tmp_path / "changes")]
        )

        assert code == 0
        assert host.read(DROPIN) is None
        record = json.loads((tmp_path / "changes" / f"{change.id}.json").read_text())
        assert record["status"] == "expired" and record["resolved_by"] == "timer"
        assert sshd.calls[-1] == ("systemctl", "reload", "ssh.service")
        assert ("sshd", "-t") in sshd.calls[-2:]

    def test_the_timer_after_a_confirmation_changes_nothing(self, sshd, host, ledger):
        change = self._applied(sshd, host, ledger)
        _journal(sshd, accepted("root", ED_FP, at=NOW + 30, port=60000))
        _security(sshd, host, ledger).confirm(change.id)

        again = ledger.revert(change.id, by="timer", expired=True)

        assert again.status == "confirmed"
        assert "PasswordAuthentication no" in (host.read(DROPIN) or "")

    def test_an_earlier_drop_in_is_restored_exactly(self, sshd, host, ledger):
        earlier = "# Generated by Noust. Earlier.\nLogLevel VERBOSE\n"
        host.write(DROPIN, earlier)
        change = self._applied(sshd, host, ledger)

        ledger.revert(change.id, by="tester")

        assert host.read(DROPIN) == earlier

    def test_a_change_whose_timer_was_lost_is_reverted_when_the_ledger_is_read(
        self, sshd, host, ledger
    ):
        change = self._applied(sshd, host, ledger)
        late = ChangeLedger(
            ledger.directory,
            runner=sshd,
            host=host.paths,
            clock=lambda: NOW + CONFIRM_WINDOW + 3600,
        )

        undone = late.revert_overdue()

        assert [item.id for item in undone] == [change.id]
        assert host.read(DROPIN) is None

    def test_a_record_cannot_be_made_to_restore_or_run_anything_else(self, sshd, host, ledger):
        change = self._applied(sshd, host, ledger)
        path = ledger.directory / f"{change.id}.json"
        record = json.loads(path.read_text())
        record["undo"] = [["bash", "-c", "id"]]
        path.write_text(json.dumps(record))

        with pytest.raises(SecurityError, match="Refusing to run bash"):
            ledger.load(change.id)


class TestProofOnRecord:
    """What the console shows before Keep is what Keep will check (one function, both uses)."""

    def _applied(self, sshd, host, ledger):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))
        return _security(sshd, host, ledger).apply("disable-passwords")

    def test_before_any_new_login_it_is_not_on_record(self, sshd, host, ledger):
        change = self._applied(sshd, host, ledger)

        proof = find_proof(_probe(sshd, host), change)

        assert proof.readable and not proof.seen and proof.login is None
        assert proof.to_dict() == {
            "proof_seen": False,
            "proof_login": None,
            "proof_readable": True,
            "proof_error": "",
        }

    def test_the_session_that_applied_it_is_not_on_record(self, sshd, host, ledger):
        change = self._applied(sshd, host, ledger)
        _journal(sshd, accepted("root", ED_FP, at=NOW - 5))

        assert not find_proof(_probe(sshd, host), change).seen

    def test_a_new_login_is_on_record_and_is_the_one_confirm_keeps(self, sshd, host, ledger):
        change = self._applied(sshd, host, ledger)
        _journal(
            sshd,
            accepted("root", ED_FP, at=NOW + 20, source="203.0.113.5", port=50001),
            accepted("root", ED_FP, at=NOW + 30, source="203.0.113.9", port=50002),
        )

        proof = find_proof(_probe(sshd, host), change)
        confirmed = _security(sshd, host, ledger).confirm(change.id)

        assert proof.seen
        assert proof.to_dict()["proof_login"] == {
            "user": "root",
            "source": "203.0.113.9",
            "at": NOW + 30,
        }
        # The newest counting login, which is what Keep records as its evidence.
        assert "203.0.113.9 port 50002" in confirmed.resolution

    def test_a_central_or_its_tunnel_is_not_on_record_for_an_sshd_change(self, sshd, host, ledger):
        host.write("/root/.ssh/authorized_keys", ED_KEY + "\n" + CENTRAL_LINE + "\n")
        change = self._applied(sshd, host, ledger)
        _journal(
            sshd,
            accepted("root", CENTRAL_FP, at=NOW + 30),
            accepted("noust-tunnel", ED_FP, at=NOW + 40),
        )

        assert not find_proof(_probe(sshd, host), change).seen
        with pytest.raises(AccessGuardError, match="No new SSH login"):
            _security(sshd, host, ledger).confirm(change.id)

    def test_a_firewall_change_takes_any_login_the_tunnel_included(self, sshd, host, ledger):
        change = self._applied(sshd, host, ledger)
        change.proof = "any"
        _journal(sshd, accepted("noust-tunnel", CENTRAL_FP, at=NOW + 40))

        proof = find_proof(_probe(sshd, host), change)

        assert proof.seen
        assert proof.to_dict()["proof_login"]["user"] == "noust-tunnel"

    def test_an_unreadable_history_says_why_verbatim_and_keep_refuses_for_the_same_reason(
        self, sshd, host, ledger
    ):
        change = self._applied(sshd, host, ledger)
        sshd.script(["journalctl"], stderr="No journal files were found.", exit_code=1)

        proof = find_proof(_probe(sshd, host), change)

        assert not proof.readable and not proof.seen
        assert "No journal files were found." in proof.error
        assert proof.to_dict()["proof_readable"] is False
        with pytest.raises(AccessGuardError, match="cannot read sshd's login history"):
            _security(sshd, host, ledger).confirm(change.id)

    @pytest.mark.parametrize(
        "lines",
        [
            [],
            [(("root", ED_FP), {"at": NOW - 1})],
            [(("root", ED_FP), {"at": NOW + 1})],
            [(("noust-tunnel", CENTRAL_FP), {"at": NOW + 1})],
            [
                (("alice", ALICE_FP), {"at": NOW + 1}),
                (("noust-tunnel", CENTRAL_FP), {"at": NOW + 2}),
            ],
        ],
        ids=["no logins", "before", "after", "tunnel only", "operator then tunnel"],
    )
    def test_what_is_listed_and_what_keep_does_never_disagree(self, sshd, host, ledger, lines):
        change = self._applied(sshd, host, ledger)
        _journal(sshd, *(accepted(*args, **kwargs) for args, kwargs in lines))
        listed = find_proof(_probe(sshd, host), change).seen

        try:
            _security(sshd, host, ledger).confirm(change.id)
            kept = True
        except AccessGuardError:
            kept = False

        assert listed is kept


class TestProofIsRemembered:
    """The console reads a pending change every few seconds; journalctl is not run each time."""

    @pytest.fixture
    def moment(self) -> list[float]:
        """The probe's clock, which a test moves by hand."""
        return [NOW]

    def _applied(self, sshd, host, ledger):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))
        return _security(sshd, host, ledger).apply("disable-passwords")

    def _probe_at(self, sshd, host, moment) -> SecurityProbe:
        return SecurityProbe(runner=sshd, host=host.paths, clock=lambda: moment[0])

    def _reads(self, sshd) -> int:
        return len(sshd.calls_to("journalctl"))

    def test_a_login_that_was_seen_stays_seen_without_reading_again(
        self, sshd, host, ledger, moment
    ):
        change = self._applied(sshd, host, ledger)
        _journal(sshd, accepted("root", ED_FP, at=NOW + 20, source="203.0.113.5", port=50001))
        first = find_proof(self._probe_at(sshd, host, moment), change)
        reads = self._reads(sshd)

        # Long after the short life of a "no", and with the journal now saying nothing.
        moment[0] += 3600
        _journal(sshd)
        again = find_proof(self._probe_at(sshd, host, moment), change)

        assert first.seen and again.seen
        assert again.login == first.login
        assert self._reads(sshd) == reads

    def test_no_login_yet_is_kept_for_a_few_seconds_only(self, sshd, host, ledger, moment):
        change = self._applied(sshd, host, ledger)
        reads = self._reads(sshd)

        first = find_proof(self._probe_at(sshd, host, moment), change)
        moment[0] += NEGATIVE_TTL / 2
        _journal(sshd, accepted("root", ED_FP, at=NOW + 1, source="203.0.113.5", port=50001))
        within = find_proof(self._probe_at(sshd, host, moment), change)
        assert self._reads(sshd) == reads + 1
        assert not first.seen and not within.seen

        moment[0] += NEGATIVE_TTL
        after = find_proof(self._probe_at(sshd, host, moment), change)

        assert self._reads(sshd) == reads + 2
        assert after.seen

    def test_an_unreadable_history_is_kept_for_a_few_seconds_only(self, sshd, host, ledger, moment):
        change = self._applied(sshd, host, ledger)
        sshd.script(["journalctl"], stderr="No journal files were found.", exit_code=1)
        reads = self._reads(sshd)

        first = find_proof(self._probe_at(sshd, host, moment), change)
        second = find_proof(self._probe_at(sshd, host, moment), change)

        assert not first.readable and second == first
        assert self._reads(sshd) == reads + 1
        moment[0] += NEGATIVE_TTL
        find_proof(self._probe_at(sshd, host, moment), change)
        assert self._reads(sshd) == reads + 2

    def test_a_clock_that_went_back_does_not_keep_an_answer_alive(self, sshd, host, ledger, moment):
        change = self._applied(sshd, host, ledger)
        reads = self._reads(sshd)
        find_proof(self._probe_at(sshd, host, moment), change)

        moment[0] -= 5
        find_proof(self._probe_at(sshd, host, moment), change)

        assert self._reads(sshd) == reads + 2

    def test_keep_does_not_refuse_on_a_remembered_no(self, sshd, host, ledger, moment):
        change = self._applied(sshd, host, ledger)
        probe = self._probe_at(sshd, host, moment)
        assert not find_proof(probe, change).seen
        _journal(sshd, accepted("root", ED_FP, at=NOW + 2, source="203.0.113.5", port=50001))

        # Still inside the life of the "no": the listing may say so, Keep must not.
        confirmed = SshSecurity(probe, ledger, actor="tester").confirm(change.id)

        assert confirmed.status == "confirmed"

    def test_keep_goes_by_a_login_that_was_already_found_without_reading(
        self, sshd, host, ledger, moment
    ):
        change = self._applied(sshd, host, ledger)
        _journal(sshd, accepted("root", ED_FP, at=NOW + 20, source="203.0.113.5", port=50001))
        probe = self._probe_at(sshd, host, moment)
        assert find_proof(probe, change).seen
        reads = self._reads(sshd)

        confirmed = SshSecurity(probe, ledger, actor="tester").confirm(change.id)

        assert confirmed.status == "confirmed"
        assert "203.0.113.5 port 50001" in confirmed.resolution
        assert self._reads(sshd) == reads

    def test_each_change_is_remembered_on_its_own(self, sshd, host, ledger, moment):
        first = self._applied(sshd, host, ledger)
        second = copy.copy(first)
        second.id = "feed1234"
        reads = self._reads(sshd)
        probe = self._probe_at(sshd, host, moment)

        find_proof(probe, first)
        find_proof(probe, second)
        find_proof(probe, first)

        assert first.id != second.id
        assert self._reads(sshd) == reads + 2

    def test_a_change_that_is_not_pending_is_not_remembered(self, sshd, host, ledger, moment):
        change = self._applied(sshd, host, ledger)
        change.status = "reverted"
        reads = self._reads(sshd)
        probe = self._probe_at(sshd, host, moment)

        find_proof(probe, change)
        find_proof(probe, change)

        assert self._reads(sshd) == reads + 2

    def test_invalidating_the_probe_forgets_what_was_found(self, sshd, host, ledger, moment):
        change = self._applied(sshd, host, ledger)
        _journal(sshd, accepted("root", ED_FP, at=NOW + 20, source="203.0.113.5", port=50001))
        probe = self._probe_at(sshd, host, moment)
        find_proof(probe, change)
        reads = self._reads(sshd)

        probe.invalidate()
        find_proof(probe, change)

        assert self._reads(sshd) == reads + 1

    def test_listing_the_changes_twice_reads_the_history_once(self, sshd, host, ledger, moment):
        self._applied(sshd, host, ledger)
        security = ServerSecurity(
            actor="tester",
            runner=sshd,
            host=host.paths,
            changes=ledger.directory,
            console_port=8080,
            clock=lambda: moment[0],
            python="/usr/bin/python3",
        )
        reads = self._reads(sshd)

        first = security.described_changes()
        second = security.described_changes()

        assert first[0]["status"] == second[0]["status"] == "pending"
        assert first[0]["proof_seen"] is second[0]["proof_seen"] is False
        assert self._reads(sshd) == reads + 1

    def test_what_was_found_about_a_settled_change_is_dropped_when_the_changes_are_listed(
        self, sshd, host, ledger, moment
    ):
        change = self._applied(sshd, host, ledger)
        _journal(sshd, accepted("root", ED_FP, at=NOW + 20, source="203.0.113.5", port=50001))
        security = ServerSecurity(
            actor="tester",
            runner=sshd,
            host=host.paths,
            changes=ledger.directory,
            console_port=8080,
            clock=lambda: moment[0],
            python="/usr/bin/python3",
        )
        assert security.described_changes()[0]["proof_seen"] is True
        ledger.revert(change.id, by="tester")
        reads = self._reads(sshd)

        assert security.described_changes()[0]["status"] == "reverted"

        assert self._reads(sshd) == reads
        assert not any(key[0] == change.id for key in security_proof._remembered)


# Keys: add and remove ---------------------------------------------------------------


class TestKeyChanges:
    def test_a_key_is_added_to_the_file_sshd_reads(self, sshd, host, ledger):
        result = _security(sshd, host, ledger).add_key("alice", RSA_3072)

        assert result["added"] and result["file"] == "/home/alice/.ssh/authorized_keys"
        assert RSA_3072.split()[1] in (host.read("/home/alice/.ssh/authorized_keys") or "")
        assert sshd.ran("chown", "-h", "1000:1000")

    def test_adding_a_key_already_there_changes_nothing(self, sshd, host, ledger):
        result = _security(sshd, host, ledger).add_key("alice", ALICE_KEY)

        assert result["added"] is False

    def test_keys_are_only_managed_for_administrators(self, sshd, host, ledger):
        with pytest.raises(SecurityError, match="not one of them"):
            _security(sshd, host, ledger).add_key("bob", ED_KEY)

    def test_the_central_key_is_not_removed_without_override(self, sshd, host, ledger):
        host.write("/root/.ssh/authorized_keys", ED_KEY + "\n" + CENTRAL_LINE + "\n")

        with pytest.raises(AccessGuardError, match="Refusing") as caught:
            _security(sshd, host, ledger).remove_key("root", CENTRAL_FP)

        assert "noust fleet deauthorize" in caught.value.details
        assert CENTRAL_FP and "noust-central:hub-1" in (
            host.read("/root/.ssh/authorized_keys") or ""
        )

    def test_the_key_of_a_session_open_now_is_not_removed_without_override(
        self, sshd, host, ledger
    ):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 60, source="198.51.100.7", port=51234))
        sshd.script(
            ["ss", "-Htnp"],
            stdout='0 0 10.0.0.5:22 198.51.100.7:51234 users:(("sshd",pid=1,fd=4))\n',
        )

        with pytest.raises(AccessGuardError) as caught:
            _security(sshd, host, ledger).remove_key("root", ED_FP)

        assert "198.51.100.7" in caught.value.details

    def test_the_last_key_while_passwords_are_off_is_not_removed(self, sshd, host, ledger):
        sshd.configured = {"passwordauthentication": "no"}
        host.write("/home/alice/.ssh/authorized_keys", "")

        with pytest.raises(AccessGuardError, match="Refusing") as caught:
            _security(sshd, host, ledger).remove_key("root", ED_FP)

        assert "last operator key" in caught.value.details

    def test_override_removes_it_and_says_what_it_overrode(self, sshd, host, ledger):
        host.write("/root/.ssh/authorized_keys", ED_KEY + "\n" + CENTRAL_LINE + "\n")

        result = _security(sshd, host, ledger).remove_key("root", CENTRAL_FP, force=True)

        assert result["overridden"] and result["removed"] == [CENTRAL_LINE]
        assert (host.read("/root/.ssh/authorized_keys") or "").strip() == ED_KEY

    def test_an_unguarded_key_is_removed_keeping_every_other_line(self, sshd, host, ledger):
        host.write("/root/.ssh/authorized_keys", f"# mine\n{ED_KEY}\n{RSA_3072}\n")

        result = _security(sshd, host, ledger).remove_key("root", RSA_3072_FP)

        assert result["removed"] == [RSA_3072]
        assert host.read("/root/.ssh/authorized_keys") == f"# mine\n{ED_KEY}\n"


# Audit ------------------------------------------------------------------------------


def _events(name: str) -> list[dict]:
    from noust.core import audit

    return [entry for entry in audit.get_log().iter_newest_first() if entry.get("action") == name]


class TestAudit:
    def test_a_refused_fix_is_on_record(self, sshd, host, ledger):
        with pytest.raises(AccessGuardError):
            _security(sshd, host, ledger).apply("disable-passwords")

        [entry] = _events("server.ssh")
        assert entry["result"] == "denied"
        assert entry["details"]["fix"] == "disable-passwords"

    def test_the_timer_revert_is_on_record_as_well(self, sshd, host, ledger):
        _journal(sshd, accepted("root", ED_FP, at=NOW - 3600))
        change = _security(sshd, host, ledger).apply("disable-passwords")

        ledger.revert(change.id, by="timer", expired=True)

        actions = [entry["details"].get("action") for entry in _events("server.ssh")]
        assert actions == ["expire", "fix"]
