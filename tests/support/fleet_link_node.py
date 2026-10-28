# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_fleet_link_node.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path

from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.fleet.link.node import link, unit_name, unlink
from noust.web.auth import TokenManager
from tests.support.fleet_support import HOST_KEY, KeygenRunner
from tests.support.link_support import NONCE

CENTRAL_HOST_KEY = HOST_KEY


class Server:
    """A server's directories, runner and token manager, for one test."""

    def __init__(self, tmp_path: Path, tokens: TokenManager) -> None:
        self.tokens = tokens
        self.link_root = tmp_path / "link"
        self.unit_dir = tmp_path / "systemd"
        self.unit_dir.mkdir()
        self.runner = KeygenRunner()
        self.runner.script(["systemctl", "is-active"], stdout="active\n")
        self.console_calls = 0
        NoustStore.reset_instance()
        self.store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())

    def console(self) -> int:
        self.console_calls += 1
        return 8080

    def link(self, **overrides):
        values = {
            "central": "nas",
            "host": "central.example.com",
            "ssh_port": 2222,
            "host_key": CENTRAL_HOST_KEY,
            "remote_port": 24001,
            "nonce": NONCE,
            "node_name": "web-5",
            "tokens": self.tokens,
            "ensure_console": self.console,
            "confirm_replace": lambda names: True,
            "runner": self.runner,
            "link_root": self.link_root,
            "unit_dir": self.unit_dir,
            "store": self.store,
            "ssh_program": "/usr/bin/ssh",
        }
        values.update(overrides)
        return link(**values)

    def unlink(self, **overrides):
        values = {
            "central": "nas",
            "tokens": self.tokens,
            "runner": self.runner,
            "link_root": self.link_root,
            "unit_dir": self.unit_dir,
        }
        values.update(overrides)
        return unlink(**values)

    @property
    def unit(self) -> Path:
        return self.unit_dir / unit_name("nas")
