# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Console 2.2, zero downtime: the blue/green history and the probes it is answered with."""

from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .app_tabs import _tabs_register, _tabs_release_id
from .machine import Unit
from .sandbox import Sandbox

# --- 2.2: zero downtime ------------------------------------------------------


def seed_zero_downtime(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
) -> None:
    """
    Seed an application in blue/green mode, turned on by the real engine.

    ``pagos.example.org`` is deployed on releases like the tabs' release app,
    then :func:`noust.deployers.bluegreen.set_zero_downtime` turns the mode on
    exactly as ``noust app zero-downtime`` would: the template written, green
    started and probed, the upstream and the site switched, the old unit
    retired. So what the console reads is what the engine leaves, and turning
    it off and on again from the console runs the same code on the same model.

    Its unit, instances and ports live in the runner's own model rather than
    the maps passed in (see :func:`_zd_model`), because the instances only
    come to exist as the runner starts them. Nothing listens on the instances'
    ports: the health probe and the port check the engine uses answer from
    the model, as :func:`_tabs_port_model` does for an application's state.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units (unused: see above).
        ports: Each unit's port (unused).
        domains: Each unit's domain (unused).
    """
    from noust.core.store import App, ReleaseRecord
    from noust.core.utils import domain_to_app_name
    from noust.deployers.bluegreen import BlueGreen, set_zero_downtime
    from tests.support.panel_factory import seed_zero_downtime_history

    model_units, model_ports, model_domains = _zd_model()
    _zd_probe_model(model_units, model_ports)

    domain = ZD_APP
    root = sandbox.apps_dir / domain_to_app_name(domain)
    port = _zd_free_port_pair()
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / ".env").write_text(
        "NODE_ENV=production\n"
        "DATABASE_URL=postgres://pagos:Q7m2vK9xLp@127.0.0.1:5432/pagos\n"
        "STRIPE_SECRET_KEY=sk_l" + "ive_51Hx8examplePaymentsSeeded\n"
        "NEXT_PUBLIC_SITE_URL=https://pagos.example.org\n",
        encoding="utf-8",
    )
    (shared / ".env").chmod(0o600)
    (root / "repo").mkdir(parents=True, exist_ok=True)

    app = App(
        domain=domain,
        app_type="nextjs",
        source="https://github.com/example-org/pagos.git",
        branch="main",
        port=port,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
        layout="releases",
        keep_releases=5,
    )
    _tabs_register(
        sandbox,
        store,
        model_units,
        model_ports,
        model_domains,
        app,
        working_directory=root / "current",
    )
    stored = store.get_app(domain)
    if stored is None or stored.id is None:
        raise RuntimeError(f"{domain} was not recorded")

    now = datetime.now()
    serving = ""
    for age_hours, commit, message in _ZD_RELEASES:
        started = now - timedelta(hours=age_hours)
        release_id = _tabs_release_id(started, commit)
        release_dir = root / "releases" / release_id
        (release_dir / ".next").mkdir(parents=True, exist_ok=True)
        (release_dir / "package.json").write_text(
            json.dumps({"name": "pagos", "version": "1.8.0"}) + "\n", encoding="utf-8"
        )
        (release_dir / ".next" / "BUILD_ID").write_text(commit + "\n", encoding="utf-8")
        (release_dir / ".env").symlink_to("../../shared/.env")
        store.record_release(
            ReleaseRecord(
                id=release_id,
                app_id=stored.id,
                git_commit=commit,
                created_at=started.astimezone(timezone.utc).isoformat(),
                activated_at=(started + timedelta(minutes=1)).astimezone(timezone.utc).isoformat(),
                status="superseded",
                path=str(release_dir),
            )
        )
        deployment = store.record_deployment_start(
            domain, "webhook", git_commit=commit, git_branch="main"
        )
        store.annotate_deployment(deployment, commit_message=message, release_id=release_id)
        store.finish_deployment(deployment, "success")
        serving = release_id
    (root / "current").symlink_to(Path("releases") / serving)
    store.mark_release_active(stored.id, serving)

    # The engine drains for real on the console's own switches; here nothing is
    # serving yet, so there is nothing to wait for.
    set_zero_downtime(
        domain,
        True,
        drain_seconds=ZD_DRAIN_SECONDS,
        engine=lambda row, out: BlueGreen(row, logger=out, sleep=lambda _seconds: None),
    )
    seed_zero_downtime_history(store, domain)


#: In blue/green mode: green serves, blue is stopped, the upstream names green.
ZD_APP = "pagos.example.org"

#: Seconds the old instance keeps running after a switch: short, so a test that
#: turns the mode off and on again does not wait long for either.
ZD_DRAIN_SECONDS = 2

#: The releases of the blue/green application: hours ago, commit, subject.
_ZD_RELEASES = (
    (50.0, "4be21c7", "Accept SEPA direct debits"),
    (26.0, "8c03f5a", "Retry webhooks from the payment provider"),
    (2.5, "d7a91e4", "Show the refund status on receipts"),
)


def _zd_model() -> tuple[dict[str, Unit], dict[str, int], dict[str, str]]:
    """
    The runner's own model: its units, their ports and their domains.

    Seeding fills maps of its own that :func:`serve` merges into the runner's
    afterwards; a unit the runner creates while seeding (a blue/green
    instance it is asked to start) lands in the runner's maps directly, so
    the application that has instances is registered there too.

    Returns:
        The maps the runner answers from.
    """
    from noust.core.runner import get_runner

    runner: Any = get_runner()
    return runner.model_units, runner.model_ports, runner.model_domains


def _zd_free_port_pair() -> int:
    """
    Find a port whose next one is free too: blue listens on it, green on the one after.

    The engine checks that green's port is free on this machine before it
    starts green, and the seeded applications' ports are picked around it.

    Returns:
        A port P with P and P + 1 both free on loopback.
    """
    for _attempt in range(64):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            candidate = int(probe.getsockname()[1])
        if candidate >= 65535:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as first:
                first.bind(("127.0.0.1", candidate))
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as second:
                    second.bind(("127.0.0.1", candidate + 1))
        except OSError:
            continue
        return candidate
    raise RuntimeError("no two consecutive free ports on loopback")


def _zd_probe_model(units: dict[str, Unit], ports: dict[str, int]) -> None:
    """
    Answer the engine's health probe and port check from the modelled units.

    A modelled unit that is active answers on its port, and holds it; any
    other port is asked of the machine for real, so a probe of something
    that genuinely listens (the tabs' health servers) still reaches it. The
    application's state reads its port through
    :func:`noust.core.app_state.port_answers`, answered the same way.

    Args:
        units: The runner's units.
        ports: Their ports.
    """
    from urllib.parse import urlsplit

    import noust.core.app_state as app_state_module
    import noust.deployers.bluegreen as bluegreen_module

    def held(port: int | None) -> bool:
        return port is not None and any(
            unit.active == "active" and ports.get(name) == port for name, unit in units.items()
        )

    real_probe = bluegreen_module.wait_until_healthy
    real_free = bluegreen_module.is_port_available
    real_answers = app_state_module.port_answers

    def probe(url: str, **kwargs: Any) -> bool:
        return held(urlsplit(url).port) or real_probe(url, **kwargs)

    def port_free(port: int) -> bool:
        return not held(port) and real_free(port)

    def port_answers(port: int, *args: Any, **kwargs: Any) -> bool:
        return held(port) or real_answers(port, *args, **kwargs)

    bluegreen_module.wait_until_healthy = probe  # type: ignore[assignment]
    bluegreen_module.is_port_available = port_free  # type: ignore[assignment]
    app_state_module.port_answers = port_answers  # type: ignore[assignment]
