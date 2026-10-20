"""
Mixed fleets: a central of this tree with a node of 3.3.1, and a node of this tree under a central of 3.3.1.

``python tests/integration/run.py --mixed-fleet`` starts two containers on a network of their own,
installs a release on one and the other from git (the ``v3.3.1`` tag's own tree) or from this
tree's wheel, enrolls the node with the real CLI over the real SSH tunnel, and drives what a
central does to a node: the node's API through the proxy, an application deployed through it,
the event stream, and the application's export (whose format name is the node's own release's).

The rename of 4.0 touched names that cross the tunnel only in one place that matters here: the
format of an export document, chosen by the version the node declares. Everything else the
central sends a node (the fleet token, ``X-Noust-Actor``, the WebSocket's ``Authorization``) never
carried one of the old names. The two cases prove that, over a real tunnel, in both directions:

- a **central of 4.0** reaches a **node of 3.3.1**: the proxy, the deploy and the stream work;
  the node's export is its own (the old format name), which the central of 4.0 can carry on to
  another server;
- a **node of 4.0** is reached by a **central of 3.3.1**: the central's requests (made with the
  tokens, headers and export of 3.3.1) are understood.

Docker: two containers at a time, named after ``NOUST_IT_PREFIX`` and labelled ``noust.test=1``,
on a network of their own. Nothing is published on the host.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import fleet_run
import run
import scenarios_upgrade

#: The release the old side of each case runs.
OLD_TAG = scenarios_upgrade.FROM_TAG


@dataclass(frozen=True)
class Case:
    """
    One pairing of releases.

    Attributes:
        label: What the evidence calls it.
        central_is_new: Whether the central runs this tree (else 3.3.1).
        node_is_new: Whether the node runs this tree (else 3.3.1).
    """

    label: str
    central_is_new: bool
    node_is_new: bool

    @property
    def export_format(self) -> str:
        """The format name the node's export carries: its own release's."""
        return "noust-app" if self.node_is_new else "wasm-app"


CASES = (
    Case("central 4.0, node 3.3.1", central_is_new=True, node_is_new=False),
    Case("central 3.3.1, node 4.0", central_is_new=False, node_is_new=True),
)


def names() -> tuple[str, str, str, str]:
    """
    Name what a case creates, after the prefix this run was given.

    Returns:
        The network, the fleet image, the central and the node.
    """
    prefix = run.RESOURCE_PREFIX
    return (
        f"{prefix}-mixed-net",
        f"{prefix}-mixed-image:latest",
        f"{prefix}-mixed-c",
        f"{prefix}-mixed-n",
    )


def build_images() -> str:
    """
    Layer OpenSSH onto the harness image, labelled like everything else this run makes.

    Returns:
        The fleet image's tag.
    """
    _network, image, _central, _node = names()
    print(f"[setup] building {image} from {run.IMAGE_TAG}")
    proc = run.subprocess.run(
        [
            "docker",
            "build",
            "--label",
            run.RESOURCE_LABEL,
            "--build-arg",
            f"BASE_IMAGE={run.IMAGE_TAG}",
            "-t",
            image,
            "-f",
            "-",
            str(run.INTEGRATION_DIR),
        ],
        input=fleet_run.FLEET_DOCKERFILE,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise run.HarnessError(f"building {image} failed:\n{proc.stdout}\n{proc.stderr}")
    return image


def start_labelled(name: str, image: str, network: str) -> None:
    """
    Start a privileged systemd container on a network, with the run's label.

    Args:
        name: The container.
        image: The image.
        network: The network.
    """
    print(f"[setup] starting container {name} on {network}")
    run.sh(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            run.RESOURCE_LABEL,
            "--network",
            network,
            "--privileged",
            "--cgroupns=host",
            "-v",
            "/sys/fs/cgroup:/sys/fs/cgroup:rw",
            "--tmpfs",
            "/run",
            "--tmpfs",
            "/run/lock",
            image,
        ],
        timeout=60,
    )


def install(name: str, wheel: Path, *, new: bool) -> str:
    """
    Install the release a side of a case runs.

    Args:
        name: The container.
        wheel: This tree's wheel.
        new: This tree (else the tag of 3.3.1).

    Returns:
        What it says it is, for the evidence.
    """
    if new:
        run.install_noust(name, wheel)
    else:
        scenarios_upgrade.install_from_git(name, OLD_TAG)
    return run.docker_exec(name, "noust --version", timeout=30).stdout.strip()


def run_case(case: Case, wheel: Path, image: str, *, keep: bool) -> int:
    """
    Run one pairing: enroll the node, then drive it through the central.

    Args:
        case: The pairing.
        wheel: This tree's wheel.
        image: The fleet image.
        keep: Leave the containers up.

    Returns:
        The number of failures.
    """
    network, _image, central_name, node_name = names()
    fleet_run.CENTRAL_NAME = central_name
    fleet_run.NODE_NAME = node_name
    print(f"\n=== mixed fleet: {case.label} ===")
    failures = 0
    central = run.Scenario(container=central_name)
    node = run.Scenario(container=node_name)
    run.subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=30)
    run.sh(["docker", "network", "create", "--label", run.RESOURCE_LABEL, network], timeout=30)
    try:
        start_labelled(node_name, image, network)
        start_labelled(central_name, image, network)
        run.wait_for_systemd(node_name)
        run.wait_for_systemd(central_name)
        node_version = install(node_name, wheel, new=case.node_is_new)
        central_version = install(central_name, wheel, new=case.central_is_new)
        print(f"[setup] node: {node_version}; central: {central_version}")
        run.install_fixtures(node_name)
        fleet_run.provision_ssh(node_name)
        master_token, totp_secret = fleet_run.provision_central(central_name)
        env = fleet_run.FleetEnv(
            central=central,
            node=node,
            wheel=wheel,
            master_token=master_token,
            totp_secret=totp_secret,
        )
        fleet_run.enroll_node(env)
        for label, step in (
            ("the node is reached through the proxy", lambda: through_the_proxy(env, case)),
            (
                "an application is deployed through the central",
                lambda: fleet_run.scenario_deploy_through_central(env),
            ),
            (
                "the event stream travels the tunnel",
                lambda: fleet_run.scenario_events_stream(env),
            ),
            ("the node's export has its own release's name", lambda: export_of_the_node(env, case)),
        ):
            print(f"--- {label}")
            central.evidence = []
            node.evidence = []
            try:
                step()
            except (AssertionError, run.HarnessError) as exc:
                failures += 1
                print(f"FAIL: {case.label}: {label}: {exc}")
            except Exception:
                failures += 1
                print(f"FAIL: {case.label}: {label} (the step itself raised)")
                traceback.print_exc(file=sys.stdout)
            else:
                print(f"PASS: {case.label}: {label}")
            finally:
                evidence = central.evidence + node.evidence
                if evidence:
                    print("--- evidence ---")
                    print("\n\n".join(evidence))
    except (AssertionError, run.HarnessError) as exc:
        failures += 1
        print(f"FAIL: {case.label}: setup: {exc}")
    finally:
        if keep:
            print(f"[teardown] --keep given, leaving {central_name}, {node_name} and {network}")
        else:
            print(f"[teardown] removing {central_name}, {node_name} and {network}")
            run.remove_container(central_name)
            run.remove_container(node_name)
            run.subprocess.run(
                ["docker", "network", "rm", network], capture_output=True, timeout=30
            )
    return failures


def through_the_proxy(env: fleet_run.FleetEnv, case: Case) -> None:
    """
    Ask the node, through the central, which release it is and what it serves.

    Args:
        env: The fleet.
        case: The pairing.
    """
    proxy = f"https://127.0.0.1:{fleet_run.CENTRAL_PORT}/api/nodes/{env.node_name}/api"
    status, body, raw = fleet_run.api_call(
        fleet_run.CENTRAL_NAME, "GET", f"{proxy}/system/version", token=env.master_token
    )
    env.central.check(
        status == 200, f"the node's version through the proxy: {status} {raw[:300]!r}"
    )
    # This tree still says 3.3.1 until the release script runs, so the version cannot tell the
    # two sides apart; the export's format name, below, can.
    env.central.evidence.append(
        f"{case.label}: the node declares {(body or {}).get('current_version')!r}"
    )
    status, body, raw = fleet_run.api_call(
        fleet_run.CENTRAL_NAME, "GET", f"{proxy}/apps", token=env.master_token
    )
    env.central.check(
        status == 200, f"the node's applications through the proxy: {status} {raw[:300]!r}"
    )
    # Mutations through the proxy carry no cookie of either release's: the fleet token is a Bearer.
    status, _body, raw = fleet_run.api_call(
        fleet_run.CENTRAL_NAME, "GET", f"{proxy}/auth/session", token=env.master_token
    )
    env.central.check(
        status == 200, f"the node's session through the proxy: {status} {raw[:300]!r}"
    )


def export_of_the_node(env: fleet_run.FleetEnv, case: Case) -> None:
    """
    Export the application deployed through the central and read the format the node wrote.

    Args:
        env: The fleet.
        case: The pairing.
    """
    proxy = f"https://127.0.0.1:{fleet_run.CENTRAL_PORT}/api/nodes/{env.node_name}/api"
    status, body, raw = fleet_run.api_call(
        fleet_run.CENTRAL_NAME,
        "GET",
        f"{proxy}/apps/{env.static_domain}/export",
        token=env.master_token,
    )
    env.central.check(status == 200, f"the node's export through the proxy: {status} {raw[:300]!r}")
    document = body or {}
    env.central.check(
        document.get("format") == case.export_format,
        f"the export says {document.get('format')!r}, not {case.export_format!r}: {json.dumps(document)[:300]}",
    )
    env.central.check(
        document.get("app", {}).get("domain") == env.static_domain,
        f"the export is of another application: {json.dumps(document)[:300]}",
    )


def run_mixed_fleet_mode(wheel: Path, *, keep: bool) -> int:
    """
    Run both pairings, one after the other.

    Args:
        wheel: This tree's wheel, on the host.
        keep: Leave the last pairing's containers up.

    Returns:
        0 when every step of both held, else 1.
    """
    started_at = time.monotonic()
    image = build_images()
    failures = 0
    for case in CASES:
        failures += run_case(case, wheel, image, keep=keep)
    elapsed = time.monotonic() - started_at
    print(f"\n[summary] {len(CASES)} pairing(s) run, {failures} failure(s), {elapsed:.1f}s total")
    return 1 if failures else 0


__all__ = ["CASES", "run_mixed_fleet_mode"]
