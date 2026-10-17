# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A reboot that systemd refuses because something holds a block inhibitor says who
(checklist F-30, owner feedback item 82).

Scheduling a reboot right after starting the operating system updates failed with
"Operation denied due to active block inhibitor": the update's own apt-get held a
shutdown inhibitor. The refusal is right, and the console showed only systemd's
sentence. Noust reads ``systemd-inhibit --list`` and says who blocks and what to do,
and it never overrides an inhibitor (``--check-inhibitors=no`` is not an option).
"""

from __future__ import annotations

import pytest

from noust.core.runner import FakeRunner
from noust.managers.server.errors import HostBusyError, ServerError
from noust.managers.server.power import parse_inhibitors
from noust.managers.server.power_records import PowerRecords
from tests.test_server_power import _healthy, _manager, host, store  # noqa: F401

INHIBITORS = """\
Unattended Upgrades Shutdown   0 root  987 unattended-upgr shutdown Stop ongoing upgrades or perform upgrades before shutdown delay
NetworkManager                 0 root  765 NetworkManager   sleep    NetworkManager needs to turn off networks delay
APT                            0 root 5678 apt-get          shutdown APT is installing or removing packages block
"""

DENIED = "Failed to schedule shutdown: Operation denied due to active block inhibitor\n"


def blocked(listing: str = INHIBITORS) -> FakeRunner:
    runner = _healthy(FakeRunner())
    runner.script(["shutdown"], stderr=DENIED, exit_code=1)
    runner.script(["systemd-inhibit"], stdout=listing)
    return runner


def test_the_listing_is_read_by_what_follows_the_account_and_the_process() -> None:
    found = parse_inhibitors(INHIBITORS)

    assert [(i.who, i.pid, i.command, i.what, i.mode) for i in found] == [
        ("Unattended Upgrades Shutdown", 987, "unattended-upgr", ("shutdown",), "delay"),
        ("NetworkManager", 765, "NetworkManager", ("sleep",), "delay"),
        ("APT", 5678, "apt-get", ("shutdown",), "block"),
    ]
    assert found[2].why == "APT is installing or removing packages"


def test_nothing_that_is_not_a_line_of_the_table_is_read() -> None:
    assert parse_inhibitors("") == []
    assert parse_inhibitors("0 inhibitors listed.\n") == []


def test_a_reboot_blocked_by_apt_names_apt_and_says_to_wait(store, host) -> None:  # noqa: F811
    runner = blocked()

    with pytest.raises(HostBusyError) as raised:
        _manager(store, host, runner).schedule("reboot", minutes=0, actor=None)

    error = raised.value
    assert "APT" in error.message and "reboot" in error.message
    assert "APT is installing or removing packages" in error.message
    assert error.holders == ["APT"], "a delay inhibitor does not refuse anything"
    assert "when it finishes" in error.details.lower()
    assert "Operation denied due to active block inhibitor" in (error.output or "")
    assert PowerRecords(store).active() is None


def test_noust_never_overrides_the_inhibitor(store, host) -> None:  # noqa: F811
    runner = blocked()

    with pytest.raises(HostBusyError):
        _manager(store, host, runner).schedule("reboot", minutes=0, actor=None, force=True)

    for call in runner.calls:
        assert "--check-inhibitors=no" not in call
        assert "-f" not in call and "--force" not in call


def test_the_listing_is_only_asked_after_a_refusal(store, host) -> None:  # noqa: F811
    runner = _healthy(FakeRunner())

    _manager(store, host, runner).schedule("reboot", minutes=1, actor=None)

    assert not runner.ran("systemd-inhibit")


def test_a_refusal_for_another_reason_stays_the_shutdown_s_own_words(store, host) -> None:  # noqa: F811
    runner = _healthy(FakeRunner())
    runner.script(["shutdown"], stderr="Failed to connect to bus", exit_code=1)

    with pytest.raises(ServerError) as raised:
        _manager(store, host, runner).schedule("reboot", minutes=1, actor=None)

    assert not isinstance(raised.value, HostBusyError)
    assert "Failed to connect to bus" in (raised.value.output or "")
    assert not runner.ran("systemd-inhibit")


def test_when_the_listing_fails_the_refusal_is_still_explained(store, host) -> None:  # noqa: F811
    runner = _healthy(FakeRunner())
    runner.script(["shutdown"], stderr=DENIED, exit_code=1)
    runner.script(["systemd-inhibit"], stderr="boom", exit_code=1)

    with pytest.raises(HostBusyError) as raised:
        _manager(store, host, runner).schedule("poweroff", minutes=0, actor=None)

    assert "inhibitor" in raised.value.message.lower()
    assert "Operation denied" in (raised.value.output or "")


def test_a_package_manager_is_recognised_as_the_update_that_was_started(store, host) -> None:  # noqa: F811
    with pytest.raises(HostBusyError) as raised:
        _manager(store, host, blocked()).schedule("reboot", minutes=0, actor=None)

    assert "package" in raised.value.details.lower()
    assert "Activity" in raised.value.details


def test_the_listing_is_declared_a_read_only_probe() -> None:
    from noust.core.runner import is_read_only

    assert is_read_only(("systemd-inhibit", "--list", "--no-pager", "--no-legend"))
