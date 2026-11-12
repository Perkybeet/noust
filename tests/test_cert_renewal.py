# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether the renewal of certificates is working, which is not the same as how many days they have (item 172).

certbot renews on a timer of its own and Noust leaves it alone. A renewal that fails (a blocked
port 80, a rule that refuses the validation, a DNS record that moved) is silent until the
certificate is close to its end, and with 45 days to live that is much less time than it used to
be. So the result of the last run is read from systemd, and said with certbot's own words.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.managers import cert_renewal
from noust.managers.cert_renewal import read_renewal

TIMER_ACTIVE = (
    "LoadState=loaded\n"
    "ActiveState=active\n"
    "UnitFileState=enabled\n"
    "NextElapseUSecRealtime=Wed 2026-11-11 00:41:10 UTC\n"
    "LastTriggerUSec=Tue 2026-11-10 12:11:03 UTC\n"
)
SERVICE_OK = (
    "ActiveState=inactive\n"
    "Result=success\n"
    "ExecMainStatus=0\n"
    "ExecMainExitTimestamp=Tue 2026-11-10 12:11:05 UTC\n"
)
SERVICE_FAILED = (
    "ActiveState=failed\n"
    "Result=exit-code\n"
    "ExecMainStatus=1\n"
    "ExecMainExitTimestamp=Tue 2026-11-10 12:11:09 UTC\n"
)
CERTBOT_SAYS = (
    "Failed to renew certificate shop.example.com with error: Some challenges have failed.\n"
    "Detail: 203.0.113.9: Fetching http://shop.example.com/.well-known/acme-challenge/x: "
    "Timeout during connect (likely firewall problem)\n"
)


@pytest.fixture
def log(tmp_path: Path) -> Path:
    """Where certbot's own log would be; absent unless a test writes it."""
    return tmp_path / "letsencrypt.log"


def _timer(runner: FakeRunner, *, show: str = TIMER_ACTIVE, service: str = SERVICE_OK) -> None:
    runner.script(["systemctl", "show", "certbot.timer"], stdout=show)
    runner.script(["systemctl", "show", "certbot.service"], stdout=service)


class TestTheTimer:
    def test_a_timer_that_runs_and_whose_last_run_worked_is_fine(
        self, runner: FakeRunner, log: Path
    ) -> None:
        _timer(runner)

        status = read_renewal(runner, log_path=log)

        assert status.mechanism == "timer"
        assert status.unit == "certbot.timer"
        assert status.timer_active is True
        assert status.last_ok is True
        assert status.last_run == "Tue 2026-11-10 12:11:05 UTC"
        assert status.next_run == "Wed 2026-11-11 00:41:10 UTC"
        assert status.problem is None
        assert status.output == ""

    def test_a_last_run_that_failed_carries_certbots_own_words(
        self, runner: FakeRunner, log: Path
    ) -> None:
        _timer(runner, service=SERVICE_FAILED)
        runner.script(["journalctl", "-u", "certbot.service"], stdout=CERTBOT_SAYS)

        status = read_renewal(runner, log_path=log)

        assert status.last_ok is False
        assert status.problem == "failed"
        assert status.output == CERTBOT_SAYS.strip()
        assert status.last_run == "Tue 2026-11-10 12:11:09 UTC"

    def test_the_journal_is_asked_for_that_service_and_nothing_is_changed(
        self, runner: FakeRunner, log: Path
    ) -> None:
        _timer(runner, service=SERVICE_FAILED)
        runner.script(["journalctl", "-u", "certbot.service"], stdout=CERTBOT_SAYS)

        read_renewal(runner, log_path=log)

        assert (
            "journalctl",
            "-u",
            "certbot.service",
            "-n",
            "40",
            "--no-pager",
            "-o",
            "cat",
        ) in runner.calls

    def test_a_stopped_timer_is_a_problem_even_if_the_last_run_worked(
        self, runner: FakeRunner, log: Path
    ) -> None:
        stopped = TIMER_ACTIVE.replace("ActiveState=active", "ActiveState=inactive").replace(
            "UnitFileState=enabled", "UnitFileState=disabled"
        )
        _timer(runner, show=stopped)

        status = read_renewal(runner, log_path=log)

        assert status.timer_active is False
        assert status.problem == "timer_stopped"

    def test_a_timer_that_has_never_fired_has_no_last_run_and_is_not_a_failure(
        self, runner: FakeRunner, log: Path
    ) -> None:
        never = "ActiveState=inactive\nResult=success\nExecMainStatus=0\nExecMainExitTimestamp=\n"
        _timer(runner, service=never)

        status = read_renewal(runner, log_path=log)

        assert status.last_run is None
        assert status.last_ok is None
        assert status.problem is None

    def test_a_run_in_progress_is_not_a_failure(self, runner: FakeRunner, log: Path) -> None:
        running = "ActiveState=activating\nResult=success\nExecMainStatus=0\n"
        _timer(runner, service=running)

        assert read_renewal(runner, log_path=log).problem is None

    def test_the_other_names_of_the_timer_are_tried(self, runner: FakeRunner, log: Path) -> None:
        """EPEL calls it certbot-renew, the snap snap.certbot.renew."""
        runner.script(["systemctl", "show", "certbot.timer"], stdout="LoadState=not-found\n")
        runner.script(
            ["systemctl", "show", "certbot-renew.timer"],
            stdout=TIMER_ACTIVE,
        )
        runner.script(["systemctl", "show", "certbot-renew.service"], stdout=SERVICE_OK)

        status = read_renewal(runner, log_path=log)

        assert status.unit == "certbot-renew.timer"
        assert status.mechanism == "timer"

    def test_the_output_is_cut_to_what_a_notification_can_carry(
        self, runner: FakeRunner, log: Path
    ) -> None:
        _timer(runner, service=SERVICE_FAILED)
        runner.script(["journalctl", "-u", "certbot.service"], stdout="x" * 20000)

        assert len(read_renewal(runner, log_path=log).output) <= cert_renewal.OUTPUT_LIMIT


class TestWithoutATimer:
    def test_a_cron_entry_without_a_log_says_nothing_of_the_last_run(
        self, runner: FakeRunner, log: Path, tmp_path: Path
    ) -> None:
        cron = tmp_path / "certbot-renew"
        cron.write_text("0 0,12 * * * root certbot renew -q\n")
        runner.script(["systemctl", "show"], stdout="LoadState=not-found\n")

        status = read_renewal(runner, log_path=log, cron_file=cron)

        assert status.mechanism == "cron"
        assert status.last_ok is None
        assert status.problem is None

    def test_a_cron_renewal_is_read_from_certbots_log(
        self, runner: FakeRunner, log: Path, tmp_path: Path
    ) -> None:
        cron = tmp_path / "certbot-renew"
        cron.write_text("0 0,12 * * * root certbot renew -q\n")
        log.write_text(
            "2026-11-10 00:00:03,1 - Processing /etc/letsencrypt/renewal/shop.example.com.conf\n"
            "2026-11-10 00:00:05,2 - Congratulations, all renewals succeeded\n"
            "2026-11-10 12:00:03,3 - Processing /etc/letsencrypt/renewal/shop.example.com.conf\n"
            "2026-11-10 12:00:09,4 - Failed to renew certificate shop.example.com with error: "
            "Some challenges have failed.\n"
            "2026-11-10 12:00:09,5 - 1 renew failure(s), 0 parse failure(s)\n"
        )
        runner.script(["systemctl", "show"], stdout="LoadState=not-found\n")

        status = read_renewal(runner, log_path=log, cron_file=cron)

        assert status.mechanism == "cron"
        assert status.last_ok is False
        assert status.problem == "failed"
        assert "Failed to renew certificate shop.example.com" in status.output

    def test_a_cron_renewal_whose_last_line_is_a_success_is_fine(
        self, runner: FakeRunner, log: Path, tmp_path: Path
    ) -> None:
        cron = tmp_path / "certbot-renew"
        cron.write_text("0 0,12 * * * root certbot renew -q\n")
        log.write_text(
            "2026-11-10 00:00:03,1 - Failed to renew certificate shop.example.com with error: x\n"
            "2026-11-10 12:00:05,2 - Congratulations, all renewals succeeded\n"
        )
        runner.script(["systemctl", "show"], stdout="LoadState=not-found\n")

        status = read_renewal(runner, log_path=log, cron_file=cron)

        assert status.last_ok is True
        assert status.problem is None

    def test_nothing_that_renews_is_not_called_a_failure(
        self, runner: FakeRunner, log: Path, tmp_path: Path
    ) -> None:
        """An operator may renew some other way; Noust names it and does not raise an alarm."""
        runner.script(["systemctl", "show"], stdout="LoadState=not-found\n")

        status = read_renewal(runner, log_path=log, cron_file=tmp_path / "missing")

        assert status.mechanism == "none"
        assert status.problem is None

    def test_a_machine_without_systemd_answers_unknown(
        self, runner: FakeRunner, log: Path, tmp_path: Path
    ) -> None:
        runner.script(["systemctl", "show"], stderr="not booted with systemd", exit_code=1)

        status = read_renewal(runner, log_path=log, cron_file=tmp_path / "missing")

        assert status.mechanism == "none"
        assert status.problem is None
