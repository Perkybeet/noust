"""
Real-machine scenarios for what Noust 3.4 added, which nothing had deployed for real.

They are scenarios of :mod:`run` (same container, same helpers, same report): ``run.py`` imports this
module before it picks what to run, so ``--scenario procfile`` selects one here like any other.
Each one drives the real CLI against real nginx, systemd and Docker, and cleans up after itself.

Docker: anything a scenario needs a container for runs in the harness container's own Docker daemon,
never the machine's.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from typing import Any

import run
from run import (
    DEPLOY_TIMEOUT,
    Scenario,
    active_release_of,
    commit_to,
    curl_host,
    journal_tail,
    json_of,
    make_node_repo,
    scenario,
    store_rows,
    wait_served,
)


def db_query(
    sc: Scenario,
    database: str,
    sql: str,
    *,
    engine: str,
    label: str,
    write: bool = False,
    check: bool = True,
) -> run.subprocess.CompletedProcess[str]:
    """
    Run one statement with ``noust db query``, quoted for the shell.

    The statement is passed whole and quoted here, so a call site holds the SQL as a literal and
    never builds it from parts.

    Args:
        sc: The scenario.
        database: The database.
        sql: One statement.
        engine: The engine's key, such as ``mysql`` or ``postgresql@pg16``.
        label: What it is for, in the evidence.
        write: Allow it to change data.
        check: Fail the scenario when the command fails.

    Returns:
        The finished command.
    """
    flags = f"--engine {engine}" + (" --write" if write else "")
    return sc.run(
        f"noust db query {database} {shlex.quote(sql)} {flags}",
        timeout=60,
        check=check,
        label=label,
    )


# ---------------------------------------------------------------------------------------------
# Process types: a Procfile beside the web process, and going back
# ---------------------------------------------------------------------------------------------

PROC_DOMAIN = "proc.test"
PROC_REPO = "proc-app"
PROC_FIXTURES = "/root/fixtures/procfile"

#: How long a worker gets to say its version after a deployment that restarts it.
PROC_SETTLE_SECONDS = 30


def worker_log(sc: Scenario, name: str, label: str) -> str:
    """The end of a process's journal, through the CLI an operator reads it with."""
    return sc.run(
        f"noust app process logs {PROC_DOMAIN} {name} -n 40",
        timeout=30,
        check=False,
        label=label,
    ).stdout


def wait_worker_version(sc: Scenario, name: str, version: str) -> None:
    """Wait until the newest line a process printed says it runs ``version``, or fail."""
    deadline = time.monotonic() + PROC_SETTLE_SECONDS
    lines: list[str] = []
    while time.monotonic() < deadline:
        proc = run.docker_exec(
            sc.container,
            f"noust app process logs {PROC_DOMAIN} {name} -n 10",
            timeout=30,
            check=False,
        )
        lines = [line for line in proc.stdout.splitlines() if "version" in line]
        if lines and lines[-1].rstrip().endswith(f"version {version}"):
            break
        time.sleep(1)
    sc.evidence.append(
        f"$ (until it says so) noust app process logs {PROC_DOMAIN} {name}\n"
        + "\n".join(lines[-3:])
    )
    sc.check(
        bool(lines) and lines[-1].rstrip().endswith(f"version {version}"),
        f"{name} does not run version {version}: its newest line is {lines[-1:] if lines else None}",
    )


def processes_of(sc: Scenario, label: str) -> dict[str, dict[str, Any]]:
    """``noust app process list --json``, by process name."""
    listing = json_of(
        sc.run(f"noust app process list {PROC_DOMAIN} --json", timeout=30, label=label),
        "app process list",
    )
    return {item["name"]: item for item in listing["processes"]}


def types_of(sc: Scenario, label: str) -> dict[str, dict[str, Any]]:
    """``noust app process-types show --json``, by process type."""
    report = json_of(
        sc.run(f"noust app process-types show {PROC_DOMAIN} --json", timeout=30, label=label),
        "app process-types show",
    )
    return {item["name"]: item for item in report["processes"]}


@scenario("procfile_process_types_and_going_back")
def scenario_procfile(sc: Scenario) -> None:
    """
    A Procfile's web, release and worker deploy together; a worker that cannot start puts the
    release back; going back declares the processes of the release it restores.

    The repository declares ``web``, ``release`` and ``worker``. The web process answers through
    nginx, the release process runs before the new version serves, and the worker is a unit of
    its own that follows the release. A version whose worker exits at once fails the health gate
    and leaves web and worker on the previous version. A later version swaps the worker for a
    clock; ``noust releases rollback`` brings the worker back and takes the clock away, because
    going back declares what the restored release declares.
    """
    run.require_cli(sc, "app process-types", what="process types from a Procfile (A-52)")
    repo, url = make_node_repo(sc, PROC_REPO)
    commit_to(
        sc,
        repo,
        f"cp {PROC_FIXTURES}/worker.js {PROC_FIXTURES}/clock.js {PROC_FIXTURES}/release.js . && "
        f"cp {PROC_FIXTURES}/Procfile.web-worker Procfile && echo 1 > VERSION",
        "a Procfile: web, release and a worker",
    )
    create = f"noust create -d {PROC_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases"
    try:
        created = sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
        wait_served(sc, PROC_DOMAIN, "ok 1")

        # -- What the repository declares, and what a deployment does with it ------------------
        types = types_of(sc, "what the Procfile declares and what the deployment uses")
        sc.check(
            set(types) == {"web", "release", "worker"}
            and all(item["in_effect"] for item in types.values()),
            f"process types as the deployment reads them: {types!r}",
        )
        started = processes_of(sc, "the processes beside the web one")
        sc.check(set(started) == {"worker"}, f"processes beside web: {sorted(started)}")
        sc.check(
            started["worker"]["source"] == "repository" and started["worker"]["running"] == 1,
            f"the worker is not the repository's, running once: {started['worker']!r}",
        )
        log = sc.run(
            f"cat $(ls -1t /root/.local/share/noust/deploy-logs/{PROC_DOMAIN}/*.log | head -1)",
            timeout=15,
            check=False,
            label="the deployment's log",
        ).stdout
        sc.check(
            "RELEASE_PROCESS_RAN for version 1" in log + created.stdout,
            "the release process did not run before the first version served",
        )
        wait_worker_version(sc, "worker", "1")
        # Updates without downtime (the default on releases) run the application as the
        # instances of a template.
        web_unit = (
            "proc-test@.service"
            if run.limits_unit(sc, "proc-test").endswith("@blue")
            else "proc-test.service"
        )
        unit = sc.run(
            f"systemctl cat {web_unit} | grep -E '^ExecStart='",
            timeout=15,
            label="the web unit's command",
        ).stdout
        sc.check("node server.js" in unit, f"the unit does not run the Procfile's web: {unit!r}")

        # -- An update restarts the worker on the new release --------------------------------
        before = active_release_of(sc, PROC_DOMAIN)
        commit_to(sc, repo, "echo 2 > VERSION", "version 2")
        update = f"noust update {PROC_DOMAIN}"
        sc.run(update, timeout=DEPLOY_TIMEOUT, label=update)
        wait_served(sc, PROC_DOMAIN, "ok 2")
        wait_worker_version(sc, "worker", "2")
        v2 = active_release_of(sc, PROC_DOMAIN)
        sc.check(v2 != before, f"current did not move: {before} -> {v2}")

        # -- A worker that cannot start puts the release back, web and worker alike -----------
        commit_to(
            sc,
            repo,
            f"echo 3 > VERSION && cp {PROC_FIXTURES}/broken-worker.js worker.js",
            "version 3: the worker cannot start",
        )
        failed = sc.run(
            update,
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label=f"{update} (the worker exits at once)",
        )
        sc.check(failed.returncode != 0, "an update whose worker cannot start reported success")
        sc.check(
            active_release_of(sc, PROC_DOMAIN) == v2,
            "current moved to a release whose worker never stayed up",
        )
        wait_served(sc, PROC_DOMAIN, "ok 2")
        wait_worker_version(sc, "worker", "2")
        sc.check(
            processes_of(sc, "the worker after the failed update")["worker"]["running"] == 1,
            "the worker is not running on the previous release after the failed update",
        )

        # -- A later version swaps the worker for a clock ----------------------------------
        commit_to(
            sc,
            repo,
            f"echo 4 > VERSION && git checkout -q HEAD~1 -- worker.js && "
            f"cp {PROC_FIXTURES}/Procfile.web-worker Procfile",
            "version 4: the worker is fixed",
        )
        sc.run(update, timeout=DEPLOY_TIMEOUT, label=f"{update} (the worker is fixed)")
        wait_served(sc, PROC_DOMAIN, "ok 4")
        wait_worker_version(sc, "worker", "4")
        v4 = active_release_of(sc, PROC_DOMAIN)

        commit_to(
            sc,
            repo,
            f"echo 5 > VERSION && cp {PROC_FIXTURES}/Procfile.web-clock Procfile",
            "version 5: a clock instead of the worker",
        )
        sc.run(update, timeout=DEPLOY_TIMEOUT, label=f"{update} (a clock instead of the worker)")
        wait_served(sc, PROC_DOMAIN, "ok 5")
        beside = processes_of(sc, "the processes after the Procfile changed")
        sc.check(
            set(beside) == {"clock"},
            f"a process dropped from the Procfile stays, or a new one is missing: {sorted(beside)}",
        )
        wait_worker_version(sc, "clock", "5")

        # -- Going back declares what the restored release declares ----------------------------
        back = f"noust releases rollback {PROC_DOMAIN} {v4.split('/')[-1]}"
        sc.run(back, timeout=DEPLOY_TIMEOUT, label=back)
        wait_served(sc, PROC_DOMAIN, "ok 4")
        returned = processes_of(sc, "the processes after going back to version 4")
        sc.check(
            set(returned) == {"worker"},
            f"going back did not declare the restored release's processes: {sorted(returned)}",
        )
        wait_worker_version(sc, "worker", "4")
        sc.check(
            returned["worker"]["running"] == 1, f"the worker does not run: {returned['worker']!r}"
        )

        # -- An operator's process of the same name always wins ----------------------------
        mine = f"noust app process set {PROC_DOMAIN} worker 'node worker.js' --instances 1"
        sc.run(mine, timeout=60, label=mine)
        sc.check(
            processes_of(sc, "the worker the operator defined")["worker"]["source"] == "operator",
            "a process the operator defines under the repository's name did not become theirs",
        )
        commit_to(sc, repo, "echo 6 > VERSION", "version 6")
        sc.run(update, timeout=DEPLOY_TIMEOUT, label=f"{update} (the operator's worker stays)")
        wait_served(sc, PROC_DOMAIN, "ok 6")
        sc.check(
            processes_of(sc, "the worker after another update")["worker"]["source"] == "operator",
            "an update took a worker over from the operator",
        )
    except AssertionError:
        journal_tail(sc, "-u 'proc-test*' -u 'noust*'", "journalctl (on failure)")
        raise
    finally:
        sc.run(
            f"noust delete {PROC_DOMAIN} -f",
            timeout=180,
            check=False,
            label="cleanup",
        )
    rows = store_rows(
        sc,
        "SELECT COUNT(*) AS n FROM apps WHERE domain = @domain",
        domain=PROC_DOMAIN,
        label="the application is gone",
    )
    sc.check(rows[0]["n"] == 0, "noust delete left the application in the store")
    leftover = sc.run(
        "systemctl list-units --all --no-legend 'proc-test*' | wc -l",
        timeout=15,
        label="units left behind",
    ).stdout.strip()
    sc.check(leftover == "0", f"{leftover} unit(s) of the application remain after noust delete")


# ---------------------------------------------------------------------------------------------
# Scheduled updates, pauses and freezes
# ---------------------------------------------------------------------------------------------

SCHED_DOMAIN = "sched.test"
SCHED_APP = "sched-test"
SCHED_REPO = "sched-app"
SCHED_TIMER = f"noust-update-{SCHED_APP}"


def schedule_state(sc: Scenario, label: str) -> dict[str, Any]:
    """``noust deploy schedule show --json``: the schedule and the timer."""
    shown = json_of(
        sc.run(f"noust deploy schedule show {SCHED_DOMAIN} --json", timeout=30, label=label),
        "deploy schedule show",
    )
    state: dict[str, Any] = shown
    return state


def last_outcome(sc: Scenario, label: str) -> str:
    """How the schedule's last run ended."""
    schedule = schedule_state(sc, label)["schedule"]
    return str(schedule["last_outcome"])


def archives_of(sc: Scenario, app: str, label: str) -> list[str]:
    """The ids of an application's local backup archives, oldest first."""
    proc = sc.run(
        f"find /var/backups/noust -name '{app}_*.tar.gz' -printf '%f\\n' | sort",
        timeout=15,
        label=label,
    )
    return [name.removesuffix(".tar.gz") for name in proc.stdout.split()]


@scenario("scheduled_update_pause_and_freeze")
def scenario_scheduled_updates(sc: Scenario) -> None:
    """
    An application updates itself from its timer, and a pause and a freeze hold it.

    The timer's own unit runs the update: nothing new does nothing, a new commit is built behind
    the health gate after the backup an update needs, a freeze holds the schedule and refuses a
    person unless they say it must go ahead (recorded), a pause holds what runs by itself and not
    a person, and resuming counts what arrived meanwhile.
    """
    run.require_cli(sc, "deploy schedule", what="scheduled updates (B-61)")
    repo, url = make_node_repo(sc, SCHED_REPO)
    create = f"noust create -d {SCHED_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases"
    try:
        sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
        wait_served(sc, SCHED_DOMAIN, "ok 1")
        # An update needs a backup of what it could not undo: the databases (a release keeps the
        # previous build on disk, which is the way back for the code). An application with none
        # needs no backup and an update takes none.
        sc.run(
            f"noust db provision {SCHED_DOMAIN} --engine mysql",
            timeout=300,
            label=f"noust db provision {SCHED_DOMAIN} --engine mysql",
        )
        wait_served(sc, SCHED_DOMAIN, "ok 1")

        # -- The schedule and its timer ----------------------------------------------------
        sc.run(
            f"noust deploy schedule set {SCHED_DOMAIN} --at '*-*-* 03:00'",
            timeout=60,
            label=f"noust deploy schedule set {SCHED_DOMAIN} --at '*-*-* 03:00'",
        )
        state = schedule_state(sc, "the schedule and its timer")
        sc.check(
            state["schedule"]["enabled"] and state["schedule"]["backup_first"],
            f"the schedule is not on with a backup first: {state!r}",
        )
        timer = sc.run(
            f"systemctl is-enabled {SCHED_TIMER}.timer; systemctl is-active {SCHED_TIMER}.timer; "
            f"systemctl cat {SCHED_TIMER}.service | grep -E '^ExecStart='",
            timeout=30,
            check=False,
            label="the timer is enabled and active, and its unit runs the scheduled update",
        )
        sc.check(
            timer.stdout.split()[:2] == ["enabled", "active"]
            and f"schedule run {SCHED_DOMAIN}" in timer.stdout,
            f"the update timer: {timer.stdout!r}",
        )

        # -- Nothing new: nothing happens --------------------------------------------------
        sc.run(
            f"systemctl start {SCHED_TIMER}.service",
            timeout=DEPLOY_TIMEOUT,
            label="the timer's unit runs with nothing new",
        )
        sc.check(
            last_outcome(sc, "how the run ended") == "nothing_new",
            "a scheduled run with nothing new did something else",
        )
        wait_served(sc, SCHED_DOMAIN, "ok 1")

        # -- A new commit: the backup an update needs, then the update ----------------------
        commit_to(sc, repo, "echo 2 > VERSION", "version 2")
        backups = archives_of(sc, SCHED_APP, "the backups before the scheduled update")
        sc.run(
            f"systemctl start {SCHED_TIMER}.service",
            timeout=DEPLOY_TIMEOUT,
            label="the timer's unit runs with a new commit",
        )
        wait_served(sc, SCHED_DOMAIN, "ok 2")
        sc.check(last_outcome(sc, "how the run ended") == "updated", "the update did not run")
        after = archives_of(sc, SCHED_APP, "the backups after the scheduled update")
        sc.check(
            len(after) == len(backups) + 1,
            f"a scheduled update takes the backup an update needs: {backups!r} -> {after!r}",
        )

        # -- A freeze holds the schedule, and a person too -----------------------------------
        sc.run(
            f"noust deploy freeze {SCHED_DOMAIN} --reason 'harness: nothing ships' --hours 1",
            timeout=30,
            label=f"noust deploy freeze {SCHED_DOMAIN} --hours 1",
        )
        commit_to(sc, repo, "echo 3 > VERSION", "version 3")
        sc.run(
            f"noust deploy schedule run {SCHED_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label="a scheduled run during the freeze",
        )
        sc.check(last_outcome(sc, "how the run ended") == "held", "a freeze did not hold the run")
        wait_served(sc, SCHED_DOMAIN, "ok 2")
        refused = sc.run(
            f"noust update {SCHED_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label="noust update during the freeze",
        )
        said = (refused.stdout + refused.stderr).lower()
        sc.check(
            refused.returncode != 0 and "freez" in said and "harness: nothing ships" in said,
            f"a person's update during a freeze: exit {refused.returncode}: {said[:300]!r}",
        )
        wait_served(sc, SCHED_DOMAIN, "ok 2")
        sc.run(
            f"noust update {SCHED_DOMAIN} --during-freeze",
            timeout=DEPLOY_TIMEOUT,
            label="noust update --during-freeze (on purpose, recorded)",
        )
        wait_served(sc, SCHED_DOMAIN, "ok 3")
        audited = json_of(
            sc.run(
                "noust audit list --action deploys.frozen_override --json",
                timeout=30,
                label="the override is in the audit trail",
            ),
            "audit list",
        )
        sc.check(
            len(audited["events"]) >= 1, "the update that went ahead of a freeze is not audited"
        )
        sc.run(f"noust deploy unfreeze {SCHED_DOMAIN}", timeout=30, label="noust deploy unfreeze")

        # -- A pause holds what runs by itself, not a person ---------------------------------
        sc.run(
            f"noust deploy pause {SCHED_DOMAIN} --reason 'harness: looking into it'",
            timeout=30,
            label=f"noust deploy pause {SCHED_DOMAIN}",
        )
        commit_to(sc, repo, "echo 4 > VERSION", "version 4")
        sc.run(
            f"noust deploy schedule run {SCHED_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label="a scheduled run while paused",
        )
        sc.check(last_outcome(sc, "how the run ended") == "held", "a pause did not hold the run")
        wait_served(sc, SCHED_DOMAIN, "ok 3")
        resumed = sc.run(
            f"noust deploy resume {SCHED_DOMAIN}", timeout=30, label="noust deploy resume"
        )
        sc.check(
            re.search(r"\b1\b", resumed.stdout) is not None,
            f"resuming does not say what arrived meanwhile: {resumed.stdout!r}",
        )
        sc.run(
            f"noust deploy schedule run {SCHED_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label="a scheduled run after resuming",
        )
        wait_served(sc, SCHED_DOMAIN, "ok 4")
        sc.check(last_outcome(sc, "how the run ended") == "updated", "the update did not run")

        # -- Without the backup first, none is taken -----------------------------------------
        sc.run(
            f"noust deploy schedule set {SCHED_DOMAIN} --at '*-*-* 03:00' --no-backup",
            timeout=60,
            label="noust deploy schedule set --no-backup",
        )
        commit_to(sc, repo, "echo 5 > VERSION", "version 5")
        backups = archives_of(sc, SCHED_APP, "the backups before the run without a backup")
        sc.run(
            f"noust deploy schedule run {SCHED_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label="a scheduled run without the backup first",
        )
        wait_served(sc, SCHED_DOMAIN, "ok 5")
        sc.check(
            archives_of(sc, SCHED_APP, "the backups after it") == backups,
            "--no-backup still took a backup",
        )

        # -- Removing the schedule removes the timer ------------------------------------------
        sc.run(
            f"noust deploy schedule remove {SCHED_DOMAIN}",
            timeout=30,
            label="noust deploy schedule remove",
        )
        gone = sc.run(
            f"systemctl is-enabled {SCHED_TIMER}.timer 2>&1; "
            f"ls /etc/systemd/system | grep -c '{SCHED_TIMER}' || true",
            timeout=15,
            check=False,
            label="the timer is gone",
        ).stdout
        sc.check(
            "enabled" not in gone.split() and gone.split()[-1] == "0",
            f"the timer of a removed schedule remains: {gone!r}",
        )
    except AssertionError:
        journal_tail(sc, f"-u '{SCHED_TIMER}*' -u '{SCHED_APP}*'", "journalctl (on failure)")
        raise
    finally:
        sc.run(
            f"noust deploy unfreeze {SCHED_DOMAIN}; noust deploy resume {SCHED_DOMAIN}; "
            f"noust deploy schedule remove {SCHED_DOMAIN}; noust delete {SCHED_DOMAIN} -f",
            timeout=180,
            check=False,
            label="cleanup",
        )


# ---------------------------------------------------------------------------------------------
# Deploy by git push
# ---------------------------------------------------------------------------------------------

PUSH_DOMAIN = "push.test"
PUSH_REPO = "push-app"
PUSH_KEY = "/root/.ssh/it-push"
PUSH_SSH = (
    f"ssh -i {PUSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no "
    "-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes"
)

#: How long a push's update gets to serve: the console queues a job, which builds a release.
PUSH_DEPLOY_SECONDS = 180


def wait_body(sc: Scenario, host: str, expected: str, *, seconds: int = PUSH_DEPLOY_SECONDS) -> str:
    """Wait up to ``seconds`` until nginx answers ``host`` with ``expected`` in the body."""
    deadline = time.monotonic() + seconds
    body = ""
    while time.monotonic() < deadline:
        body = run.docker_exec(
            sc.container,
            f"curl -sS -m 5 -H 'Host: {host}' http://127.0.0.1/",
            timeout=30,
            check=False,
        ).stdout.strip()
        if expected in body:
            break
        time.sleep(2)
    sc.evidence.append(f"$ (until it answers) curl -H 'Host: {host}' http://127.0.0.1/\n{body}")
    sc.check(expected in body, f"{host} answered {body!r}, not {expected!r}")
    return body


def git_push(
    sc: Scenario, repo: str, refspec: str, label: str
) -> run.subprocess.CompletedProcess[str]:
    """``git push noust <refspec>`` from a fixture repository, through the push account's key."""
    return sc.run(
        f"cd {repo} && GIT_SSH_COMMAND='{PUSH_SSH}' git push noust {refspec} 2>&1",
        timeout=120,
        check=False,
        label=label,
    )


@scenario("deploy_by_git_push")
def scenario_git_push(sc: Scenario) -> None:
    """
    ``git push`` to the server deploys: the hook tells the console, which builds behind the gate.

    ``noust push setup`` makes the account and sshd's rules for it; an application is given a
    repository, a hook and a key. A push of the branch is deployed, a push marked ``[skip ci]`` is
    not, a push to another branch is stored and says how to deploy it, a key the server does not
    know is refused, and the account runs nothing but git (a command that is not git's gets no
    shell). Turning it off takes the keys away.
    """
    run.require_cli(sc, "push", what="deploy by git push (A-23)")
    probe = run.docker_exec(sc.container, "command -v sshd git-shell", check=False)
    if probe.returncode != 0:
        raise run.ScenarioSkipped(
            "sshd or git-shell is not in the harness image (rebuild it from Dockerfile.systemd)"
        )
    repo, url = make_node_repo(sc, PUSH_REPO)
    try:
        sc.run(
            "systemctl start ssh.service 2>&1 || systemctl start sshd.service; "
            "for i in $(seq 1 20); do ss -ltnH '( sport = :22 )' | grep -q . && exit 0; "
            "sleep 1; done; exit 1",
            timeout=60,
            label="sshd runs and listens on 22",
        )
        sc.run("noust push setup", timeout=120, label="noust push setup")
        status = json_of(
            sc.run("noust push status --json", timeout=30, label="noust push status --json"),
            "push status",
        )
        sc.check(status.get("ready") is True, f"the server does not take pushes: {status!r}")
        sc.run(
            f"mkdir -p /root/.ssh && rm -f {PUSH_KEY} {PUSH_KEY}.pub && "
            f"ssh-keygen -q -t ed25519 -N '' -C laptop -f {PUSH_KEY} && "
            f"rm -f {PUSH_KEY}-stranger {PUSH_KEY}-stranger.pub && "
            f"ssh-keygen -q -t ed25519 -N '' -C stranger -f {PUSH_KEY}-stranger",
            timeout=30,
            label="two ssh keys: the laptop's, and a stranger's the server does not know",
        )

        create = f"noust create -d {PUSH_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases"
        sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
        wait_served(sc, PUSH_DOMAIN, "ok 1")
        run.wait_for_console(sc)
        enabled = json_of(
            sc.run(
                f"noust push enable {PUSH_DOMAIN} --key-file {PUSH_KEY}.pub --label laptop "
                "--host 127.0.0.1 --json",
                timeout=60,
                label=f"noust push enable {PUSH_DOMAIN} --key-file ... --label laptop",
            ),
            "push enable",
        )
        remote = str(enabled["remote"])
        sc.check(
            enabled["enabled"] and remote.startswith("noust-git@127.0.0.1:"),
            f"push is not on, or its remote is odd: {enabled!r}",
        )
        sc.check(enabled["hook_ok"], f"the hook is not as Noust writes it: {enabled!r}")
        sc.run(
            f"cd {repo} && git remote add noust {remote}",
            timeout=15,
            label=f"git remote add noust {remote}",
        )

        # -- A push of the branch is deployed -----------------------------------------------
        commit_to(sc, repo, "echo 2 > VERSION", "version 2")
        pushed = git_push(sc, repo, "main", "git push noust main")
        sc.check(pushed.returncode == 0, f"git push failed: {pushed.stdout!r}")
        sc.check(
            "queued the update" in pushed.stdout,
            f"the pusher is not told the update is queued: {pushed.stdout!r}",
        )
        wait_body(sc, PUSH_DOMAIN, "ok 2")
        state = json_of(
            sc.run(f"noust push show {PUSH_DOMAIN} --json", timeout=30, label="noust push show"),
            "push show",
        )
        sc.check(bool(state["last_push_at"]), "the push is not recorded")

        # -- [skip ci] is stored and not deployed ---------------------------------------------
        commit_to(sc, repo, "echo 3 > VERSION", "version 3 [skip ci]")
        skipped = git_push(sc, repo, "main", "git push noust main ([skip ci])")
        sc.check(skipped.returncode == 0, f"git push failed: {skipped.stdout!r}")
        sc.check(
            "nothing was deployed" in skipped.stdout,
            f"a [skip ci] push does not say it deployed nothing: {skipped.stdout!r}",
        )
        time.sleep(5)
        wait_served(sc, PUSH_DOMAIN, "ok 2")

        # -- Another branch is stored and says how to deploy it --------------------------------
        sc.run(f"cd {repo} && git branch feature", timeout=15, label="git branch feature")
        other = git_push(sc, repo, "feature", "git push noust feature")
        sc.check(other.returncode == 0, f"git push of another branch failed: {other.stdout!r}")
        wait_served(sc, PUSH_DOMAIN, "ok 2")

        # -- The next push of the branch carries the skipped commit with it ----------------------
        commit_to(sc, repo, "echo 4 > VERSION", "version 4")
        git_push(sc, repo, "main", "git push noust main")
        wait_body(sc, PUSH_DOMAIN, "ok 4")

        # -- A key the server does not know, and what the account is allowed to run --------------
        stranger = sc.run(
            f"cd {repo} && GIT_SSH_COMMAND='{PUSH_SSH.replace(PUSH_KEY, PUSH_KEY + '-stranger')}' "
            "git push noust main 2>&1",
            timeout=60,
            check=False,
            label="git push with a key the server does not know",
        )
        sc.check(stranger.returncode != 0, "a key nobody added could push")
        shell = sc.run(
            f"{PUSH_SSH} noust-git@127.0.0.1 'cat /etc/passwd' 2>&1",
            timeout=60,
            check=False,
            label="ssh noust-git@127.0.0.1 'cat /etc/passwd' (the account gets no shell)",
        )
        sc.check(
            "root:x:0:0" not in shell.stdout, "the push account ran a command that is not git's"
        )
        terminal = sc.run(
            f"{PUSH_SSH} -tt noust-git@127.0.0.1 2>&1 < /dev/null",
            timeout=60,
            check=False,
            label="ssh -tt noust-git@127.0.0.1 (no terminal)",
        )
        sc.check(
            "root:x:0:0" not in terminal.stdout and terminal.returncode != 0,
            f"the push account gave a terminal: {terminal.stdout!r}",
        )

        # -- Turning it off takes the keys away -------------------------------------------------
        sc.run(
            f"noust push disable {PUSH_DOMAIN}",
            timeout=60,
            label=f"noust push disable {PUSH_DOMAIN}",
        )
        commit_to(sc, repo, "echo 5 > VERSION", "version 5")
        after = git_push(sc, repo, "main", "git push noust main (push turned off)")
        sc.check(after.returncode != 0, "a push was accepted after push was turned off")
        wait_served(sc, PUSH_DOMAIN, "ok 4")
    except AssertionError:
        journal_tail(sc, "-u ssh -u sshd -u 'noust*' -u push-test", "journalctl (on failure)")
        raise
    finally:
        sc.run(
            f"noust web stop; noust push disable {PUSH_DOMAIN} --delete-repository; "
            f"noust delete {PUSH_DOMAIN} -f",
            timeout=180,
            check=False,
            label="cleanup",
        )


# ---------------------------------------------------------------------------------------------
# Code that arrives as an archive, and a pasted Dockerfile
# ---------------------------------------------------------------------------------------------

UPLOAD_DOMAIN = "upload.test"
UPLOAD_APP = "upload-test"
UPLOAD_DIR = "/root/it-uploads"


def upload_ids(sc: Scenario, label: str) -> dict[str, dict[str, Any]]:
    """``noust upload list --json``, by source."""
    listed = json_of(sc.run("noust upload list --json", timeout=30, label=label), "upload list")
    return {item["source"]: item for item in listed}


@scenario("uploaded_source_archive")
def scenario_uploaded_source(sc: Scenario) -> None:
    """
    Code that is in no repository: an archive is extracted once, deployed, replaced and refused
    when it is hostile.

    A tar.gz deploys an application; a zip with a wrapping directory is the next version
    (``noust app source change``, then ``noust update``); the first upload is no longer in use and
    can be removed, the second is in use and cannot. Archives with a path outside the project, an
    absolute one, a link out, a setuid file or a device are refused and leave nothing behind, and
    deleting the application removes the tree it deployed from.
    """
    run.require_cli(sc, "upload", what="uploaded sources (A-22)")
    sc.run(
        f"rm -rf {UPLOAD_DIR} && /usr/bin/python3 -I /root/fixtures/uploads/make_archives.py "
        f"/root/fixtures/node-app {UPLOAD_DIR}",
        timeout=60,
        label="(fixtures) a tar.gz, a zip with a wrapping directory, and six archives that are not "
        "what they seem",
    )
    try:
        # -- The hostile ones first: refused, and nothing is left -----------------------------
        for name in (
            "hostile-dotdot",
            "hostile-absolute",
            "hostile-link",
            "hostile-device",
            "not-an-archive",
        ):
            refused = sc.run(
                f"noust upload add {UPLOAD_DIR}/{name}.tar.gz",
                timeout=60,
                check=False,
                label=f"noust upload add {name}.tar.gz",
            )
            sc.check(refused.returncode != 0, f"{name} was accepted as the code of an application")
        sc.check(
            not upload_ids(sc, "nothing was kept of them"),
            "a refused archive left an upload behind",
        )
        outside = sc.run(
            "ls /tmp/it-absolute.txt /var/lib/noust/outside.txt /outside.txt 2>&1 | grep -vc 'No such'",
            timeout=15,
            check=False,
            label="nothing was written outside the project",
        ).stdout.strip()
        sc.check(outside == "0", "a hostile archive wrote outside its project")

        # A setuid file is not refused: its bit is dropped, so what is deployed cannot run as root.
        sc.run(
            f"noust upload add {UPLOAD_DIR}/hostile-setuid.tar.gz",
            timeout=60,
            label="noust upload add hostile-setuid.tar.gz (the bit is dropped, not the archive)",
        )
        found = sc.run(
            "find / -xdev -name su-me -printf '%m %p\\n' 2>/dev/null",
            timeout=60,
            label="the file as it was extracted",
        ).stdout.split()
        sc.check(
            len(found) == 2 and not int(found[0], 8) & 0o6000,
            f"the setuid file kept its bit, or was not extracted: {found!r}",
        )
        for item in upload_ids(sc, "the upload of the setuid file").values():
            sc.run(f"noust upload remove {item['source']}", timeout=30, label="noust upload remove")

        # -- An archive deploys an application -------------------------------------------------
        first = json_of(
            sc.run(
                f"noust upload add {UPLOAD_DIR}/app-1.tar.gz --json",
                timeout=120,
                label="noust upload add app-1.tar.gz",
            ),
            "upload add",
        )
        sc.check(first["source"].startswith("upload:"), f"the upload names no source: {first!r}")
        create = (
            f"noust create -d {UPLOAD_DOMAIN} --source {first['source']} -t nodejs --no-ssl "
            "--layout releases"
        )
        sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
        wait_served(sc, UPLOAD_DOMAIN, "ok 1")
        sc.check(
            upload_ids(sc, "the upload is in use")[first["source"]]["in_use"] is True,
            "the upload an application deploys from is not said to be in use",
        )

        # -- A zip with a wrapping directory is the next version -------------------------------
        second = json_of(
            sc.run(
                f"noust upload add {UPLOAD_DIR}/app-2.zip --json",
                timeout=120,
                label="noust upload add app-2.zip",
            ),
            "upload add",
        )
        sc.check(
            second["files"] >= 4,
            f"the zip was not extracted with its wrapping directory off: {second!r}",
        )
        sc.run(
            f"noust app source change {UPLOAD_DOMAIN} {second['source']} --yes",
            timeout=120,
            label="noust app source change ... (a new version of the code)",
        )
        wait_served(sc, UPLOAD_DOMAIN, "ok 1")
        sc.run(
            f"noust update {UPLOAD_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {UPLOAD_DOMAIN}",
        )
        wait_served(sc, UPLOAD_DOMAIN, "ok 2")

        # -- What is in use stays, what is not can go ---------------------------------------
        listing = upload_ids(sc, "the uploads after the change")
        sc.check(
            listing[first["source"]]["in_use"] is False and listing[second["source"]]["in_use"],
            f"which upload is in use: {listing!r}",
        )
        in_use = sc.run(
            f"noust upload remove {second['source']}",
            timeout=30,
            check=False,
            label="noust upload remove (the one in use)",
        )
        sc.check(in_use.returncode != 0, "an upload an application deploys from was removed")
        sc.run(
            f"noust upload remove {first['source']}",
            timeout=30,
            label="noust upload remove (unused)",
        )
        sc.check(
            list(upload_ids(sc, "what is left")) == [second["source"]],
            "removing an unused upload removed something else, or nothing",
        )
        mode = sc.run(
            f"stat -c '%a' /var/lib/noust/uploads/{second['id']} 2>/dev/null || "
            f"find / -maxdepth 6 -type d -name '{second['id']}' -exec stat -c '%a' {{}} \\; 2>/dev/null | head -1",
            timeout=30,
            check=False,
            label="the extracted tree is root's alone",
        ).stdout.strip()
        sc.check(mode in ("700", ""), f"the extracted tree is {mode}, not 0700")

        # -- Deleting the application removes the tree it deployed from ---------------------------
        sc.run(
            f"noust delete {UPLOAD_DOMAIN} -f", timeout=180, label=f"noust delete {UPLOAD_DOMAIN}"
        )
        sc.check(
            second["source"] not in upload_ids(sc, "after the application is deleted"),
            "the tree of a deleted application is still listed",
        )
    except AssertionError:
        journal_tail(sc, f"-u '{UPLOAD_APP}*' -u 'noust*'", "journalctl (on failure)")
        raise
    finally:
        sc.run(
            f"noust delete {UPLOAD_DOMAIN} -f; noust upload prune; rm -rf {UPLOAD_DIR}",
            timeout=180,
            check=False,
            label="cleanup",
        )


PASTE_DOMAIN = "paste.test"
PASTE_PORT = 18213
PASTE_IMAGES = "noust-ctr-paste-test"
PASTE_DOCKERFILE = (
    "FROM nginx:alpine\\n"
    "RUN echo 'pasted v{version}' > /usr/share/nginx/html/index.html\\n"
    "EXPOSE 80\\n"
)


@scenario("pasted_dockerfile_container")
def scenario_pasted_dockerfile(sc: Scenario) -> None:
    """
    A Dockerfile and nothing else is an application: pasted, stored, built and served.

    The text goes to the server as a project of one file (``noust upload add --dockerfile -``),
    which is the source of a container application. A new Dockerfile is a new upload and a
    change of source; a text with no ``FROM``, or an empty one, is refused before anything is
    stored; the build context holds nothing else.
    """
    run.require_cli(sc, "upload", what="a pasted Dockerfile (A-22)")
    run.require_cli(sc, "app container", what="container applications")
    run.require_docker(sc)
    run.pull_images(sc, run.CONTAINER_BASE)
    try:
        for bad, why in (("", "empty"), ("RUN echo no-from\\n", "no FROM")):
            refused = sc.run(
                f"printf '{bad}' | noust upload add - --dockerfile",
                timeout=30,
                check=False,
                label=f"a pasted Dockerfile that is {why}",
            )
            sc.check(refused.returncode != 0, f"a pasted Dockerfile with {why} was accepted")
        sc.check(not upload_ids(sc, "nothing was stored"), "a refused Dockerfile was stored")

        first = json_of(
            sc.run(
                f"printf '{PASTE_DOCKERFILE.format(version=1)}' | noust upload add - --dockerfile --json",
                timeout=60,
                label="noust upload add - --dockerfile (pasted v1)",
            ),
            "upload add",
        )
        sc.check(first["files"] == 1, f"a pasted Dockerfile is a project of one file: {first!r}")
        sc.run(
            f"noust create -d {PASTE_DOMAIN} --source {first['source']} -t container "
            f"-p {PASTE_PORT} --no-ssl",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust create -d {PASTE_DOMAIN} --source {first['source']} -t container",
        )
        wait_served(sc, PASTE_DOMAIN, "pasted v1")
        shown = json_of(
            sc.run(
                f"noust app container show {PASTE_DOMAIN} --json",
                timeout=30,
                label="noust app container show",
            ),
            "app container show",
        )
        sc.check(shown["method"] == "dockerfile", f"the method: {shown['method']!r}")

        second = json_of(
            sc.run(
                f"printf '{PASTE_DOCKERFILE.format(version=2)}' | noust upload add - --dockerfile --json",
                timeout=60,
                label="noust upload add - --dockerfile (pasted v2)",
            ),
            "upload add",
        )
        sc.run(
            f"noust app source change {PASTE_DOMAIN} {second['source']} --yes",
            timeout=120,
            label="noust app source change (the new text)",
        )
        sc.run(
            f"noust update {PASTE_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {PASTE_DOMAIN}",
        )
        wait_served(sc, PASTE_DOMAIN, "pasted v2")
        sc.run(
            f"noust releases rollback {PASTE_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label="noust releases rollback (the image of the first text)",
        )
        wait_served(sc, PASTE_DOMAIN, "pasted v1")

        # A COPY finds nothing: the context holds the Dockerfile alone.
        third = json_of(
            sc.run(
                "printf 'FROM nginx:alpine\\nCOPY index.html /usr/share/nginx/html/\\nEXPOSE 80\\n' "
                "| noust upload add - --dockerfile --json",
                timeout=60,
                label="noust upload add - --dockerfile (a COPY of a file that is not there)",
            ),
            "upload add",
        )
        sc.run(
            f"noust app source change {PASTE_DOMAIN} {third['source']} --yes",
            timeout=120,
            label="noust app source change (a text whose COPY finds nothing)",
        )
        failed = sc.run(
            f"noust update {PASTE_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            check=False,
            label="noust update (the build cannot COPY)",
        )
        sc.check(failed.returncode != 0, "a build that cannot COPY was reported as an update")
        wait_served(sc, PASTE_DOMAIN, "pasted v1")
    finally:
        run.container_clean(sc, PASTE_DOMAIN, PASTE_IMAGES)
        sc.run("noust upload prune", timeout=60, check=False, label="noust upload prune")


# ---------------------------------------------------------------------------------------------
# Backups to an S3-compatible destination (MinIO), and the restore from it
# ---------------------------------------------------------------------------------------------

S3_DOMAIN = "s3bk.test"
S3_APP = "s3bk-test"
S3_NAME = "noust-it-minio"
S3_PORT = 19000
S3_BUCKET = "noust-it"
S3_USER = "noustit"
#: The release the machine's other work pins; pulled once into the inner Docker's volume.
S3_IMAGE = "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"


@scenario("backups_to_s3_and_restore")
def scenario_s3_backups(sc: Scenario) -> None:
    """
    Scheduled backups go to an S3 bucket (MinIO) encrypted, with retention, and come back.

    MinIO runs as a container of the harness container's own Docker, published on its loopback
    only. The destination is refused until ``security.allow_private_hosts`` names the address (a
    decision, not a default); three scheduled runs leave two backups in the bucket and one local;
    nothing readable is in the bucket (names and bodies are rclone crypt's); a backup is listed
    and restored from the bucket after the page was tampered with; the secret key is in the
    secret store only; and a bucket that stops answering fails the destination test without
    printing the key.
    """
    run.require_docker(sc)
    run.pull_images(sc, S3_IMAGE)
    secret = run.secrets.token_urlsafe(18)
    create = f"noust create -d {S3_DOMAIN} -s /root/fixtures/static-site -t static --no-ssl"
    findings: list[str] = []
    try:
        sc.run(create, timeout=DEPLOY_TIMEOUT, label=create)
        sc.run(
            f"docker rm -f {S3_NAME} >/dev/null 2>&1; docker run -d --name {S3_NAME} "
            f"-p 127.0.0.1:{S3_PORT}:9000 -e MINIO_ROOT_USER={S3_USER} "
            f"-e MINIO_ROOT_PASSWORD={secret} {S3_IMAGE} server /data",
            timeout=120,
            label=f"docker run -d --name {S3_NAME} {S3_IMAGE} server /data (loopback only)",
        )
        sc.run(
            f"for i in $(seq 1 60); do curl -fsS http://127.0.0.1:{S3_PORT}/minio/health/ready "
            "&& exit 0; sleep 1; done; exit 1",
            timeout=90,
            label="wait for MinIO to be ready",
        )

        add = (
            f"printf '%s' '{secret}' | noust backup destination add itminio --type s3 "
            f"--field provider=Minio --field endpoint=http://127.0.0.1:{S3_PORT} "
            f"--field access_key_id={S3_USER} --field path={S3_BUCKET}/server --stdin"
        )
        shown = add.replace(secret, "<secret key>")
        refused = sc.run(
            add, timeout=60, check=False, label=shown + "  (before security.allow_private_hosts)"
        )
        sc.check(
            refused.returncode != 0 and "allow_private_hosts" in refused.stdout + refused.stderr,
            f"an S3 endpoint on the machine's own network was not refused with the way to allow "
            f"it: {refused.stdout!r} {refused.stderr!r}",
        )
        sc.run(
            "noust config set security.allow_private_hosts 127.0.0.1 --list",
            timeout=30,
            label="noust config set security.allow_private_hosts 127.0.0.1 --list",
        )
        sc.run(add, timeout=60, label=shown)
        sc.run(
            "noust backup destination test itminio",
            timeout=120,
            label="noust backup destination test itminio (the bucket does not exist yet)",
        )
        stored = sc.run(
            f"grep -rl '{secret}' /var/lib/noust /root/.local/share/noust /etc/noust 2>/dev/null; "
            f"ps -eo args | grep -c '{secret}' || true",
            timeout=30,
            check=False,
            label="where the secret key is stored in clear (the secret store only)",
        )
        sc.check(
            all("secrets" in line or line.strip().isdigit() for line in stored.stdout.splitlines()),
            f"the S3 secret key is stored in clear outside the secret store: {stored.stdout!r}",
        )

        # -- Three runs, two kept in the bucket, one kept here ----------------------------
        sc.run(
            f"noust backup schedule create {S3_DOMAIN} --schedule daily --retention-count 1 "
            "--destination itminio:2",
            timeout=60,
            label="noust backup schedule create ... --destination itminio:2",
        )
        for number in range(1, 4):
            sc.run(
                f"noust backup run-schedule {S3_DOMAIN}",
                timeout=300,
                label=f"noust backup run-schedule {S3_DOMAIN} (run {number} of 3)",
            )
        remote = json_of(
            sc.run(
                f"noust backup remote-list itminio --app {S3_APP} --json",
                timeout=120,
                label=f"noust backup remote-list itminio --app {S3_APP} --json",
            ),
            "remote-list",
        )
        ids = run.remote_ids(remote)
        sc.check(len(ids) == 2, f"the bucket should keep 2 backups of {S3_APP}: {ids!r}")
        local = archives_of(sc, S3_APP, "the local archives")
        sc.check(
            local == [ids[-1]], f"local retention 1 keeps the newest ({ids[-1]}) only: {local!r}"
        )

        # -- What the bucket holds is encrypted: names and bodies ---------------------------
        objects = sc.run(
            f"docker exec {S3_NAME} sh -c 'find /data/{S3_BUCKET} -type f | sort'",
            timeout=30,
            label="every object in the bucket, as MinIO stores it",
        ).stdout
        sc.check(
            "xl.meta" in objects and S3_APP not in objects and "tar.gz" not in objects,
            f"the bucket holds readable names, or nothing at all: {objects[:400]!r}",
        )

        # -- Restore from the bucket --------------------------------------------------------
        root = sc.run(
            f"readlink -e /var/www/apps/{S3_APP}/current || echo /var/www/apps/{S3_APP}",
            timeout=15,
            label="the served tree",
        ).stdout.strip()
        sc.run(f"echo tampered > {root}/index.html", timeout=15, label="tamper with the page")
        sc.check(curl_host(sc, S3_DOMAIN).strip() == "tampered", "the tampered page is not served")
        restore = f"noust backup restore {ids[-1]} --from itminio -f"
        sc.run(restore, timeout=300, label=restore)
        page = curl_host(sc, S3_DOMAIN)
        sc.check(
            "Noust Integration Static Fixture" in page,
            f"restoring from the bucket did not bring the page back: {page[:200]!r}",
        )

        # -- A bucket that stops answering ----------------------------------------------------
        sc.run(f"docker stop -t 1 {S3_NAME}", timeout=60, label=f"docker stop {S3_NAME}")
        down = sc.run(
            "noust backup destination test itminio",
            timeout=300,
            check=False,
            label="noust backup destination test itminio (the bucket is down)",
        )
        sc.check(down.returncode != 0, "the test of an unreachable bucket succeeded")
        sc.check(
            secret not in down.stdout + down.stderr,
            "the secret key was printed by a failing destination test",
        )
        sc.check(not findings, "product findings:\n- " + "\n- ".join(findings))
    finally:
        sc.run(
            f"noust backup schedule delete {S3_DOMAIN}; "
            "noust backup destination remove itminio -f; "
            f"noust delete {S3_DOMAIN} -f; docker rm -f {S3_NAME}",
            timeout=180,
            check=False,
            label="cleanup",
        )


# ---------------------------------------------------------------------------------------------
# Database engines Noust runs itself: instances
# ---------------------------------------------------------------------------------------------

DBI_NAME = "pg16"
DBI_ENGINE = f"postgresql@{DBI_NAME}"
DBI_DATABASE = "shop"


def instance_row(sc: Scenario, name: str, label: str) -> dict[str, Any]:
    """``noust db instance show --json``."""
    shown: dict[str, Any] = json_of(
        sc.run(f"noust db instance show {name} --json", timeout=60, label=label),
        "db instance show",
    )
    return shown


def orders(sc: Scenario, engine: str, database: str, label: str) -> str:
    """The rows of the ``orders`` table the scenario writes, through ``noust db query``."""
    return db_query(
        sc,
        database,
        "SELECT id, quantity FROM orders ORDER BY id",
        engine=engine,
        label=label,
        check=False,
    ).stdout


@scenario("database_instance_lifecycle")
def scenario_database_instances(sc: Scenario) -> None:
    """
    An engine Noust runs itself, in a container beside the host's, lives its whole life.

    PostgreSQL 16 is made from the official image pinned by digest, published on loopback only and
    held to its memory limit; a database in it takes data, is dumped and restored as a new one,
    survives a stop and a start and an upgrade of its image, and moves to PostgreSQL 17 with its
    data (the old instance stays until it is removed). Removing an instance takes its container,
    its volume and its unit with it.
    """
    run.require_cli(sc, "db instance", what="managed database instances (C-01)")
    run.require_docker(sc)
    try:
        catalog = json_of(
            sc.run(
                "noust db instance catalog --json", timeout=30, label="noust db instance catalog"
            ),
            "db instance catalog",
        )
        offered = {
            entry["flavour"]: [v["version"] for v in entry["versions"]]
            for entry in catalog["engines"]
        }
        sc.check("16" in offered.get("postgresql", []), f"the catalog offers: {offered!r}")

        made = json_of(
            sc.run(
                f"noust db instance create {DBI_NAME} --engine postgresql --version 16 "
                "--memory 512m --yes --json",
                timeout=900,
                label=f"noust db instance create {DBI_NAME} --engine postgresql --version 16 --memory 512m",
            ),
            "db instance create",
        )
        sc.check(made["key"] == DBI_ENGINE, f"the instance is addressed as {made['key']!r}")
        sc.check(
            "@sha256:" in made["reference"],
            f"the image is not pinned by digest: {made['reference']!r}",
        )
        port = made["host_port"]

        # -- It is published on loopback and held to its memory ---------------------------
        container = sc.run(
            "docker ps -q -f name=noust-db-pg16 | head -1",
            timeout=15,
            label="the instance's container",
        ).stdout.strip()
        sc.check(bool(container), "no container is running for the instance")
        published = sc.run(
            f"docker port {container}", timeout=15, label="docker port <the instance>"
        ).stdout
        sc.check(
            f"127.0.0.1:{port}" in published and "0.0.0.0" not in published,  # noqa: S104 - the check that it is not bound
            f"the instance is published beyond loopback: {published!r}",
        )
        memory = sc.run(
            f"docker inspect -f '{{{{.HostConfig.Memory}}}}' {container}",
            timeout=15,
            label="the memory limit Docker holds it to",
        ).stdout.strip()
        sc.check(memory == str(512 * 1024 * 1024), f"the container's memory limit is {memory}")
        unit = sc.run(
            f"systemctl is-enabled noust-db-{DBI_NAME}.service; systemctl is-active noust-db-{DBI_NAME}.service",
            timeout=15,
            check=False,
            label="the unit that brings it back after a reboot",
        ).stdout.split()
        sc.check(unit[:2] == ["enabled", "active"], f"the instance's unit: {unit!r}")
        secret_files = sc.run(
            f"grep -rl POSTGRES_PASSWORD /var/lib/noust/db-instances/{DBI_NAME} | "
            "xargs -r stat -c '%a %U %n'",
            timeout=15,
            check=False,
            label="the file that holds the generated superuser password",
        ).stdout.splitlines()
        sc.check(
            bool(secret_files)
            and all(
                line.split()[:2] in (["600", "root"], ["400", "root"]) for line in secret_files
            ),
            f"the instance's password is in a file others can read, or nowhere: {secret_files!r}",
        )
        in_argv = sc.run(
            "ps -eo args | grep -c 'POSTGRES_PASSWORD=' || true",
            timeout=15,
            check=False,
            label="the password is in no process's arguments",
        ).stdout.strip()
        sc.check(in_argv == "0", f"POSTGRES_PASSWORD= is in {in_argv} command line(s)")

        # -- A database in it takes data ---------------------------------------------------
        sc.run(
            f"noust db create {DBI_DATABASE} --engine {DBI_ENGINE}",
            timeout=120,
            label=f"noust db create {DBI_DATABASE} --engine {DBI_ENGINE}",
        )
        db_query(
            sc,
            DBI_DATABASE,
            "CREATE TABLE orders (id int PRIMARY KEY, quantity int)",
            engine=DBI_ENGINE,
            write=True,
            label="CREATE TABLE orders",
        )
        db_query(
            sc,
            DBI_DATABASE,
            "INSERT INTO orders VALUES (1, 10), (2, 20)",
            engine=DBI_ENGINE,
            write=True,
            label="INSERT two orders",
        )
        rows = orders(sc, DBI_ENGINE, DBI_DATABASE, "the orders")
        sc.check("10" in rows and "20" in rows, f"the data is not there: {rows!r}")
        engines = sc.run(
            "noust db engines --json", timeout=60, label="noust db engines --json"
        ).stdout
        sc.check(DBI_ENGINE in engines, "the instance is not listed among the engines")

        # -- A dump, restored as a new database ----------------------------------------------
        sc.run(
            f"noust db backup {DBI_DATABASE} --engine {DBI_ENGINE} --output /root/it-shop.dump",
            timeout=120,
            label="noust db backup shop",
        )
        sc.run(
            f"noust db restore {DBI_DATABASE} /root/it-shop.dump --engine {DBI_ENGINE} "
            f"--as-new shop_copy --force",
            timeout=120,
            label="noust db restore --as-new shop_copy",
        )
        copy = orders(sc, DBI_ENGINE, "shop_copy", "the orders of the copy")
        sc.check("10" in copy and "20" in copy, f"the restored copy lacks the data: {copy!r}")

        # -- Stopped and started: the data is in the volume -------------------------------------
        sc.run(f"noust db instance stop {DBI_NAME}", timeout=120, label="noust db instance stop")
        gone = db_query(
            sc,
            DBI_DATABASE,
            "SELECT 1",
            engine=DBI_ENGINE,
            check=False,
            label="a query while it is stopped",
        )
        sc.check(gone.returncode != 0, "a stopped instance answered a query")
        sc.run(f"noust db instance start {DBI_NAME}", timeout=180, label="noust db instance start")
        again = orders(sc, DBI_ENGINE, DBI_DATABASE, "the orders after a stop and a start")
        sc.check("10" in again and "20" in again, f"the data did not survive a restart: {again!r}")

        # -- New limits: a restart behind the same readiness gate ----------------------------
        sc.run(
            f"noust db instance limits {DBI_NAME} --memory 768m --yes",
            timeout=180,
            label="noust db instance limits --memory 768m",
        )
        container = sc.run(
            "docker ps -q -f name=noust-db-pg16 | head -1", timeout=15, label="the container again"
        ).stdout.strip()
        memory = sc.run(
            f"docker inspect -f '{{{{.HostConfig.Memory}}}}' {container}",
            timeout=15,
            label="the memory limit after the change",
        ).stdout.strip()
        sc.check(memory == str(768 * 1024 * 1024), f"the memory limit after the change: {memory}")

        # -- The newest image of its version ------------------------------------------------
        upgraded = json_of(
            sc.run(
                f"noust db instance upgrade {DBI_NAME} --json",
                timeout=900,
                label="noust db instance upgrade",
            ),
            "db instance upgrade",
        )
        sc.evidence.append(f"(upgrade answered) {json.dumps(upgraded)[:300]}")
        after = orders(sc, DBI_ENGINE, DBI_DATABASE, "the orders after the upgrade")
        sc.check(
            "10" in after and "20" in after, f"the data did not survive the upgrade: {after!r}"
        )

        # -- To PostgreSQL 17, with the data --------------------------------------------------
        moved = json_of(
            sc.run(
                f"noust db instance migrate {DBI_NAME} --to-version 17 --name pg17 --yes --json",
                timeout=1800,
                label="noust db instance migrate pg16 --to-version 17",
            ),
            "db instance migrate",
        )
        sc.evidence.append(f"(migrate answered) {json.dumps(moved)[:400]}")
        new = instance_row(sc, "pg17", "the new instance")
        sc.check(new["version"].startswith("17"), f"the new instance runs {new['version']!r}")
        seventeen = orders(sc, "postgresql@pg17", DBI_DATABASE, "the orders on PostgreSQL 17")
        sc.check("10" in seventeen and "20" in seventeen, f"the data did not move: {seventeen!r}")
        moves = json_of(
            sc.run(
                "noust db instance migrations --json",
                timeout=30,
                label="noust db instance migrations",
            ),
            "db instance migrations",
        )
        sc.check(bool(moves), f"the move is not on record: {moves!r}")
        old = orders(sc, DBI_ENGINE, DBI_DATABASE, "the old instance is left as it was")
        sc.check("10" in old, "the instance a move leaves was changed or removed by the move")
    except AssertionError:
        journal_tail(sc, "-u 'noust-db-*' -u docker", "journalctl (on failure)")
        raise
    finally:
        sc.run(
            "noust db drop shop_copy --engine postgresql@pg16 --force; "
            "noust db instance remove pg17 --yes; noust db instance remove pg16 --yes; "
            "rm -f /root/it-shop.dump",
            timeout=300,
            check=False,
            label="cleanup",
        )
    left = sc.run(
        "docker ps -aq -f name=noust-db- | wc -l; docker volume ls -q -f name=noust-db- | wc -l; "
        "ls /var/lib/noust/db-instances 2>/dev/null | wc -l; "
        "systemctl list-unit-files 'noust-db-*' --no-legend | wc -l",
        timeout=60,
        label="what removing the instances left behind",
    ).stdout.split()
    sc.check(left == ["0", "0", "0", "0"], f"containers, volumes, files, units left: {left!r}")


# ---------------------------------------------------------------------------------------------
# Previews by hand: a branch, a name template, the variables, a clone of the data, the hooks
# ---------------------------------------------------------------------------------------------

PREVB_DOMAIN = "prevb.test"
PREVB_REPO = "prevb-app"
PREVB_BASE = "pvw.test"
PREVB_PREVIEW = f"x1-prevb-test.{PREVB_BASE}"
PREVB_FIXTURES = "/root/fixtures/previews"


def host_json(sc: Scenario, host: str, path: str, label: str) -> dict[str, Any]:
    """GET a JSON document from an application through nginx."""
    body = run.docker_exec(
        sc.container,
        f"curl -sS -m 10 -H 'Host: {host}' http://127.0.0.1{path}",
        timeout=30,
        check=False,
    ).stdout
    sc.evidence.append(f"$ {label}\n{body.strip()[:600]}")
    parsed: dict[str, Any] = json.loads(body)
    return parsed


def database_of(sc: Scenario, domain: str) -> str:
    """The name of the database an application's ``DATABASE_URL`` points at."""
    url = run.env_value(sc, domain, "DATABASE_URL", f"DATABASE_URL of {domain}")
    match = re.search(r"/([^/?]+)(?:\?.*)?$", url)
    sc.check(match is not None, f"{domain} has no DATABASE_URL naming a database")
    return match.group(1) if match else ""


@scenario("preview_by_branch_with_cloned_data_and_hooks")
def scenario_preview_by_hand(sc: Scenario) -> None:
    """
    A preview made by hand from a branch: named by a template, given only what it may have, with a
    database that starts as a copy of the application's, seeded and cleaned up by its own hooks.

    The application has a MariaDB database with a row. Previews are on with ``--data clone``, a
    name template, an allow-list of variables and one variable of their own. The preview answers
    at the name the template makes, tells search engines to stay out, is told which application it
    copies, gets the allowed variable and the preview-only one and not the application's secret,
    has a database of its own holding the row (a change in it does not reach the application's),
    runs its ``preview_post_create`` hook once it serves, and removing it runs ``preview_pre_destroy``,
    drops the database and takes the site away.
    """
    run.require_cli(sc, "preview create", what="previews by hand")
    repo, url = make_node_repo(sc, PREVB_REPO)
    commit_to(
        sc,
        repo,
        f"cp {PREVB_FIXTURES}/server.js {PREVB_FIXTURES}/noust.yaml {PREVB_FIXTURES}/preview-hook.js .",
        "a server that tells what it is given, and the hooks of a preview's life",
    )
    sc.run(
        f"cd {repo} && git checkout -q -b feature-x && echo 2 > VERSION && git add -A && "
        "git commit -q -m 'the feature' && git checkout -q main",
        timeout=30,
        label="(fixture repo) a branch feature-x at version 2",
    )
    preview_db = ""
    try:
        sc.run(
            f"noust create -d {PREVB_DOMAIN} -s {url} -t nodejs --no-ssl --layout releases",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust create -d {PREVB_DOMAIN}",
        )
        wait_served(sc, PREVB_DOMAIN, "ok 1")
        sc.run(
            f"noust db provision {PREVB_DOMAIN} --engine mysql",
            timeout=300,
            label=f"noust db provision {PREVB_DOMAIN} --engine mysql",
        )
        app_db = database_of(sc, PREVB_DOMAIN)
        db_query(
            sc,
            app_db,
            "CREATE TABLE seed (id int PRIMARY KEY, note varchar(20))",
            engine="mysql",
            write=True,
            label="CREATE TABLE seed",
        )
        db_query(
            sc,
            app_db,
            "INSERT INTO seed VALUES (1, 'from production')",
            engine="mysql",
            write=True,
            label="INSERT the row a clone must carry",
        )
        sc.run(
            f"noust env set {PREVB_DOMAIN} SECRET_KEY=production-only NEXT_PUBLIC_X=public-ok",
            timeout=120,
            label="noust env set SECRET_KEY NEXT_PUBLIC_X",
        )
        sc.run(
            f"noust preview enable {PREVB_DOMAIN} --domain {PREVB_BASE} --max 2 --ttl 1h "
            "--data clone --env-mode allowlist --allow-env NEXT_PUBLIC_X "
            "--domain-template '{{pr_id}}-{{name}}.{{base}}'",
            timeout=60,
            label="noust preview enable --data clone --domain-template ...",
        )
        sc.run(
            f"noust preview vars set {PREVB_DOMAIN} PREVIEW_ONLY hello-preview",
            timeout=60,
            label="noust preview vars set PREVIEW_ONLY",
        )

        created = sc.run(
            f"noust preview create {PREVB_DOMAIN} --branch feature-x",
            timeout=DEPLOY_TIMEOUT * 2,
            label=f"noust preview create {PREVB_DOMAIN} --branch feature-x",
        )
        listing = json_of(
            sc.run(
                f"noust preview list {PREVB_DOMAIN} --json", timeout=30, label="noust preview list"
            ),
            "preview list",
        )
        names = [item["domain"] for item in listing["items"]]
        sc.check(names == [PREVB_PREVIEW], f"the template made {names!r}, not {PREVB_PREVIEW}")
        sc.check(
            "ready" in {item["status"] for item in listing["items"]},
            f"the preview is not ready: {listing['items']!r}",
        )
        wait_served(sc, PREVB_PREVIEW, "ok 2")
        wait_served(sc, PREVB_DOMAIN, "ok 1")

        # -- What it is told and what it is given ------------------------------------------
        seen = host_json(sc, PREVB_PREVIEW, "/env", "GET /env of the preview")
        sc.check(
            seen["previewOf"] == PREVB_DOMAIN and seen["pr"] is None,
            f"the preview is not told which application it copies: {seen!r}",
        )
        sc.check(
            seen["publicKey"] == "public-ok" and seen["previewOnly"] == "hello-preview",
            f"the preview lacks the variable it may have: {seen!r}",
        )
        sc.check(
            seen["secretKey"] is None, f"the application's secret reached the preview: {seen!r}"
        )
        production = host_json(sc, PREVB_DOMAIN, "/env", "GET /env of the application")
        sc.check(production["secretKey"] == "production-only", "the application lost its secret")
        sc.check(
            seen["databaseUrl"] and seen["databaseUrl"] != production["databaseUrl"],
            f"the preview shares the application's database: {seen['databaseUrl']!r}",
        )
        headers = curl_host(sc, PREVB_PREVIEW, head=True).lower()
        sc.check("x-robots-tag: noindex" in headers, f"a preview is indexable: {headers!r}")

        # -- A database of its own that started as a copy ----------------------------------------
        preview_db = database_of(sc, PREVB_PREVIEW)
        sc.check(preview_db != app_db, "the preview was given the application's database")
        copied = db_query(
            sc,
            preview_db,
            "SELECT note FROM seed",
            engine="mysql",
            label="the row, in the preview's database",
        ).stdout
        sc.check("from production" in copied, f"the clone lacks the row: {copied!r}")
        db_query(
            sc,
            preview_db,
            "INSERT INTO seed VALUES (2, 'only in the preview')",
            engine="mysql",
            write=True,
            label="write in the preview's database",
        )
        parent = db_query(
            sc,
            app_db,
            "SELECT note FROM seed",
            engine="mysql",
            label="the application's database after it",
        ).stdout
        sc.check(
            "from production" in parent and "only in the preview" not in parent,
            f"a write in the preview reached the application's database: {parent!r}",
        )

        # -- Its hook ran once it served ----------------------------------------------------------
        sc.check(
            "PREVIEW_HOOK seeded" in created.stdout + created.stderr,
            f"the preview_post_create hook did not run, or said nothing: {created.stdout[-600:]!r}",
        )

        # -- Redeploy, stop, start ---------------------------------------------------------------
        sc.run(
            f"noust preview redeploy {PREVB_PREVIEW}",
            timeout=DEPLOY_TIMEOUT,
            label="noust preview redeploy",
        )
        wait_served(sc, PREVB_PREVIEW, "ok 2")
        sc.run(f"noust preview stop {PREVB_PREVIEW}", timeout=120, label="noust preview stop")
        stopped = curl_host(sc, PREVB_PREVIEW)
        sc.check("ok 2" not in stopped, "a stopped preview still answers")
        sc.run(f"noust preview start {PREVB_PREVIEW}", timeout=120, label="noust preview start")
        wait_served(sc, PREVB_PREVIEW, "ok 2")

        # -- Removing it ------------------------------------------------------------------------------
        removed = sc.run(
            f"noust preview remove {PREVB_PREVIEW}",
            timeout=300,
            label=f"noust preview remove {PREVB_PREVIEW}",
        )
        sc.check(
            "PREVIEW_HOOK goodbye" in removed.stdout + removed.stderr,
            f"the preview_pre_destroy hook did not run: {removed.stdout[-600:]!r}",
        )
        sc.check(
            run.site_files(sc, PREVB_PREVIEW) == "",
            "the removed preview's site is still there",
        )
        left = sc.run(
            "mysql -N -e 'SHOW DATABASES'", timeout=60, check=False, label="the databases left"
        ).stdout
        sc.check(preview_db not in left, f"the preview's database {preview_db} was not dropped")
        sc.check(
            json_of(
                sc.run(f"noust preview list {PREVB_DOMAIN} --json", timeout=30, label="previews"),
                "preview list",
            )["items"]
            == [],
            "a removed preview is still listed",
        )
        wait_served(sc, PREVB_DOMAIN, "ok 1")
    except AssertionError:
        journal_tail(sc, "-u 'noust*' -u 'x1-*' -u prevb-test", "journalctl (on failure)")
        raise
    finally:
        sc.run(
            f"noust preview remove {PREVB_PREVIEW}; noust preview disable {PREVB_DOMAIN} -y; "
            f"noust delete {PREVB_DOMAIN} -f",
            timeout=300,
            check=False,
            label="cleanup",
        )


PREVC_DOMAIN = "prevc.test"
PREVC_PORT = 18214
PREVC_BASE = "pvw.test"


@scenario("preview_of_a_compose_stack")
def scenario_preview_compose(sc: Scenario) -> None:
    """
    A Docker Compose application has previews too, as a stack of its own beside the original.

    The preview runs from a copy of the compose file written beside it: its containers, network
    and volumes are its own, only its web service is published (on loopback, at the port it was
    given), the original is never touched and keeps serving its own version. Removing the
    preview removes its containers and volumes and leaves the original alone.
    """
    run.require_cli(sc, "preview create", what="previews of a Compose stack")
    run.require_docker(sc)
    run.pull_images(sc, run.COMPOSE_BASE_IMAGE)
    repo, url = run.make_compose_repo(sc, "prevc-stack", "web", port=PREVC_PORT)
    sc.run(
        f"cd {repo} && git checkout -q -b feature-c && printf 'v2\\n' > VERSION && git add -A && "
        "git commit -q -m 'the feature' && git checkout -q main",
        timeout=30,
        label="(fixture repo) a branch feature-c at v2",
    )
    preview_domain = f"x1-prevc-test.{PREVC_BASE}"
    try:
        run.compose_create(sc, PREVC_DOMAIN, url, port=PREVC_PORT)
        wait_served(sc, PREVC_DOMAIN, "web v1")
        sc.run(
            f"noust preview enable {PREVC_DOMAIN} --domain {PREVC_BASE} --max 2 --ttl 1h "
            "--domain-template '{{pr_id}}-{{name}}.{{base}}'",
            timeout=60,
            label="noust preview enable",
        )
        original = run.compose_ids(sc, PREVC_DOMAIN, "web")
        sc.run(
            f"noust preview create {PREVC_DOMAIN} --branch feature-c",
            timeout=DEPLOY_TIMEOUT * 2,
            label=f"noust preview create {PREVC_DOMAIN} --branch feature-c",
        )
        wait_served(sc, preview_domain, "web v2")
        wait_served(sc, PREVC_DOMAIN, "web v1")
        sc.check(
            run.compose_ids(sc, PREVC_DOMAIN, "web") == original,
            "the preview touched the original stack's container",
        )
        projects = sc.run(
            "docker ps --format '{{.Label \"com.docker.compose.project\"}}' | sort -u",
            timeout=30,
            label="the Compose projects that run",
        ).stdout.split()
        preview_project = run.compose_project(preview_domain)
        sc.check(
            run.compose_project(PREVC_DOMAIN) in projects and preview_project in projects,
            f"the preview is not a project of its own: {projects!r}",
        )
        published = sc.run(
            f"docker ps --format '{{{{.Ports}}}}' "
            f"-f label=com.docker.compose.project={preview_project}",
            timeout=30,
            label="what the preview publishes",
        ).stdout
        sc.check(
            "127.0.0.1:" in published and "0.0.0.0" not in published,  # noqa: S104 - the check
            f"the preview publishes beyond loopback: {published!r}",
        )
        sc.check(
            f"127.0.0.1:{PREVC_PORT}->" not in published,
            "the preview took the original's published port",
        )
        sc.run(f"noust preview remove {preview_domain}", timeout=300, label="noust preview remove")
        gone = sc.run(
            f"docker ps -aq -f label=com.docker.compose.project={preview_project} | wc -l; "
            f"docker volume ls -q -f label=com.docker.compose.project={preview_project} | wc -l",
            timeout=30,
            label="what the removed preview left",
        ).stdout.split()
        sc.check(gone == ["0", "0"], f"containers and volumes left by the preview: {gone!r}")
        wait_served(sc, PREVC_DOMAIN, "web v1")
        sc.check(
            run.compose_ids(sc, PREVC_DOMAIN, "web") == original,
            "removing the preview recreated the original",
        )
    except AssertionError:
        journal_tail(sc, "-u 'noust*' -u docker", "journalctl (on failure)")
        raise
    finally:
        sc.run(
            f"noust preview remove {preview_domain}; noust preview disable {PREVC_DOMAIN} -y",
            timeout=300,
            check=False,
            label="cleanup: previews",
        )
        run.compose_clean(sc, PREVC_DOMAIN)


# ---------------------------------------------------------------------------------------------
# The Compose relay, killed midway
# ---------------------------------------------------------------------------------------------

RELAYK_DOMAIN = "relayk.test"
RELAYK_PORT = 18215
RELAYK_UNIT = "noust-it-relay-update"


@scenario("compose_relay_update_killed_midway")
def scenario_relay_killed(sc: Scenario) -> None:
    """
    An update killed while a relay serves leaves a site that answers, and the next update resolves it.

    The new version takes eight seconds to listen, so the relay serves for a while: the update is
    killed (its whole control group, Compose client included) as soon as the servers file names
    the relay. The site keeps answering from the relay; ``noust setup doctor`` names the leftover
    with the command that resolves it; the next ``noust update`` puts the traffic back on the
    stack's own container before anything is rebuilt, and no relay is left.
    """
    run.require_module(sc, "noust.deployers.compose_relay", what="the Compose relay")
    run.require_docker(sc)
    run.pull_images(sc, run.COMPOSE_BASE_IMAGE)
    repo, url = run.make_compose_repo(sc, "relayk-stack", "web", port=RELAYK_PORT)
    run.compose_create(sc, RELAYK_DOMAIN, url, port=RELAYK_PORT)
    servers = f"/etc/nginx/noust-upstreams/{run.compose_project(RELAYK_DOMAIN)}/web.servers"
    try:
        wait_served(sc, RELAYK_DOMAIN, "web v1")
        sc.run(
            f"noust app zero-downtime {RELAYK_DOMAIN} on --drain 2",
            timeout=120,
            label=f"noust app zero-downtime {RELAYK_DOMAIN} on --drain 2",
        )
        commit_to(
            sc,
            repo,
            "printf 'v2\\n' > VERSION && printf '8\\n' > DELAY",
            "v2: eight seconds to start",
        )
        sc.run(
            f"systemctl reset-failed {RELAYK_UNIT} 2>/dev/null; systemd-run --unit {RELAYK_UNIT} "
            f"--collect noust update {RELAYK_DOMAIN}",
            timeout=30,
            label=f"noust update {RELAYK_DOMAIN}, as a transient unit that can be killed whole",
        )
        own = f"server 127.0.0.1:{RELAYK_PORT};"
        deadline = time.monotonic() + 120
        pointed = own
        while time.monotonic() < deadline:
            pointed = run.docker_exec(
                sc.container, f"cat {servers}", timeout=15, check=False
            ).stdout.strip()
            if pointed != own:
                break
            time.sleep(0.2)
        sc.evidence.append(f"$ (until the servers file names the relay) cat {servers}\n{pointed}")
        sc.check(pointed != own, "the servers file never named a relay while the update ran")
        sc.run(
            f"systemctl kill --signal=KILL {RELAYK_UNIT}; sleep 1; systemctl is-active {RELAYK_UNIT}; true",
            timeout=30,
            label="kill the update, Compose client and all",
        )

        # -- The site still answers, from the relay ----------------------------------------------
        sc.check(
            run.docker_exec(sc.container, f"cat {servers}", timeout=15).stdout.strip() == pointed,
            "killing the update changed the servers file",
        )
        wait_served(sc, RELAYK_DOMAIN, "web ")
        relays = sc.run(
            "docker ps --format '{{.Names}}' | grep -- '-relay' || true",
            timeout=30,
            label="the relay that serves",
        ).stdout.split()
        sc.check(len(relays) == 1, f"expected one relay serving: {relays!r}")

        # -- The doctor names it, with the command that resolves it ------------------------------
        doctor = sc.run(
            "noust setup doctor", timeout=300, check=False, label="noust setup doctor"
        ).stdout
        sc.check(
            "Docker Compose relays" in doctor and RELAYK_DOMAIN in doctor,
            f"noust setup doctor does not name the leftover relay: {doctor[-600:]!r}",
        )

        # -- The next update resolves it before anything is rebuilt -------------------------------
        sc.run(
            f"noust update {RELAYK_DOMAIN}",
            timeout=DEPLOY_TIMEOUT,
            label=f"noust update {RELAYK_DOMAIN} (the next one)",
        )
        wait_served(sc, RELAYK_DOMAIN, "web v2")
        sc.check(
            run.docker_exec(sc.container, f"cat {servers}", timeout=15).stdout.strip() == own,
            "the servers file was not given back to the stack's own port",
        )
        left = sc.run(
            "docker ps -a --format '{{.Names}}' | grep -- '-relay' || true",
            timeout=30,
            label="no relay is left",
        ).stdout.strip()
        sc.check(not left, f"a relay was left behind: {left!r}")
        clean = sc.run("noust setup doctor", timeout=300, check=False, label="noust setup doctor")
        sc.check("Docker Compose relays" not in clean.stdout, "the doctor still names a relay")
    except AssertionError:
        journal_tail(sc, "-u 'noust*'", "journalctl (on failure)")
        raise
    finally:
        sc.run(f"systemctl kill --signal=KILL {RELAYK_UNIT}; true", timeout=30, check=False)
        run.compose_clean(sc, RELAYK_DOMAIN)
