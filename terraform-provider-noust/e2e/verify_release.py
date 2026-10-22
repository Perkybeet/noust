#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: T201 - a command line tool: its output is the report
"""
Hold a GoReleaser output directory to what the Terraform Registry reads from a release.

The shape is the registry's documented one (developer.hashicorp.com/terraform/registry/providers/publishing):
one zip per platform named ``terraform-provider-NAME_VERSION_OS_ARCH.zip`` holding a binary named
``terraform-provider-NAME_vVERSION``, ``..._SHA256SUMS`` with the checksum of every zip and of the manifest,
``..._SHA256SUMS.sig`` as a *binary* detached signature, and ``..._manifest.json`` declaring protocol 6.

    verify_release.py DIST VERSION PUBLIC_KEY

Standard library only, so it runs in the golang container.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

NAME = "terraform-provider-noust"
PLATFORMS = {
    "darwin": ["amd64", "arm64"],
    "freebsd": ["386", "amd64", "arm", "arm64"],
    "linux": ["386", "amd64", "arm", "arm64"],
    "windows": ["386", "amd64", "arm64"],
}


def main(dist: Path, version: str, public_key: Path) -> list[str]:
    """
    Args:
        dist: The directory GoReleaser wrote.
        version: The release's version, without the ``v``.
        public_key: The armoured public half of the key that signed it.

    Returns:
        What is wrong, empty when the release is what the registry reads.
    """
    problems: list[str] = []
    prefix = f"{NAME}_{version}"
    # GoReleaser does not copy an extra file into dist/: it checksums the original and attaches it to the
    # release under the name the template gives.
    sums, signature = dist / f"{prefix}_SHA256SUMS", dist / f"{prefix}_SHA256SUMS.sig"
    manifest_name = f"{prefix}_manifest.json"
    manifest = dist.parent / "terraform-registry-manifest.json"
    for path in (sums, signature, manifest):
        if not path.is_file():
            problems.append(f"missing {path}")
    if problems:
        return problems
    # With publishing skipped the release's own extra files are not in artifacts.json, so what makes the
    # manifest part of the release is read from the configuration: it is checksummed and attached under the
    # name the registry looks for.
    configuration = (dist.parent / ".goreleaser.yml").read_text(encoding="utf-8")
    template = 'name_template: "{{ .ProjectName }}_{{ .Version }}_manifest.json"'
    if configuration.count(template) != 2:
        problems.append("the manifest is not both checksummed and attached to the release by name")

    wanted = {
        f"{prefix}_{goos}_{goarch}.zip" for goos, arches in PLATFORMS.items() for goarch in arches
    }
    found = {path.name for path in dist.glob("*.zip")}
    if found != wanted:
        problems.append(
            f"zips differ: missing {sorted(wanted - found)}, unexpected {sorted(found - wanted)}"
        )

    listed = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        listed[name] = digest
    for name in sorted(wanted | {manifest_name}):
        path = manifest if name == manifest_name else dist / name
        if name not in listed:
            problems.append(f"{sums.name} does not list {name}")
        elif path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != listed[name]:
            problems.append(f"the checksum of {name} is not the file's")

    raw = signature.read_bytes()
    if raw.startswith(b"-----BEGIN PGP"):
        problems.append("the signature is armoured; the registry reads a binary detached signature")
    with tempfile.TemporaryDirectory() as home:
        os.chmod(home, 0o700)
        env = {**os.environ, "GNUPGHOME": home}
        subprocess.run(
            ["gpg", "--batch", "--quiet", "--import", str(public_key)],
            check=True,
            env=env,
            capture_output=True,
        )
        checked = subprocess.run(
            ["gpg", "--batch", "--verify", str(signature), str(sums)],
            env=env,
            capture_output=True,
            text=True,
        )
        if checked.returncode != 0:
            problems.append(
                "the signature does not verify against the public key: " + checked.stderr
            )

    binary = f"{NAME}_v{version}"
    for name in sorted(found):
        with zipfile.ZipFile(dist / name) as archive:
            members = [member for member in archive.namelist() if member.startswith(f"{NAME}_v")]
            if members != [binary + (".exe" if "_windows_" in name else "")]:
                problems.append(f"{name} holds {members}, not the provider binary {binary}")

    declared = json.loads(manifest.read_text(encoding="utf-8"))
    if declared != {"version": 1, "metadata": {"protocol_versions": ["6.0"]}}:
        problems.append(f"the manifest says {declared}")

    # The Linux build of the machine this runs on is a program: it starts and says it is a plugin.
    native = (
        f"{prefix}_linux_{'arm64' if os.uname().machine in {'aarch64', 'arm64'} else 'amd64'}.zip"
    )
    with tempfile.TemporaryDirectory() as unpacked:
        with zipfile.ZipFile(dist / native) as archive:
            archive.extract(binary, unpacked)
        program = Path(unpacked, binary)
        program.chmod(0o755)
        ran = subprocess.run([str(program)], capture_output=True, text=True, timeout=30)
        if ran.returncode == 0 or "plugin" not in (ran.stdout + ran.stderr).lower():
            problems.append(
                f"{binary} does not behave as a provider plugin: {ran.stdout}{ran.stderr}"
            )
        said = subprocess.run([str(program), "-version"], capture_output=True, text=True, timeout=30)
        if said.stdout.strip() != f"{NAME} {version}":
            problems.append(f"the binary says {said.stdout.strip()!r}, not {version}")
    return problems
    # With publishing skipped the release's own extra files are not in artifacts.json, so what makes the
    # manifest part of the release is read from the configuration: it is checksummed and attached under the
    # name the registry looks for.
    configuration = (dist.parent / ".goreleaser.yml").read_text(encoding="utf-8")
    template = 'name_template: "{{ .ProjectName }}_{{ .Version }}_manifest.json"'
    if configuration.count(template) != 2:
        problems.append("the manifest is not both checksummed and attached to the release by name")

    wanted = {
        f"{prefix}_{goos}_{goarch}.zip" for goos, arches in PLATFORMS.items() for goarch in arches
    }
    found = {path.name for path in dist.glob("*.zip")}
    if found != wanted:
        problems.append(
            f"zips differ: missing {sorted(wanted - found)}, unexpected {sorted(found - wanted)}"
        )

    listed = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        listed[name] = digest
    for name in sorted(wanted | {manifest_name}):
        path = manifest if name == manifest_name else dist / name
        if name not in listed:
            problems.append(f"{sums.name} does not list {name}")
        elif path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != listed[name]:
            problems.append(f"the checksum of {name} is not the file's")

    raw = signature.read_bytes()
    if raw.startswith(b"-----BEGIN PGP"):
        problems.append("the signature is armoured; the registry reads a binary detached signature")
    with tempfile.TemporaryDirectory() as home:
        os.chmod(home, 0o700)
        env = {**os.environ, "GNUPGHOME": home}
        subprocess.run(
            ["gpg", "--batch", "--quiet", "--import", str(public_key)],
            check=True,
            env=env,
            capture_output=True,
        )
        checked = subprocess.run(
            ["gpg", "--batch", "--verify", str(signature), str(sums)],
            env=env,
            capture_output=True,
            text=True,
        )
        if checked.returncode != 0:
            problems.append(
                "the signature does not verify against the public key: " + checked.stderr
            )

    binary = f"{NAME}_v{version}"
    for name in sorted(found):
        with zipfile.ZipFile(dist / name) as archive:
            members = [member for member in archive.namelist() if member.startswith(f"{NAME}_v")]
            if members != [binary + (".exe" if "_windows_" in name else "")]:
                problems.append(f"{name} holds {members}, not the provider binary {binary}")

    declared = json.loads(manifest.read_text(encoding="utf-8"))
    if declared != {"version": 1, "metadata": {"protocol_versions": ["6.0"]}}:
        problems.append(f"the manifest says {declared}")

    # The Linux build of the machine this runs on is a program: it starts and says it is a plugin.
    native = (
        f"{prefix}_linux_{'arm64' if os.uname().machine in {'aarch64', 'arm64'} else 'amd64'}.zip"
    )
    with tempfile.TemporaryDirectory() as unpacked:
        with zipfile.ZipFile(dist / native) as archive:
            archive.extract(binary, unpacked)
        program = Path(unpacked, binary)
        program.chmod(0o755)
        ran = subprocess.run([str(program)], capture_output=True, text=True, timeout=30)
        if ran.returncode == 0 or "plugin" not in (ran.stdout + ran.stderr).lower():
            problems.append(
                f"{binary} does not behave as a provider plugin: {ran.stdout}{ran.stderr}"
            )
        built = subprocess.run(
            ["go", "version", "-m", str(program)], capture_output=True, text=True, timeout=60
        ).stdout
        if f"main.version={version}" not in built:
            problems.append(f"the version {version} is not built into the binary:\n{built}")
    return problems


if __name__ == "__main__":
    wrong = main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
    for line in wrong:
        print("FAIL:", line, file=sys.stderr)
    if wrong:
        sys.exit(1)
    print(f"release {sys.argv[2]}: the registry's shape, checksums, signature and binary are right")
