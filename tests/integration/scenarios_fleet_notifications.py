# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fleet's notifications over a real fleet: a central and two nodes, over real SSH tunnels (item 90, D3).

``python tests/integration/fleet_run.py --scenario fleet_notifications`` registers it (the file is
loaded by ``fleet_run.py``, which passes itself to :func:`register`). What it proves, with two real
Noust nodes and a real central, no fakes:

- a channel of the fleet that routes by **domain** is pushed to the node that serves the domain and
  to no other, and one that routes by **server** to the server it names: the other node is sent
  nothing, the credential is not in its configuration, and the central says so (``out_of_reach``,
  ``reach`` in the status);
- the address of the central's console reaches each node with the node's own name in it, and the
  link a node builds for a page of its own opens inside the central's console;
- **the central stops** (its console and its monitor): the node whose monitor runs says through its
  own channel that the central stopped calling, once, and a channel routed by domain does not hear
  it, which is what a route is for; when the central is back the node says that too;
- the **record of deliveries** of the fleet lists what the node delivered, with the node it was
  delivered by, newest first, and its counts by state are the node's whole record.

Docker: one more container than ``fleet_run.py`` starts (``<prefix>-node2``), labelled
``noust.test=1`` and removed when the scenario ends. A webhook receiver runs inside the first node,
as a transient systemd unit, on loopback.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import time
from typing import Any

#: The second node.
NODE2_ID = "node2"

#: The application the first node serves, and the pattern of the route that reaches it.
SHOP_DOMAIN = "shop.avisos.example.com"
SHOP_PATTERN = "*.avisos.example.com"

#: Where the receiver listens inside the first node, and what it is called.
SINK_PORT = 9099
SINK_UNIT = "fleet-notify-sink"
SINK_LOG = "/var/tmp/fleet-notify-sink.log"

#: The address of the central's console the fleet gives its servers.
CENTRAL_CONSOLE = "https://central.example.test"

#: How long the central may stay quiet before the first node says so, in minutes (the least a
#: central that promised a call every half minute is allowed: three calls).
SILENT_MINUTES = 1

TERMINAL_JOB_STATES = ("succeeded", "failed", "aborted", "interrupted")

#: The receiver: it keeps what it is sent, one line of JSON each, and answers 200.
SINK = f"""
import http.server
import json


class Hook(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(size).decode("utf-8", "replace")
        with open({SINK_LOG!r}, "a") as log:
            log.write(json.dumps({{"path": self.path, "body": body}}) + "\\n")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", {SINK_PORT}), Hook).serve_forever()
"""


def register(fr: Any) -> None:
    """
    Add the scenario to ``fleet_run``'s.

    Args:
        fr: The ``fleet_run`` module.
    """
    node2_container = f"{fr.PREFIX}-node2"

    @fr.fleet_scenario("fleet_notifications")
    def scenario_fleet_notifications(env: Any) -> None:
        central = env.central
        fr.ensure_enrolled(env)
        node1 = env.node
        node2 = fr.Scenario(container=node2_container)
        node1_name = env.node_name
        hook = f"http://127.0.0.1:{SINK_PORT}/hook"

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

        def channels_of(node: str) -> set[str]:
            return {c["name"] for c in proxied(node, "notifications/channels")["items"]}

        def items_of(job: dict[str, Any], node: str) -> dict[tuple[str, str], dict[str, Any]]:
            state = next(n for n in job["nodes"] if n["node"] == node)
            return {(i["resource"], i["name"]): i for i in state.get("items") or []}

        def sink_lines() -> list[dict[str, Any]]:
            text = node1.run(
                f"cat {SINK_LOG} 2>/dev/null || true", check=False, label="cat the receiver's log"
            ).stdout
            return [json.loads(line) for line in text.splitlines() if line.strip()]

        def said(code: str) -> int:
            """How many times the receiver was told ``code``, whichever channel told it."""
            return sum(1 for line in sink_lines() if json.loads(line["body"]).get("code") == code)

        def centrals_noted() -> str:
            return node1.run(
                "/opt/noust/bin/python -c 'from noust.fleet.central_calls import CentralCalls; "
                "print([(c.central, c.heartbeat_s) for c in CentralCalls().list()])'",
                check=False,
                label="what the first node noted of its central",
            ).stdout

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

            # -- both deliver, to a receiver that only the first has ---------------------------
            for sc in (node1, node2):
                sc.run("noust config set notifications.enabled true", label="notifications on")
                sc.run(
                    "noust config set notifications.allow_private_hosts 127.0.0.1 --list",
                    label="allow the loopback for webhooks",
                )
            encoded = base64.b64encode(SINK.encode()).decode()
            node1.run(
                f"echo {encoded} | base64 -d > /usr/local/bin/fleet-notify-sink.py; "
                f"systemd-run --unit={SINK_UNIT} --property=Type=simple "
                "/usr/bin/python3 /usr/local/bin/fleet-notify-sink.py",
                label="start a webhook receiver inside the first node",
            )

            # -- the first node serves an application the second does not ------------------------
            created = call(
                "POST",
                f"/api/nodes/{node1_name}/api/apps",
                {
                    "domain": SHOP_DOMAIN,
                    "source": fr.STATIC_SITE_GIT_URL,
                    "app_type": "static",
                    "ssl": False,
                },
                expect=202,
            )
            fr.wait_for_job(fr.CENTRAL_NAME, created["job_id"], env.master_token, node=node1_name)

            # -- three channels of the fleet, with three routes ------------------------------------
            settings: dict[str, dict[str, Any]] = {
                "ops-all": {"type": "webhook", "settings": {"webhook_url": hook}},
                "ops-shop": {
                    "type": "webhook",
                    "settings": {"webhook_url": hook},
                    "route": {"apps": [SHOP_PATTERN]},
                },
                "ops-node2": {
                    "type": "webhook",
                    "settings": {"webhook_url": hook},
                    "route": {"servers": [NODE2_ID]},
                },
            }
            for name, value in settings.items():
                call("PUT", f"/api/fleet/settings/channel/{name}", {"value": value, "apply": False})
            applied = call("POST", "/api/fleet/settings/apply", {})
            job = job_done(applied["job"]["job_id"])
            central.check(job["status"] == "succeeded", f"applying to the fleet failed: {job!r}")

            first, second = items_of(job, node1_name), items_of(job, NODE2_ID)
            central.check(
                {name: first[("channel", name)]["state"] for name in settings}
                == {"ops-all": "created", "ops-shop": "created", "ops-node2": "out_of_reach"},
                f"the first node was not sent what its routes reach: {first!r}",
            )
            central.check(
                {name: second[("channel", name)]["state"] for name in settings}
                == {"ops-all": "created", "ops-shop": "out_of_reach", "ops-node2": "created"},
                f"the second node was not sent what its routes reach: {second!r}",
            )
            central.check(
                second[("channel", "ops-shop")].get("why") == "domain"
                and first[("channel", "ops-node2")].get("why") == "server",
                "a channel that is not sent does not say what cannot match",
            )
            central.check(
                {"ops-all", "ops-shop"} <= channels_of(node1_name)
                and "ops-node2" not in channels_of(node1_name),
                f"the first node has the wrong channels: {sorted(channels_of(node1_name))!r}",
            )
            central.check(
                {"ops-all", "ops-node2"} <= channels_of(NODE2_ID)
                and "ops-shop" not in channels_of(NODE2_ID),
                f"the second node has the wrong channels: {sorted(channels_of(NODE2_ID))!r}",
            )
            kept = node2.run(
                "grep -l ops-shop /etc/noust/config.yaml || true",
                check=False,
                label="grep the second node's configuration for the channel it was not sent",
            ).stdout.strip()
            node2.check(
                kept == "", f"the channel of another domain is in the second node's file: {kept!r}"
            )

            status = call("GET", "/api/fleet/settings/status")
            cells = {
                c["node"]: c
                for c in next(s for s in status["settings"] if s["name"] == "ops-shop")["servers"]
            }
            central.check(
                cells[node1_name]["sync"] == "in_sync"
                and cells[NODE2_ID]["sync"] == "not_applicable"
                and cells[NODE2_ID]["reach"] == {"reaches": False, "why": "domain"},
                f"the status does not say where the channel reaches: {cells!r}",
            )

            # -- the central's address, with each server's own name in it ----------------------------
            saved = call(
                "PUT",
                "/api/fleet/settings/console_link/console",
                {"value": {"url": CENTRAL_CONSOLE}},
            )
            job_done(saved["apply"]["job_id"])
            for node in (node1_name, NODE2_ID):
                seen = proxied(node, "notifications/console-url")
                central.check(
                    seen == {"url": f"{CENTRAL_CONSOLE}/n/{node}"},
                    f"{node} was not sent its own address in the central's console: {seen!r}",
                )
            link = node1.run(
                "/opt/noust/bin/python -c 'from noust.core.config import Config; "
                "from noust.core.notifications.context import NotificationContext as C; "
                'c = Config(); c.reload(); print(C.from_config(c).link("/apps/x").url)\'',
                label="the link the first node builds for a page of its own",
            ).stdout.strip()
            node1.check(
                link == f"{CENTRAL_CONSOLE}/n/{node1_name}/apps/x",
                f"the first node's links do not open inside the central's console: {link!r}",
            )

            # -- the central stops, and the first node says so ---------------------------------------
            for sc in (central, node1):
                sc.run("noust monitor enable", label="noust monitor enable")
            node1.run(
                f"noust config set notifications.central_silent_minutes {SILENT_MINUTES}",
                label="the central may be quiet for a minute (and never less than three calls)",
            )
            fr.wait_until(
                lambda: ", 30)" in centrals_noted(),
                "the first node to note that its central keeps watch (calls every 30 seconds)",
                timeout=240,
            )
            listed = proxied(node1_name, "managed-settings")["centrals"]
            central.check(
                len(listed) == 1 and listed[0]["keeps_watch"] and listed[0]["heartbeat_s"] == 30,
                f"the first node does not list the central that keeps watch: {listed!r}",
            )
            before = said("central.silent")
            central.run(
                "systemctl stop noust-monitor noust-web",
                label="the central stops (console and monitor)",
            )
            try:
                fr.wait_until(
                    lambda: said("central.silent") > before,
                    "the first node to say that its central stopped calling",
                    timeout=420,
                )
                time.sleep(75)
                # Two channels of the first node send to the receiver and only one has no route: the
                # one routed by domain does not hear what is about the server itself, and the
                # silence is said once and not at every look.
                node1.check(
                    said("central.silent") == before + 1,
                    "the first node said the silence more than once, or a channel routed by "
                    "domain heard what is about the server itself",
                )
            finally:
                central.run(
                    "systemctl start noust-web noust-monitor",
                    label="the central starts again",
                )
            fr.wait_until(
                lambda: said("central.heard") >= 1,
                "the first node to say that its central calls again",
                timeout=300,
            )
            fr.wait_until(
                lambda: fr.api_call(
                    fr.CENTRAL_NAME,
                    "GET",
                    f"https://127.0.0.1:{fr.CENTRAL_PORT}/api/fleet/servers?refresh=true",
                    token=env.master_token,
                )[0]
                == 200,
                "the central's console to answer again",
                timeout=120,
            )

            # -- the record of deliveries of the whole fleet ---------------------------------------------
            book = call("GET", f"/api/views/deliveries?node={node1_name}")
            rows = [r for r in book["items"] if r["kind"] == "central_silent"]
            central.check(
                {r["code"] for r in rows} >= {"central.silent", "central.heard"}
                and all(r["node"] == node1_name and r["state"] == "sent" for r in rows),
                f"the fleet's record does not list what the node delivered: {rows!r}",
            )
            central.check(
                [r["created_at"] for r in book["items"]]
                == sorted((r["created_at"] for r in book["items"]), reverse=True),
                "the record is not newest first",
            )
            central.check(
                book["counts"][node1_name].get("sent", 0) >= len(rows),
                f"the counts are not the node's whole record: {book['counts']!r}",
            )
            failed = call("GET", f"/api/views/deliveries?node={node1_name}&state=failed")
            central.check(
                not [r for r in failed["items"] if r["kind"] == "central_silent"],
                "a filter by state returned another state",
            )
        finally:
            for sc in (central, node1):
                sc.run("noust monitor disable || true", check=False, label="noust monitor disable")
            central.run("systemctl start noust-web || true", check=False, label="the central is up")
            node1.run(
                "noust config set notifications.central_silent_minutes 15; "
                f"systemctl stop {SINK_UNIT} || true",
                check=False,
                label="the first node as it was",
            )
            central.run(
                f"noust node remove {NODE2_ID} -f --no-revoke >/dev/null 2>&1; true",
                check=False,
                label=f"noust node remove {NODE2_ID}",
            )
            for kind, name in (
                ("channel", "ops-all"),
                ("channel", "ops-shop"),
                ("channel", "ops-node2"),
                ("console_link", "console"),
            ):
                fr.api_call(
                    fr.CENTRAL_NAME,
                    "DELETE",
                    f"https://127.0.0.1:{fr.CENTRAL_PORT}/api/fleet/settings/{kind}/{name}?apply=false",
                    token=env.master_token,
                )
            for name in ("ops-all", "ops-shop", "ops-node2"):
                fr.api_call(
                    fr.CENTRAL_NAME,
                    "DELETE",
                    f"https://127.0.0.1:{fr.CENTRAL_PORT}/api/nodes/{env.node_name}/api/notifications/channels/{name}",
                    token=env.master_token,
                )
            fr.api_call(
                fr.CENTRAL_NAME,
                "DELETE",
                f"https://127.0.0.1:{fr.CENTRAL_PORT}/api/nodes/{env.node_name}/api/apps/{SHOP_DOMAIN}",
                token=env.master_token,
            )
            node1.run(
                "noust config set notifications.console_url '' || true",
                check=False,
                label="the first node's links as they were",
            )
            fr.remove_container(node2_container)
