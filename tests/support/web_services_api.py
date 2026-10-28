# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_services_api.py`` that other tests build on."""

from __future__ import annotations

from typing import ClassVar


class FakeServiceManager:
    """Stand-in for ServiceManager that records calls instead of running systemctl."""

    instances: ClassVar[list[FakeServiceManager]] = []
    #: Scripted answers for get_status(), keyed by unit name. Reset per test
    #: by the fake_manager fixture.
    statuses: ClassVar[dict[str, dict]] = {}
    #: Scripted listing: every unit on the host, as
    #: ServiceManager.list_services reports it; ``managed`` defaults to true.
    all_units: ClassVar[list[dict]] = []
    #: Scripted (success, output) answer for verify_unit().
    verify_result: ClassVar[tuple[bool, str]] = (True, "")

    def __init__(self, verbose: bool = False):
        """
        Args:
            verbose: Ignored, kept for signature compatibility.
        """
        self.calls: list[tuple[str, ...]] = []
        FakeServiceManager.instances.append(self)

    def get_status(self, name: str, *, require_managed: bool = True) -> dict:
        """
        Return the scripted status for a unit, recording the call.

        Args:
            name: Unit name.
            require_managed: Recorded, but this stub never raises for a
                foreign unit - the real ownership guard is exercised in
                tests/test_service_manager.py.

        Returns:
            The scripted dict, or a bland "exists but stopped" default.
        """
        self.calls.append(("get_status", name, require_managed))
        return FakeServiceManager.statuses.get(
            name,
            {
                "name": name,
                "exists": True,
                "active": False,
                "enabled": False,
                "pid": "",
                "memory": "",
                "uptime": "",
                "active_state": "",
                "sub_state": "",
                "restarts": "0",
                "result": "",
                "managed": True,
            },
        )

    def list_statuses(self, all_services: bool = False, state: str | None = None) -> list[dict]:
        """
        Return the scripted listing, each unit described like get_status.

        Args:
            all_services: Recorded for assertions.
            state: Recorded for assertions; the real filter is exercised in
                tests/test_service_manager.py.

        Returns:
            The scripted status of every unit in ``all_units``.
        """
        self.calls.append(("list_statuses", all_services))
        self.calls.append(("list_statuses_state", state))
        return [
            FakeServiceManager.statuses.get(unit["name"], {"name": unit["name"]})
            for unit in FakeServiceManager.all_units
            if all_services or unit.get("managed", True)
        ]

    def daemon_reload(self) -> bool:
        """Record a daemon-reload request."""
        self.calls.append(("daemon-reload",))
        return True

    def enable(self, name: str) -> bool:
        """Record an enable request."""
        self.calls.append(("enable", name))
        return True

    def disable(self, name: str) -> bool:
        """Record a disable request."""
        self.calls.append(("disable", name))
        return True

    def stop(self, name: str) -> bool:
        """Record a stop request."""
        self.calls.append(("stop", name))
        return True

    def start(self, name: str) -> bool:
        """Record a start request."""
        self.calls.append(("start", name))
        return True

    def restart(self, name: str) -> bool:
        """Record a restart request."""
        self.calls.append(("restart", name))
        return True

    def update_config(self, name: str, content: str) -> bool:
        """Record a unit rewrite."""
        self.calls.append(("update_config", name))
        return True

    def delete_service(self, name: str) -> bool:
        """Record a deletion."""
        self.calls.append(("delete_service", name))
        return True

    def create_from_unit(self, name: str, content: str) -> bool:
        """Record a creation from a raw unit."""
        self.calls.append(("create_from_unit", name))
        return True

    def logs(self, name: str, lines: int = 50) -> str:
        """Return canned log output."""
        self.calls.append(("logs", name))
        return f"logs for {name}"

    def verify_unit(self, content: str) -> tuple[bool, str]:
        """Record the candidate content and return the scripted verdict."""
        self.calls.append(("verify_unit", content))
        return FakeServiceManager.verify_result
