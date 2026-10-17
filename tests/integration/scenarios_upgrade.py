"""
The upgrade from Noust 3.3.1 to this tree, on a server that carries every legacy name.

``python tests/integration/run.py --upgrade-from-3.3`` starts a container, installs the ``v3.3.1`` tag
from a git checkout (not from PyPI: it is the tag's own tree), and seeds a server the way the
production servers look after years of releases: an application whose unit still has the ``wasm-``
prefix of before 0.14.1, a blue/green application whose upstream is ``wasm_bg_<app>`` and an
operator's hand-written site that proxies to it, a Docker Compose stack on the relay, a
``wasm_ro_`` read-only database role, a ``wasm-backup`` archive in the ``wasm-backups`` folder of
an S3 bucket, and an API token. It then installs this tree's wheel over it, as the packages do, and
checks that every application stays healthy.

Two kinds of check, because wave 5 of the 3.4 program (the rename of everything ``wasm``) has not
run yet:

- the **hard** ones are what the upgrade must not break whatever wave 5 does: every application
  serves, nothing is restarted by the upgrade, the operator's site still loads, the old backup
  restores, the old role still reads, the old token still signs in, an update of each application
  works;
- the **wave 5** ones state what the rename must leave once it has run (the unit renamed at its next
  deploy, the generated upstream under its new name with the operator's line named by ``noust setup
  doctor`` and never rewritten, the read-only role renamed, the next backup in the new folder with the
  old ones still listed). They are expected to fail until it runs: each is reported ``XFAIL``, or
  ``XPASS`` when it already holds, and none fails the run. Set :data:`WAVE5_DONE` when the rename
  has landed and they become hard.

Docker: MinIO and the Compose stack run in the harness container's own Docker daemon.
"""

from __future__ import annotations

import re
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import run
import scenarios_34
from run import (
    DEPLOY_TIMEOUT,
    REPO_ROOT,
    HarnessError,
    Scenario,
    commit_to,
    json_of,
    make_node_repo,
    sh,
    start_container,
    wait_for_systemd,
)

#: The tag the server is seeded with.
FROM_TAG = "v3.3.1"

#: Flip to True when wave 5 (the rename of every ``wasm`` name) has landed: the expectations
#: that are reported as expected failures until then become failures of the run.
WAVE5_DONE = False

LEGACY_DOMAIN = "legacy.test"
LEGACY_APP = "legacy-test"
LEGACY_UNIT = f"wasm-{LEGACY_APP}"
LEGACY_DB = "legacydb"

BG_DOMAIN = "bgapp.test"
BG_APP = "bgapp-test"
BG_UPSTREAM = "wasm_bg_bgapp_test"

EDGE_DOMAIN = "edge.test"
EDGE_SITE = "/etc/nginx/sites-available/edge"
EDGE_TEXT = f"""# Written by hand, long before the applications behind it were deployed by Noust.
server {{
    listen 80;
    listen [::]:80;
    server_name {EDGE_DOMAIN};

    location / {{
        proxy_pass http://{BG_UPSTREAM};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }}
}}
"""

STACK_DOMAIN = "stack.test"
STACK_PORT = 18301
STACK_APP = "stack-test"

S3_NAME = "noust-it-minio-legacy"
S3_PORT = 19001
S3_USER = "noustit"
S3_DESTINATION = "legacy-s3"
S3_BUCKET = "wasm-backups"

TOKEN_NAME = "legacy-admin-token"
WASM_TOKEN_NAME = "legacy-wasm-token"
COOKIE_JAR = "/root/legacy.cookies"

#: Mints a token the way 2.x did (``wasm_tok_`` prefix, stored hashed), with the code of the
#: installed Noust: 3.x only ever issues ``noust_tok_`` and still accepts the old prefix, so the
#: only way to have one on a seeded server is to store one. It is written to a file, not printed.
MAKE_WASM_TOKEN = f"""/opt/noust/bin/python -I - <<'PY'
import secrets, time
from noust.cli.web_state import token_manager
manager = token_manager()
token = "wasm_tok_" + secrets.token_urlsafe(32)
manager.sessions.create_api_token(
    "{WASM_TOKEN_NAME}", manager._hash_token(token), "admin", time.time(), None, created_by="seed"
)
open("/root/legacy-wasm-token", "w").write(token)
PY
chmod 600 /root/legacy-wasm-token"""

#: The operator's site for the Compose stack, the way Proggest's is: its own upstream block that
#: includes the servers file of Noust's relay instead of naming a port.
STACK_EDGE_DOMAIN = "stack-edge.test"
STACK_EDGE_SITE = "/etc/nginx/sites-available/stack-edge"
STACK_EDGE_TEXT = f"""# Proggest-style: an upstream of the operator's own that includes the relay's servers file.
upstream stack_edge_upstream {{
    include /etc/nginx/noust-upstreams/{STACK_APP}/web.servers;
}}

server {{
    listen 80;
    listen [::]:80;
    server_name {STACK_EDGE_DOMAIN};

    location / {{
        proxy_pass http://stack_edge_upstream;
        proxy_set_header Host $host;
    }}
}}
"""


@dataclass
class Wave5:
    """What the rename must leave, judged without failing the run until wave 5 has landed."""

    results: list[tuple[bool, str]] = field(default_factory=list)

    def expect(self, holds: bool, what: str, detail: str = "") -> None:
        """Record one expectation.

        Args:
            holds: Whether it holds now.
            what: The expectation, as a sentence.
            detail: What was found, for a failure.
        """
        self.results.append((holds, what + (f" ({detail})" if detail and not holds else "")))

    def report(self, sc: Scenario) -> list[str]:
        """Say how the expectations stand; return the ones that fail when wave 5 is done.

        Args:
            sc: The scenario, whose evidence gets the lines.

        Returns:
            The failures to raise: empty until :data:`WAVE5_DONE`.
        """
        lines = []
        for holds, what in self.results:
            if holds:
                lines.append(f"{'PASS' if WAVE5_DONE else 'XPASS'}: {what}")
            else:
                lines.append(f"{'FAIL' if WAVE5_DONE else 'XFAIL'}: {what}")
        sc.evidence.append("[wave 5] what the rename must leave:\n" + "\n".join(lines))
        held = sum(1 for holds, _ in self.results if holds)
        print(
            f"[wave 5] {held} of {len(self.results)} expectation(s) already hold; "
            f"{'the rest fail the run' if WAVE5_DONE else 'the rest are expected to fail until it runs'}"
        )
        for line in lines:
            print(f"[wave 5]   {line}")
        if not WAVE5_DONE:
            return []
        return [what for holds, what in self.results if not holds]


def install_from_git(name: str, tag: str) -> str:
    """Install a tag of this repository into ``/opt/noust`` from a git checkout in the container.

    Args:
        name: The container.
        tag: The tag.

    Returns:
        The commit the tag names, as the container's checkout says it.
    """
    with tempfile.TemporaryDirectory(prefix="noust-it-upgrade-") as tmp:
        source = Path(tmp) / "noust-src"
        sh(
            [
                "git",
                "clone",
                "-q",
                "--depth",
                "1",
                "--branch",
                tag,
                f"file://{REPO_ROOT}",
                str(source),
            ],
            timeout=300,
        )
        sh(["docker", "cp", str(source), f"{name}:/root/noust-src"], timeout=300)
    run.docker_exec(name, "chown -R root:root /root/noust-src", timeout=60)
    commit = run.docker_exec(
        name, "git -c safe.directory='*' -C /root/noust-src rev-parse HEAD", timeout=30
    ).stdout.strip()
    described = run.docker_exec(
        name, "git -c safe.directory='*' -C /root/noust-src describe --tags", timeout=30
    ).stdout.strip()
    if described != tag:
        raise HarnessError(f"the checkout is at {described!r}, not {tag}")
    print(f"[setup] installing {tag} ({commit[:10]}) from the git checkout in the container")
    run.docker_exec(name, "python3 -m venv /opt/noust", timeout=60)
    run.docker_exec(name, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=120)
    run.docker_exec(
        name,
        "/opt/noust/bin/pip install --quiet '/root/noust-src[all]'",
        timeout=run.PIP_INSTALL_TIMEOUT,
    )
    run.docker_exec(name, "ln -sf /opt/noust/bin/noust /usr/local/bin/noust", timeout=15)
    run.docker_exec(name, "ln -sf /opt/noust/bin/wasm /usr/local/bin/wasm", timeout=15)
    version = run.docker_exec(name, "noust --version", timeout=30).stdout.strip()
    print(f"[setup] installed {version}")
    return commit


def listed_domains(listing: dict[str, Any]) -> list[str]:
    """The domains ``noust list --json`` lists, sorted."""
    return sorted(str(item["domain"]) for item in listing["items"])


def body_of(sc: Scenario, host: str, label: str) -> str:
    """What nginx answers ``host`` on ``/``."""
    return sc.run(
        f"curl -sS -m 10 -H 'Host: {host}' http://127.0.0.1/",
        timeout=30,
        check=False,
        label=f"{label}: curl -H 'Host: {host}' http://127.0.0.1/",
    ).stdout.strip()


def units_state(sc: Scenario, units: list[str], label: str) -> dict[str, tuple[str, str]]:
    """``(ActiveState, MainPID)`` of units: a pid that changes is a restart."""
    state: dict[str, tuple[str, str]] = {}
    for unit in units:
        out = sc.run(
            f"systemctl show -p ActiveState -p MainPID --value {unit} | paste -sd' '",
            timeout=15,
            check=False,
            label=f"{label}: {unit}",
        ).stdout.split()
        state[unit] = (out[0], out[1]) if len(out) == 2 else ("unknown", "0")
    return state


def wait_page(sc: Scenario, host: str, expected: str, label: str, seconds: int = 120) -> None:
    """Wait for a page to say ``expected``, or fail with what it said."""
    deadline = time.monotonic() + seconds
    body = ""
    while time.monotonic() < deadline:
        body = run.docker_exec(
            sc.container,
            f"curl -sS -m 10 -H 'Host: {host}' http://127.0.0.1/",
            timeout=30,
            check=False,
        ).stdout.strip()
        if expected in body:
            break
        time.sleep(2)
    sc.evidence.append(f"$ {label}: until {host} says {expected!r}\n{body[:300]}")
    sc.check(expected in body, f"{label}: {host} answered {body[:200]!r}, not {expected!r}")


@dataclass
class Seeded:
    """What the seeding left behind that the checks need."""

    node_repo: str
    bg_repo: str
    stack_repo: str
    backup_ids: list[str]
    token: str
    wasm_token: str
    master: str
    units: list[str]


def jar_login(sc: Scenario, master: str, label: str) -> set[str]:
    """Sign in with the access token, keeping the cookies in :data:`COOKIE_JAR`.

    The token goes to curl on its standard input (a config), never an argument.

    Args:
        sc: The scenario.
        master: The access token.
        label: What the evidence calls it.

    Returns:
        The names of the cookies the console set.
    """
    body = '{"token": "%s"}' % master
    config = "\n".join(
        [
            f'url = "{run.PANEL_URL}/api/auth/login"',
            'request = "POST"',
            "silent",
            "show-error",
            f'cookie-jar = "{COOKIE_JAR}"',
            'header = "Content-Type: application/json"',
            f"data = '{body}'",
        ]
    )
    proc = run.subprocess.run(
        ["docker", "exec", "-i", sc.container, "curl", "-K", "-"],
        input=config + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    sc.evidence.append(f"$ {label}\n(response withheld: it holds the session)")
    if proc.returncode != 0:
        raise AssertionError(f"the sign-in failed: {proc.stderr.strip()[:200]}")
    jar = run.docker_exec(
        sc.container, f"awk '!/^#/ || /HttpOnly/ {{print $6}}' {COOKIE_JAR}", timeout=15
    ).stdout.split()
    return set(jar)


def jar_get(sc: Scenario, path: str, label: str) -> tuple[int, Any]:
    """GET a path of the console with the cookies of :data:`COOKIE_JAR`.

    Args:
        sc: The scenario.
        path: The path under the console.
        label: What the evidence calls it.

    Returns:
        The status code and the parsed body.
    """
    proc = run.docker_exec(
        sc.container,
        f"curl -sS -m 15 -b {COOKIE_JAR} -w '\\n%{{http_code}}' {run.PANEL_URL}{path}",
        timeout=30,
        check=False,
    )
    text, _, code = proc.stdout.rpartition("\n")
    try:
        payload: Any = run.json.loads(text) if text.strip() else None
    except ValueError:
        payload = text
    sc.evidence.append(f"$ {label}\n{code} {str(payload)[:400]}")
    return int(code or 0), payload


def seed_legacy_server(sc: Scenario) -> Seeded:
    """Make every legacy name on a 3.3.1 server.

    Args:
        sc: The scenario, in a container that runs Noust 3.3.1.

    Returns:
        What the checks need of it.
    """
    old = "[noust 3.3.1]"
    run.require_docker(sc)
    run.pull_images(sc, run.COMPOSE_BASE_IMAGE, scenarios_34.S3_IMAGE)

    # -- An in-place application whose unit has the prefix of before 0.14.1 -------------------
    node_repo = "/root/fixtures/node-app"
    sc.run(
        f"noust create -d {LEGACY_DOMAIN} -s {node_repo} -t nodejs --no-ssl --layout inplace",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust create -d {LEGACY_DOMAIN} (in place)",
    )
    sc.run(
        f"systemctl stop {LEGACY_APP} && systemctl disable {LEGACY_APP} 2>&1 && "
        f"mv /etc/systemd/system/{LEGACY_APP}.service /etc/systemd/system/{LEGACY_UNIT}.service && "
        f"sed -i 's/Generated by Noust/Generated by WASM/' /etc/systemd/system/{LEGACY_UNIT}.service && "
        f"systemctl daemon-reload && systemctl enable --now {LEGACY_UNIT}",
        timeout=120,
        label=f"{old} the unit of {LEGACY_DOMAIN} as an application from before 0.14.1 has it",
    )
    wait_page(sc, LEGACY_DOMAIN, "ok ", f"{old} {LEGACY_DOMAIN}")
    status = sc.run(
        f"noust status {LEGACY_DOMAIN}", timeout=30, label=f"{old} noust status {LEGACY_DOMAIN}"
    )
    sc.check(
        LEGACY_UNIT in status.stdout, f"3.3.1 does not resolve the legacy unit: {status.stdout!r}"
    )

    # -- A blue/green application: wasm_bg_<app> --------------------------------------------
    bg_repo, bg_url = make_node_repo(sc, "bgapp")
    sc.run(
        f"noust create -d {BG_DOMAIN} -s {bg_url} -t nodejs --no-ssl --layout releases",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust create -d {BG_DOMAIN} (releases, without downtime)",
    )
    wait_page(sc, BG_DOMAIN, "ok 1", f"{old} {BG_DOMAIN}")
    named = sc.run(
        f"grep -rl '{BG_UPSTREAM}' /etc/nginx",
        timeout=15,
        check=False,
        label=f"{old} where nginx knows {BG_UPSTREAM}",
    ).stdout.split()
    sc.check(bool(named), f"3.3.1 did not write the {BG_UPSTREAM} upstream")

    # -- The operator's site, which names that upstream --------------------------------------
    sc.run(
        f"printf '%s' '{EDGE_TEXT}' > {EDGE_SITE} && ln -sf {EDGE_SITE} /etc/nginx/sites-enabled/edge && "
        "nginx -t && systemctl reload nginx",
        timeout=30,
        label=f"{old} the operator's site {EDGE_DOMAIN}, proxying to {BG_UPSTREAM}",
    )
    wait_page(sc, EDGE_DOMAIN, "ok 1", f"{old} {EDGE_DOMAIN}")

    # -- A Compose stack on the relay: wasm_bg_<app>_<service> -------------------------------
    stack_repo, stack_url = run.make_compose_repo(sc, "legacy-stack", "web", port=STACK_PORT)
    run.compose_create(sc, STACK_DOMAIN, stack_url, port=STACK_PORT)
    run.wait_served(sc, STACK_DOMAIN, "web v1")
    sc.run(
        f"noust app zero-downtime {STACK_DOMAIN} on --drain 2",
        timeout=120,
        label=f"{old} noust app zero-downtime {STACK_DOMAIN} on",
    )
    sc.check(
        sc.run(
            f"grep -rl 'wasm_bg_{STACK_APP.replace('-', '_')}_web' /etc/nginx",
            timeout=15,
            check=False,
            label=f"{old} the stack's relay upstream",
        ).stdout.strip()
        != "",
        "3.3.1 did not write the stack's wasm_bg_ upstream",
    )
    # The operator's own site for the stack, the way Proggest's is: an upstream block of its own
    # that includes the relay's servers file. Noust never wrote it and never rewrites it.
    sc.run(
        f"printf '%s' '{STACK_EDGE_TEXT}' > {STACK_EDGE_SITE} && "
        f"ln -sf {STACK_EDGE_SITE} /etc/nginx/sites-enabled/stack-edge && nginx -t && "
        "systemctl reload nginx",
        timeout=30,
        label=f"{old} the operator's site {STACK_EDGE_DOMAIN}, with the relay's include",
    )
    wait_page(sc, STACK_EDGE_DOMAIN, "web v1", f"{old} {STACK_EDGE_DOMAIN}")

    # A server that has lived: one update of each application under 3.3.1, so the trees hold a
    # previous release, the runtime stamps and the image tags that an update leaves.
    commit_to(sc, node_repo, "echo 1b > VERSION", "version 1b")
    sc.run(
        f"noust update {LEGACY_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust update {LEGACY_DOMAIN}",
    )
    commit_to(sc, bg_repo, "echo 1b > VERSION", "version 1b")
    sc.run(
        f"noust update {BG_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust update {BG_DOMAIN}",
    )
    commit_to(sc, stack_repo, "printf 'v1b\\n' > VERSION", "version 1b")
    sc.run(
        f"noust update {STACK_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{old} noust update {STACK_DOMAIN}",
    )
    wait_page(sc, STACK_DOMAIN, "web v1b", f"{old} {STACK_DOMAIN}")
    wait_page(sc, STACK_EDGE_DOMAIN, "web v1b", f"{old} {STACK_EDGE_DOMAIN}")
    wait_page(sc, BG_DOMAIN, "ok 1b", f"{old} {BG_DOMAIN}")
    wait_page(sc, EDGE_DOMAIN, "ok 1b", f"{old} {EDGE_DOMAIN}")
    wait_page(sc, LEGACY_DOMAIN, "ok 1b", f"{old} {LEGACY_DOMAIN}")

    # -- A database, and the read-only role the first read makes ------------------------------
    sc.run(
        f"noust db create {LEGACY_DB} --engine mysql --app {LEGACY_DOMAIN}",
        timeout=120,
        label=f"{old} noust db create {LEGACY_DB} --engine mysql",
    )
    scenarios_34.db_query(
        sc,
        LEGACY_DB,
        "CREATE TABLE seed (id int PRIMARY KEY, note varchar(40))",
        engine="mysql",
        write=True,
        label=f"{old} CREATE TABLE seed",
    )
    scenarios_34.db_query(
        sc,
        LEGACY_DB,
        "INSERT INTO seed VALUES (1, 'written under 3.3.1')",
        engine="mysql",
        write=True,
        label=f"{old} INSERT a row",
    )
    read = scenarios_34.db_query(
        sc,
        LEGACY_DB,
        "SELECT note FROM seed",
        engine="mysql",
        label=f"{old} a read, which makes the read-only account",
    )
    sc.check(
        "written under 3.3.1" in read.stdout, f"3.3.1 cannot read its own row: {read.stdout!r}"
    )
    role = sc.run(
        "mysql -N -e \"SELECT User FROM mysql.user WHERE User LIKE 'wasm\\_ro\\_%'\"",
        timeout=30,
        label=f"{old} the read-only accounts of the engine",
    ).stdout.split()
    sc.check(f"wasm_ro_{LEGACY_DB}" in role, f"3.3.1 made no wasm_ro_ account: {role!r}")

    # -- A backup in the wasm-backups folder of an S3 bucket ------------------------------------
    secret = secrets.token_urlsafe(18)
    sc.run(
        f"docker rm -f {S3_NAME} >/dev/null 2>&1; docker run -d --name {S3_NAME} "
        f"-p 127.0.0.1:{S3_PORT}:9000 -e MINIO_ROOT_USER={S3_USER} "
        f"-e MINIO_ROOT_PASSWORD={secret} {scenarios_34.S3_IMAGE} server /data",
        timeout=120,
        label=f"{old} MinIO, for a destination whose folder is the default one",
    )
    sc.run(
        f"for i in $(seq 1 60); do curl -fsS http://127.0.0.1:{S3_PORT}/minio/health/ready "
        "&& exit 0; sleep 1; done; exit 1",
        timeout=90,
        label="wait for MinIO",
    )
    sc.run(
        "noust config set security.allow_private_hosts 127.0.0.1 --list",
        timeout=30,
        label=f"{old} noust config set security.allow_private_hosts 127.0.0.1 --list",
    )
    add = (
        f"printf '%s' '{secret}' | noust backup destination add {S3_DESTINATION} --type s3 "
        f"--field provider=Minio --field endpoint=http://127.0.0.1:{S3_PORT} "
        f"--field access_key_id={S3_USER} --stdin"
    )
    sc.run(add, timeout=60, label=add.replace(secret, "<secret key>"))
    sc.run(
        f"noust backup destination test {S3_DESTINATION}",
        timeout=120,
        label=f"{old} noust backup destination test {S3_DESTINATION}",
    )
    sc.run(
        f"noust backup schedule create {LEGACY_DOMAIN} --schedule daily --retention-count 2 "
        f"--destination {S3_DESTINATION}:5",
        timeout=60,
        label=f"{old} noust backup schedule create {LEGACY_DOMAIN} --destination {S3_DESTINATION}",
    )
    sc.run(
        f"noust backup run-schedule {LEGACY_DOMAIN}",
        timeout=300,
        label=f"{old} noust backup run-schedule {LEGACY_DOMAIN}",
    )
    listing = json_of(
        sc.run(
            f"noust backup remote-list {S3_DESTINATION} --app {LEGACY_APP} --json",
            timeout=120,
            label=f"{old} noust backup remote-list {S3_DESTINATION}",
        ),
        "remote-list",
    )
    ids = run.remote_ids(listing)
    sc.check(len(ids) == 1, f"the bucket should hold the one backup: {ids!r}")
    folders = sc.run(
        f"docker exec {S3_NAME} sh -c 'ls /data'",
        timeout=30,
        label=f"{old} the buckets MinIO holds",
    ).stdout.split()
    sc.check(S3_BUCKET in folders, f"the backup is not in the {S3_BUCKET} bucket: {folders!r}")

    # -- The console, an API token, and the access token ----------------------------------------
    enable = run.docker_exec(sc.container, "noust web enable", timeout=60, check=False)
    sc.evidence.append(f"$ {old} noust web enable\n(output withheld: it holds the access token)")
    master_match = re.search(r"Access Token:\s*(\S+)", enable.stdout)
    sc.check(master_match is not None, "noust web enable printed no access token")
    run.wait_for_console(sc)
    issued = run.docker_exec(
        sc.container, f"noust token create {TOKEN_NAME} --scope admin", timeout=30
    )
    sc.evidence.append(f"$ {old} noust token create {TOKEN_NAME} --scope admin\n(withheld)")
    token_match = re.search(r"Token:\s*(\S+)", issued.stdout)
    sc.check(token_match is not None, "noust token create printed no token")
    token = token_match.group(1) if token_match else ""
    code, session = run.api_secret(
        sc, "GET", "/api/auth/session", f"{old} GET /api/auth/session", bearer=token
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the API token does not authenticate on 3.3.1: {code} {session!r}",
    )
    # A token the way 2.x issued it: the prefix 3.x still accepts but never issues.
    sc.run(
        MAKE_WASM_TOKEN,
        timeout=60,
        label=f"{old} a wasm_tok_ API token, as 2.x stored them (written to a file, not shown)",
    )
    wasm_token = run.docker_exec(sc.container, "cat /root/legacy-wasm-token", timeout=15).stdout
    wasm_token = wasm_token.strip()
    sc.check(wasm_token.startswith("wasm_tok_"), "the seeded token has not the legacy prefix")
    code, session = run.api_secret(
        sc, "GET", "/api/auth/session", f"{old} GET /api/auth/session (wasm_tok_)", bearer=wasm_token
    )
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the wasm_tok_ token does not authenticate on 3.3.1: {code} {session!r}",
    )
    # A signed-in browser: the session and CSRF cookies the console sets, kept in a jar.
    master = master_match.group(1) if master_match else ""
    cookies = jar_login(sc, master, f"{old} POST /api/auth/login (the cookie jar of a signed-in browser)")
    sc.check(
        "wasm_session" in cookies and "wasm_csrf" in cookies,
        f"3.3.1 did not set its session and CSRF cookies: {sorted(cookies)}",
    )
    code, session = jar_get(sc, "/api/auth/session", f"{old} GET /api/auth/session (the cookies)")
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the session cookie does not authenticate on 3.3.1: {code} {session!r}",
    )
    # The console stays up through the upgrade, as on a production server: the packages restart it.

    units = [LEGACY_UNIT, f"{BG_APP}@blue", f"{BG_APP}@green", "nginx", "mariadb", "docker"]
    return Seeded(
        node_repo=node_repo,
        bg_repo=bg_repo,
        stack_repo=stack_repo,
        backup_ids=ids,
        token=token,
        wasm_token=wasm_token,
        master=master,
        units=units,
    )


def run_upgrade_rehearsal(sc: Scenario, wheel: Path) -> None:
    """Seed a 3.3.1 server with every legacy name, upgrade it, check it stays healthy.

    Args:
        sc: The scenario, in a container that runs Noust 3.3.1.
        wheel: This tree's wheel, on the host.
    """
    seeded = seed_legacy_server(sc)
    new = "[this tree]"
    wave5 = Wave5()

    pages_before = {
        LEGACY_DOMAIN: body_of(sc, LEGACY_DOMAIN, "before"),
        BG_DOMAIN: body_of(sc, BG_DOMAIN, "before"),
        EDGE_DOMAIN: body_of(sc, EDGE_DOMAIN, "before"),
        STACK_DOMAIN: body_of(sc, STACK_DOMAIN, "before"),
        STACK_EDGE_DOMAIN: body_of(sc, STACK_EDGE_DOMAIN, "before"),
    }
    sc.check(
        all(body for body in pages_before.values()),
        f"an application did not serve: {pages_before!r}",
    )
    state_before = units_state(sc, seeded.units, "before the upgrade")
    list_before = json_of(
        sc.run("noust list --json", timeout=60, label="[noust 3.3.1] noust list --json"), "list"
    )

    # -- The upgrade: the wheel over the venv, as the packages do ---------------------------------
    run.upgrade_noust_venv(sc, wheel)
    # The first command that opens the store is the one that migrates it.
    list_after = json_of(
        sc.run(
            "noust list --json", timeout=60, label=f"{new} noust list --json (migrates the store)"
        ),
        "list",
    )

    # -- Nothing the upgrade did restarted anything -------------------------------------------------
    state_after = units_state(sc, seeded.units, "after the upgrade")
    sc.check(
        state_after == state_before,
        f"the upgrade restarted or stopped something: {state_before!r} -> {state_after!r}",
    )
    for domain, before in pages_before.items():
        sc.check(
            body_of(sc, domain, "after") == before,
            f"{domain} no longer answers what it did before the upgrade",
        )
    sc.run("nginx -t", timeout=30, label=f"{new} nginx -t")

    # -- The same applications, listed and resolved ----------------------------------------------------
    sc.check(
        listed_domains(list_after) == listed_domains(list_before),
        f"the applications listed changed: {listed_domains(list_before)} -> "
        f"{listed_domains(list_after)}",
    )
    status = sc.run(
        f"noust status {LEGACY_DOMAIN}", timeout=30, label=f"{new} noust status {LEGACY_DOMAIN}"
    )
    sc.check(
        LEGACY_UNIT in status.stdout,
        f"the legacy unit is not resolved after the upgrade: {status.stdout!r}",
    )

    # -- The old backup restores -----------------------------------------------------------------------
    listing = json_of(
        sc.run(
            f"noust backup remote-list {S3_DESTINATION} --app {LEGACY_APP} --json",
            timeout=120,
            label=f"{new} noust backup remote-list {S3_DESTINATION}",
        ),
        "remote-list",
    )
    sc.check(
        run.remote_ids(listing) == seeded.backup_ids,
        f"the backup written under 3.3.1 is not listed: {listing!r}",
    )
    sc.run(
        f"rm -rf /var/www/apps/{LEGACY_APP}/VERSION && echo tampered > /var/www/apps/{LEGACY_APP}/VERSION",
        timeout=15,
        label=f"{new} tamper with {LEGACY_DOMAIN}",
    )
    sc.run(
        f"noust backup restore {seeded.backup_ids[-1]} --from {S3_DESTINATION} -f",
        timeout=300,
        label=f"{new} noust backup restore {seeded.backup_ids[-1]} --from {S3_DESTINATION}",
    )
    wait_page(sc, LEGACY_DOMAIN, "ok 1b", f"{new} after the restore")
    sc.check(
        body_of(sc, LEGACY_DOMAIN, "restored") == "ok 1b",
        "the restore of the backup written under 3.3.1 did not bring its version back",
    )

    # -- The old read-only role still reads -----------------------------------------------------------
    read = scenarios_34.db_query(
        sc,
        LEGACY_DB,
        "SELECT note FROM seed",
        engine="mysql",
        label=f"{new} a read of a database whose read-only account 3.3.1 made",
    )
    sc.check("written under 3.3.1" in read.stdout, f"the old role no longer reads: {read.stdout!r}")

    # -- The console, restarted as the packages do, still knows who is signed in ------------------------
    # It ran 3.3.1 through the upgrade (as on a production server); the packages restart it.
    sc.run(
        "systemctl restart noust-web.service; for i in $(seq 1 30); do "
        f"curl -fsS -o /dev/null {run.PANEL_URL}/health && exit 0; sleep 1; done; exit 1",
        timeout=120,
        label=f"{new} systemctl restart noust-web (the new code answers)",
    )
    code, session = jar_get(sc, "/api/auth/session", f"{new} GET /api/auth/session (the old cookies)")
    sc.check(
        code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
        f"the session of a browser signed in under 3.3.1 does not survive the upgrade: {code} {session!r}",
    )
    for what, bearer in (
        ("the noust_tok_ token made under 3.3.1", seeded.token),
        ("the wasm_tok_ token made under 2.x", seeded.wasm_token),
    ):
        code, session = run.api_secret(
            sc, "GET", "/api/auth/session", f"{new} GET /api/auth/session ({what})", bearer=bearer
        )
        sc.check(
            code == 200 and isinstance(session, dict) and session.get("authenticated") is True,
            f"{what} does not authenticate after the upgrade: {code} {session!r}",
        )
    code, login = run.api_secret(
        sc,
        "POST",
        "/api/auth/login",
        f"{new} POST /api/auth/login (the access token)",
        body={"token": seeded.master},
    )
    sc.check(code == 200, f"the access token does not sign in after the upgrade: {code} {login!r}")
    fresh = jar_login(sc, seeded.master, f"{new} POST /api/auth/login (a new cookie jar)")
    wave5.expect(
        "noust_session" in fresh and "wasm_session" not in fresh,
        "the console sets noust_session and noust_csrf, not the wasm_ cookies",
        f"cookies: {sorted(fresh)}",
    )

    # -- The doctor and the health report answer ---------------------------------------------------------
    doctor = sc.run(
        "noust setup doctor", timeout=300, check=False, label=f"{new} noust setup doctor"
    )
    sc.check("Traceback" not in doctor.stdout + doctor.stderr, "noust setup doctor crashed")
    health = sc.run("noust health --json", timeout=120, check=False, label=f"{new} noust health")
    sc.check("Traceback" not in health.stdout + health.stderr, "noust health crashed")

    # -- An update of each application is a deploy like any other ------------------------------------------
    commit_to(sc, seeded.node_repo, "echo 2 > VERSION", "version 2")
    sc.run(
        f"noust update {LEGACY_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{new} noust update {LEGACY_DOMAIN} (the unit is the legacy one)",
    )
    wait_page(sc, LEGACY_DOMAIN, "ok 2", f"{new} after its update")
    commit_to(sc, seeded.bg_repo, "echo 2 > VERSION", "version 2")
    sc.run(
        f"noust update {BG_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{new} noust update {BG_DOMAIN} (without downtime)",
    )
    wait_page(sc, BG_DOMAIN, "ok 2", f"{new} after its update")
    wait_page(sc, EDGE_DOMAIN, "ok 2", f"{new} the operator's site after the update")
    commit_to(sc, seeded.stack_repo, "printf 'v2\\n' > VERSION", "version 2")
    sc.run(
        f"noust update {STACK_DOMAIN}",
        timeout=DEPLOY_TIMEOUT,
        label=f"{new} noust update {STACK_DOMAIN} (the relay)",
    )
    wait_page(sc, STACK_DOMAIN, "web v2", f"{new} after its update")
    sc.run("nginx -t", timeout=30, label=f"{new} nginx -t (after the updates)")

    # -- Wave 5: what the rename must leave ------------------------------------------------------------------
    units = sc.run(
        f"ls /etc/systemd/system | grep -E '^(wasm-)?{LEGACY_APP}.service$'",
        timeout=15,
        check=False,
        label=f"{new} the unit files of {LEGACY_DOMAIN}",
    ).stdout.split()
    wave5.expect(
        units == [f"{LEGACY_APP}.service"],
        "the legacy unit is renamed by the application's next deploy, and only by it",
        f"unit files: {units}",
    )
    generated = sc.run(
        "grep -rl 'Generated by Noust' /etc/nginx | xargs -r grep -l 'wasm_bg_' || true",
        timeout=15,
        check=False,
        label=f"{new} generated nginx files that still say wasm_bg_",
    ).stdout.split()
    wave5.expect(
        generated == [], "no file Noust generated still says wasm_bg_", f"still in {generated}"
    )
    operator = sc.run(
        f"grep -c '{BG_UPSTREAM}' {EDGE_SITE}",
        timeout=15,
        check=False,
        label=f"{new} the operator's site is as it was",
    ).stdout.strip()
    sc.check(operator == "1", "an operator's site was rewritten by the upgrade or by a deploy")
    tested = sc.run("nginx -t 2>&1", timeout=30, check=False, label=f"{new} nginx -t").returncode
    wave5.expect(tested == 0, "nginx still accepts the configuration with the operator's site")
    wave5.expect(
        EDGE_SITE in doctor.stdout and BG_UPSTREAM in doctor.stdout,
        "noust setup doctor names the operator's site and the line that names the old upstream",
    )
    roles = sc.run(
        "mysql -N -e \"SELECT User FROM mysql.user WHERE User LIKE '%\\_ro\\_%'\"",
        timeout=30,
        label=f"{new} the read-only accounts of the engine",
    ).stdout.split()
    wave5.expect(
        roles == [f"noust_ro_{LEGACY_DB}"], "the read-only role is renamed", f"roles: {roles}"
    )
    after = sc.run(
        f"noust backup run-schedule {LEGACY_DOMAIN}; noust backup remote-list {S3_DESTINATION} "
        f"--app {LEGACY_APP} --json",
        timeout=300,
        check=False,
        label=f"{new} a backup after the upgrade, and the destination's listing",
    )
    buckets = sc.run(
        f"docker exec {S3_NAME} sh -c 'ls /data'",
        timeout=30,
        label=f"{new} the buckets MinIO holds now",
    ).stdout.split()
    wave5.expect(
        "noust-backups" in buckets and S3_BUCKET in buckets,
        "the next backup goes to the new folder and the old one stays listed",
        f"buckets: {buckets}; {after.stdout[-200:]!r}",
    )
    wave5.expect(
        sc.run(
            "find /var/www/apps -maxdepth 3 -name '.wasm*' | wc -l",
            timeout=30,
            label=f"{new} files named .wasm* in the applications' trees",
        ).stdout.strip()
        == "0",
        "no application tree keeps a .wasm* file",
    )
    failed = wave5.report(sc)
    if failed:
        raise AssertionError("wave 5 left these undone:\n- " + "\n- ".join(failed))


def run_upgrade_33_mode(wheel: Path, *, keep: bool) -> int:
    """Run the 3.3.1 -> this tree rehearsal in its own container.

    Args:
        wheel: This tree's wheel, on the host.
        keep: Do not remove the container when done.

    Returns:
        0 when every hard check held (the wave 5 expectations that do not yet hold are reported,
        not failed), 1 otherwise.
    """
    started_at = time.monotonic()
    label = "upgrade_noust_3_3_1_with_every_legacy_name"
    name = run.random_container_name()
    sc = Scenario(container=name)
    failures = 0
    print(f"\n=== {label} ({name}) ===")
    try:
        try:
            start_container(name)
            wait_for_systemd(name)
            run.prepare_inner_docker(name)
            run.install_fixtures(name)
            install_from_git(name, FROM_TAG)
            run_upgrade_rehearsal(sc, wheel)
        except (AssertionError, HarnessError) as exc:
            failures += 1
            print(f"FAIL: {label}: {exc}")
        else:
            print(f"PASS: {label}")
    finally:
        if sc.evidence:
            print("--- evidence ---")
            print("\n\n".join(sc.evidence))
        run.docker_exec(name, f"docker rm -f {S3_NAME}", timeout=60, check=False)
        if keep:
            print(f"\n[teardown] --keep given, leaving container {name} running")
        else:
            print(f"\n[teardown] removing container {name}")
            run.remove_container(name)
    elapsed = time.monotonic() - started_at
    print(f"\n[summary] 1 scenario(s) run, {failures} failure(s), {elapsed:.1f}s total")
    return 1 if failures else 0


__all__ = ["WAVE5_DONE", "run_upgrade_33_mode"]
