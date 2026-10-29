# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fleet's settings over a real fleet: a central and two nodes, over real SSH tunnels (item 90).

``python tests/integration/fleet_run.py --scenario fleet_settings`` registers it (the file is
loaded by ``fleet_run.py``, which passes itself to :func:`register`). What it proves, with two real
Noust nodes and a real central, no fakes:

- a setting saved on the central reaches **both** nodes by their own typed routes, signed with the
  person who approved the change and the change, and each node records it as the fleet's
  (``/api/managed-settings``), with its audit trail naming the person and the change;
- a secret (a registry login's password) is kept sealed on the central and shown as ``***``
  everywhere, and the node still holds it;
- one node's operator changes a managed setting **by the command line** and the node says
  ``overridden``; the central shows it as that node's own exception, and a new value of the fleet
  reaches the other node and leaves this one alone;
- "back to the fleet's" puts it back, and the node says ``managed`` again;
- a node whose console is down is **reconciled when it comes back**, by itself, from the note the
  central leaves when it sees it answer again;
- a node made independent is let go of what it had and keeps it;
- a request signed as a push is admitted on the fleet's settings and on nothing else, and only while
  the node's ceiling is ``admin``: a deploy signed as a push is refused, and so is a setting once
  the ceiling is lowered; the confirmation alone, without the signature, confirms nothing.

Docker: one more container than ``fleet_run.py`` starts (``<prefix>-node2``), labelled
``noust.test=1`` and removed when the scenario ends.
"""

from __future__ import annotations

import dataclasses
import json
import time
from typing import Any

#: The second node.
NODE2_ID = "node2"

#: A change id and a person for the pushes this scenario signs by hand.
HAND_SIGNATURE = "fleet-sync on behalf of harness (change c0123456789ab)"
HAND_CONFIRMATION = "change:c0123456789ab"

TERMINAL_JOB_STATES = ("succeeded", "failed", "aborted", "interrupted")


def register(fr: Any) -> None:
    """
    Add the scenario to ``fleet_run``'s.

    Args:
        fr: The ``fleet_run`` module.
    """
    node2_container = f"{fr.PREFIX}-node2"

    @fr.fleet_scenario("fleet_settings")
    def scenario_fleet_settings(env: Any) -> None:
        central = env.central
        fr.ensure_enrolled(env)
        node1 = env.node
        node2 = fr.Scenario(container=node2_container)

        def call(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
            status, parsed, raw = fr.api_call(
                fr.CENTRAL_NAME,
                method,
                f"https://127.0.0.1:{fr.CENTRAL_PORT}{path}",
                token=env.master_token,
                json_body=body,
            )
            fr.log_api(central, method, path, status, raw[:400])
            central.check(
                status == expect, f"{method} {path}: expected {expect}, got {status}: {raw[:600]!r}"
            )
            return parsed

        def job_done(job_id: str, timeout: float = 150.0) -> dict[str, Any]:
            deadline = time.monotonic() + timeout
            last: dict[str, Any] = {}
            while time.monotonic() < deadline:
                last = call("GET", f"/api/fleet/jobs/{job_id}")
                if last.get("status") in TERMINAL_JOB_STATES:
                    return last
                time.sleep(2)
            raise AssertionError(f"fleet job {job_id} did not finish: {last!r}")

        def proxied(node: str, path: str) -> Any:
            return call("GET", f"/api/nodes/{node}/api/{path}")

        def managed(node: str) -> dict[tuple[str, str], dict[str, Any]]:
            items = proxied(node, "managed-settings").get("items", [])
            return {(i["resource"], i["name"]): i for i in items}

        def variable(node: str, name: str) -> str | None:
            found = [v for v in proxied(node, "variables/server")["variables"] if v["name"] == name]
            return str(found[0]["value"]) if found else None

        def items_of(job: dict[str, Any], node: str) -> dict[tuple[str, str], str]:
            state = next(n for n in job["nodes"] if n["node"] == node)
            return {(i["resource"], i["name"]): i["state"] for i in state.get("items") or []}

        second_env = dataclasses.replace(
            env,
            node=node2,
            node_name=NODE2_ID,
            node_ssh_target=f"{fr.TUNNEL_USER}@{node2_container}",
            node_console_port=0,
        )
        try:
            # -- a second node, enrolled the real way -------------------------------------------
            fr.remove_container(node2_container)
            fr.start_fleet_container(node2_container, fr.FLEET_IMAGE_TAG, fr.NETWORK_NAME)
            fr.wait_for_systemd(node2_container)
            fr.install_noust(node2_container, env.wheel)
            fr.provision_ssh(node2_container)
            fr.enroll_node(second_env)
            node1_port = env.node_console_port

            # -- the fleet wants three settings ------------------------------------------------
            call(
                "PUT",
                "/api/fleet/settings/variable/REGION",
                {"value": {"value": "eu"}, "apply": False},
            )
            call(
                "PUT",
                "/api/fleet/settings/alert_rule/disk-high",
                {"value": {"metric": "cpu", "threshold": 90}, "apply": False},
            )
            saved = call(
                "PUT",
                "/api/fleet/settings/registry/mirror",
                {
                    "value": {
                        "kind": "custom",
                        "server": "registry.example.com",
                        "username": "bot",
                        "password": "s3cret-registry-password",
                    },
                    "apply": False,
                },
            )
            central.check(
                "s3cret" not in json.dumps(saved)
                and saved["setting"]["value"]["password"] == "***",
                f"a secret came back from the central: {saved!r}",
            )
            applied = call("POST", "/api/fleet/settings/apply", {})
            job = job_done(applied["job"]["job_id"])
            central.check(job["status"] == "succeeded", f"applying to the fleet failed: {job!r}")
            for node in (env.node_name, NODE2_ID):
                central.check(
                    items_of(job, node)
                    == {
                        ("alert_rule", "disk-high"): "created",
                        ("registry", "mirror"): "created",
                        ("variable", "REGION"): "created",
                    },
                    f"{node}: unexpected results {items_of(job, node)!r}",
                )
                rows = managed(node)
                central.check(
                    set(rows)
                    == {("alert_rule", "disk-high"), ("registry", "mirror"), ("variable", "REGION")}
                    and all(r["state"] == "managed" for r in rows.values()),
                    f"{node} does not manage the three settings: {rows!r}",
                )
                central.check(variable(node, "REGION") == "eu", f"{node}: REGION is not eu")
            there = node2.run(
                "noust registry list --json",
                label="noust registry list --json (on the second node)",
            )
            node2.check(
                "mirror" in there.stdout, "the registry login did not reach the second node"
            )
            node2.check("s3cret" not in there.stdout, "a node printed the registry password")

            # -- the node's audit trail names the person and the change ------------------------
            audit = node1.run(
                "F=$(find /etc/noust /var/lib/noust -name web-audit.log 2>/dev/null | head -1); "
                'test -n "$F" && tail -c 12000 "$F" || echo NO_AUDIT_LOG',
                label="tail the first node's audit log",
            ).stdout
            node1.check(
                "(change c" in audit and "on behalf of" in audit,
                f"the node's audit log does not name the person and the change: {audit[-600:]!r}",
            )

            # -- a node's operator changes a managed setting by the command line ----------------
            node1.run(
                "noust variables set server REGION us", label="noust variables set server REGION us"
            )
            rows = managed(env.node_name)
            central.check(
                rows[("variable", "REGION")]["state"] == "overridden",
                f"the node did not notice its operator's change: {rows!r}",
            )
            status = call("GET", "/api/fleet/settings/status")
            cells = next(s for s in status["settings"] if s["name"] == "REGION")["servers"]
            by_node = {c["node"]: c for c in cells}
            central.check(
                (by_node[env.node_name]["origin"], by_node[env.node_name]["sync"])
                == ("local_exception", "diverged"),
                f"the central does not show the node's own exception: {by_node[env.node_name]!r}",
            )
            central.check(
                (by_node[NODE2_ID]["origin"], by_node[NODE2_ID]["sync"]) == ("fleet", "in_sync"),
                f"the other node is not in sync: {by_node[NODE2_ID]!r}",
            )

            # -- a new value of the fleet reaches the other node and leaves this one alone ----------
            newer = call("PUT", "/api/fleet/settings/variable/REGION", {"value": {"value": "ap"}})
            job = job_done(newer["apply"]["job_id"])
            central.check(variable(NODE2_ID, "REGION") == "ap", "the new value did not reach node2")
            central.check(
                variable(env.node_name, "REGION") == "us",
                "the central overwrote what the node's operator changed",
            )
            central.check(
                items_of(job, env.node_name).get(("variable", "REGION")) == "overridden",
                f"the job did not say the setting was left: {items_of(job, env.node_name)!r}",
            )

            # -- back to the fleet's -----------------------------------------------------------
            back = call(
                "POST", f"/api/fleet/servers/{env.node_name}/settings/variable/REGION/restore"
            )
            job_done(back["job_id"])
            central.check(
                variable(env.node_name, "REGION") == "ap", "the node did not go back to the fleet's"
            )
            central.check(
                managed(env.node_name)[("variable", "REGION")]["state"] == "managed",
                "the node does not say it is managed again",
            )

            # -- a node that is down is reconciled when it comes back -----------------------------
            node2.run(
                "systemctl stop noust-web",
                label="systemctl stop noust-web (the second node is down)",
            )
            for _ in range(2):
                call("GET", "/api/fleet/servers?refresh=true")
                time.sleep(2)
            call(
                "PUT",
                "/api/fleet/settings/variable/REGION",
                {"value": {"value": "sa"}},
            )
            time.sleep(5)
            node2.run("systemctl start noust-web", label="systemctl start noust-web (it is back)")
            fr.wait_until(
                lambda: fr.api_call(
                    fr.CENTRAL_NAME,
                    "GET",
                    f"https://127.0.0.1:{fr.CENTRAL_PORT}/api/fleet/servers?refresh=true",
                    token=env.master_token,
                )[0]
                == 200
                and variable_or_none(call, NODE2_ID) is not None,
                "the second node to answer again",
                timeout=120,
            )
            fr.wait_until(
                lambda: variable_or_none(call, NODE2_ID) == "sa",
                "the second node to be reconciled by itself after it came back",
                timeout=150,
            )

            # -- a node made independent keeps what it had and is pushed nothing -----------------
            alone = call(
                "PUT", f"/api/fleet/servers/{NODE2_ID}/settings-mode", {"mode": "independent"}
            )
            job_done(alone["apply"]["job_id"])
            central.check(managed(NODE2_ID) == {}, "an independent node still manages settings")
            central.check(
                variable(NODE2_ID, "REGION") == "sa", "an independent node lost what it had"
            )
            call("PUT", "/api/fleet/settings/variable/REGION", {"value": {"value": "na"}})
            time.sleep(8)
            central.check(
                variable(NODE2_ID, "REGION") == "sa", "an independent node was pushed a value"
            )

            # -- what a node admits as a push, and nothing else ----------------------------------
            token = fr.docker_exec(
                fr.CENTRAL_NAME,
                f"cat {env.secret_path('fleet', 'nodes', env.node_name, 'token')}",
                timeout=15,
            ).stdout.strip()
            fr.redact(central, f"cat the fleet token stored for {env.node_name}", "the fleet token")
            base = f"http://127.0.0.1:{node1_port}"
            signed = {"X-Noust-Actor": HAND_SIGNATURE, "X-Noust-Elevated": HAND_CONFIRMATION}

            def on_node(
                method: str, path: str, headers: dict[str, str], body: Any = None
            ) -> tuple[int, Any]:
                status, parsed, raw = fr.api_call(
                    fr.NODE_NAME,
                    method,
                    f"{base}{path}",
                    token=token,
                    headers=headers,
                    json_body=body,
                    max_time=15,
                )
                fr.log_api(node1, method, f"{path} ({sorted(headers)})", status, raw[:300])
                return status, parsed

            status, _ = on_node("PUT", "/api/variables/server/BYHAND", signed, {"value": "x"})
            node1.check(status == 200, f"a signed push of a setting was refused: {status}")
            status, body = on_node("POST", "/api/apps/shop.example.com/restart", signed, {})
            node1.check(
                status == 403 and (body or {}).get("error") == "fleet_sync_refused",
                f"a deploy signed as a push was not refused: {status} {body!r}",
            )
            status, body = on_node(
                "PUT",
                "/api/variables/server/BYHAND",
                {
                    "X-Noust-Actor": "harness",
                    "X-Noust-Actor-Role": "admin",
                    "X-Noust-Elevated": HAND_CONFIRMATION,
                },
                {"value": "x"},
            )
            node1.check(
                status == 403 and (body or {}).get("error") == "elevation_required",
                f"the confirmation of a change counted without the signature: {status} {body!r}",
            )
            node1.run(
                "noust fleet access --level deploy", label="noust fleet access --level deploy"
            )
            try:
                status, body = on_node(
                    "PUT", "/api/variables/server/BYHAND", signed, {"value": "y"}
                )
                node1.check(
                    status == 403 and (body or {}).get("error") == "fleet_sync_ceiling",
                    f"a node held to deploy took a setting: {status} {body!r}",
                )
            finally:
                node1.run(
                    "noust fleet access --level admin", label="noust fleet access --level admin"
                )
        finally:
            central.run(
                f"noust node remove {NODE2_ID} -f --no-revoke >/dev/null 2>&1; true",
                check=False,
                label=f"noust node remove {NODE2_ID}",
            )
            # What the scenario made on the first node stays no longer than the scenario.
            for path in (
                "/api/fleet/settings/variable/REGION",
                "/api/fleet/settings/alert_rule/disk-high",
                "/api/fleet/settings/registry/mirror",
            ):
                fr.api_call(
                    fr.CENTRAL_NAME,
                    "DELETE",
                    f"https://127.0.0.1:{fr.CENTRAL_PORT}{path}?apply=false",
                    token=env.master_token,
                )
            for path in (
                "variables/server/REGION",
                "variables/server/BYHAND",
                "alerts/rules/disk-high",
                "registries/mirror",
            ):
                fr.api_call(
                    fr.CENTRAL_NAME,
                    "DELETE",
                    f"https://127.0.0.1:{fr.CENTRAL_PORT}/api/nodes/{env.node_name}/api/{path}",
                    token=env.master_token,
                )
            fr.remove_container(node2_container)


def variable_or_none(call: Any, node: str) -> str | None:
    """
    Read one of a node's shared variables through the central, or None while it does not answer.

    Args:
        call: The scenario's call to the central.
        node: The node.

    Returns:
        The value of ``REGION``; None when the node is not answering or has none.
    """
    try:
        found = call("GET", f"/api/nodes/{node}/api/variables/server")
    except AssertionError:
        return None
    values = [v["value"] for v in found.get("variables", []) if v["name"] == "REGION"]
    return str(values[0]) if values else None
