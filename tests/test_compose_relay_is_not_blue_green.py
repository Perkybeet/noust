"""
A Compose stack in zero-downtime mode is updated through relays, not as blue/green units.

Found in production (proggest.es, 2026-10-03, owner item 65): turning the relay on for a Compose
stack set ``zero_downtime``, which the unit-based code read as "two instances <name>@blue and
<name>@green": the console said Stopped ("there is no systemd unit for it"), and starting or
restarting the application would have targeted a unit that does not exist. And item 66: the
Compose unit pulled newer images at every start, so a boot recreated the database with whatever
``postgres:16-alpine`` had become.
"""

from __future__ import annotations

from pathlib import Path

from noust.core.store import App, runs_as_instances
from tests.test_service_manager import manager, store, unit_dirs  # noqa: F401


def _stack(**overrides: object) -> App:
    values: dict[str, object] = {
        "domain": "proggest.es",
        "app_type": "docker-compose",
        "port": 3001,
        "app_path": "/opt/proggest",
        "zero_downtime": True,
    }
    values.update(overrides)
    return App(**values)  # type: ignore[arg-type]


def test_only_unit_based_applications_run_as_instances() -> None:
    assert runs_as_instances(_stack()) is False
    assert runs_as_instances(_stack(app_type="nodejs")) is True
    assert runs_as_instances(_stack(app_type="nodejs", zero_downtime=False)) is False


def test_a_relayed_stack_is_served_by_its_own_unit(manager) -> None:  # noqa: F811
    assert manager.serving_units(_stack()) == ["proggest-es"]
    assert manager.app_units(_stack()) == ["proggest-es"]
    # A unit-based application in the same mode still runs as its two instances.
    assert manager.app_units(_stack(app_type="nodejs", domain="shop.example.com"))[0].startswith(
        "shop-example-com@"
    )


def test_a_relayed_stack_serves_on_its_own_port() -> None:
    from noust.deployers.bluegreen import serving_port

    assert serving_port(_stack(active_color="green")) == 3001


def test_the_compose_unit_never_pulls_at_start() -> None:
    template = (
        Path(__file__).resolve().parents[1]
        / "src/noust/templates/systemd/docker-compose.service.j2"
    )

    assert " pull" not in template.read_text()
