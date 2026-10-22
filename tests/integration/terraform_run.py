#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Build and test the Terraform provider (and the Python SDK) against a real Noust API.

``terraform-provider-noust/`` is Go and ``sdk/python`` is Python; both are clients of the HTTP API, and a
client is only proved by a server that answers. The server here is ``scripts/console_server.py``: the real
application, seeded and sandboxed, started by :mod:`tests.integration.live_api` with a code host and
listeners so that an application can really be deployed in it.

This file is not collected by pytest (it does not match ``test_*.py``), because it starts processes and,
by default, Docker containers. Nothing is installed on the machine: Go runs in a ``golang`` container and
Terraform in a ``hashicorp/terraform`` one.

Stages::

    unit         gofmt, go vet, staticcheck, go build and the unit tests, including the contract tests that
                 hold the provider's operations table and request types to panel/openapi.json
    docs         regenerate the reference under terraform-provider-noust/docs (tfplugindocs) from the
                 schemas and examples/; a difference is a file to commit
    acceptance   the resources and data sources against the live API, as Terraform runs them
                 (terraform-plugin-testing, TF_ACC=1), with tokens narrowed to the permissions each
                 profile names: never the master token
    e2e          the provider binary under the real Terraform CLI: apply, an empty plan, a change, destroy
    e2e-tofu     the same under OpenTofu, which speaks the same plugin protocol
    sdk          the generated Python client against the same live API
    release      (not in the default set) the registry's release, made for real and signed with a key made
                 for the run: scripts/prepare_terraform_registry.py stages the public repository, which is
                 vetted and tested as its own workflow would, then built by GoReleaser from the same
                 .goreleaser.yml, and e2e/verify_release.py holds the files to what the registry reads.
                 Nothing is published: no token, no upload, no key of the owner's

Usage::

    .venv/bin/python tests/integration/terraform_run.py                       # every stage, in Docker
    .venv/bin/python tests/integration/terraform_run.py --stage acceptance -k TestAccApplication
    .venv/bin/python tests/integration/terraform_run.py --native              # go and terraform on PATH (CI)
    .venv/bin/python tests/integration/terraform_run.py --clean               # also remove the cache volume

Docker rules (the host's Docker holds other people's containers): every container, volume and image this
creates is named ``<prefix>-...`` and labelled ``noust.test=1``, nothing publishes a port (the clients share
the server container's network namespace, so ``127.0.0.1`` is the server), nothing is pruned, and only
what is named exactly here is removed.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parent.parent.parent
PROVIDER = REPO / "terraform-provider-noust"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_api  # noqa: E402 - the sibling module, importable once the directory is on the path

#: Names this lane's containers, volumes and images; ``--prefix`` changes it.
DEFAULT_PREFIX = "noust-test-jl"
LABEL = "noust.test=1"

GO_IMAGE = "golang:1.25"
TERRAFORM_IMAGE = "hashicorp/terraform:1.16.6"
OPENTOFU_IMAGE = "ghcr.io/opentofu/opentofu:1.12.8"
PYTHON_IMAGE = "python:3.12-slim-bookworm"
HELPER_IMAGE = "alpine:latest"

STAGES = ("unit", "docs", "acceptance", "e2e", "e2e-tofu", "sdk", "release")

#: What runs when no stage is named. The release stage downloads GoReleaser and builds thirteen binaries, which
#: is worth doing when the release machinery changes and before the owner publishes, and not on every run.
DEFAULT_STAGES = tuple(stage for stage in STAGES if stage != "release")

#: The version the release stage tags the staged repository with; it is never pushed.
RELEASE_VERSION = "0.1.0"


@dataclass
class Live:
    """A server the stages run against, and the tokens issued for them."""

    url: str
    tokens: dict[str, str]

    def env(self) -> dict[str, str]:
        """The environment the clients read: the server and one token per profile."""
        return {
            "NOUST_URL": self.url,
            "NOUST_TOKEN": self.tokens["provider"],
            "NOUST_TOKEN_NOSUDO": self.tokens["nosudo"],
            "NOUST_TOKEN_READ": self.tokens["read"],
            "NOUST_ACCOUNT": self.tokens["account"],
        }


def provider_permissions() -> list[str]:
    """
    Returns:
        What a token that runs any configuration holds: the union the provider's operations table
        computes (``permissions.json``, kept equal to it by a Go test).
    """
    report = json.loads((PROVIDER / "permissions.json").read_text(encoding="utf-8"))
    return list(report["all"])


def server_requirements() -> list[str]:
    """
    Returns:
        The Python packages the server needs, from ``pyproject.toml``: the dependencies and the ``all`` extra.
    """
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wanted = list(project["dependencies"]) + list(project["optional-dependencies"]["all"])
    return sorted(set(wanted))


def stage_for_registry(destination: Path) -> int:
    """
    Write the repository the registry would publish, with the script the owner uses.

    Args:
        destination: The directory to write.

    Returns:
        The script's exit status.
    """
    return subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "prepare_terraform_registry.py"),
            "--out",
            str(destination),
        ],
        stdout=subprocess.DEVNULL,
    ).returncode


class Backend:
    """Where the stages run: Docker containers, or this machine."""

    def start(self) -> contextlib.AbstractContextManager[Live]:
        raise NotImplementedError

    def go(
        self,
        args: Sequence[str],
        live: Live | None,
        extra_env: dict[str, str],
        mounts: Sequence[str] = (),
    ) -> int:
        raise NotImplementedError

    def e2e(self, live: Live, engine: str = "terraform") -> int:
        raise NotImplementedError

    def release(self) -> int:
        raise NotImplementedError

    def sdk(self, live: Live) -> int:
        raise NotImplementedError

    def close(self, *, clean: bool) -> None:
        """Remove what this run created."""


class DockerBackend(Backend):
    """Go, Terraform and Python in containers; the server in a container the clients share a network with."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.server = f"{prefix}-server"
        self.cache = f"{prefix}-gocache"
        self.dist = f"{prefix}-dist"
        self.config_dir = tempfile.mkdtemp(prefix="noust-docker-config-")
        # Public images need no login, and the host's credential helper (Docker Desktop's, from WSL) may
        # not run here: a private, empty configuration is the one that cannot fail on it.
        Path(self.config_dir, "config.json").write_text("{}\n", encoding="utf-8")
        self.created: list[tuple[str, str]] = []  # (kind, name), removed by close()

    # -- docker -------------------------------------------------------------------------------------

    def docker(
        self,
        *args: str,
        check: bool = True,
        capture: bool = True,
        stdin: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run the docker CLI with the private configuration."""
        return subprocess.run(
            ["docker", *args],
            env={**os.environ, "DOCKER_CONFIG": self.config_dir},
            input=stdin,
            text=True,
            capture_output=capture,
            check=check,
            timeout=timeout,
        )

    def has_image(self, image: str) -> bool:
        return self.docker("image", "inspect", image, check=False).returncode == 0

    def ensure_image(self, image: str) -> None:
        if not self.has_image(image):
            print(f"pulling {image}", flush=True)
            self.docker("pull", "-q", image, capture=False)

    def name(self, kind: str) -> str:
        return f"{self.prefix}-{kind}-{os.getpid()}"

    def volume(self, name: str) -> None:
        if self.docker("volume", "inspect", name, check=False).returncode != 0:
            self.docker("volume", "create", "--label", LABEL, name)
            # The containers run as an unprivileged user and write their caches here.
            self.docker(
                "run",
                "--rm",
                "--label",
                LABEL,
                "--name",
                self.name("chmod"),
                "-v",
                f"{name}:/v",
                HELPER_IMAGE,
                "chmod",
                "0777",
                "/v",
            )

    # -- the server ---------------------------------------------------------------------------------

    def build_server_image(self) -> str:
        """
        Returns:
            The tag of an image with the server's Python dependencies (the repository is mounted, not copied).
        """
        requirements = server_requirements()
        digest = hashlib.sha256("\n".join([PYTHON_IMAGE, *requirements]).encode()).hexdigest()[:12]
        tag = f"{self.prefix}-server:{digest}"
        if self.has_image(tag):
            return tag
        self.ensure_image(PYTHON_IMAGE)
        with tempfile.TemporaryDirectory(prefix="noust-server-image-") as context:
            Path(context, "requirements.txt").write_text(
                "\n".join(requirements) + "\n", encoding="utf-8"
            )
            Path(context, "Dockerfile").write_text(
                f"FROM {PYTHON_IMAGE}\n"
                "COPY requirements.txt /requirements.txt\n"
                "RUN pip install --no-cache-dir -r /requirements.txt\n"
                "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n",
                encoding="utf-8",
            )
            print(f"building {tag}", flush=True)
            self.docker("build", "-q", "--label", LABEL, "-t", tag, context, capture=False)
        self.created.append(("image", tag))
        return tag

    @contextlib.contextmanager
    def start(self) -> Iterator[Live]:
        image = self.build_server_image()
        print(f"starting {self.server}", flush=True)
        self.docker("rm", "-f", self.server, check=False)
        self.docker(
            "run",
            "-d",
            "--name",
            self.server,
            "--label",
            LABEL,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,mode=1777",
            "-u",
            "1000:1000",
            "-v",
            f"{REPO}:/repo:ro",
            "-w",
            "/repo",
            "-e",
            "PYTHONPATH=/repo/src:/repo",
            "-e",
            "NOUST_E2E_CODE_HOST=1",
            image,
            "python",
            "scripts/console_server.py",
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
        )
        try:
            details = self._wait_for_server()
            tokens = self._mint(details)
            yield Live(url=details["url"], tokens=tokens)
        finally:
            self.docker("rm", "-f", self.server, check=False)

    def _wait_for_server(self) -> dict[str, str]:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            logs = self.docker("logs", self.server, check=False)
            for line in (logs.stdout + logs.stderr).splitlines():
                if line.startswith("{") and '"url"' in line:
                    return json.loads(line)  # type: ignore[no-any-return]
            if (
                self.docker(
                    "inspect", "-f", "{{.State.Running}}", self.server, check=False
                ).stdout.strip()
                != "true"
            ):
                raise RuntimeError(
                    "the server container stopped:\n" + logs.stdout[-2000:] + logs.stderr[-2000:]
                )
            time.sleep(2)
        raise RuntimeError("the server printed no connection line in 180 seconds")

    def _mint(self, details: dict[str, str]) -> dict[str, str]:
        request = json.dumps(
            {
                "url": details["url"],
                "token": details["token"],
                "permissions": provider_permissions(),
            }
        )
        done = self.docker(
            "exec",
            "-i",
            self.server,
            "python",
            "-I",
            "/repo/tests/integration/live_api.py",
            "mint",
            stdin=request,
        )
        return json.loads(done.stdout)  # type: ignore[no-any-return]

    # -- the clients --------------------------------------------------------------------------------

    def _terraform_binary(self) -> None:
        """Put the Terraform CLI of the hashicorp/terraform image where the Go container can run it."""
        self.volume(self.cache)
        probe = self.docker(
            "run",
            "--rm",
            "--label",
            LABEL,
            "--name",
            self.name("probe"),
            "-v",
            f"{self.cache}:/cache",
            HELPER_IMAGE,
            "test",
            "-x",
            "/cache/tf-1.16.6/terraform",
            check=False,
        )
        if probe.returncode != 0:
            self.ensure_image(TERRAFORM_IMAGE)
            self.docker(
                "run",
                "--rm",
                "--label",
                LABEL,
                "--name",
                self.name("tfcopy"),
                "-v",
                f"{self.cache}:/cache",
                "--entrypoint",
                "sh",
                TERRAFORM_IMAGE,
                "-c",
                "mkdir -p /cache/tf-1.16.6 && cp /bin/terraform /cache/tf-1.16.6/terraform",
            )

    def go(
        self,
        args: Sequence[str],
        live: Live | None,
        extra_env: dict[str, str],
        mounts: Sequence[str] = (),
    ) -> int:
        self.ensure_image(GO_IMAGE)
        self._terraform_binary()
        env = {
            "GOCACHE": "/cache/build",
            "GOMODCACHE": "/cache/mod",
            "GOFLAGS": "-buildvcs=false",
            "HOME": "/cache/home",
            "CGO_ENABLED": "0",
            "NOUST_OPENAPI": "/openapi.json",
            "NOUST_REQUIRE_OPENAPI": "1",
            "TF_ACC_TERRAFORM_PATH": "/cache/tf-1.16.6/terraform",
            "PATH": "/cache/tf-1.16.6:/go/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "TF_ACC_PROVIDER_NAMESPACE": "perkybeet",
            "TF_IN_AUTOMATION": "1",
            **extra_env,
        }
        command = [
            "docker",
            "run",
            "--rm",
            "--label",
            LABEL,
            "--name",
            self.name("go"),
            "-u",
            "1000:1000",
            "-w",
            "/src",
            "-v",
            f"{PROVIDER}:/src",
            "-v",
            f"{REPO / 'panel' / 'openapi.json'}:/openapi.json:ro",
            "-v",
            f"{self.cache}:/cache",
        ]
        for mount in mounts:
            command += ["-v", mount]
        if live is not None:
            command += ["--network", f"container:{self.server}"]
        for key, value in {**env, **(live.env() if live else {})}.items():
            command += ["-e", f"{key}={value}"]
        command += [GO_IMAGE, *args]
        return subprocess.run(
            command, env={**os.environ, "DOCKER_CONFIG": self.config_dir}
        ).returncode

    def e2e(self, live: Live, engine: str = "terraform") -> int:
        self.volume(self.dist)
        built = self.go(
            ["go", "build", "-o", "/cache/dist/terraform-provider-noust", "."], None, {}
        )
        if built != 0:
            return built
        image = OPENTOFU_IMAGE if engine == "tofu" else TERRAFORM_IMAGE
        self.ensure_image(image)
        command = [
            "docker",
            "run",
            "--rm",
            "--label",
            LABEL,
            "--name",
            self.name("tf"),
            "--network",
            f"container:{self.server}",
            "--entrypoint",
            "sh",
            "--tmpfs",
            "/work:exec,mode=1777",
            "-u",
            "1000:1000",
            "-v",
            f"{PROVIDER / 'e2e'}:/e2e:ro",
            "-v",
            f"{PROVIDER / 'examples' / 'complete'}:/example:ro",
            "-v",
            f"{self.cache}:/cache:ro",
            "-e",
            "TF_IN_AUTOMATION=1",
            "-e",
            "TF_CLI_CONFIG_FILE=/work/terraformrc",
            "-e",
            f"TF_BIN={'tofu' if engine == 'tofu' else 'terraform'}",
        ]
        for key, value in live.env().items():
            command += ["-e", f"{key}={value}"]
        command += [image, "/e2e/run.sh", "/cache/dist"]
        return subprocess.run(
            command, env={**os.environ, "DOCKER_CONFIG": self.config_dir}
        ).returncode

    def release(self) -> int:
        with tempfile.TemporaryDirectory(
            prefix="noust-release-", ignore_cleanup_errors=True
        ) as scratch:
            staged = Path(scratch, "terraform-provider-noust")
            code = stage_for_registry(staged)
            if code != 0:
                return code
            return self.go(
                ["sh", "/e2e/release.sh", "/stage", RELEASE_VERSION],
                None,
                {},
                mounts=[f"{staged}:/stage", f"{PROVIDER / 'e2e'}:/e2e:ro"],
            )

    def sdk(self, live: Live) -> int:
        self.ensure_image(PYTHON_IMAGE)
        command = [
            "docker",
            "run",
            "--rm",
            "--label",
            LABEL,
            "--name",
            self.name("sdk"),
            "--network",
            f"container:{self.server}",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,mode=1777",
            "-u",
            "1000:1000",
            "-v",
            f"{REPO}:/repo:ro",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
        ]
        for key, value in live.env().items():
            command += ["-e", f"{key}={value}"]
        command += [PYTHON_IMAGE, "python", "-I", "/repo/tests/integration/sdk_live.py"]
        return subprocess.run(
            command, env={**os.environ, "DOCKER_CONFIG": self.config_dir}
        ).returncode

    def close(self, *, clean: bool) -> None:
        self.docker("rm", "-f", self.server, check=False)
        if clean:
            self.docker("volume", "rm", "-f", self.cache, self.dist, check=False)
            for kind, name in self.created:
                if kind == "image":
                    self.docker("rmi", name, check=False)
        shutil.rmtree(self.config_dir, ignore_errors=True)


class NativeBackend(Backend):
    """Go, Terraform and Python on this machine (a CI job that installed them)."""

    @contextlib.contextmanager
    def start(self) -> Iterator[Live]:
        with live_api.native_server() as server:
            tokens = live_api.mint_tokens(server.url, server.master_token, provider_permissions())
            yield Live(url=server.url, tokens=tokens)

    def go(
        self,
        args: Sequence[str],
        live: Live | None,
        extra_env: dict[str, str],
        mounts: Sequence[str] = (),
    ) -> int:
        env = {
            **os.environ,
            "NOUST_OPENAPI": str(REPO / "panel" / "openapi.json"),
            "NOUST_REQUIRE_OPENAPI": "1",
            "TF_ACC_PROVIDER_NAMESPACE": "perkybeet",
            "TF_IN_AUTOMATION": "1",
        }
        env.update(extra_env)
        if live is not None:
            env.update(live.env())
        return subprocess.run(list(args), cwd=PROVIDER, env=env).returncode

    def e2e(self, live: Live, engine: str = "terraform") -> int:
        with tempfile.TemporaryDirectory(prefix="noust-e2e-") as scratch:
            dist = Path(scratch, "dist")
            built = self.go(
                ["go", "build", "-o", str(dist / "terraform-provider-noust"), "."], None, {}
            )
            if built != 0:
                return built
            env = {
                **os.environ,
                **live.env(),
                "TF_IN_AUTOMATION": "1",
                "NOUST_E2E_EXAMPLE": str(PROVIDER / "examples" / "complete"),
                "NOUST_E2E_WORK": str(Path(scratch, "work")),
                "TF_BIN": "tofu" if engine == "tofu" else "terraform",
            }
            return subprocess.run(
                ["sh", str(PROVIDER / "e2e" / "run.sh"), str(dist)], cwd=PROVIDER, env=env
            ).returncode

    def release(self) -> int:
        with tempfile.TemporaryDirectory(
            prefix="noust-release-", ignore_cleanup_errors=True
        ) as scratch:
            staged = Path(scratch, "terraform-provider-noust")
            code = stage_for_registry(staged)
            if code != 0:
                return code
            return subprocess.run(
                ["sh", str(PROVIDER / "e2e" / "release.sh"), str(staged), RELEASE_VERSION],
                env={**os.environ, "GOFLAGS": "-buildvcs=false"},
            ).returncode

    def sdk(self, live: Live) -> int:
        return subprocess.run(
            [sys.executable, "-I", str(REPO / "tests" / "integration" / "sdk_live.py")],
            env={**os.environ, **live.env()},
        ).returncode


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the stages.

    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        0 when every stage passed, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--stage", action="append", choices=STAGES, help="a stage to run; all when omitted"
    )
    parser.add_argument(
        "--native",
        action="store_true",
        help="use go, terraform and python on this machine, no Docker",
    )
    parser.add_argument(
        "-k", dest="run", default="", help="go test -run pattern for the acceptance stage"
    )
    parser.add_argument("--prefix", default=os.environ.get("NOUST_TEST_PREFIX", DEFAULT_PREFIX))
    parser.add_argument(
        "--clean",
        action="store_true",
        help="also remove the build cache volume and the server image",
    )
    args = parser.parse_args(argv)
    stages = args.stage or list(DEFAULT_STAGES)

    backend: Backend = NativeBackend() if args.native else DockerBackend(args.prefix)
    results: dict[str, int] = {}
    try:
        if "unit" in stages:
            print("== unit", flush=True)
            results["unit"] = backend.go(
                [
                    "sh",
                    "-c",
                    'test -z "$(gofmt -l .)" || { gofmt -l .; exit 1; }; go vet ./... && go run honnef.co/go/tools/cmd/staticcheck@2025.1.1 ./... && go build ./... && go test ./...',
                ],
                None,
                {},
            )
        if "docs" in stages:
            print("== docs", flush=True)
            results["docs"] = backend.go(["go", "generate", "./..."], None, {})
        needs_server = [s for s in stages if s in ("acceptance", "e2e", "e2e-tofu", "sdk")]
        if needs_server:
            with backend.start() as live:
                if "acceptance" in needs_server:
                    print("== acceptance", flush=True)
                    pattern = ["-run", args.run] if args.run else []
                    results["acceptance"] = backend.go(
                        [
                            "go",
                            "test",
                            "-count=1",
                            "-timeout",
                            "40m",
                            "-v",
                            *pattern,
                            "./internal/provider/",
                        ],
                        live,
                        {"TF_ACC": "1"},
                    )
                if "e2e" in needs_server:
                    print("== e2e", flush=True)
                    results["e2e"] = backend.e2e(live)
                if "e2e-tofu" in needs_server:
                    print("== e2e-tofu", flush=True)
                    results["e2e-tofu"] = backend.e2e(live, "tofu")
                if "sdk" in needs_server:
                    print("== sdk", flush=True)
                    results["sdk"] = backend.sdk(live)
        if "release" in stages:
            print("== release", flush=True)
            results["release"] = backend.release()
    finally:
        backend.close(clean=args.clean)
    for stage, code in results.items():
        print(f"{'PASS' if code == 0 else 'FAIL'}  {stage}")
    return 0 if all(code == 0 for code in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
