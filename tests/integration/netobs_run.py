#!/usr/bin/env python3
"""
Real-software harness for the network and monitoring changes of Noust 4.0: the web server that
falls, the access log with the time of each request, the ports a Compose stack holds, a
container that keeps restarting, and a channel's credentials at rest, against the real programs.

pytest proves these with fakes (tests/conftest.py refuses a real process), which proves that
Noust writes what it means to write and says nothing about whether nginx, Docker, systemd and
curl agree. This harness is the other half. It reuses the machinery of ``network_run.py`` (the
Debian 13 systemd container with nginx, an inner Docker daemon and Noust installed from the
wheel, and a client to ask from) under names of its own, and adds scenarios.

Containers (two at a time, on a network of their own, no published ports):

- ``noust-test-w6n-server``: Debian 13 with systemd, nginx, an inner Docker daemon and Noust.
- ``noust-test-w6n-client``: a visitor with curl and a second address on its interface.

Every name carries the prefix ``noust-test-w6n-`` and the label ``noust.test=1``. Nothing else on
the machine's Docker is read, stopped, started or removed: ``network_run.owned`` refuses any name
that is not ours, and teardown removes by exact name, never with ``prune``. The Compose stacks of
the scenarios live in the server's own inner Docker, which goes away with its volume.

The scenarios:

- ``webserver_watch``: nginx is killed (the monitor starts it and says so), broken (the monitor
  says its own test failed and starts nothing) and stopped on purpose (said once, left alone).
- ``timed_log_format``: the site Noust writes logs the time of each request, ``noust app traffic``
  reports the median and the 95th percentile and the visitors, an old-format line and an
  operator's site beside it break nothing, and Apache accepts the format it is given.
- ``ports_registry``: a second Compose stack asking for a port a first one publishes is refused
  by Noust, naming the first, before Docker says anything; a stack updated is not refused its own.
- ``container_restart_loop``: a container Docker keeps restarting is announced past the limit,
  and the Docker events show in the timeline.
- ``channel_secrets``: a webhook channel's URL, secret and headers are sealed in the file, and
  the delivery still carries the headers and a valid signature.

This file is excluded from pytest's collection (it does not match ``test_*.py``) and is not run
by ``run.py``.

Usage (from the repository root, with the repository's own venv):
    .venv/bin/python tests/integration/netobs_run.py
    .venv/bin/python tests/integration/netobs_run.py --scenario webserver_watch
    .venv/bin/python tests/integration/netobs_run.py --keep
    .venv/bin/python tests/integration/netobs_run.py --reuse --refresh --scenario ports_registry
    .venv/bin/python tests/integration/netobs_run.py --teardown

Evidence (every command and its output, per scenario) is written to ``--evidence-dir``
(``tests/integration/.build/netobs-evidence`` by default).
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

INTEGRATION_DIR = Path(__file__).resolve().parent


def _load_network_run() -> Any:
    """
    Load tests/integration/network_run.py as a module, without touching sys.path.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        "noust_network_run", INTEGRATION_DIR / "network_run.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {INTEGRATION_DIR / 'network_run.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


nr = _load_network_run()

# ---------------------------------------------------------------------------
# Names. network_run reads these module globals when it runs, so pointing them at
# ours is all it takes for every container, the network, the volume and the images
# to be this harness's and nobody else's.
# ---------------------------------------------------------------------------

PREFIX = "noust-test-w6n"
nr.PREFIX = PREFIX
nr.NETWORK = f"{PREFIX}-net"
nr.SERVER = f"{PREFIX}-server"
nr.CLIENT = f"{PREFIX}-client"
nr.AUTHELIA = f"{PREFIX}-authelia"
nr.OAUTH2 = f"{PREFIX}-oauth2"
nr.TRUST = f"{PREFIX}-trust"
nr.DOCKER_VOLUME = f"{PREFIX}-docker"
nr.SERVER_IMAGE = f"{PREFIX}-server:latest"
nr.CLIENT_IMAGE = f"{PREFIX}-client:latest"
nr.OWNED_CONTAINERS = (nr.CLIENT, nr.SERVER)

# Only this harness's scenarios: the ones network_run registered are not ours to run here.
nr.SCENARIOS[:] = []
scenario = nr.scenario
Env = nr.Env
ensure_app = nr.ensure_app
wait_for = nr.wait_for
put_file = nr.put_file
json_of = nr.json_of

APP = "netobs.test"
OPERATOR_SITE = "operator.test"
#: Where the recorder that stands for the receiver of a webhook listens, inside the server.
RECORDER_PORT = 9099
RECORDER_LOG = "/root/w6n/recorder.jsonl"
WEBHOOK_SECRET = "w6n-signing-secret"
WEBHOOK_HEADERS = "X-Netobs-Check: sealed\nAuthorization: Bearer w6n-token"
BUSYBOX = "busybox:1.37"

RECORDER = f"""\
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = "{RECORDER_LOG}"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace")
        with open(LOG, "a") as handle:
            handle.write(json.dumps({{"path": self.path, "headers": dict(self.headers), "body": body}}) + "\\n")
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", {RECORDER_PORT}), Handler).serve_forever()
"""


# ---------------------------------------------------------------------------
# Helpers the scenarios share
# ---------------------------------------------------------------------------


def recorder_running(env: Env) -> bool:
    """Whether the recorder answers on its port."""
    return (
        nr.docker(
            "exec",
            nr.SERVER,
            "bash",
            "-lc",
            "test -s /root/w6n/recorder.pid && kill -0 $(cat /root/w6n/recorder.pid)",
            check=False,
        ).returncode
        == 0
    )


def start_recorder(env: Env) -> None:
    """Start the receiver of the webhook, once, and empty its log."""
    env.srv("mkdir -p /root/w6n")
    put_file(nr.SERVER, "/root/w6n/recorder.py", RECORDER)
    if not recorder_running(env):
        env.srv(
            "setsid nohup python3 /root/w6n/recorder.py >/dev/null 2>&1 < /dev/null & "
            "echo $! > /root/w6n/recorder.pid",
            label="start the recorder",
        )
        wait_for(lambda: recorder_running(env), "the recorder to start", timeout=15)
    env.srv(f": > {RECORDER_LOG}", label="empty the recorder's log")


def stop_recorder(env: Env) -> None:
    """Stop the recorder by the process id it wrote, nothing else."""
    env.srv(
        "test -s /root/w6n/recorder.pid && kill $(cat /root/w6n/recorder.pid); true",
        check=False,
        label="stop the recorder",
    )


def received(env: Env) -> list[dict[str, Any]]:
    """What the recorder was sent, oldest first."""
    text = quiet(f"cat {RECORDER_LOG}")
    found: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.strip():
            found.append(json.loads(line))
    return found


def told(env: Env, code: str) -> list[dict[str, Any]]:
    """The deliveries whose body names ``code``."""
    return [item for item in received(env) if code in item["body"]]


def wait_told(env: Env, code: str, *, timeout: float = 120.0) -> list[dict[str, Any]]:
    """Wait until the receiver was told ``code``, and return what it was told."""
    wait_for(lambda: bool(told(env, code)), f"the notification {code}", timeout=timeout)
    sent = told(env, code)
    env.note(f"the receiver was told {code}: {sent[0]['body'][:600]}")
    return sent


def set_config(env: Env, key: str, value: str) -> None:
    """Set one configuration key through the command."""
    env.srv(f"noust config set {key} {value}", label=f"noust config set {key} {value}")


def webhook_to_recorder(env: Env, *, headers: str = "", secret: str = "") -> None:
    """
    Point the generic webhook channel at the recorder.

    Args:
        env: The environment.
        headers: The receiver's own headers, one per line, given on standard input.
        secret: The signing secret, given on standard input.
    """
    set_config(env, "notifications.enabled", "true")
    env.srv(
        "noust config set notifications.allow_private_hosts 127.0.0.1 --list",
        label="noust config set notifications.allow_private_hosts 127.0.0.1 --list",
    )
    set_config(
        env, "notifications.channels.webhook.webhook_url", f"http://127.0.0.1:{RECORDER_PORT}/hook"
    )
    if secret:
        env.srv(
            f"printf '%s' '{secret}' | noust config set notifications.channels.webhook.secret --stdin",
            label="noust config set notifications.channels.webhook.secret --stdin",
        )
    if headers:
        env.srv(
            f"printf '%s' '{headers}' | noust config set "
            "notifications.channels.webhook.webhook_headers --stdin",
            label="noust config set notifications.channels.webhook.webhook_headers --stdin",
        )


def quick_monitor(env: Env) -> None:
    """Make the monitor look every ten seconds and start it."""
    set_config(env, "monitor.scan_interval", "10")
    set_config(env, "monitor.webserver_check_seconds", "10")
    set_config(env, "monitor.container_check_seconds", "10")
    env.srv("noust monitor enable", label="noust monitor enable", timeout=90)
    wait_for(lambda: unit_state(env, "noust-monitor") == "active", "the monitor to run", timeout=30)


def forget_what_the_monitor_remembers(env: Env, *scopes: str) -> None:
    """
    Clear what the watches of the monitor remember between two runs of a scenario.

    The restart limit of the web server counts the last hour and an announced outage is not
    announced twice: a second run of a scenario would otherwise find the first one's state.

    Args:
        env: The environment.
        *scopes: The scopes of ``notification_state`` to forget.
    """
    quoted = ", ".join(f"'{scope}'" for scope in scopes)
    statement = "DELETE FROM notification_state WHERE scope IN (" + quoted + ")"  # noqa: S608
    env.srv(
        f'sqlite3 /var/lib/noust/noust.db "{statement}"',
        label="forget what the monitor remembered of an earlier run",
    )


def stop_monitor(env: Env) -> None:
    """Stop the monitor so a scenario starts from nothing."""
    env.srv("systemctl stop noust-monitor", check=False, label="stop the monitor")


def quiet(script: str) -> str:
    """
    Run a script in the server without adding it to the evidence.

    For the questions a scenario asks again and again while it waits: the evidence says what
    was waited for and how it ended, not every poll.

    Args:
        script: The script.

    Returns:
        Its standard output.
    """
    return nr.docker("exec", nr.SERVER, "bash", "-lc", script, check=False).stdout


def unit_state(env: Env, unit: str) -> str:
    """The active state of a unit (not kept in the evidence: it is asked while waiting)."""
    return quiet(f"systemctl is-active {unit}").strip()


def prepare_state(env: Env) -> None:
    """
    Give the server the state directory a package installs.

    The wheel is installed without the package's scripts, so /var/lib/noust does not exist and the
    first command that makes the store puts it under the user's home; the monitor's unit can only
    write /var/lib/noust, and would find no store. A server that installed the package has the
    directory, so the harness makes it and moves a store made before it.
    """
    env.srv(
        "mkdir -p /var/lib/noust; "
        "if [ -f /root/.local/share/noust/noust.db ] && [ ! -f /var/lib/noust/noust.db ]; then "
        "systemctl stop noust-monitor 2>/dev/null; "
        "cp -a /root/.local/share/noust/. /var/lib/noust/ && "
        "mv /root/.local/share/noust /root/.local/share/noust.moved; fi",
        label="give the server the state directory a package installs",
    )
    # The daemon that serves the fixtures dies with the container: a server that was stopped and
    # started again (--reuse after a pause) has none, and an application cannot be created from it.
    if (
        env.srv(f"git ls-remote --exit-code {nr.ECHO_URL} HEAD", check=False).returncode != 0
        and env.srv("test -d /root/fixtures/echo-app", check=False).returncode == 0
    ):
        env.srv(
            "git daemon --reuseaddr --export-all --base-path=/root/fixtures "
            "--listen=127.0.0.1 --detach /root/fixtures",
            label="start the fixtures' daemon again",
        )


def pull_busybox(env: Env) -> None:
    """Have the inner Docker hold the small image the stacks run."""
    if env.srv(f"docker image inspect {BUSYBOX}", check=False).returncode != 0:
        env.srv(f"docker pull -q {BUSYBOX}", timeout=300, label=f"docker pull {BUSYBOX}")


def stack_folder(env: Env, name: str, compose: str) -> str:
    """
    Write a Compose project into the server.

    Args:
        env: The environment.
        name: The folder's name under /root/w6n.
        compose: The text of its docker-compose.yml.

    Returns:
        The folder's path.
    """
    folder = f"/root/w6n/{name}"
    env.srv(f"rm -rf {folder} && mkdir -p {folder}")
    put_file(nr.SERVER, f"{folder}/docker-compose.yml", compose)
    return folder


def project_of(domain: str) -> str:
    """The Compose project Noust gives an application."""
    return nr.project_of(domain)


def drop_stack(env: Env, domain: str) -> None:
    """Delete an application, and the stack's leftovers in the inner Docker, from nothing."""
    if env.srv(f"noust status {domain}", check=False).returncode == 0:
        env.srv(
            f"noust delete {domain} -y", timeout=300, check=False, label=f"noust delete {domain}"
        )
    label = f"label=com.docker.compose.project={project_of(domain)}"
    env.srv(
        f"docker ps -aq --filter {label} | xargs -r docker rm -f; "
        f"docker volume ls -q --filter {label} | xargs -r docker volume rm",
        check=False,
        timeout=120,
        label=f"the leftovers of {domain} in the inner Docker",
    )


def web_stack(port: int) -> str:
    """A Compose file with one web service published on a loopback port."""
    return f"""\
services:
  web:
    image: {BUSYBOX}
    command: ["sh", "-c", "mkdir -p /www && echo ok > /www/index.html && exec httpd -f -p 8080 -h /www"]
    ports:
      - "127.0.0.1:{port}:8080"
"""


# ---------------------------------------------------------------------------
# The web server that falls
# ---------------------------------------------------------------------------


@scenario("webserver_watch")
def scenario_webserver_watch(env: Env) -> None:
    """nginx killed is started again; nginx broken is not, and the words are nginx's; stopped is left."""
    prepare_state(env)
    start_recorder(env)
    webhook_to_recorder(env)
    ensure_app(env, APP)
    forget_what_the_monitor_remembers(env, "webserver")
    quick_monitor(env)
    try:
        env.check(unit_state(env, "nginx") == "active", "nginx should be running before the test")

        env.note("1. the master process is killed: the unit fails, the monitor starts it again")
        env.srv("systemctl kill --signal=SIGKILL --kill-whom=all nginx", label="kill nginx")
        wait_for(
            lambda: unit_state(env, "nginx") == "active", "nginx to be started again", timeout=120
        )
        sent = wait_told(env, "webserver.restarted")
        env.check(len(sent) == 1, f"the restart should be told once, it was told {len(sent)} times")
        answer = nr.ask(env, APP, "/", extra="-o /dev/null -w '%{http_code}'")
        env.check(
            answer.stdout.strip() == "200", f"the site should answer again: {answer.stdout!r}"
        )
        audited = env.srv("noust audit list --json 2>/dev/null || true", check=False).stdout
        env.check(
            "monitor.webserver_restart" in audited,
            "the restart should be in the audit trail (monitor.webserver_restart)",
        )

        env.note(
            "2. its configuration is broken and it falls: nothing is restarted, nginx says why"
        )
        env.srv(
            "printf 'this is not an nginx directive;\\n' > /etc/nginx/conf.d/zz-w6n-broken.conf",
            label="write a broken configuration",
        )
        env.srv("systemctl kill --signal=SIGKILL --kill-whom=all nginx", label="kill nginx")
        sent = wait_told(env, "webserver.config_invalid")
        env.check(
            "unknown directive" in sent[0]["body"],
            "the notification should carry nginx's own words (unknown directive)",
        )
        time.sleep(35)  # three looks of the monitor: a restart would have happened by now
        env.check(
            unit_state(env, "nginx") != "active",
            "nginx must not be restarted while its configuration does not pass its own test",
        )
        env.srv(
            "rm -f /etc/nginx/conf.d/zz-w6n-broken.conf", label="remove the broken configuration"
        )
        env.srv("systemctl reset-failed nginx; systemctl start nginx", label="start nginx by hand")
        wait_for(lambda: unit_state(env, "nginx") == "active", "nginx to run again", timeout=30)
        wait_told(env, "unit.recovered", timeout=60)

        env.note("3. an operator stops it on purpose: said once, left alone")
        env.srv("systemctl stop nginx", label="systemctl stop nginx")
        wait_told(env, "webserver.stopped")
        time.sleep(35)
        env.check(
            unit_state(env, "nginx") == "inactive",
            "a web server stopped on purpose must stay stopped",
        )
        env.check(
            len(told(env, "webserver.stopped")) == 1,
            "a server stopped on purpose is announced once, not at every look",
        )
        env.srv("systemctl start nginx", label="systemctl start nginx")
        wait_for(lambda: unit_state(env, "nginx") == "active", "nginx to run again", timeout=30)
    finally:
        stop_monitor(env)
        env.srv("rm -f /etc/nginx/conf.d/zz-w6n-broken.conf; systemctl start nginx", check=False)


# ---------------------------------------------------------------------------
# The time of each request
# ---------------------------------------------------------------------------

OPERATOR_CONFIG = """\
# Written by hand: Noust did not make this site and never rewrites it.
server {
    listen 80;
    server_name operator.test;
    access_log /var/log/nginx/operator.test.access.log;
    location / {
        return 200 "the operator's site\\n";
    }
}
"""

#: The format an operator's site or a Noust older than this one writes: no time, no visitor key. The
#: date is filled in when it is appended, because the traffic of the last day is what is read.
OLD_FORMAT_LINE = (
    '198.51.100.7 - - [{when} +0000] "GET /old-format HTTP/1.1" 200 12 "-" "old-agent"\n'
)


def traffic_of(app: str) -> dict[str, Any] | None:
    """What ``noust app traffic`` reports for the last day, or None when it reports nothing yet."""
    out = quiet(f"noust app traffic {app} --window 24h --json 2>/dev/null")
    if not out.strip().startswith("{"):
        return None
    return json.loads(out)


@scenario("timed_log_format")
def scenario_timed_log_format(env: Env) -> None:
    """The log has the time of each request; the traffic shows p50, p95 and visitors; nothing else breaks."""
    prepare_state(env)
    # An application of its own at every run: the traffic of the last day is what is counted, and
    # a second run on the same name would add to the first one's.
    app = f"t{int(time.time())}.timed.test"
    ensure_app(env, app)
    config = env.srv(f"cat /etc/nginx/sites-available/{app}*", label="the site Noust wrote").stdout
    env.check("log_format noust_" in config, "the site should define its own log format")
    env.check("rt=$request_time" in config, "the format should carry the time of each request")
    env.srv("nginx -t", label="nginx -t on what Noust wrote")

    env.note("an operator's site next to it, with nginx's own format")
    put_file(nr.SERVER, f"/etc/nginx/sites-available/{OPERATOR_SITE}", OPERATOR_CONFIG)
    env.srv(
        f"ln -sf /etc/nginx/sites-available/{OPERATOR_SITE} /etc/nginx/sites-enabled/{OPERATOR_SITE} "
        "&& nginx -t && systemctl reload nginx",
        label="enable the operator's site",
    )
    before = traffic_of(app)
    already = before["totals"]["requests"] if before else 0

    # The monitor reads a log from its end the first time it sees it, so it starts first.
    quick_monitor(env)
    try:
        time.sleep(30)

        env.note("traffic: quick and slow requests from two addresses with different browsers")
        env.cli(
            f"for i in 1 2 3 4 5 6 7 8 9; do curl -s -o /dev/null -A browser-a -H 'Host: {app}' "
            f"http://{env.server_ip}/fast$i; done; "
            f"for i in 1 2; do curl -s -o /dev/null -A browser-b --interface {env.cdn_ip} "
            f"-H 'Host: {app}' http://{env.server_ip}/fast$i; done",
            label="eleven quick requests from two visitors",
        )
        env.cli(
            f"for i in 1 2 3; do curl -s -o /dev/null -A browser-a -H 'Host: {app}' "
            f"'http://{env.server_ip}/slow?ms=600'; done",
            label="three slow requests",
            timeout=60,
        )
        env.cli(
            f"curl -s -o /dev/null -H 'Host: {OPERATOR_SITE}' "
            f"http://{env.server_ip}/from-the-operator-site"
        )
        log = env.srv(f"tail -n 14 /var/log/nginx/{app}.access.log", label="the access log").stdout
        slow = [line for line in log.splitlines() if "/slow" in line]
        env.check(
            len(slow) == 3 and all(float(line.split(" rt=")[1].split()[0]) >= 0.5 for line in slow),
            f"the three slow requests should show more than half a second in the log: {slow}",
        )
        operator_log = env.srv(
            f"cat /var/log/nginx/{OPERATOR_SITE}.access.log", label="the operator's log"
        ).stdout
        env.check(
            "from-the-operator-site" in operator_log and " rt=" not in operator_log,
            "the operator's site keeps nginx's own format, with no time",
        )

        env.note("a line in the old format lands in the same log: it is counted and nothing breaks")
        when = env.srv("date -u +%d/%b/%Y:%H:%M:%S", label="the server's clock").stdout.strip()
        put_file(nr.SERVER, "/root/w6n/old-line.txt", OLD_FORMAT_LINE.format(when=when))
        env.srv(
            f"cat /root/w6n/old-line.txt >> /var/log/nginx/{app}.access.log",
            label="append an old-format line",
        )

        def counted() -> dict[str, Any] | None:
            data = traffic_of(app)
            return data if data and data["totals"]["requests"] - already >= 15 else None

        wait_for(lambda: counted() is not None, "the monitor to count the requests", timeout=120)
        found = counted()
        env.check(found is not None, "no traffic was counted")
        assert found is not None
        totals = found["totals"]
        env.check(
            totals["requests"] - already == 15,
            f"14 timed requests and 1 old line expected, got {totals['requests'] - already}",
        )
        env.check(totals["latency_p95_ms"] is not None, "the 95th percentile should be reported")
        env.check(
            totals["latency_p95_ms"] >= 400,
            f"3 of 14 timed requests took 600 ms: p95 {totals['latency_p95_ms']}",
        )
        env.check(
            totals["latency_p50_ms"] < 200,
            f"the median of mostly quick requests should be small: {totals['latency_p50_ms']}",
        )
        env.check(
            totals["visitors"] == 3,
            f"3 visitors (browser-a, browser-b, the old line's) were expected: {totals['visitors']}",
        )
        text = env.srv(f"noust app traffic {app} --window 24h", label="noust app traffic").stdout
        env.check(
            "p95" in text and "different" in text, "the command should say p95 and the visitors"
        )
        env.check(
            unit_state(env, "noust-monitor") == "active",
            "the monitor must have survived the old-format line",
        )
        journal = env.srv("journalctl -u noust-monitor --no-pager -n 200", check=False).stdout
        env.check(
            "Traceback" not in journal, "the monitor logged a traceback while reading the log"
        )
    finally:
        stop_monitor(env)
        env.srv(f"noust delete {app} -y", check=False, timeout=120, label="remove the application")
        env.srv(
            f"rm -f /etc/nginx/sites-enabled/{OPERATOR_SITE} /etc/nginx/sites-available/{OPERATOR_SITE}; "
            "nginx -t && systemctl reload nginx",
            check=False,
        )


@scenario("timed_log_format_apache")
def scenario_apache_log_format(env: Env) -> None:
    """The format an Apache vhost is given (rtus=%D, noust_<domain>) passes apache2ctl -t."""
    if (
        env.srv(
            "apt-get install -y -q apache2 >/dev/null 2>&1", check=False, timeout=600
        ).returncode
        != 0
    ):
        raise nr.ScenarioSkipped("apache2 could not be installed in the server (no network?)")
    env.srv("systemctl stop apache2; systemctl disable apache2", check=False)
    rendered = env.srv(
        "/opt/noust/bin/python - <<'PY'\n"
        "from noust.managers.apache_manager import ApacheManager\n"
        "text = ApacheManager().render_config('apache.test', 'proxy', {'port': 3000})\n"
        "print(text)\n"
        "PY",
        label="render an Apache vhost the way Noust does",
    ).stdout
    env.check(
        "LogFormat" in rendered and "rtus=%D" in rendered,
        "the vhost should define the timed format",
    )
    put_file(nr.SERVER, "/etc/apache2/sites-available/apache-test.conf", rendered)
    env.srv(
        "a2enmod -q proxy proxy_http headers rewrite ssl >/dev/null; "
        "a2ensite -q apache-test >/dev/null; apache2ctl -t",
        label="apache2ctl -t",
    )
    env.srv(
        "a2dissite -q apache-test; rm -f /etc/apache2/sites-available/apache-test.conf", check=False
    )


# ---------------------------------------------------------------------------
# The ports a stack holds
# ---------------------------------------------------------------------------


@scenario("ports_registry")
def scenario_ports_registry(env: Env) -> None:
    """A second stack asking for a port a first one publishes is refused, naming the first."""
    prepare_state(env)
    pull_busybox(env)
    for domain in ("ports-a.test", "ports-b.test"):
        drop_stack(env, domain)
    first = stack_folder(env, "stack-a", web_stack(18081))
    second = stack_folder(env, "stack-b", web_stack(18081))

    env.srv(
        f"noust create -d ports-a.test -s {first} -t docker-compose --port 18081 --no-ssl",
        timeout=600,
    )
    published = env.srv(
        f"docker ps --filter label=com.docker.compose.project={project_of('ports-a.test')} "
        "--format '{{.Names}} {{.Ports}}'",
        label="what the first stack publishes",
    ).stdout
    env.check(
        "127.0.0.1:18081->8080" in published, f"the first stack should hold 18081: {published}"
    )

    env.note("a second stack that asks for the same port")
    refused = env.srv(
        f"noust create -d ports-b.test -s {second} -t docker-compose --port 18082 --no-ssl",
        check=False,
        timeout=300,
    )
    said = refused.stdout + refused.stderr
    env.check(
        refused.returncode != 0, "a stack asking for a port another stack holds must be refused"
    )
    env.check("18081" in said, f"the refusal should name the port: {said}")
    env.check(
        "ports-a" in said,
        f"the refusal should name the stack that holds it (ports-a): {said}",
    )
    env.check(
        "already allocated" not in said and "address already in use" not in said,
        f"Noust should refuse before Docker does, not repeat Docker's own error: {said}",
    )
    stray = env.srv(
        f"docker ps -a --filter label=com.docker.compose.project={project_of('ports-b.test')} -q",
        label="containers of the refused stack",
    ).stdout.strip()
    env.check(not stray, f"nothing of the refused stack should have been started: {stray}")

    env.note("a stream asking for the same port is refused too")
    stream = env.srv("noust stream add w6n-clash --port 18081 --to 18081", check=False)
    said = stream.stdout + stream.stderr
    env.check(stream.returncode != 0, "a stream on a port a stack publishes must be refused")
    env.check("ports-a" in said, f"the stream refusal should name the stack: {said}")

    env.note("the stack that holds the port is not refused its own on an update")
    updated = env.srv("noust update ports-a.test", check=False, timeout=600)
    env.check(
        updated.returncode == 0 and "already" not in (updated.stdout + updated.stderr).lower(),
        f"updating the stack that holds the port must work: {updated.stdout} {updated.stderr}",
    )
    env.srv("noust ports --json >/dev/null 2>&1 || true", check=False)
    for domain in ("ports-a.test", "ports-b.test"):
        drop_stack(env, domain)


# ---------------------------------------------------------------------------
# A container that keeps restarting
# ---------------------------------------------------------------------------


@scenario("container_restart_loop")
def scenario_container_restart_loop(env: Env) -> None:
    """A container Docker keeps restarting is announced past the limit; the events are in the timeline."""
    prepare_state(env)
    pull_busybox(env)
    domain = "loop.test"
    drop_stack(env, domain)
    start_recorder(env)
    webhook_to_recorder(env)
    set_config(env, "monitor.container_restart_limit", "3")
    folder = stack_folder(
        env,
        "stack-loop",
        f"""\
services:
  worker:
    image: {BUSYBOX}
    # Healthy until a file appears; then it exits at once, and Docker restarts it under its
    # policy for as long as the file is there (a restart keeps the container's own filesystem).
    command: ["sh", "-c", "while true; do if [ -f /tmp/die ]; then exit 1; fi; sleep 1; done"]
    restart: always
""",
    )
    env.srv(f"noust create -d {domain} -s {folder} -t docker-compose --no-ssl", timeout=600)
    container = f"{project_of(domain)}-worker-1"
    forget_what_the_monitor_remembers(env, "containers")
    quick_monitor(env)
    try:
        time.sleep(25)  # the monitor records the container's restart count as its baseline
        env.note("the container starts to exit at once: Docker restarts it again and again")
        env.srv(f"docker exec {container} touch /tmp/die", label="make the container fail")

        def restarts() -> int:
            out = quiet(f"docker inspect -f '{{{{.RestartCount}}}}' {container}").strip()
            return int(out) if out.isdigit() else 0

        wait_for(lambda: restarts() >= 4, "Docker to restart the container four times", timeout=90)
        env.note(f"RestartCount is {restarts()}")
        sent = wait_told(env, "container_restart_loop", timeout=120)
        env.check(
            domain in sent[0]["body"] or container in sent[0]["body"],
            "the notice should name the stack",
        )
        timeline = json_of(
            env.srv(
                "/opt/noust/bin/python - <<'PY'\n"
                "import json, time\n"
                "from noust.managers.timeline import TimelineBuilder\n"
                "from noust.web.api.timeline import build_readers\n"
                "now = int(time.time())\n"
                f"built = TimelineBuilder(build_readers()).build(now - 3600, now, app='{domain}', "
                "sources=['monitor'])\n"
                "print(json.dumps([event.to_dict() for event in built.events]))\n"
                "PY",
                label="the timeline of the application, from the monitor's events",
            ),
            "the timeline",
        )
        docker_events = [event for event in timeline if event["kind"] == "docker_event"]
        env.check(
            len(docker_events) >= 3,
            f"Docker's events (die, start, restart) should be in the timeline: {timeline}",
        )
        env.check(
            all(event["app"] == domain for event in docker_events),
            "every Docker event should belong to the application whose stack it came from",
        )
    finally:
        stop_monitor(env)
        drop_stack(env, domain)


# ---------------------------------------------------------------------------
# What a channel keeps at rest
# ---------------------------------------------------------------------------


@scenario("channel_secrets")
def scenario_channel_secrets(env: Env) -> None:
    """URL, secret and headers of a channel are sealed in the file; the delivery still carries them."""
    prepare_state(env)
    start_recorder(env)
    webhook_to_recorder(env, headers=WEBHOOK_HEADERS, secret=WEBHOOK_SECRET)
    env.srv("noust config protect", label="noust config protect")
    on_disk = env.srv("cat /etc/noust/config.yaml", label="the configuration file").stdout
    env.check(
        WEBHOOK_SECRET not in on_disk and "w6n-token" not in on_disk,
        "the signing secret and the header value must not be readable in the file",
    )
    env.check(
        f"127.0.0.1:{RECORDER_PORT}/hook" not in on_disk, "the URL is a capability: sealed too"
    )
    env.check("noust-channel:v1:" in on_disk, "what is sealed should say so")
    mode = env.srv("stat -c '%a %U' /etc/noust/secrets.key", label="the key file").stdout.strip()
    env.check(mode == "600 root", f"the key must be 0600 and root's: {mode}")
    again = env.srv("noust config protect", label="noust config protect, again").stdout
    after = env.srv("cat /etc/noust/config.yaml").stdout
    env.check(after == on_disk, f"protecting twice must not change the file ({again!r})")

    shown = env.srv("noust config show", label="noust config show").stdout
    env.check(
        WEBHOOK_SECRET not in shown and "w6n-token" not in shown,
        "noust config show must never print a credential",
    )

    env.note("the sealed credentials are the ones used to deliver")
    start_recorder(env)
    env.srv("noust notify test webhook", label="noust notify test webhook", timeout=90)
    wait_for(lambda: bool(received(env)), "the test notification to arrive", timeout=60)
    delivery = received(env)[-1]
    headers = {key.lower(): value for key, value in delivery["headers"].items()}
    env.check(headers.get("x-netobs-check") == "sealed", f"the custom header is missing: {headers}")
    env.check(
        headers.get("authorization") == "Bearer w6n-token", "the Authorization header is missing"
    )
    expected = (
        "sha256="
        + hmac.new(WEBHOOK_SECRET.encode(), delivery["body"].encode(), hashlib.sha256).hexdigest()
    )
    signature = headers.get("x-noust-signature", "")
    env.check(
        signature in (expected, expected.removeprefix("sha256=")),
        f"the signature does not match the secret: {signature} (wanted {expected})",
    )

    env.note("a configuration read by a restart of the process still opens")
    env.srv("systemctl restart noust-web 2>/dev/null || true", check=False)
    env.srv("noust config show >/dev/null", label="read it again")


# ---------------------------------------------------------------------------
# A name that rests, and a site that answers over HTTP as well
# ---------------------------------------------------------------------------

SWITCHED_APP = "switched.test"
ALIAS = "alias.switched.test"
FORCE_APP = "plainhttp.test"


def serving(env: Env) -> str:
    """The whole configuration nginx loads: what it serves, not what a file says."""
    return env.srv("nginx -T 2>/dev/null", label="nginx -T").stdout


@scenario("domain_switched_off")
def scenario_domain_switched_off(env: Env) -> None:
    """A name switched off is gone from nginx and kept in the store; switched on, it is back."""
    prepare_state(env)
    ensure_app(env, SWITCHED_APP)
    env.srv(
        f"noust domain remove {SWITCHED_APP} {ALIAS}", check=False, label="(a re-run) start clean"
    )
    env.srv(f"noust domain add {SWITCHED_APP} {ALIAS} --no-dns")
    env.check(f"{ALIAS}" in serving(env), "nginx should serve the alias once it is added")
    answer = nr.ask(env, ALIAS, extra="-o /dev/null -w '%{http_code}'")
    env.check(answer.stdout.strip() == "200", f"the alias should answer: {answer.stdout!r}")

    env.srv(f"noust domain disable {SWITCHED_APP} {ALIAS}")
    env.srv("nginx -t", label="nginx accepts what Noust wrote without the name")
    env.check(ALIAS not in serving(env), "nginx must not serve a name that is switched off")
    listed = json_of(env.srv(f"noust domain list {SWITCHED_APP} --json"), "domain list --json")
    states = {item["domain"]: item["enabled"] for item in listed["items"]}
    env.check(states.get(ALIAS) is False, f"the name should be kept and off: {states}")
    env.check(states.get(SWITCHED_APP) is True, "the primary is untouched")
    text = env.srv(f"noust domain list {SWITCHED_APP}").stdout
    env.check("(off)" in text, f"the list should say which name is off:\n{text}")

    refused = env.srv(f"noust domain disable {SWITCHED_APP} {SWITCHED_APP}", check=False)
    env.check(refused.returncode != 0, "the primary domain cannot be switched off")
    env.check(
        SWITCHED_APP in nr.ask(env, SWITCHED_APP, extra="-o /dev/null -w '%{http_code}'").stdout
        or nr.ask(env, SWITCHED_APP, extra="-o /dev/null -w '%{http_code}'").stdout.strip()
        == "200",
        "the application itself keeps answering",
    )

    env.srv(f"noust domain enable {SWITCHED_APP} {ALIAS}")
    env.srv("nginx -t")
    env.check(ALIAS in serving(env), "nginx should serve the name again once it is switched on")
    env.srv(f"noust domain remove {SWITCHED_APP} {ALIAS}", check=False, label="clean up")


@scenario("https_not_forced")
def scenario_https_not_forced(env: Env) -> None:
    """With the policy off, the one nginx server answers on 80 and 443 and sends no HSTS."""
    prepare_state(env)
    # Before the site is written with its certificate: HSTS is part of what it renders.
    env.srv("noust config set ssl.hsts true", label="noust config set ssl.hsts true")
    nr.self_signed(env, "w6ncert", FORCE_APP)
    ensure_app(env, FORCE_APP)
    env.srv(f"noust cert use {FORCE_APP} --upload w6ncert")

    def plain() -> str:
        return env.cli(
            f"curl -s -o /dev/null -w '%{{http_code}} %{{redirect_url}}' -H 'Host: {FORCE_APP}' "
            f"http://{env.server_ip}/",
            check=False,
        ).stdout.strip()

    def secure_headers() -> str:
        return env.cli(
            f"curl -skI --resolve {FORCE_APP}:443:{env.server_ip} https://{FORCE_APP}/",
            check=False,
        ).stdout.lower()

    env.srv(
        f"noust app policy set {FORCE_APP} --force-https", label="(a re-run) back to the default"
    )
    sent = plain()
    env.check(sent.startswith("301 https://"), f"the default sends port 80 to HTTPS: {sent!r}")
    env.check(
        "strict-transport-security" in secure_headers(), "HSTS goes with a site that forces HTTPS"
    )

    env.srv(f"noust app policy set {FORCE_APP} --no-force-https")
    env.srv("nginx -t")
    served = plain()
    env.check(served.startswith("200"), f"port 80 should serve the application itself: {served!r}")
    headers = secure_headers()
    env.check(" 200" in headers.splitlines()[0], f"HTTPS must answer too: {headers!r}")
    env.check(
        "strict-transport-security" not in headers,
        "a site that also answers over HTTP must not tell browsers to use HTTPS only",
    )
    config = env.srv(f"cat /etc/nginx/sites-available/{FORCE_APP}*").stdout
    env.check(config.count("server {") == 1, "one server block answers on both ports")

    refused = env.srv(f"noust app policy set {FORCE_APP} --hsts on", check=False)
    env.check(
        refused.returncode != 0, "HSTS cannot be switched on for a site that also serves HTTP"
    )

    env.srv(f"noust app policy set {FORCE_APP} --force-https")
    env.check(plain().startswith("301 https://"), "putting it back redirects again")
    env.srv("noust config set ssl.hsts false", label="noust config set ssl.hsts false")


def main() -> int:
    """Run the harness with its own evidence folder."""
    if "--evidence-dir" not in sys.argv:
        sys.argv += ["--evidence-dir", str(INTEGRATION_DIR / ".build" / "netobs-evidence")]
    return int(nr.main())


if __name__ == "__main__":
    sys.exit(main())
