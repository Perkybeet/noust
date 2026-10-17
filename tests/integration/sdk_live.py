#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The generated Python SDK (``sdk/python``) against a live Noust API (checklist J-18).

``tests/test_sdk_python.py`` holds the client to the schema and to the real application through a fake
transport. This is the other half: the same client over real HTTP, with real tokens, against the server the
Terraform provider is proved with (``scripts/console_server.py`` with its code host), doing what a script
does: make a team, deploy an application and wait for the job, change it, and see the refusals a script
meets - a token that may only look, a token that cannot enter sudo mode, a deployment the health gate
refuses - arrive as the exceptions the SDK documents, with the server's words in them.

Not collected by pytest: it needs a server. Run it by ``tests/integration/terraform_run.py --stage sdk``, or
by hand::

    NOUST_URL=... NOUST_TOKEN=... NOUST_TOKEN_NOSUDO=... NOUST_TOKEN_READ=... \\
        python -I tests/integration/sdk_live.py

or with no variables at all, when it starts a server of its own on this machine and issues the tokens
(:mod:`live_api`). Standard library only, like the SDK.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sys
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "sdk" / "python"))
sys.path.insert(0, str(HERE))

import live_api  # noqa: E402
from noust_sdk import APIError, Client, ElevationRequired, JobFailed  # noqa: E402

SITE = "https://code.example.net/acme/site.git"
SERVICE = "https://code.example.net/acme/service.git"


class Failure(AssertionError):
    """A check of this script failed."""


def check(condition: object, message: str) -> None:
    """
    Args:
        condition: What must be true.
        message: What is wrong when it is not.

    Raises:
        Failure: The condition is false.
    """
    if not condition:
        raise Failure(message)


def refusal(call: Callable[[], object], kind: type[APIError]) -> APIError:
    """
    Args:
        call: A call the server must refuse.
        kind: The exception class the SDK documents for the refusal.

    Returns:
        The exception, for the caller to read the server's words in.

    Raises:
        Failure: The call succeeded or raised something else.
    """
    try:
        call()
    except kind as error:
        return error
    except Exception as error:
        raise Failure(f"expected {kind.__name__}, got {type(error).__name__}: {error}") from error
    raise Failure(f"expected {kind.__name__}, the call succeeded")


def tokens_from_environment() -> dict[str, str] | None:
    """
    Returns:
        The profiles' tokens when the environment holds them, else None.
    """
    url, provider = os.environ.get("NOUST_URL"), os.environ.get("NOUST_TOKEN")
    if not url or not provider:
        return None
    return {
        "url": url,
        "provider": provider,
        "nosudo": os.environ.get("NOUST_TOKEN_NOSUDO", ""),
        "read": os.environ.get("NOUST_TOKEN_READ", ""),
    }


@contextlib.contextmanager
def credentials() -> Iterator[dict[str, str]]:
    """
    Yields:
        The server's address and a token per profile: from the environment, or from a server started here.
    """
    given = tokens_from_environment()
    if given is not None:
        yield given
        return
    permissions = [
        "apps.manage",
        "apps.operate",
        "apps.read",
        "backups.manage",
        "backups.read",
        "databases.manage",
        "databases.read",
        "databases.write",
        "fleet.read",
        "root_equivalent",
        "teams.manage",
        "teams.read",
    ]
    with live_api.native_server() as server:
        tokens = live_api.mint_tokens(server.url, server.master_token, permissions)
        yield {"url": server.url, **tokens}


# -- the scenarios ---------------------------------------------------------------------------------


def a_token_is_checked(c: dict[str, str]) -> None:
    client = Client(c["url"], token=c["provider"])
    check(client.authentication.verify_token()["valid"] is True, "the provider token is not valid")
    wrong = Client(c["url"], token="noust_tok_not-a-token")
    error = refusal(lambda: wrong.authentication.verify_token(), APIError)
    check(error.status == 401, f"a wrong token answered {error.status}, not 401")


def teams_projects_and_environments(c: dict[str, str]) -> None:
    client = Client(c["url"], token=c["provider"])
    slug = "sdk-" + secrets.token_hex(3)
    team = client.teams.create_team({"name": "SDK team", "slug": slug})
    check(team["slug"] == slug, f"team slug {team['slug']!r}")
    project = client.teams.create_project(slug, {"name": "Web", "slug": "web"})
    check(project["team"] == slug and project["slug"] == "web", f"project {project}")
    environment = client.teams.create_environment(
        slug, "web", {"name": "Staging", "slug": "staging"}
    )
    check(environment["slug"] == "staging", f"environment {environment}")
    check(
        client.teams.get_team(slug)["projects"] == 2,
        "a team holds its default project and the new one",
    )
    client.teams.delete_environment(slug, "web", "staging")
    client.teams.delete_project(slug, "web")
    client.teams.delete_team(slug)
    gone = refusal(lambda: client.teams.get_team(slug), APIError)
    check(
        gone.status == 404 and gone.code == "not_found",
        f"a deleted team answered {gone.status} {gone.code}",
    )


def deploy_change_and_delete_an_application(c: dict[str, str]) -> None:
    client = Client(c["url"], token=c["provider"])
    domain = f"sdk-{secrets.token_hex(3)}.example.net"
    queued = client.applications.create_app(
        {
            "domain": domain,
            "source": SERVICE,
            "branch": "main",
            "app_type": "nodejs",
            "env_vars": {"MODE": "blue"},
        }
    )
    job = client.wait_for_job(queued, interval=0.5)
    check(job["state"] == "succeeded", f"the deployment ended {job['state']}: {job.get('error')}")

    app = client.applications.get_app(domain)
    check(
        app["app_type"] == "nodejs" and app["layout"] == "releases",
        f"app {app['app_type']} {app['layout']}",
    )
    check(app["last_deployment"]["status"] == "success", "the last deployment is not a success")

    # Variables: a merge names what changed and never a value; a secret is redacted on the way out.
    merged = client.applications.merge_app_env(
        domain, {"set": {"API_KEY": "s3cr3t", "MODE": "green"}}
    )
    check(sorted(merged["changed"]) == ["API_KEY", "MODE"], f"changed {merged['changed']}")
    dry = client.applications.merge_app_env(domain, {"set": {"MODE": "green"}, "dry_run": True})
    check(dry["changed"] == [], f"a dry run of what is already there changed {dry['changed']}")
    env = client.applications.get_app_env(domain)["variables"]
    check(env["MODE"] == "green" and env["API_KEY"] != "s3cr3t", f"env {env}")

    # The branch is pinned on the remote's say-so, and an unknown one is refused with the server's words.
    pinned = client.applications.update_app_branch(domain, {"branch": "staging"})
    check(pinned["branch"] == "staging" and pinned["previous"] == "main", f"branch {pinned}")
    missing = refusal(
        lambda: client.applications.update_app_branch(domain, {"branch": "nope"}), APIError
    )
    check(
        missing.status == 400 and "nope" in str(missing),
        f"an unknown branch answered {missing.status}: {missing}",
    )

    # A name, a backup schedule and a deploy key.
    alias = f"alt-{domain}"
    added = client.domains.post_domain(domain, {"domain": alias, "kind": "alias"})
    check(any(d["domain"] == alias for d in added["domains"]), "the alias is not listed")
    if added.get("certificate_job_id"):
        client.wait_for_job(added["certificate_job_id"], interval=0.5)
    client.domains.delete_domain(domain, alias)
    schedule = client.backup_schedules.create_schedule(
        {"domain": domain, "schedule": "daily", "retention_count": 3}
    )
    check(schedule["schedule"]["retention_count"] == 3, f"schedule {schedule}")
    client.backup_schedules.delete_schedule(domain)
    key = client.applications.create_deploy_key(domain, {"name": "sdk-" + secrets.token_hex(2)})
    check(
        key["key"]["public_key"].startswith("ssh-ed25519 "), "the deploy key is not an ed25519 key"
    )
    client.applications.delete_deploy_key(domain)

    client.wait_for_job(client.applications.delete_app(domain, remove_files=True), interval=0.5)
    check(
        refusal(lambda: client.applications.get_app(domain), APIError).status == 404,
        "the app is still there",
    )


def a_database_is_made_and_dropped_by_a_job(c: dict[str, str]) -> None:
    client = Client(c["url"], token=c["provider"])
    name = "sdk_" + secrets.token_hex(3)
    made = client.databases.create_database({"name": name, "engine": "postgresql"})
    check(made["name"] == name and made["owner"], f"database {made}")
    client.wait_for_job(
        client.databases.drop_database("postgresql", name, keep_backup=False), interval=0.5
    )
    listed = client.databases.list_databases(engine="postgresql")["databases"]
    check(all(d["name"] != name for d in listed), "the database is still listed")


def a_registry_account_never_returns_its_password(c: dict[str, str]) -> None:
    client = Client(c["url"], token=c["provider"])
    name = "sdk-" + secrets.token_hex(3)
    made = client.registries.add_registry_login(
        {"name": name, "kind": "ghcr", "username": "deployer", "password": "do-not-return-me"}
    )
    check("password" not in made, "the account's password came back")
    check(made["server"] == "ghcr.io", f"server {made['server']}")
    client.registries.remove_registry_login(name)


def a_deployment_the_gate_refuses_is_a_job_failure_with_the_servers_words(
    c: dict[str, str],
) -> None:
    client = Client(c["url"], token=c["provider"])
    domain = f"sdk-bad-{secrets.token_hex(3)}.example.net"
    queued = client.applications.create_app(
        {
            "domain": domain,
            "source": SERVICE,
            "branch": "main",
            "app_type": "nodejs",
            "health_path": "/",
            "health_expect": "404",
            "health_timeout": 5,
        }
    )
    try:
        client.wait_for_job(queued, interval=0.5)
    except JobFailed as error:
        check(
            "did not pass its health check" in str(error), f"the failure was paraphrased: {error}"
        )
        check(error.job["state"] == "failed", f"job state {error.job['state']}")
    else:
        raise Failure("a deployment the gate refuses succeeded")
    check(
        refusal(lambda: client.applications.get_app(domain), APIError).status == 404,
        "a refused deployment left an application",
    )


def the_refusals_a_script_meets(c: dict[str, str]) -> None:
    reader = Client(c["url"], token=c["read"])
    check(reader.applications.list_apps()["total"] >= 0, "a token that may look cannot look")
    denied = refusal(lambda: reader.teams.create_team({"name": "No"}), APIError)
    check(
        denied.status == 403 and denied.code == "permission_denied",
        f"{denied.status} {denied.code}",
    )
    check(
        "teams.manage" in denied.detail,
        f"the refusal does not name the permission: {denied.detail}",
    )

    no_sudo = Client(c["url"], token=c["nosudo"])
    slug = "sdk-sudo-" + secrets.token_hex(3)
    no_sudo.teams.create_team({"name": "Sudo probe", "slug": slug})
    needs = refusal(lambda: no_sudo.teams.delete_team(slug), ElevationRequired)
    check(
        needs.status == 403 and needs.code == "elevation_required", f"{needs.status} {needs.code}"
    )
    Client(c["url"], token=c["provider"]).teams.delete_team(slug)


SCENARIOS = [
    a_token_is_checked,
    teams_projects_and_environments,
    deploy_change_and_delete_an_application,
    a_database_is_made_and_dropped_by_a_job,
    a_registry_account_never_returns_its_password,
    a_deployment_the_gate_refuses_is_a_job_failure_with_the_servers_words,
    the_refusals_a_script_meets,
]


def main() -> int:
    """
    Run every scenario and report each.

    Returns:
        0 when all passed.
    """
    failures = 0
    with credentials() as c:
        for scenario in SCENARIOS:
            try:
                scenario(c)
            except Exception:
                failures += 1
                print(f"FAIL  {scenario.__name__}")
                traceback.print_exc()
            else:
                print(f"PASS  {scenario.__name__}")
    print(f"{len(SCENARIOS) - failures}/{len(SCENARIOS)} scenarios passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
