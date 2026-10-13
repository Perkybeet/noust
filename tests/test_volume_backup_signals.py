# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An application stopped for a backup is started again when the process is told to stop (checklist H-25)."""

from __future__ import annotations

import os
import signal
import threading
from pathlib import Path
from typing import Any

import pytest

from noust.core.signal_guard import stop_as_exit
from noust.managers.volume_backups import VolumeBackupManager
from tests.test_volume_backups import (  # noqa: F401
    APP,
    DOMAIN,
    FakeServices,
    _reset_store,
    app,
    app_dir,
    manager,
    runner,
    services,
)


class TestTheGuard:
    def test_sigterm_becomes_an_exit_the_finally_blocks_see(self) -> None:
        ran: list[str] = []

        with pytest.raises(SystemExit) as stopped:
            with stop_as_exit() as installed:
                assert installed is True
                try:
                    os.kill(os.getpid(), signal.SIGTERM)
                    signal.pause()
                finally:
                    ran.append("finally")

        assert ran == ["finally"] and stopped.value.code == 128 + signal.SIGTERM

    def test_the_handlers_are_put_back(self) -> None:
        before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))

        with stop_as_exit():
            assert signal.getsignal(signal.SIGTERM) is not before[0]

        assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)) == before

    def test_off_the_main_thread_nothing_is_installed(self) -> None:
        seen: list[bool] = []

        def run() -> None:
            with stop_as_exit() as installed:
                seen.append(installed)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()

        assert seen == [False]


class TestAStoppedApplication:
    def test_a_sigterm_during_the_copy_still_starts_it_again(
        self,
        manager: VolumeBackupManager,  # noqa: F811
        services: FakeServices,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def interrupted(*args: Any, **kwargs: Any) -> Any:
            os.kill(os.getpid(), signal.SIGTERM)
            signal.pause()

        monkeypatch.setattr(manager, "_write_path_archive", interrupted)

        with pytest.raises(SystemExit):
            manager.create(DOMAIN, consistency="stop")

        assert services.calls == [("stop", APP), ("start", APP)]

    def test_a_live_backup_installs_no_handler(
        self,
        manager: VolumeBackupManager,  # noqa: F811
        services: FakeServices,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[Any] = []

        def look(*args: Any, **kwargs: Any) -> Any:
            seen.append(signal.getsignal(signal.SIGTERM))
            raise RuntimeError("stop here")

        monkeypatch.setattr(manager, "_write_path_archive", look)
        before = signal.getsignal(signal.SIGTERM)

        with pytest.raises(RuntimeError):
            manager.create(DOMAIN, consistency="live")

        assert seen == [before] and Path(".")
