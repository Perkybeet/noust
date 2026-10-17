#!/usr/bin/env python3
"""
The installer against the platform matrix, one throwaway container per release.

``packaging/install.sh`` is the way Noust gets onto a server, and the table of
releases it declares (``noust.managers.server.platforms.TARGETS``, shown in
``docs/PLATFORMS.md``) is only worth what has been run. This harness runs it:
for each image it checks what the script detects, runs the dry run, installs the
published package from the real Open Build Service repository (the key checked
by fingerprint), runs it a second time, and checks that a repository key with
another fingerprint is refused before anything is written.

It is not part of pytest (nothing here is named ``test_*``): it drives Docker and
the network. The containers have no systemd, so ``noust setup init`` and the
console are not part of it; the systemd scenarios of ``run.py`` are, on
Ubuntu 24.04 amd64, and ``docs/PLATFORMS.md`` says so.

Usage:
    .venv/bin/python tests/integration/install_matrix.py
    .venv/bin/python tests/integration/install_matrix.py --image debian:12
    .venv/bin/python tests/integration/install_matrix.py --image debian:12 --platform linux/arm64
    .venv/bin/python tests/integration/install_matrix.py --list

Every container is named ``noust-it-install-<random>`` (the start is NOUST_IT_PREFIX, shared
with run.py), carries the label ``noust.test=1`` and is removed when it ends; nothing of the
host's Docker is shared with it.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "packaging" / "install.sh"

#: The start of every container's name, and the label every one carries: the same convention as
#: run.py, so whoever shares the machine's Docker can find exactly what a run made.
CONTAINER_PREFIX = os.environ.get("NOUST_IT_PREFIX", "noust-it")
RESOURCE_LABEL = "noust.test=1"

#: A fingerprint that is not the project's, for the refusal check.
WRONG_FINGERPRINT = "0123456789ABCDEF0123456789ABCDEF01234567"

#: How long an install may take: a cold package index, the packages, and their dependencies.
INSTALL_TIMEOUT = 900


@dataclass(frozen=True)
class Case:
    """
    One image of the matrix.

    Attributes:
        image: The Docker image.
        status: What the table must say about it: the installer's ``status=`` line.
        repo: The repository directory it must pick, or ``-``.
        installs: Whether the script is expected to install Noust there.
        platforms: The architectures to run it on.
    """

    image: str
    status: str
    repo: str
    installs: bool = True
    platforms: tuple[str, ...] = ("linux/amd64",)


CASES: tuple[Case, ...] = (
    Case("debian:12", "supported", "Debian_12", platforms=("linux/amd64", "linux/arm64")),
    Case("debian:13", "supported", "Debian_13"),
    Case("ubuntu:22.04", "supported", "xUbuntu_22.04"),
    Case("ubuntu:24.04", "supported", "xUbuntu_24.04", platforms=("linux/amd64", "linux/arm64")),
    Case("ubuntu:26.04", "supported", "xUbuntu_26.04"),
    Case("fedora:42", "best-effort", "Fedora_42"),
    Case("fedora:43", "supported", "Fedora_43"),
    Case("fedora:44", "best-effort", "Fedora_43"),
    Case("opensuse/leap:15.6", "best-effort", "15.6"),
    Case("opensuse/leap:16.0", "supported", "16.0"),
    Case("opensuse/tumbleweed", "supported", "openSUSE_Tumbleweed"),
    Case("rockylinux:9", "pip", "-"),
    Case("rockylinux:10", "pip", "-"),
    Case("almalinux:9", "pip", "-"),
    Case("almalinux:10", "pip", "-"),
    # The rest of the RHEL family: no package of ours, so the pip method, on each release.
    Case("redhat/ubi9", "pip", "-"),
    Case("redhat/ubi10", "pip", "-"),
    Case("quay.io/centos/centos:stream9", "pip", "-"),
    Case("quay.io/centos/centos:stream10", "pip", "-"),
    Case("oraclelinux:9", "pip", "-"),
    Case("oraclelinux:10", "pip", "-"),
    # Refused: nothing may be installed and the exit status says why. One image for each row of
    # the table that says so (managers/server/platforms.py).
    Case("debian:11", "unsupported", "-", installs=False),
    Case("ubuntu:20.04", "unsupported", "-", installs=False),
    Case("ubuntu:25.04", "unsupported", "-", installs=False),
    Case("ubuntu:25.10", "unsupported", "-", installs=False),
    Case("fedora:41", "unsupported", "-", installs=False),
    Case("archlinux:latest", "unsupported", "-", installs=False),
    Case("alpine:3.20", "unsupported", "-", installs=False),
)

#: What a container runs: detection, the dry run, the refusal of a wrong key, the install, the
#: second run. Each step prints a ``STEP`` line the harness reads, and the commands are the
#: operator's own.
SCRIPT = r"""
set -u
step() { printf 'STEP %s\n' "$1"; }
step platform
sh /install.sh --print-platform
step dry-run
sh /install.sh --dry-run >/tmp/dry.out 2>&1; echo "dry-run exit=$?"; tail -n 3 /tmp/dry.out
step wrong-key
NOUST_REPO_KEY_FPR=__WRONG__ sh /install.sh --yes --skip-setup --no-console >/tmp/wrong.out 2>&1
echo "wrong-key exit=$?"
tail -n 6 /tmp/wrong.out
ls /etc/apt/sources.list.d/noust.list /etc/yum.repos.d/noust.repo /usr/share/keyrings/noust.gpg 2>/dev/null \
    && echo "WRITTEN-BEFORE-VERIFY" || echo "nothing written"
step install
sh /install.sh --yes --skip-setup --no-console >/tmp/install.out 2>&1; echo "install exit=$?"
tail -n 12 /tmp/install.out
step version
noust --version; echo "noust exit=$?"
wasm --version; echo "wasm exit=$?"
step again
sh /install.sh --yes --skip-setup --no-console >/tmp/again.out 2>&1; echo "again exit=$?"
tail -n 4 /tmp/again.out
""".replace("__WRONG__", WRONG_FINGERPRINT)

#: For a platform with no package (the RHEL family): the pip method has no repository
#: key to check, so there is no wrong-key step; the rest is the same.
PIP_SCRIPT = r"""
set -u
step() { printf 'STEP %s\n' "$1"; }
step platform
sh /install.sh --print-platform
step dry-run
sh /install.sh --dry-run >/tmp/dry.out 2>&1; echo "dry-run exit=$?"; tail -n 3 /tmp/dry.out
step install
sh /install.sh --yes --skip-setup --no-console >/tmp/install.out 2>&1; echo "install exit=$?"
tail -n 12 /tmp/install.out
step version
noust --version; echo "noust exit=$?"
wasm --version; echo "wasm exit=$?"
step again
sh /install.sh --yes --skip-setup --no-console >/tmp/again.out 2>&1; echo "again exit=$?"
tail -n 4 /tmp/again.out
"""

#: For an image the table refuses: only the refusal.
REFUSAL_SCRIPT = r"""
set -u
sh /install.sh --print-platform
sh /install.sh --yes >/tmp/refused.out 2>&1; echo "refused exit=$?"
tail -n 5 /tmp/refused.out
"""


def docker(*argv: str, timeout: int) -> subprocess.CompletedProcess[str]:
    """
    Run ``docker`` on the host.

    Args:
        *argv: Its arguments.
        timeout: Seconds before it is given up on.

    Returns:
        The finished process, output captured.
    """
    return subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=timeout)


def run_case(case: Case, platform: str, *, verbose: bool) -> tuple[bool, list[str]]:
    """
    Run the installer in one container and judge what it did.

    Args:
        case: The image.
        platform: ``linux/amd64`` or ``linux/arm64`` (the latter emulated on an amd64 host).
        verbose: Print the container's whole output.

    Returns:
        Whether every expectation held, and a line per finding.
    """
    name = f"{CONTAINER_PREFIX}-install-{secrets.token_hex(4)}"
    script = (PIP_SCRIPT if case.status == "pip" else SCRIPT) if case.installs else REFUSAL_SCRIPT
    try:
        proc = docker(
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            RESOURCE_LABEL,
            "--platform",
            platform,
            "-v",
            f"{INSTALLER}:/install.sh:ro",
            case.image,
            "sh",
            "-c",
            script,
            timeout=INSTALL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        docker("rm", "-f", name, timeout=60)
        return False, [f"timed out after {INSTALL_TIMEOUT}s"]
    out = proc.stdout + proc.stderr
    if verbose:
        print(out)
    findings: list[str] = []
    expectations = [
        (f"status={case.status}", f"detected status {case.status}"),
        (f"repo={case.repo}", f"picked repository {case.repo}"),
    ]
    if case.installs:
        expectations += [("dry-run exit=0", "the dry run succeeds")]
        if case.status != "pip":
            expectations.append(("nothing written", "a wrong key writes nothing"))
        expectations += [
            ("install exit=0", "the install succeeds"),
            ("noust exit=0", "noust --version runs"),
            ("wasm exit=0", "wasm --version runs"),
            ("again exit=0", "a second run succeeds"),
        ]
        if case.status != "pip" and ("WRITTEN-BEFORE-VERIFY" in out or "wrong-key exit=0" in out):
            findings.append("FAIL: a repository key with another fingerprint was accepted")
    else:
        expectations.append(("refused exit=3", "the platform is refused with status 3"))
    ok = True
    for needle, meaning in expectations:
        if needle in out:
            findings.append(f"ok:   {meaning}")
        else:
            ok = False
            findings.append(f"FAIL: {meaning} (no '{needle}' in the output)")
    if not ok or "FAIL" in "".join(findings):
        findings.append("--- output (tail) ---")
        findings += out.splitlines()[-40:]
    return ok and not any(f.startswith("FAIL") for f in findings), findings


def main(argv: list[str] | None = None) -> int:
    """
    Run the matrix, or the images asked for.

    Args:
        argv: Arguments; ``sys.argv`` by default.

    Returns:
        0 when every container behaved, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--image", action="append", help="only this image; may repeat")
    parser.add_argument("--platform", help="one platform instead of each case's own")
    parser.add_argument("--list", action="store_true", help="list the images and stop")
    parser.add_argument("--verbose", action="store_true", help="print every container's output")
    args = parser.parse_args(argv)

    cases = [case for case in CASES if not args.image or case.image in args.image]
    if args.list:
        for case in cases:
            print(f"{case.image}\t{case.status}\t{case.repo}\t{','.join(case.platforms)}")
        return 0
    if not cases:
        print(f"no image matches {args.image!r}", file=sys.stderr)
        return 1

    failures = 0
    for case in cases:
        for platform in [args.platform] if args.platform else case.platforms:
            print(f"=== {case.image} on {platform}", flush=True)
            ok, findings = run_case(case, platform, verbose=args.verbose)
            print("\n".join(findings))
            print(f"{'PASS' if ok else 'FAIL'}: {case.image} on {platform}\n", flush=True)
            failures += 0 if ok else 1
    print(f"[summary] {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
