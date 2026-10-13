# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Making the image of a container application: a Dockerfile, Railpack, or a registry.

Every image a container application runs is named ``noust-<app>:<release>``
(see :mod:`noust.deployers.helpers.container_compose`), however it came to
exist: built from the repository's Dockerfile, built by Railpack, or pulled
from a registry and named for the release. That one name is what makes a
release an image, and going back to one a rename of a compose file.

**The build runs in the Docker daemon, not in the build sandbox.** This is the
documented exception (decision D2 of the 3.4 design) every Compose build
already is: ``docker build`` hands the Dockerfile to a daemon that is root, so
the sandbox of ``noust-build`` cannot hold it. What this module does about it
is what can be done from here: it never reads a file the repository points out
of itself (a Dockerfile or a context that is a link is refused), it never puts a
secret in a command line, and it hands the daemon nothing of Noust's own
environment.

**Secrets never reach argv.** A build secret is mounted from the 0600 file of the
secret store (``--secret id=...,src=...``), never ``--build-arg``, which lands in
the image's history; Railpack reads its secrets from the environment of the
command. A registry password goes in on stdin to a ``docker login`` that writes
to a configuration directory of its own, which is removed as soon as the
command that needed it is over: nothing is left in ``/root/.docker``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from noust.core.container_store import (
    ContainerSettings,
    build_secret_name,
)
from noust.core.exceptions import (
    DeploymentError,
    DockerError,
    NoustError,
    SecurityError,
    ValidationError,
)
from noust.core.fs import SECRET_DIR_MODE, FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.core.secrets import SecretStore
from noust.deployers.helpers.container_compose import image_repository, release_of_tag
from noust.managers import railpack
from noust.validators.container import METHOD_RAILPACK
from noust.validators.names import resolve_within

#: Building images pulls layers and compiles; give it room but not forever.
BUILD_TIMEOUT = 1800

#: Pulling an image, and logging in to the registry that has it.
PULL_TIMEOUT = 900
LOGIN_TIMEOUT = 60

#: Reading what Docker has, and Railpack's analysis of a repository.
QUERY_TIMEOUT = 60
PLAN_TIMEOUT = 300

#: The names a Dockerfile has: Docker's, and the one other tools use.
DOCKERFILE_NAMES = ("Dockerfile", "Containerfile")

#: What ``docker inspect`` is asked of an image: its id, the digests a registry
#: knows it by and the ports it says it listens on, as JSON so no separator can
#: be confused.
_INSPECT_FORMAT = "{{.Id}}|{{json .RepoDigests}}|{{json .Config.ExposedPorts}}"

#: The environment every ``docker build`` is given: BuildKit, for secrets and
#: cache mounts, whatever the daemon's own default is.
_BUILD_ENV = {"DOCKER_BUILDKIT": "1"}


#: ``EXPOSE 8080`` or ``EXPOSE 8080/tcp``: where a Dockerfile says its process listens.
_EXPOSE = re.compile(r"^[ \t]*EXPOSE[ \t]+(\d{1,5})(?:/tcp)?(?=\s|$)", re.IGNORECASE | re.MULTILINE)


def exposed_port(dockerfile: str) -> int | None:
    """
    Read the port a Dockerfile says its process listens on.

    Args:
        dockerfile: The Dockerfile's text.

    Returns:
        The first TCP port of its first ``EXPOSE``, or None when it names none
        (a variable, a UDP port, no instruction).
    """
    found = _EXPOSE.search(dockerfile)
    if found is None:
        return None
    port = int(found.group(1))
    return port if 1 <= port <= 65535 else None


@dataclass(frozen=True)
class PulledImage:
    """
    An image taken from a registry.

    Attributes:
        reference: What was pulled, as the application records it.
        image_id: The image's id on this host.
        digest: The digest the registry knows it by, or None when it has none
            (an image that never came from one).
        exposed_ports: The TCP ports the image says it listens on.
    """

    reference: str
    image_id: str
    digest: str | None
    exposed_ports: tuple[int, ...] = ()


def _tail(text: str, lines: int = 40) -> str:
    """
    Keep the end of an output, which is where a failure is.

    Args:
        text: The output.
        lines: How many lines to keep.

    Returns:
        The last lines.
    """
    kept = text.strip().splitlines()[-lines:]
    return "\n".join(kept)


class ImageBuilder:
    """Builds, pulls, lists and removes the images of one container application."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        logger: Logger,
        app_name: str,
        domain: str,
        app_path: Path,
        secrets: SecretStore | None = None,
        work_dir: Path,
    ) -> None:
        """
        Args:
            runner: Runs docker and railpack; the process-wide one when None.
            fs: Every file change goes through it; the process-wide one when None.
            logger: Where a build's output is streamed.
            app_name: The application's name, which names its images.
            domain: The application's domain.
            app_path: The application directory, where the repository is.
            secrets: Where build secrets and the registry password are kept;
                the process-wide store when None.
            work_dir: A private directory for Railpack's plan, removed after use.
        """
        self._runner = runner
        self._fs = fs
        self.logger = logger
        self.app_name = app_name
        self.domain = domain
        self.app_path = app_path
        self._secrets = secrets
        self.work_dir = work_dir

    @property
    def runner(self) -> CommandRunner:
        """The runner everything is executed through."""
        return self._runner if self._runner is not None else get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem every change goes through."""
        return self._fs if self._fs is not None else get_fs()

    @property
    def secrets(self) -> SecretStore:
        """The secret store."""
        if self._secrets is None:
            self._secrets = SecretStore()
        return self._secrets

    # -- The registry account ------------------------------------------------

    @contextmanager
    def registry_session(self, settings: ContainerSettings) -> Iterator[dict[str, str]]:
        """
        Log in to the registry an application has an account at, for one command.

        Args:
            settings: The application's settings.

        Yields:
            The environment the docker commands inside the block are run with:
            empty without an account, ``DOCKER_CONFIG`` pointing at a
            directory that holds the login with one. The directory is removed
            when the block ends, however it ends.

        Raises:
            DeploymentError: The password of the account is not in the secret store.
            DockerError: The registry did not accept the account.
        """
        if not settings.has_account or settings.registry_secret is None:
            yield {}
            return
        server = settings.account_server() or "docker.io"
        password = self.secrets.read(settings.registry_secret)
        if not password:
            raise DeploymentError(
                f"The password of the {server} account is missing",
                details="Give it again so it is kept in the secret store: noust app container "
                f"set {self.domain} --registry-user USER --registry-password-stdin",
            )
        config = self.secrets.root / "container" / self.app_name / "docker"
        self.fs.make_dir(config, mode=SECRET_DIR_MODE, parents=True)
        env = {"DOCKER_CONFIG": str(config)}
        try:
            login = self.runner.run(
                [
                    "docker",
                    "login",
                    server,
                    "--username",
                    settings.registry_username or "",
                    "--password-stdin",
                ],
                input=password,
                env=env,
                secrets=[password],
                timeout=LOGIN_TIMEOUT,
            )
            if not login.success:
                raise DockerError(
                    f"{server} did not accept the account {settings.registry_username}",
                    details=(login.stderr or login.stdout).replace(password, "***").strip(),
                )
            yield env
        finally:
            self.fs.remove_tree(config)

    # -- Building ------------------------------------------------------------

    def build(self, settings: ContainerSettings, tag: str, *, no_cache: bool = False) -> None:
        """
        Build the image of a release from the repository.

        Args:
            settings: The application's settings; ``dockerfile`` or ``railpack``.
            tag: The image's name, ``noust-<app>:<release>``.
            no_cache: Ignore BuildKit's layer cache and pull the base images
                again.

        Raises:
            DeploymentError: A path leaves the repository, there is no Dockerfile,
                Railpack is not installed, or a secret has no value.
            DockerError: The build failed; the daemon's output is carried.
        """
        with self.registry_session(settings) as login_env:
            if settings.method == METHOD_RAILPACK:
                self._build_railpack(settings, tag, login_env, no_cache=no_cache)
            else:
                self._build_dockerfile(settings, tag, login_env, no_cache=no_cache)

    def _inside(self, relative: str, what: str) -> Path:
        """
        Resolve a path of the repository, refusing any way out of it.

        Args:
            relative: The path, relative to the repository.
            what: What it is, for the error.

        Returns:
            The resolved path.

        Raises:
            DeploymentError: A link in the repository resolves it elsewhere.
        """
        if relative == ".":
            return self.app_path.resolve()
        try:
            return resolve_within(self.app_path, relative)
        except (SecurityError, ValidationError) as exc:
            raise DeploymentError(
                f"The {what} {relative!r} resolves outside the repository",
                details=f"A link in the repository leads out of {self.app_path}. The daemon that "
                "builds is root: commit the file itself instead of a link to it.",
            ) from exc

    def _dockerfile(self, settings: ContainerSettings, context: Path) -> Path:
        """
        Find the Dockerfile of a build.

        Args:
            settings: The application's settings.
            context: The resolved build context.

        Returns:
            The Dockerfile.

        Raises:
            DeploymentError: There is none, or it resolves outside the repository.
        """
        if settings.dockerfile is not None:
            found = self._inside(settings.dockerfile, "Dockerfile")
            if not found.is_file():
                raise DeploymentError(
                    f"The Dockerfile {settings.dockerfile!r} is not in the repository",
                    details="Check the path is relative to the repository root and that the file "
                    "is committed on the branch being deployed.",
                )
            return found
        for name in DOCKERFILE_NAMES:
            relative = name if settings.context == "." else f"{settings.context}/{name}"
            if (context / name).is_file():
                return self._inside(relative, "Dockerfile")
        raise DeploymentError(
            "No Dockerfile found in the repository",
            details=f"Looked for {' and '.join(DOCKERFILE_NAMES)} in {context}. Name the "
            "Dockerfile with --dockerfile, or build without one: install Railpack with "
            f"'noust setup railpack' and deploy with --builder railpack.",
        )

    def locate_dockerfile(self, settings: ContainerSettings) -> Path:
        """
        Find the Dockerfile a build uses.

        Args:
            settings: The application's settings.

        Returns:
            The Dockerfile, resolved inside the repository.

        Raises:
            DeploymentError: There is none, or a link leads out of the repository.
        """
        return self._dockerfile(settings, self._inside(settings.context, "build context"))

    def _secret_sources(self, settings: ContainerSettings) -> list[tuple[str, Path]]:
        """
        Name the files the build's secrets are mounted from.

        Args:
            settings: The application's settings.

        Returns:
            ``(id, path)`` for each secret, in order.

        Raises:
            DeploymentError: A secret has no value.
        """
        sources = []
        for secret_id in settings.build_secrets:
            name = build_secret_name(self.app_name, secret_id)
            if self.secrets.read(name) is None:
                raise DeploymentError(
                    f"The build secret {secret_id} has no value",
                    details=f"Give it one: noust app container set {self.domain} "
                    f"--build-secret {secret_id}=FILE",
                )
            sources.append((secret_id, self.secrets.usable_path(name)))
        return sources

    def _discard_sources(self, settings: ContainerSettings) -> None:
        """
        Remove the decrypted copies a sealed store made for a build.

        Args:
            settings: The application's settings.
        """
        for secret_id in settings.build_secrets:
            with suppress(NoustError):
                self.secrets.discard_usable_copy(build_secret_name(self.app_name, secret_id))

    def _build_dockerfile(
        self,
        settings: ContainerSettings,
        tag: str,
        login_env: Mapping[str, str],
        *,
        no_cache: bool = False,
    ) -> None:
        """
        Build with the repository's Dockerfile.

        Args:
            settings: The application's settings.
            tag: The image's name.
            login_env: The registry session's environment.
            no_cache: Ignore BuildKit's layer cache and pull the base images again.

        Raises:
            DeploymentError: See :meth:`build`.
            DockerError: The build failed.
        """
        context = self._inside(settings.context, "build context")
        dockerfile = self._dockerfile(settings, context)
        argv = [
            "docker",
            "build",
            "--progress=plain",
            "--file",
            str(dockerfile),
            "--tag",
            tag,
        ]
        if no_cache:
            argv += ["--no-cache", "--pull"]
        if settings.target:
            argv += ["--target", settings.target]
        for name, value in settings.build_args.items():
            argv += ["--build-arg", f"{name}={value}"]
        try:
            for secret_id, source in self._secret_sources(settings):
                argv += ["--secret", f"id={secret_id},src={source}"]
            argv.append(str(context))
            self._run_build(argv, {**_BUILD_ENV, **login_env})
        finally:
            self._discard_sources(settings)

    def _run_build(
        self, argv: list[str], env: Mapping[str, str], secrets: Sequence[str] = ()
    ) -> None:
        """
        Run ``docker build``, streaming what it prints.

        Args:
            argv: The command.
            env: Its environment.
            secrets: Values to keep out of what is recorded of the command.

        Raises:
            DockerError: It failed; the daemon's own output is the details.
        """
        result = self.runner.stream(
            argv, on_line=self.logger.debug, env=env, timeout=BUILD_TIMEOUT, secrets=secrets
        )
        if not result.success:
            raise DockerError(
                "Failed to build the Docker image",
                details=_tail(result.stdout or result.stderr) or "docker build printed nothing.",
            )
        self.logger.substep("Image built")

    def _build_railpack(
        self,
        settings: ContainerSettings,
        tag: str,
        login_env: Mapping[str, str],
        *,
        no_cache: bool = False,
    ) -> None:
        """
        Build with Railpack: its plan, then Docker through its BuildKit frontend.

        Railpack takes its variables as ``--env NAME=VALUE`` and its secrets as
        ``--env NAME``, reading the value from its own environment; every name
        becomes a secret of the plan, which Docker then has to be given. A
        value is never in Docker's command line, and Docker's variables carry
        generated names (``NOUST_BUILD_SECRET_<n>``), so what a build is called
        cannot steer the daemon's client.

        Args:
            settings: The application's settings.
            tag: The image's name.
            login_env: The registry session's environment.
            no_cache: Ignore BuildKit's layer cache and pull the base images again.

        Raises:
            DeploymentError: Railpack is not installed, or a secret has no value.
            DockerError: Analysing the repository or building failed.
        """
        if not self.runner.exists(railpack.BINARY_NAME):
            raise DeploymentError(
                "Railpack is not installed on this server",
                details="Install the pinned release with: noust setup railpack. Or build from a "
                "Dockerfile, or deploy an image from a registry.",
            )
        context = self._inside(settings.context, "build context")
        version = railpack.installed_version(self.runner) or railpack.VERSION
        secret_values: dict[str, str] = {}
        for secret_id in settings.build_secrets:
            value = self.secrets.read(build_secret_name(self.app_name, secret_id))
            if value is None:
                raise DeploymentError(
                    f"The build secret {secret_id} has no value",
                    details=f"Give it one: noust app container set {self.domain} "
                    f"--build-secret {secret_id}=FILE",
                )
            secret_values[secret_id] = value
        every = {**settings.build_args, **secret_values}
        bridge = {f"NOUST_BUILD_SECRET_{n}": value for n, value in enumerate(every.values())}

        self.fs.make_dir(self.work_dir, mode=SECRET_DIR_MODE, parents=True)
        plan = self.work_dir / "railpack-plan.json"
        info = self.work_dir / "railpack-info.json"
        try:
            prepare = [
                railpack.BINARY_NAME,
                "prepare",
                str(context),
                "--plan-out",
                str(plan),
                "--info-out",
                str(info),
            ]
            for name, value in settings.build_args.items():
                prepare += ["--env", f"{name}={value}"]
            for name in secret_values:
                prepare += ["--env", name]
            analysed = self.runner.stream(
                prepare,
                on_line=self.logger.debug,
                env=secret_values,
                timeout=PLAN_TIMEOUT,
                secrets=list(secret_values.values()),
                # It reads a repository nobody here wrote: not with Noust's own environment.
                clean_env=True,
            )
            if not analysed.success:
                raise DockerError(
                    "Railpack could not make a build plan for the repository",
                    details=_tail(analysed.stderr or analysed.stdout)
                    or "railpack prepare printed nothing.",
                )
            argv = [
                "docker",
                "build",
                "--progress=plain",
                "--build-arg",
                f"BUILDKIT_SYNTAX={railpack.frontend_reference(version)}",
                "--file",
                str(plan),
                "--tag",
                tag,
            ]
            if no_cache:
                argv += ["--no-cache", "--pull"]
            for (name, _), variable in zip(every.items(), bridge, strict=True):
                argv += ["--secret", f"id={name},env={variable}"]
            if every:
                # BuildKit keeps a layer when only a secret changed; this makes
                # it a different build.
                digest = hashlib.sha256(
                    "\n".join(f"{name}={value}" for name, value in sorted(every.items())).encode()
                ).hexdigest()
                argv += ["--build-arg", f"secrets-hash={digest}"]
            argv.append(str(context))
            self._run_build(
                argv, {**_BUILD_ENV, **login_env, **bridge}, secrets=list(bridge.values())
            )
        finally:
            self.fs.remove_tree(self.work_dir)

    # -- Pulling -------------------------------------------------------------

    def pull(self, settings: ContainerSettings, tag: str) -> PulledImage:
        """
        Pull an image from a registry and name it for a release.

        Args:
            settings: The application's settings; ``image`` is what is pulled.
            tag: The name to give it, ``noust-<app>:<release>``.

        Returns:
            What was pulled.

        Raises:
            DeploymentError: The account's password is missing.
            DockerError: The registry refused the account or the pull failed, or
                the pulled image cannot be read.
        """
        reference = settings.image or ""
        with self.registry_session(settings) as login_env:
            result = self.runner.stream(
                ["docker", "pull", reference],
                on_line=self.logger.debug,
                env=login_env or None,
                timeout=PULL_TIMEOUT,
            )
            if not result.success:
                raise DockerError(
                    f"Failed to pull {reference}",
                    details=_tail(result.stdout or result.stderr)
                    + f"\n\nIf the image is private, give the registry account: noust app "
                    f"container set {self.domain} --registry-user USER --registry-password-stdin",
                )
        inspected = self.runner.run(
            ["docker", "inspect", "--type", "image", "--format", _INSPECT_FORMAT, reference],
            timeout=QUERY_TIMEOUT,
        )
        if not inspected.success:
            raise DockerError(
                f"{reference} was pulled but Docker cannot describe it",
                details=(inspected.stderr or inspected.stdout).strip(),
            )
        image_id, digest, ports = self._read_inspection(inspected, reference)
        named = self.runner.run(["docker", "image", "tag", image_id, tag], timeout=QUERY_TIMEOUT)
        if not named.success:
            raise DockerError(
                f"The pulled image could not be named {tag}",
                details=(named.stderr or named.stdout).strip(),
            )
        self.logger.substep(f"Pulled {reference}" + (f" ({digest})" if digest else ""))
        return PulledImage(
            reference=reference, image_id=image_id, digest=digest, exposed_ports=ports
        )

    @staticmethod
    def _read_inspection(
        inspected: CommandResult, reference: str
    ) -> tuple[str, str | None, tuple[int, ...]]:
        """
        Read an image's id, the digest of the repository it was pulled from and its ports.

        Args:
            inspected: What ``docker inspect`` printed.
            reference: The reference that was pulled.

        Returns:
            ``(id, digest, ports)``; the digest is None when the image has none.

        Raises:
            DockerError: The output is not what the format asked for.
        """
        image_id, _, rest = inspected.stdout.strip().partition("|")
        raw_digests, _, raw_ports = rest.partition("|")
        try:
            digests = json.loads(raw_digests) if raw_digests else []
            exposed = json.loads(raw_ports) if raw_ports else {}
        except json.JSONDecodeError as exc:
            raise DockerError(
                f"Docker described {reference} in a way Noust cannot read", details=rest
            ) from exc
        if not image_id or not isinstance(digests, list):
            raise DockerError(f"Docker did not give the id of {reference}", details=rest)
        # The digest of this repository, not of another the same image is also in.
        name = reference.split("@", 1)[0]
        last_slash = name.rfind("/")
        if ":" in name[last_slash + 1 :]:
            name = name[: name.rfind(":")]
        listed = [d for d in digests if isinstance(d, str)]
        own = [d for d in listed if d.split("@", 1)[0].endswith(name)]
        chosen = (own or listed or [""])[0]
        ports = sorted(
            {
                int(key.split("/", 1)[0])
                for key in (exposed if isinstance(exposed, dict) else {})
                if isinstance(key, str) and key.endswith("/tcp") and key.split("/", 1)[0].isdigit()
            }
        )
        return image_id, chosen.split("@", 1)[1] if "@" in chosen else None, tuple(ports)

    # -- What is on the host -------------------------------------------------

    def present_releases(self) -> set[str]:
        """
        List the releases of this application whose image is on this host.

        Returns:
            Their ids.

        Raises:
            DockerError: Docker could not list its images.
        """
        listed = self.runner.run(
            [
                "docker",
                "images",
                "--format",
                "{{.Repository}}:{{.Tag}}",
                "--filter",
                f"reference={image_repository(self.app_name)}:*",
            ],
            timeout=QUERY_TIMEOUT,
        )
        if not listed.success:
            raise DockerError(
                "Docker could not list the images of this application",
                details=(listed.stderr or listed.stdout).strip(),
            )
        found = (release_of_tag(self.app_name, line.strip()) for line in listed.stdout.splitlines())
        return {release for release in found if release is not None}

    def remove_images(self, releases: Iterable[str]) -> list[str]:
        """
        Remove the images of some releases.

        Only the name is removed: an image another name or a container still
        uses stays, which Docker says, and is reported rather than forced.

        Args:
            releases: The release ids.

        Returns:
            One line for each image that could not be removed; empty when all were.
        """
        failures: list[str] = []
        for release in releases:
            tag = f"{image_repository(self.app_name)}:{release}"
            result = self.runner.run(["docker", "image", "rm", tag], timeout=QUERY_TIMEOUT)
            if not result.success:
                failures.append(f"{tag}: {(result.stderr or result.stdout).strip()}")
        return failures
