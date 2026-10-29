# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A central gives the server it manages the GitHub access to deploy a private repository (item 90, D6).

``python tests/integration/fleet_run.py --scenario fleet_sources`` registers it (the file is loaded
by ``fleet_run.py``, which passes itself to :func:`register`). A real central and a real node over a
real SSH tunnel, and a GitHub that behaves as GitHub documents (``tests/support/github_api_fake.py``)
served over TLS from the central's container as a GitHub Enterprise Server (``ghe.test``), with a real
repository behind ``git http-backend`` that serves it **only** to a token that reaches it. Nothing of
Noust is faked. What it proves:

- the node holds no GitHub App and no deploy key, and still creates an application from the private
  repository, because the central leased it a token: scoped to that repository and to reading its
  contents (the fake records what each token reaches), and without which the repository answers 401;
- the lease is the node's secret (sealed on the node; not in its audit trail, in no argument and in
  nothing the central answers), and is renewed while the node serves the repository;
- a signed push delivered to the **central's** ``/hooks/github`` is handed to the node, which queues
  the update as it does for its own webhook and deploys the new commit; its audit trail names the
  delivery; and the central writes the commit's deployment on GitHub as it goes (queued or in
  progress, then success) with its own credentials, which no node ever holds;
- the central stops: the lease ends on the node's own clock and an update that starts on the node
  fails saying exactly that (whose access it was, when it ended, that the central has not called),
  not "the repository is private"; and when the central is back the access is leased again within a
  minute and the update works.

Docker: nothing beyond the central and the node ``fleet_run.py`` starts. The fake runs in the
central as a systemd unit (``ghe-fake``) on port 443; ``ghe.test`` is a line of ``/etc/hosts`` in
both containers and its certificate is trusted by both.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import time
from pathlib import Path
from typing import Any

HOST = "ghe.test"
PORT = 443
AUTHORITY = HOST
REPOSITORY = "you/app"
SOURCE = f"https://{AUTHORITY}/{REPOSITORY}.git"
DOMAIN = "private.fuentes.example.com"
INSTALLATION = 77

#: How long the fake's tokens live. Short on purpose: the scenario waits for one to end.
TOKEN_LIFETIME = 150

#: The webhook secret of the central's App; a harness constant, never a real one.
WEBHOOK_SECRET = "fuentes-it-webhook-secret"

TERMINAL_JOB = ("completed", "failed", "cancelled")

FAKE = Path(__file__).resolve().parents[1] / "support" / "github_api_fake.py"

#: Makes the central's App: its row, its installation, its key and its webhook secret.
MAKE_APP = f"""
from pathlib import Path
from noust.core.secrets import SecretStore
from noust.core.store import GitHubAppRecord, GitHubInstallationRecord, get_store

store = get_store()
store.save_github_app(
    GitHubAppRecord(app_id=4242, slug="noust-fuentes", name="noust-fuentes", owner="you")
)
store.save_github_installation(
    GitHubInstallationRecord(
        installation_id={INSTALLATION},
        account="you",
        account_type="User",
        repository_selection="selected",
    )
)
secrets = SecretStore()
secrets.write("github/private-key.pem", Path("/opt/ghe/app.pem").read_text())
secrets.write("github/webhook-secret", {WEBHOOK_SECRET!r})
print("app made")
"""


def register(fr: Any) -> None:
    """
    Add the scenario to ``fleet_run``'s.

    Args:
        fr: The ``fleet_run`` module.
    """

    @fr.fleet_scenario("fleet_sources")
    def scenario_fleet_sources(env: Any) -> None:
        central, node = env.central, env.node
        fr.ensure_enrolled(env)
        name = env.node_name

        def call(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
            status, parsed, raw = fr.api_call(
                fr.CENTRAL_NAME,
                method,
                f"https://127.0.0.1:{fr.CENTRAL_PORT}{path}",
                token=env.master_token,
                json_body=body,
            )
            fr.log_api(central, method, path, status, raw[:500])
            central.check(
                status == expect, f"{method} {path}: expected {expect}, got {status}: {raw[:700]!r}"
            )
            return parsed

        def github() -> dict[str, Any]:
            """What the fake GitHub has recorded."""
            status, parsed, raw = fr.api_call(
                fr.CENTRAL_NAME, "GET", f"https://{AUTHORITY}/__fake/state"
            )
            central.check(status == 200, f"the fake GitHub does not answer: {status} {raw[:300]!r}")
            return dict(parsed)

        def sources() -> dict[str, Any]:
            return dict(call("GET", "/api/fleet/sources"))

        def leases_on_node() -> list[dict[str, Any]]:
            return list(call("GET", f"/api/nodes/{name}/api/sources/leases")["items"])

        def wait_for_job(job_id: str, timeout: int = 240) -> dict[str, Any]:
            return dict(
                fr.wait_for_job(
                    fr.CENTRAL_NAME, job_id, env.master_token, node=name, timeout=timeout
                )
            )

        def page() -> str:
            return str(
                node.run(
                    f"curl -sS -H 'Host: {DOMAIN}' http://127.0.0.1/",
                    timeout=20,
                    check=False,
                    label=f"curl -H 'Host: {DOMAIN}' http://127.0.0.1/  (on the node itself)",
                ).stdout
            )

        # -- a GitHub of the kind an enterprise runs, with a private repository ----------------------
        central.run(
            "mkdir -p /opt/ghe /srv/ghe/you && cd /opt/ghe && "
            f"openssl req -x509 -newkey rsa:2048 -nodes -keyout ghe.key -out ghe.crt -days 2 "
            f"-subj /CN={HOST} -addext subjectAltName=DNS:{HOST} && "
            "openssl genrsa -out app.pem 2048 && chmod 600 ghe.key app.pem",
            timeout=120,
            label="a certificate for ghe.test and the App's key (on the central)",
        )
        with tempfile.TemporaryDirectory() as work:
            certificate = Path(work) / "ghe.crt"
            fr.sh(["docker", "cp", f"{fr.CENTRAL_NAME}:/opt/ghe/ghe.crt", str(certificate)])
            fr.sh(
                [
                    "docker",
                    "cp",
                    str(certificate),
                    f"{fr.NODE_NAME}:/usr/local/share/ca-certificates/ghe-test.crt",
                ]
            )
            fr.sh(
                [
                    "docker",
                    "cp",
                    str(certificate),
                    f"{fr.CENTRAL_NAME}:/usr/local/share/ca-certificates/ghe-test.crt",
                ]
            )
        fr.sh(["docker", "cp", str(FAKE), f"{fr.CENTRAL_NAME}:/opt/ghe/github_api_fake.py"])
        central_address = str(
            fr.sh(
                [
                    "docker",
                    "inspect",
                    "-f",
                    f'{{{{(index .NetworkSettings.Networks "{fr.NETWORK_NAME}").IPAddress}}}}',
                    fr.CENTRAL_NAME,
                ]
            ).stdout
        ).strip()
        central.check(bool(central_address), "the central has no address on the harness network")
        central.run(
            f"echo '127.0.0.1 {HOST}' >> /etc/hosts && update-ca-certificates",
            label="ghe.test is this machine, and its certificate is trusted (central)",
        )
        node.run(
            f"echo '{central_address} {HOST}' >> /etc/hosts && update-ca-certificates",
            label="ghe.test is the central, and its certificate is trusted (node)",
        )
        central.run(
            "rm -rf /tmp/work && mkdir -p /tmp/work && cd /tmp/work && git init -q -b main && "
            "git config user.email it@example.com && git config user.name It && "
            "echo '<h1>Leased v1</h1>' > index.html && git add -A && git commit -q -m v1 && "
            f"git init -q --bare -b main /srv/ghe/{REPOSITORY}.git && "
            f"git push -q /srv/ghe/{REPOSITORY}.git main && "
            f"git -C /srv/ghe/{REPOSITORY}.git config http.uploadpack true",
            timeout=60,
            label="a private repository behind git's smart HTTP",
        )
        central.run(
            "systemd-run --unit=ghe-fake --property=Type=simple "
            f"/opt/noust/bin/python /opt/ghe/github_api_fake.py --port {PORT} "
            "--cert /opt/ghe/ghe.crt --key /opt/ghe/ghe.key --prefix /api/v3 --git-root /srv/ghe "
            f"--token-lifetime {TOKEN_LIFETIME} --installation you:{INSTALLATION}:app",
            label="GitHub, as an Enterprise Server (ghe.test)",
        )
        fr.wait_until(
            lambda: fr.api_call(fr.CENTRAL_NAME, "GET", f"https://{AUTHORITY}/__fake/state")[0]
            == 200,
            "the fake GitHub to answer",
            timeout=60,
        )

        # -- nothing on the node can read the repository by itself --------------------------------------
        refused = node.run(
            f"GIT_TERMINAL_PROMPT=0 git ls-remote {SOURCE}",
            check=False,
            label="git ls-remote of the private repository, on the node, with no credential",
        )
        node.check(
            refused.returncode != 0,
            "the private repository answered to a node that holds nothing",
        )

        # -- the central has the App, and says whether GitHub can reach it ---------------------------------
        fr.docker_exec_stdin(fr.CENTRAL_NAME, "/opt/noust/bin/python -", MAKE_APP)
        central.run(
            f"noust config set github.host {AUTHORITY} && "
            f"noust config set notifications.allow_private_hosts {HOST} --list",
            label="the central's GitHub is ghe.test",
        )
        central.run("systemctl restart noust-web", label="the central's console reads it")
        fr.wait_until(
            lambda: fr.api_call(
                fr.CENTRAL_NAME,
                "GET",
                f"https://127.0.0.1:{fr.CENTRAL_PORT}/api/fleet/sources",
                token=env.master_token,
            )[0]
            == 200,
            "the central's console to answer",
            timeout=120,
        )
        status = sources()
        central.check(status["github"]["configured"] is True, f"no App on the central: {status!r}")
        central.check(
            [w["coded"]["code"] for w in status["github"]["warnings"]]
            == ["sources.webhook_unreachable"],
            f"the central did not say GitHub cannot reach it: {status['github']!r}",
        )

        # -- the first application: the repository is asked for before the application exists -----------------
        leased = call(
            "POST",
            "/api/fleet/sources/leases",
            {"nodes": [name], "repositories": [REPOSITORY]},
        )
        mine = {n["node"]: n for n in leased["nodes"]}[name]
        central.check(mine["state"] == "leased", f"the node was not leased: {mine!r}")
        held = leases_on_node()
        central.check(
            len(held) == 1 and held[0]["repositories"] == [REPOSITORY],
            f"the node does not hold the lease it should: {held!r}",
        )
        central.check(
            held[0]["host"] == AUTHORITY and 0 < held[0]["expires_in"] <= TOKEN_LIFETIME,
            f"the lease is not the one asked of GitHub: {held[0]!r}",
        )

        created = call(
            "POST",
            f"/api/nodes/{name}/api/apps",
            {"domain": DOMAIN, "source": SOURCE, "app_type": "static", "ssl": False},
            expect=202,
        )
        job = wait_for_job(created["job_id"])
        central.check(
            job.get("status") == "completed",
            f"the node did not create the application from the private repository: {job!r}",
        )
        node.check(
            "Leased v1" in page(),
            f"the node does not serve the private repository's page: {page()!r}",
        )

        # -- what the lease is, and is not ----------------------------------------------------------------------
        seen = github()
        scoped = [t for t in seen["tokens"] if t["repositories"] == ["app"]]
        central.check(
            scoped and all(t["permissions"].get("contents") == "read" for t in scoped),
            f"no token of GitHub is scoped to the one repository and to reading: {seen['tokens']!r}",
        )
        central.check(
            not any(t["repositories"] is None for t in seen["tokens"]),
            "the central asked GitHub for a token of the whole installation before it needed "
            f"one of its own: {seen['tokens']!r}",
        )
        authorised = [e for e in seen["git_authorizations"] if e["status"] == 200]
        central.check(
            authorised and all(e["repository"] == REPOSITORY for e in authorised),
            f"the repository was not served to a token: {seen['git_authorizations']!r}",
        )
        central.check(
            any(e["status"] == 401 for e in seen["git_authorizations"]),
            "the repository was never refused: the node's own check did not reach it",
        )
        listing = json.dumps(sources()) + json.dumps(leases_on_node())
        central.check("ghs_" not in listing, "a token is in what the API shows")
        sealed = node.run(
            "grep -rl 'noust-secret:v1:' $(/opt/noust/bin/python -c "
            "'from noust.core.secrets import secrets_dir; print(secrets_dir() / \"leases\")') "
            "| wc -l",
            label="the node keeps the token as a sealed secret",
        )
        node.check(int(sealed.stdout.strip() or "0") >= 1, "no sealed lease on the node")
        audit_path = node.run(
            "/opt/noust/bin/python -c 'from noust.core import audit; print(audit.get_log().path)'",
            label="where the node's audit trail is",
        ).stdout.strip()
        trail = node.run(f"cat {audit_path}", label="the node's audit trail").stdout
        node.check("sources.lease.hold" in trail, "the node did not audit the lease")
        node.check("ghs_" not in trail, "a token is in the node's audit trail")

        # -- the lease follows the application, renewed while it serves the repository ---------------------------
        asked = len(github()["tokens"])
        fr.wait_until(
            lambda: len(github()["tokens"]) > asked,
            "the central to ask GitHub for a fresh token while the node serves the repository",
            timeout=240,
        )
        fr.wait_until(
            lambda: any(lease["expires_in"] > 30 for lease in leases_on_node()),
            "the node to hold the renewed lease",
            timeout=60,
        )

        # -- a push, delivered to the central, deploys on the node -------------------------------------------------
        sha = central.run(
            "cd /tmp/work && echo '<h1>Leased v2</h1>' > index.html && git add -A && "
            f"git commit -q -m v2 && git push -q /srv/ghe/{REPOSITORY}.git main && "
            "git rev-parse HEAD",
            label="a second commit lands in the repository",
        ).stdout.strip()
        payload = {
            "ref": "refs/heads/main",
            "after": sha,
            "repository": {
                "full_name": REPOSITORY,
                "clone_url": SOURCE,
                "default_branch": "main",
            },
            "installation": {"id": INSTALLATION},
        }
        signature = (
            "sha256="
            + hmac.new(
                WEBHOOK_SECRET.encode(), json.dumps(payload).encode(), hashlib.sha256
            ).hexdigest()
        )
        status_code, answer, raw = fr.api_call(
            fr.CENTRAL_NAME,
            "POST",
            f"https://127.0.0.1:{fr.CENTRAL_PORT}/hooks/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "fuentes-push-1",
                "X-Hub-Signature-256": signature,
            },
            json_body=payload,
        )
        fr.log_api(central, "POST", "/hooks/github  (a signed push)", status_code, raw[:500])
        central.check(
            status_code == 202, f"the central did not accept the push: {status_code} {raw[:500]!r}"
        )
        central.check(
            [item["node"] for item in answer.get("forwarded", [])] == [name],
            f"the central did not hand the push to the node: {answer!r}",
        )

        def delivery() -> dict[str, Any] | None:
            items = call("GET", "/api/fleet/sources/deliveries")["items"]
            found = [i for i in items if i["delivery"] == "fuentes-push-1"]
            return dict(found[0]) if found else None

        fr.wait_until(
            lambda: (delivery() or {}).get("state") == "delivered"
            and bool((delivery() or {}).get("follows"))
            and all(f["state"] == "done" for f in (delivery() or {}).get("follows", [])),
            "the node to take the push and the central to see its deployment through",
            timeout=300,
        )
        done = delivery() or {}
        central.check(
            [j["domain"] for j in done["jobs"]] == [DOMAIN],
            f"the node did not queue the update of the application: {done!r}",
        )
        node.check("Leased v2" in page(), f"the node does not serve the pushed commit: {page()!r}")
        recorded = github()
        statuses = [s["state"] for items in recorded["statuses"].values() for s in items]
        central.check(
            statuses
            and statuses[-1] == "success"
            and set(statuses) <= {"queued", "in_progress", "success"},
            f"the commit's deployment on GitHub did not go to success: {statuses!r}",
        )
        deployments = recorded["deployments"].get(REPOSITORY, [])
        central.check(
            deployments and deployments[-1]["ref"] == sha,
            f"GitHub's deployment is not of the pushed commit: {deployments!r}",
        )
        wide = [t for t in recorded["tokens"] if t["repositories"] is None]
        central.check(
            wide and all(t["permissions"].get("deployments") == "write" for t in wide),
            f"the central did not write the status with the installation's own token: {recorded['tokens']!r}",
        )
        trail = node.run(f"cat {audit_path}", label="the node's audit trail again").stdout
        node.check(
            "sources.event.forward" in trail and "fuentes-push-1" in trail,
            "the node's audit trail does not name the delivery it was handed",
        )

        # -- the central goes quiet: the access ends, and the error says exactly that ----------------------------------
        central.run(
            "cd /tmp/work && echo '<h1>Leased v3</h1>' > index.html && git add -A && "
            f"git commit -q -m v3 && git push -q /srv/ghe/{REPOSITORY}.git main",
            label="a third commit lands in the repository, which only an update that clones can see",
        )
        central.run("systemctl stop noust-web", label="the central stops (its console)")
        try:
            time.sleep(TOKEN_LIFETIME + 25)
            held_now = node.run(
                "/opt/noust/bin/python -c 'from noust.integrations.github.leases import SourceLeases; "
                "import time; print([round(l.expires_at - time.time()) for l in SourceLeases().list()])'",
                label="how long the node's lease has left",
            ).stdout
            node.check(
                all(int(x) <= 0 for x in json.loads(held_now.strip())),
                f"the node's lease did not end on its own: {held_now!r}",
            )
            failed = node.run(
                f"noust update {DOMAIN} -y",
                timeout=180,
                check=False,
                label="an update that starts on the node while the central is silent",
            )
            text = failed.stdout + failed.stderr
            node.check(
                failed.returncode != 0, f"the update succeeded without any access: {text[-600:]!r}"
            )
            node.check(
                f"leased to this server for {REPOSITORY}" in text
                and "has not called since" in text,
                f"the error does not say the lease ended and the central went quiet: {text[-900:]!r}",
            )
            node.check(
                "private or the credentials were refused" not in text,
                f"the error is the generic one: {text[-900:]!r}",
            )
            node.check("Leased v2" in page(), "the application stopped serving while it failed")
        finally:
            central.run("systemctl start noust-web", label="the central starts again")

        # -- the central is back: the access is leased again, and the update works --------------------------------------
        def newest_lease() -> int:
            status_code, body, _raw = fr.api_call(
                fr.CENTRAL_NAME,
                "GET",
                f"https://127.0.0.1:{fr.CENTRAL_PORT}/api/nodes/{name}/api/sources/leases",
                token=env.master_token,
            )
            if status_code != 200 or not isinstance(body, dict):
                return -1
            return max((int(lease["expires_in"]) for lease in body["items"]), default=-1)

        fr.wait_until(
            lambda: newest_lease() > 30,
            "the central to lease the node access again when it is back",
            timeout=240,
        )
        fixed = node.run(
            f"noust update {DOMAIN} -y",
            timeout=240,
            check=False,
            label="the same update, once the central has called again",
        )
        node.check(
            fixed.returncode == 0,
            f"the update still fails with the central back: {(fixed.stdout + fixed.stderr)[-900:]!r}",
        )
        node.check("Leased v3" in page(), f"the node does not serve the third commit: {page()!r}")
