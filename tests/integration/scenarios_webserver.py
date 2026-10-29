# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving applications between nginx and Apache, on a real machine (backlog item 105).

``python tests/integration/run.py --scenario webserver_move`` runs it: ``run.py`` imports this module
before it picks what to run. Real systemd, real nginx, real Apache 2.4, real PHP-FPM and a real
Node process; only certbot is a stand-in that signs its own certificates. Four applications (a
static site, a Node app, a PHP app and a static site with a certificate) are deployed behind nginx,
then:

- what stops a site is said with the command that fixes it (a Node app on releases is in
  zero-downtime mode, which only nginx supports), and the command runs as it is written;
- a plan says that Apache is not serving, so the move is a handover of ports 80 and 443; moving one
  application alone is refused with the command that moves all of them, and so is the setting;
- an application whose health check asks for something only the old site answered (a hand-edited
  route that nginx answers itself) does not answer through Apache: the move is **undone** and
  nginx serves all four exactly as before, with nothing of Apache's left behind;
- with that out of the way, all four move: Apache holds the ports (the ``Server`` header says so),
  nginx is stopped and no longer starts at boot, the store says where each one is, the certificate
  is presented by Apache and the plain port still redirects, and the setting of new applications
  can now change;
- and back: the four answer through nginx again, Apache is stopped.

Apache is installed with the distribution's package; a machine with no network skips the scenario
with the reason.
"""

from __future__ import annotations

import json
from typing import Any

import run
from run import (
    DEPLOY_TIMEOUT,
    GIT_IDENTITY,
    Scenario,
    ScenarioSkipped,
    journal_tail,
    make_node_repo,
    scenario,
)

STATIC = "mv-static.test"
NODE = "mv-node.test"
PHP = "mv-php.test"
TLS = "mv-tls.test"
DOMAINS = (STATIC, NODE, PHP, TLS)

#: What each application says at its front page.
EXPECTED = {
    STATIC: "Noust Integration Static Fixture",
    NODE: "ok 1",
    PHP: "php-ok",
}

#: A route only nginx answers: it is in the Node app's site and nowhere in its code.
NGINX_ONLY = "/nginx-only"


def install_apache(sc: Scenario) -> None:
    """
    Install Apache without letting the package start it, as a server whose nginx holds the ports.

    Args:
        sc: The scenario.

    Raises:
        ScenarioSkipped: The package cannot be installed (no network).
    """
    have = sc.run("command -v apache2", timeout=15, check=False, label="command -v apache2")
    if have.returncode != 0:
        # The package starts the service on install, and the port is nginx's: a policy file says no.
        sc.run(
            "printf '#!/bin/sh\\nexit 101\\n' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d",
            timeout=15,
            label="(keep the package from starting Apache)",
        )
        installed = sc.run(
            "apt-get update -qq >/dev/null 2>&1; "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq apache2 >/dev/null 2>&1",
            timeout=600,
            check=False,
            label="apt-get install -y apache2",
        )
        sc.run("rm -f /usr/sbin/policy-rc.d", timeout=15, label="(policy-rc.d removed)")
        if installed.returncode != 0:
            raise ScenarioSkipped("apache2 could not be installed in the server (no network?)")
    sc.run(
        "systemctl stop apache2; systemctl disable apache2 >/dev/null 2>&1; "
        "systemctl is-active apache2 || true",
        timeout=60,
        check=False,
        label="(Apache installed, stopped and not started at boot)",
    )


def answer(sc: Scenario, domain: str, path: str = "/") -> tuple[str, str, str]:
    """
    Ask the web server on this machine for a path of a site, as a visitor would.

    Args:
        sc: The scenario.
        domain: The site.
        path: The path.

    Returns:
        The status, the body and the ``Server`` header.
    """
    proc = sc.run(
        f"curl -sS -D /tmp/headers -o /tmp/body -w '%{{http_code}}' -H 'Host: {domain}' "
        f"http://127.0.0.1{path}; echo; cat /tmp/body; echo '--headers--'; "
        "grep -i '^server:' /tmp/headers | tr -d '\\r'",
        timeout=30,
        check=False,
        label=f"curl -H 'Host: {domain}' http://127.0.0.1{path}",
    )
    head, _, rest = proc.stdout.partition("\n")
    body, _, headers = rest.partition("--headers--")
    return head.strip(), body.strip(), headers.strip().removeprefix("Server:").strip().lower()


def check_all(sc: Scenario, server: str, what: str) -> None:
    """
    Check that every application answers with its own page, through the web server named.

    Args:
        sc: The scenario.
        server: ``nginx`` or ``apache`` (what the ``Server`` header says).
        what: Where in the scenario this is, for the failure.
    """
    for domain, needle in EXPECTED.items():
        status, body, header = answer(sc, domain)
        sc.check(status == "200", f"{what}: {domain} answered {status}: {body[:200]!r}")
        sc.check(needle in body, f"{what}: {domain} did not say {needle!r}: {body[:200]!r}")
        sc.check(
            header.startswith(server),
            f"{what}: {domain} was answered by {header!r}, not by {server}",
        )


def check_tls(sc: Scenario, server: str, what: str) -> None:
    """
    Check that the application with a certificate answers over TLS and redirects from the plain port.

    Args:
        sc: The scenario.
        server: ``nginx`` or ``apache`` (what the ``Server`` header says).
        what: Where in the scenario this is, for the failure.
    """
    proc = sc.run(
        f"curl -sSk -D /tmp/headers -o /tmp/body -w '%{{http_code}}' --resolve {TLS}:443:127.0.0.1 "
        f"https://{TLS}/; echo; cat /tmp/body; echo '--headers--'; "
        "grep -i '^server:' /tmp/headers | tr -d '\\r'",
        timeout=30,
        check=False,
        label=f"curl -k https://{TLS}/",
    )
    head, _, rest = proc.stdout.partition("\n")
    body, _, headers = rest.partition("--headers--")
    sc.check(head.strip() == "200", f"{what}: {TLS} over TLS answered {head!r}: {body[:200]!r}")
    sc.check("Noust Integration Static Fixture" in body, f"{what}: {TLS} said {body[:200]!r}")
    sc.check(
        headers.strip().lower().removeprefix("server:").strip().startswith(server),
        f"{what}: {TLS} over TLS was answered by {headers.strip()!r}, not by {server}",
    )
    plain = sc.run(
        f"curl -sS -o /dev/null -w '%{{http_code}} %{{redirect_url}}' -H 'Host: {TLS}' http://127.0.0.1/",
        timeout=30,
        check=False,
        label=f"curl -H 'Host: {TLS}' http://127.0.0.1/ (it redirects to HTTPS)",
    )
    sc.check(
        plain.stdout.strip() == f"301 https://{TLS}/",
        f"{what}: the plain port said {plain.stdout!r}",
    )


def status(sc: Scenario) -> dict[str, Any]:
    """
    Returns:
        ``noust webserver status --json``.
    """
    proc = sc.run(
        "noust webserver status --json", timeout=60, label="noust webserver status --json"
    )
    data: dict[str, Any] = json.loads(proc.stdout)
    return data


@scenario("webserver_move_between_servers")
def scenario_webserver_move(sc: Scenario) -> None:
    """A static, a Node and a PHP application move to Apache and back; one that cannot is put back."""
    run.require_cli(sc, "webserver migrate", what="moving applications between web servers")
    install_apache(sc)
    run.install_fake_certbot(sc)
    try:
        _move(sc)
    finally:
        run.remove_fake_certbot(sc)
        journal_tail(sc, "-u nginx -u apache2", "journalctl -u nginx -u apache2 (tail)")
        for domain in DOMAINS:
            sc.run(f"noust delete {domain} --force", timeout=180, check=False, label="cleanup")
        sc.run(
            "noust config set webserver nginx; systemctl stop apache2; "
            "systemctl disable apache2 >/dev/null 2>&1; systemctl enable nginx; "
            "systemctl start nginx; systemctl is-active nginx",
            timeout=120,
            check=False,
            label="(nginx serves again, Apache is stopped)",
        )


def _deploy(sc: Scenario) -> None:
    _, node_url = make_node_repo(sc, "mv-node")
    sc.run(
        "rm -rf /root/fixtures/mv-php && mkdir -p /root/fixtures/mv-php && "
        "cd /root/fixtures/mv-php && "
        'printf \'<?php echo "php-ok " . $_SERVER["REQUEST_URI"];\\n\' > index.php && '
        f"git init -q -b main && {GIT_IDENTITY} && git add -A && git commit -q -m 'php'",
        timeout=30,
        label="(fixture repo) /root/fixtures/mv-php: a PHP page",
    )
    for domain, source, kind in (
        (STATIC, "/root/fixtures/static-site", "static"),
        (NODE, node_url, "nodejs"),
        (PHP, "/root/fixtures/mv-php", "php-fpm"),
        (TLS, "/root/fixtures/static-site", "static"),
    ):
        # The last one gets a certificate (the harness has a certbot stand-in that signs its own).
        command = f"noust create -d {domain} -s {source} -t {kind}" + (
            "" if domain == TLS else " --no-ssl"
        )
        sc.run(command, timeout=DEPLOY_TIMEOUT, label=command)
    check_all(sc, "nginx", "before the move")
    check_tls(sc, "nginx", "before the move")


def _move(sc: Scenario) -> None:
    _deploy(sc)

    # -- What stops a site is said with the command that fixes it -----------------------------
    # A Node application on releases is deployed in zero-downtime mode, which only nginx supports.
    first = json.loads(
        sc.run(
            "noust webserver plan --to apache --all --json", timeout=60, label="plan --to apache"
        ).stdout
    )
    node = next(site for site in first["sites"] if site["domain"] == NODE)
    sc.check(first["blocked"] is True and node["movable"] is False, "the Node app cannot move yet")
    (blocker,) = node["blockers"]
    sc.check(blocker["code"] == "webserver_move.zero_downtime", f"why: {blocker}")
    sc.run(blocker["params"]["command"], timeout=180, label=blocker["params"]["command"])

    # -- The plan, and the half-way move that is refused --------------------------------------
    plan = json.loads(
        sc.run(
            "noust webserver plan --to apache --all --json", timeout=60, label="plan --to apache"
        ).stdout
    )
    sc.check(plan["mode"] == "handover", f"Apache is stopped, so it is a handover: {plan['mode']}")
    sc.check(plan["blocked"] is False, f"nothing should stop the move: {plan['blockers']}")
    sc.check({site["domain"] for site in plan["sites"]} == set(DOMAINS), "the plan is for all four")
    sc.check("proxy_fcgi" in plan["modules"], f"PHP needs proxy_fcgi: {plan['modules']}")
    sc.check("ssl" in plan["modules"], f"the certificate needs ssl: {plan['modules']}")

    partial = sc.run(
        f"noust webserver migrate --to apache {NODE} --yes",
        timeout=120,
        check=False,
        label=f"noust webserver migrate --to apache {NODE} --yes",
    )
    sc.check(partial.returncode != 0, "moving one application of a machine that must hand over")
    sc.check(
        "noust webserver migrate --to apache --all" in partial.stdout + partial.stderr,
        "the refusal should name the command that moves all of them",
    )
    guard = sc.run(
        "noust config set webserver apache",
        timeout=60,
        check=False,
        label="noust config set webserver apache",
    )
    sc.check(guard.returncode != 0, "the setting cannot change while the sites are behind nginx")
    sc.check(
        "noust webserver migrate --to apache --all" in guard.stdout + guard.stderr,
        "the refusal of the setting should name the command that moves them",
    )
    check_all(sc, "nginx", "after the refusals")
    check_tls(sc, "nginx", "after the refusals")

    # -- One that does not answer through Apache: everything is put back -----------------------
    _fails_on_purpose(sc)

    # -- All of them move ------------------------------------------------------------------------
    sc.run(
        "noust webserver migrate --to apache --all --yes",
        timeout=300,
        label="noust webserver migrate --to apache --all --yes",
    )
    check_all(sc, "apache", "behind Apache")
    check_tls(sc, "apache", "behind Apache")
    after = status(sc)
    sc.check(after["holds_ports"] == "apache", f"Apache holds the ports: {after}")
    sc.check(after["servers"]["apache"]["applications"] == 4, f"four behind Apache: {after}")
    sc.check(after["servers"]["nginx"]["running"] is False, "nginx is stopped")
    boot = sc.run(
        "systemctl is-enabled apache2; systemctl is-enabled nginx || true",
        timeout=30,
        check=False,
        label="systemctl is-enabled apache2 nginx",
    )
    sc.check(boot.stdout.split() == ["enabled", "disabled"], f"the next boot: {boot.stdout!r}")
    sc.check(
        sc.run(
            "ls /etc/nginx/sites-enabled /etc/nginx/sites-available | grep -c 'mv-' || true",
            timeout=30,
            check=False,
            label="(nothing of theirs is left in nginx's directories)",
        ).stdout.strip()
        == "0",
        "the old files were retired",
    )
    sc.run(
        "noust config set webserver apache", timeout=60, label="noust config set webserver apache"
    )

    # -- And back ------------------------------------------------------------------------------
    sc.run(
        "noust webserver migrate --to nginx --all --yes",
        timeout=300,
        label="noust webserver migrate --to nginx --all --yes",
    )
    check_all(sc, "nginx", "behind nginx again")
    check_tls(sc, "nginx", "behind nginx again")
    back = status(sc)
    sc.check(back["holds_ports"] == "nginx", f"nginx holds the ports again: {back}")
    sc.check(back["servers"]["nginx"]["applications"] == 4, f"four behind nginx: {back}")
    sc.run("noust config set webserver nginx", timeout=60, label="noust config set webserver nginx")


def _fails_on_purpose(sc: Scenario) -> None:
    """
    Make the Node application ask, as its health check, for a route only the old site answers.

    The route is in the application's nginx site (a hand edit, which a redeploy would replace and
    the site's history keeps) and the translation says it has no equivalent. The move is allowed
    to go ahead with that accepted, and Apache cannot answer it: the gate must notice.

    Args:
        sc: The scenario.
    """
    site = f"/etc/nginx/sites-available/{NODE}"
    sc.run(f"cp {site} /tmp/{NODE}.nginx", timeout=15, label=f"cp {site} (to put it back)")
    sc.run(
        f"sed -i 's|^    location / {{|    location = {NGINX_ONLY} {{ default_type text/plain; "
        f'return 200 "nginx only"; }}\\n    location / {{|\' {site} && nginx -t && '
        "systemctl reload nginx",
        timeout=60,
        label=f"(hand edit) {NODE}: a route only nginx answers",
    )
    status_code, body, _ = answer(sc, NODE, NGINX_ONLY)
    sc.check(status_code == "200", f"nginx answers its own route: {status_code} {body!r}")
    sc.run(
        f"noust app health {NODE} --path {NGINX_ONLY} --expect 200 --timeout 10",
        timeout=60,
        label=f"noust app health {NODE} --path {NGINX_ONLY} --expect 200",
    )

    plan = json.loads(
        sc.run(
            "noust webserver plan --to apache --all --json", timeout=60, label="plan --to apache"
        ).stdout
    )
    node = next(site for site in plan["sites"] if site["domain"] == NODE)
    sc.check(
        any(
            item["severity"] == "loses" and item["directive"] == "return"
            for item in node["findings"]
        ),
        f"the plan should say the return has no equivalent: {node['findings']}",
    )

    refused = sc.run(
        "noust webserver migrate --to apache --all --yes",
        timeout=120,
        check=False,
        label="noust webserver migrate --to apache --all --yes (without accepting the loss)",
    )
    sc.check(refused.returncode != 0, "the loss has to be accepted by name")
    check_all(sc, "nginx", "after a move that was not accepted")

    failed = sc.run(
        "noust webserver migrate --to apache --all --yes --accept-losses",
        timeout=300,
        check=False,
        label="noust webserver migrate --to apache --all --yes --accept-losses",
    )
    sc.check(failed.returncode != 0, "the move must fail: the Node application's check cannot pass")
    text = failed.stdout + failed.stderr
    sc.check(
        NODE in text and "did not answer" in text, f"it should name the application: {text[-600:]}"
    )

    # Everything is as it was: nginx serves the four, Apache nothing, the store the same.
    check_all(sc, "nginx", "after the move that failed")
    check_tls(sc, "nginx", "after the move that failed")
    sc.check(
        answer(sc, NODE, NGINX_ONLY)[0] == "200", "nginx still answers the route of its own site"
    )
    state = status(sc)
    sc.check(state["holds_ports"] == "nginx", f"nginx holds the ports: {state}")
    sc.check(state["servers"]["nginx"]["applications"] == 4, f"still four behind nginx: {state}")
    sc.check(state["servers"]["apache"]["applications"] == 0, f"none behind Apache: {state}")
    sc.check(state["servers"]["apache"]["running"] is False, "Apache is stopped")
    left = sc.run(
        "ls /etc/apache2/sites-available /etc/apache2/sites-enabled | grep -c 'mv-' || true",
        timeout=30,
        check=False,
        label="(nothing of theirs is left in Apache's directories)",
    )
    sc.check(left.stdout.strip() == "0", f"Apache's directories: {left.stdout!r}")
    boot = sc.run(
        "systemctl is-enabled nginx; systemctl is-enabled apache2 || true",
        timeout=30,
        check=False,
        label="systemctl is-enabled nginx apache2",
    )
    sc.check(boot.stdout.split() == ["enabled", "disabled"], f"the next boot: {boot.stdout!r}")

    # Put the Node application back as it was, for the move that goes through.
    sc.run(
        f"cp /tmp/{NODE}.nginx {site} && nginx -t && systemctl reload nginx && "
        f"noust app health {NODE} --path / --expect 200-399 --timeout 30",
        timeout=60,
        label="(the Node application is as it was)",
    )
