# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the health gate asks, when what keeps a deployment alive can say where it answers.

A server's gate asks 127.0.0.1 on the port the application publishes, and is exactly what it was. Where
Docker keeps the deployment alive and Noust is in another container, the control that restarts it also
knows where it answers from Noust's side (an address on a network they share, and the port inside the
container), and the gate asks there, once the restart has made the container what it will be. What is held
here: that nothing changes for a control that does not say, that the address is looked up after the
restart and not before, that a port the operator put in the check is kept, that a control that cannot find
the address fails the gate with its reason and not with a probe of an address nobody listens on, and that
the evidence of a failed probe names the address that was asked.
"""

from __future__ import annotations

from typing import Any

from noust.core.exceptions import ServiceError
from noust.core.logger import Logger
from noust.deployers.helpers.health_gate import HealthCheck, HealthGate


class Units:
    """What the gate needs of a unit's manager, and nothing about where it answers."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def restart(self, name: str) -> None:
        self.events.append(f"restart {name}")

    def logs(self, name: str, lines: int = 50) -> str:
        return "the log"


class Docker(Units):
    """The same, and it says where the application answers."""

    def __init__(self, answer: str | ServiceError | None = "172.18.0.3:8080") -> None:
        super().__init__()
        self.answer = answer

    def address_of(self, name: str) -> str | None:
        self.events.append(f"address {name}")
        if isinstance(self.answer, ServiceError):
            raise self.answer
        return self.answer


class Probe:
    def __init__(self, units: Units | None = None, healthy: bool = True) -> None:
        self.units, self.healthy = units, healthy
        self.urls: list[str] = []

    def __call__(self, url: str, **kwargs: Any) -> bool:
        self.urls.append(url)
        if self.units is not None:
            self.units.events.append(f"probe {url}")
        if not self.healthy:
            kwargs["on_attempt"]("Health check attempt 1 failed: connection refused")
        return self.healthy


def gate(units: Units, probe: Probe, *, check: HealthCheck | None = None) -> HealthGate:
    made = check or HealthCheck()
    return HealthGate(
        unit="shop-example-com",
        url=made.url(8081),
        services=units,
        logger=Logger(),
        probe=probe,
        check=made,
    )


def test_a_manager_that_does_not_say_where_it_answers_is_asked_where_it_always_was() -> None:
    units = Units()
    probe = Probe(units)

    assert gate(units, probe).restart_and_probe() == (True, "")

    assert probe.urls == ["http://127.0.0.1:8081/"]


def test_a_manager_that_says_where_gets_the_probe_there_and_with_the_same_path_and_scheme() -> None:
    units = Docker("172.18.0.3:8080")
    probe = Probe(units)
    check = HealthCheck(path="/healthz", scheme="https")

    assert gate(units, probe, check=check).restart_and_probe() == (True, "")

    assert probe.urls == ["https://172.18.0.3:8080/healthz"]


def test_the_address_is_looked_up_after_the_restart_because_a_restart_may_give_another() -> None:
    units = Docker()
    probe = Probe(units)

    gate(units, probe).restart_and_probe()

    assert [event.split(" ")[0] for event in units.events] == ["restart", "address", "probe"]


def test_a_port_the_operator_put_in_the_check_is_kept_and_only_the_address_changes() -> None:
    units = Docker("172.18.0.3:8080")
    probe = Probe(units)

    gate(units, probe, check=HealthCheck(port=9000)).restart_and_probe()

    assert probe.urls == ["http://172.18.0.3:9000/"]


def test_a_manager_that_has_nothing_to_say_leaves_the_address_as_it_was() -> None:
    """A central on the machine's own network is asked nothing, and loopback is right there."""
    units = Docker(None)
    probe = Probe(units)

    gate(units, probe).restart_and_probe()

    assert probe.urls == ["http://127.0.0.1:8081/"]


def test_an_address_that_cannot_be_found_fails_the_gate_with_the_reason_and_asks_nobody() -> None:
    units = Docker(ServiceError("The container 5f3a has no address on any Docker network"))
    probe = Probe(units)

    healthy, evidence = gate(units, probe).restart_and_probe()

    assert healthy is False
    assert "no address on any Docker network" in evidence
    assert probe.urls == []


def test_the_evidence_of_a_probe_that_failed_names_the_address_it_asked() -> None:
    units = Docker("172.18.0.3:8080")
    probe = Probe(units, healthy=False)

    healthy, evidence = gate(units, probe).restart_and_probe()

    assert healthy is False
    assert "connection refused" in evidence
    assert "http://172.18.0.3:8080/" in evidence


def test_a_stand_in_that_answers_anything_is_not_taken_for_a_manager_that_says_where() -> None:
    """Tests give the gate mocks, which have every attribute: what they return is not an address."""

    class Anything(Units):
        def __getattr__(self, name: str) -> Any:
            return lambda *args, **kwargs: object()

    units = Anything()
    probe = Probe(units)

    gate(units, probe).restart_and_probe()

    assert probe.urls == ["http://127.0.0.1:8081/"]
