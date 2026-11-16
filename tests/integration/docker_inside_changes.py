#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The scenarios of what changes Docker, run inside an engine (``docker_run.py``).

The local registry with its small images, a registry asked for a new version with buildx and without it, and a
Compose document read on the standard input and rehearsed (``config`` and ``up --dry-run``) by each Compose
the engines carry.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

from docker_inside_lib import (
    BUSYBOX,
    LABEL,
    REGISTRY,
    SMALL,
    WORK,
    Run,
    compose_version_short,
    docker,
    make_store,
    must,
    numbers,
    project_down,
    reset,
    runner,
    scenario,
    up,
    wait_for,
    web_service,
    write_stack,
)
from docker_inside_zone import run_labelled

# ====================================================================== the images of the registry


def build_and_push(tag: str, dockerfile: str) -> None:
    """
    Build a small image and push it to the local registry.

    Args:
        tag: ``w3rd/name:tag`` below the registry.
        dockerfile: Its Dockerfile.
    """
    folder = WORK / "_seed" / tag.replace("/", "_").replace(":", "_")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    name = f"{REGISTRY}/{tag}"
    # Built and pushed under a name of its own and then given the tag in the registry, as a publisher does: a
    # build under the tag itself would move it on this machine, and removing it afterwards would take it from the
    # image a container runs, which a real registry never does to anybody's machine.
    unique = f"{REGISTRY}/w3rd/build:{os.getpid()}-{time.monotonic_ns()}"
    must(
        docker("build", "-q", "--label", LABEL, "-t", unique, str(folder), timeout=300),
        f"build {name}",
    )
    must(docker("push", "-q", unique, timeout=300), f"push {name}")
    must(
        docker("buildx", "imagetools", "create", "-t", name, unique, timeout=120),
        f"giving {name} to what was pushed",
    )
    docker("rmi", unique)


def small_dockerfile(version: str, command: str = "") -> str:
    """
    Args:
        version: The version the image says it is, by its label and its variable.
        command: What it runs, as a ``CMD`` line; it sleeps by default.

    Returns:
        A Dockerfile for a few-kilobyte image of that version.
    """
    return (
        f"FROM {BUSYBOX}\n"
        f"ENV SMALL_VERSION={version}\n"
        f'LABEL org.opencontainers.image.version="{version}"\n'
        f"RUN echo {version} > /version\n"
        + (command or 'CMD ["sh", "-c", "echo small $SMALL_VERSION; exec sleep 3600"]\n')
    )


def web_dockerfile(page: str, serves: bool = True) -> str:
    """
    Args:
        page: What the page says, ending in its version.
        serves: Whether it listens on 8080 at all.

    Returns:
        A Dockerfile for an image that exposes 8080 and, when it serves, answers ``page``.
    """
    command = (
        'CMD ["sh", "-c", "exec httpd -f -p 8080 -h /www"]\n'
        if serves
        else 'CMD ["sh", "-c", "exec sleep 3600"]\n'
    )
    return (
        f"FROM {BUSYBOX}\nEXPOSE 8080\nRUN mkdir -p /www && echo '{page}' > /www/index.html\n"
        f'LABEL org.opencontainers.image.version="{page.split()[-1]}"\n' + command
    )


@scenario("_seed")
def seed(run: Run) -> None:
    """Fill the engine's local registry with the small images the scenarios use."""

    def ready() -> bool:
        try:
            with urllib.request.urlopen(f"http://{REGISTRY}/v2/", timeout=3) as response:
                return bool(response.status == 200)
        except OSError:
            return False

    run.check("the local registry answers", wait_for(ready, 60), "")
    if docker("buildx", "imagetools", "inspect", f"{SMALL}:multi").returncode == 0:
        run.check("the registry is already seeded", True)
        return
    # Docker Hub is a network away and a copy between two registries is long enough to be cut once.
    for attempt in range(3):
        copied = docker(
            "buildx",
            "imagetools",
            "create",
            "-t",
            BUSYBOX,
            "docker.io/library/busybox:1.36",
            timeout=600,
        )
        if copied.returncode == 0:
            break
        time.sleep(5 * (attempt + 1))
    must(copied, "copying busybox to the local registry")
    for tag, version in (
        ("1", "1.0.0"),
        ("2", "1.0.1"),
        ("3", "1.1.0"),
        ("4", "2.0.0"),
        ("stable", "1.0.0"),
    ):
        build_and_push(f"w3rd/small:{tag}", small_dockerfile(version))
    build_and_push(
        "w3rd/small:bad", small_dockerfile("1.0.9", 'CMD ["sh", "-c", "echo boom >&2; exit 3"]\n')
    )
    build_and_push("w3rd/web:1", web_dockerfile("web v1"))
    build_and_push("w3rd/web:2", web_dockerfile("web v2"))
    build_and_push("w3rd/web:broken", web_dockerfile("web v3", serves=False))
    raw = json.loads(
        must(docker("buildx", "imagetools", "inspect", "--raw", BUSYBOX), "busybox's index")
    )
    digests = {
        f"{m['platform']['os']}/{m['platform']['architecture']}": m["digest"]
        for m in raw.get("manifests", [])
        if m.get("platform", {}).get("os") == "linux"
    }
    amd, arm = digests.get("linux/amd64"), digests.get("linux/arm64")
    run.check("busybox has an amd64 and an arm64 image", bool(amd and arm), sorted(digests))
    must(
        docker(
            "buildx",
            "imagetools",
            "create",
            "-t",
            f"{SMALL}:multi",
            f"{REGISTRY}/w3rd/busybox@{amd}",
            f"{REGISTRY}/w3rd/busybox@{arm}",
            timeout=300,
        ),
        "the tag of two platforms",
    )
    run.check(
        "the registry is seeded",
        docker("buildx", "imagetools", "inspect", f"{SMALL}:multi").returncode == 0,
        "",
    )


def retag(floating: str, to: str) -> None:
    """
    Move a tag of the local registry to what another tag holds, in the registry: what a publisher does to a
    floating tag, with no change to any tag on this machine.

    Args:
        floating: The tag that moves.
        to: The tag it comes to hold.
    """
    must(
        docker(
            "buildx",
            "imagetools",
            "create",
            "-t",
            f"{SMALL}:{floating}",
            f"{SMALL}:{to}",
            timeout=120,
        ),
        f"moving {floating} to {to}",
    )


@scenario("_push")
def push(run: Run) -> None:
    """Build one small image and push it to the local registry, as its arguments in ``W3RD_ARG`` say."""
    arg = json.loads(os.environ["W3RD_ARG"])
    if arg["kind"] == "web":
        dockerfile = web_dockerfile(arg["page"], serves=arg.get("serves", True))
    else:
        dockerfile = small_dockerfile(arg["version"], arg.get("command", ""))
    build_and_push(arg["tag"], dockerfile)
    run.check(
        f"{arg['tag']} is in the local registry",
        docker("buildx", "imagetools", "inspect", f"{REGISTRY}/{arg['tag']}").returncode == 0,
    )


# ====================================================================== 7. new versions of images

PLUGINS = Path("/usr/local/libexec/docker/cli-plugins")


class NoBuildx:
    """The engine's own buildx plugin put aside, as in a Docker that has none, and put back."""

    def __enter__(self) -> NoBuildx:
        self.moved = PLUGINS / "docker-buildx"
        self.aside = PLUGINS / "docker-buildx.w3rd-off"
        if self.moved.exists():
            self.moved.rename(self.aside)
        return self

    def __exit__(self, *_: object) -> None:
        if self.aside.exists():
            self.aside.rename(self.moved)


@scenario("versions")
def versions(run: Run) -> None:
    """A registry is asked what a tag is now, with buildx and without it, and what is new is found."""
    from noust.core.docker import registry as registries
    from noust.core.docker.gate import DockerGate
    from noust.core.docker.owners import Registry
    from noust.core.docker.versions import parse_reference
    from noust.managers.docker_image_versions import ImageVersions

    reset("versions")
    real = runner()
    store = make_store("versions")
    try:
        have_buildx = docker("buildx", "version").returncode == 0
        run.note("docker buildx version", docker("buildx", "version").stdout.strip()[:120])
        ref = parse_reference(f"{SMALL}:1")
        run.check("a reference to the local registry is read", ref is not None, "")
        assert ref is not None
        must(docker("pull", "-q", f"{SMALL}:1"), "pull small:1")
        truth = (
            docker("inspect", "--format", "{{index .RepoDigests 0}}", f"{SMALL}:1")
            .stdout.strip()
            .partition("@")[2]
        )

        if have_buildx:
            run.note(
                "imagetools inspect, one platform",
                docker("buildx", "imagetools", "inspect", f"{SMALL}:1").stdout[:700],
            )
            found = registries.look_at_registry(real, ref)
            run.check(
                "the registry is asked without a download",
                found.ok and found.method == "imagetools",
                found,
            )
            run.check(
                "the digest is the one Docker pulls", found.digest == truth, (found.digest, truth)
            )
            run.check(
                "and what the image says of itself is read",
                found.created is not None
                and found.labels.get("org.opencontainers.image.version") == "1.0.0"
                and "SMALL_VERSION=1.0.0" in found.env,
                found,
            )
            run.note(
                "imagetools inspect --format '{{json .Image}}', one platform",
                docker(
                    "buildx", "imagetools", "inspect", "--format", "{{json .Image}}", f"{SMALL}:1"
                ).stdout[:400],
            )
            multi_ref = parse_reference(f"{SMALL}:multi")
            assert multi_ref is not None
            multi = registries.look_at_registry(real, multi_ref, platform="linux/amd64")
            run.note(
                "imagetools inspect, several platforms",
                docker("buildx", "imagetools", "inspect", f"{SMALL}:multi").stdout[:900],
            )
            run.note(
                "imagetools inspect --format '{{json .Image}}', several platforms",
                docker(
                    "buildx",
                    "imagetools",
                    "inspect",
                    "--format",
                    "{{json .Image}}",
                    f"{SMALL}:multi",
                ).stdout[:500],
            )
            run.check(
                "a tag of several platforms is read too",
                multi.ok and multi.digest is not None and multi.created is not None,
                multi,
            )
            gone = registries.look_at_registry(
                real, parse_reference(f"{REGISTRY}/w3rd/small:nope") or ref
            )
            run.check(
                "a tag that is not there says so", not gone.ok and gone.why == "not_found", gone
            )
            nobody = registries.look_at_registry(
                real, parse_reference("localhost:5999/w3rd/small:1") or ref
            )
            run.check(
                "a registry that does not answer says so",
                not nobody.ok and nobody.why in ("unreachable", "denied"),
                nobody,
            )
            run.note("a registry that does not answer", nobody.detail)
            pinned = registries.look_at_registry(real, parse_reference(f"{SMALL}@{truth}") or ref)
            run.check(
                "a digest is answered without asking",
                pinned.ok and pinned.digest == truth and pinned.method == "",
                pinned,
            )
        else:
            run.skip("asking with buildx", "this engine has no buildx plugin")

        with NoBuildx():
            blind = registries.look_at_registry(real, ref)
            run.check(
                "without buildx the question cannot be asked and the answer says why",
                not blind.ok and blind.why == "unsupported",
                blind,
            )
            run.note("without buildx", blind.detail)
            pulled = registries.look_at_registry(real, ref, pull=True)
            run.check(
                "and --pull downloads and compares",
                pulled.ok and pulled.method == "pull" and pulled.digest == truth,
                pulled,
            )

        retag("stable", "1")
        must(docker("pull", "-q", f"{SMALL}:stable"), "pull stable")
        run_labelled("w3rd-v-stable", "versions", f"{SMALL}:stable", "sleep", "3600")
        run_labelled("w3rd-v-one", "versions", f"{SMALL}:1", "sleep", "3600")
        retag("stable", "2")
        gate = DockerGate(runner=real, store=store, registry=lambda: Registry())
        checker = ImageVersions(gate, store=store, runner=real)
        if have_buildx:
            checked = checker.check()
            by_ref = {item.reference: item for item in checked.records}
            run.note("the look", checked.to_dict())
            stable = by_ref.get(f"{SMALL}:stable")
            one = by_ref.get(f"{SMALL}:1")
            run.check(
                "a tag that moved is newer, and it is a patch",
                stable is not None and stable.state == "newer" and stable.change == "patch",
                stable,
            )
            run.check(
                "and says from which version to which",
                stable is not None
                and stable.current_version == "1.0.0"
                and stable.latest_version == "1.0.1",
                stable,
            )
            run.check(
                "a tag that did not move is current",
                one is not None and one.state == "current",
                one,
            )
        else:
            run.skip("the look at what moved", "no buildx")
        with NoBuildx():
            nothing = checker.check()
            run.check(
                "without buildx the look does not download and says it did not look",
                {item.state for item in nothing.records} <= {"unknown", "not_looked"}
                and all(item.method != "pull" for item in nothing.records),
                nothing.to_dict(),
            )
            local = docker(
                "inspect",
                "--format",
                '{{index .Config.Labels "org.opencontainers.image.version"}}',
                f"{SMALL}:stable",
            ).stdout.strip()
            run.check("the local tag did not move", local == "1.0.0", local)
            by_ref = {item.reference: item for item in checker.check(pull=True).records}
            stable = by_ref.get(f"{SMALL}:stable")
            run.check(
                "--pull downloads what moved and finds it",
                stable is not None and stable.state == "newer" and stable.change == "patch",
                stable,
            )
        if have_buildx:
            # The pull above is what a person or an updater does: it moved the tag on this machine and left the
            # container on the image it was made from, so Docker's own listing now names that container's image by
            # its id. ``noust.core.docker.image_sources`` says it has no name to ask by then: recorded, not a check.
            listed = docker(
                "ps", "--filter", "name=w3rd-v-stable", "--format", "{{.Image}}"
            ).stdout.strip()
            run.note(
                "what docker ps says the image of that container is, after its tag moved here",
                listed,
            )
    finally:
        reset("versions")
        retag("stable", "1")


# ====================================================================== 9. a Compose document on the standard input


@scenario("compose_check")
def compose_check(run: Run) -> None:
    """A document is resolved and rehearsed by Compose on its standard input; what it says is read the way it says it."""
    from noust.core.docker.compose_documents import rehearse_document, resolve_document
    from noust.deployers import compose_stack_edit as editor
    from noust.deployers.compose_adopt import changed_lines

    reset("compose_check")
    real = runner()
    folder = WORK / "compose_check"
    folder.mkdir(parents=True, exist_ok=True)
    project = "w3rdcheck"
    version = compose_version_short()
    can_rehearse = numbers(version) >= (2, 20)
    run.note("Compose", version)

    def document(command: str = "sleep 3600", image: str = BUSYBOX) -> str:
        return (
            "services:\n  web:\n"
            f"    image: {image}\n    command: {command}\n    volumes: [data:/data]\n"
            f"    labels:\n      w3rd.scenario: compose_check\nvolumes:\n  data:\n    labels:\n      w3rd.scenario: compose_check\n"
        )

    def effects(output: str) -> list[tuple[str, str, str]]:
        found = []
        for raw in output.splitlines():
            line = editor._DRY_RUN_PREFIX.sub("", raw).strip()
            match = editor._EFFECT.match(line)
            if match is not None and editor._ACTIONS.get(match.group(3).lower()) is not None:
                found.append(
                    (
                        match.group(1).lower(),
                        match.group(2),
                        editor._ACTIONS[match.group(3).lower()],
                    )
                )
        return found

    file = folder / "compose.yaml"
    try:
        model, problem = resolve_document(real, project, document(), folder)
        run.check(
            "a document is resolved on the standard input",
            model is not None and "web" in model.get("services", {}),
            problem,
        )
        if model is not None:
            run.check(
                "and what comes back is the model, not the text",
                model["services"]["web"]["image"] == BUSYBOX,
                list(model),
            )
        run.check(
            "without writing it anywhere",
            not any(folder.iterdir()),
            [p.name for p in folder.iterdir()],
        )

        succeeded, output = rehearse_document(real, project, document(), folder)
        run.note("up --dry-run of a stack that is not running", output[:800])
        if can_rehearse:
            run.check("the rehearsal of a stack that is not running works", succeeded, output)
            found = effects(output)
            run.check(
                "and says what it would create: a network, a volume and a container",
                {"network", "volume", "container"} <= {kind for kind, _, _ in found},
                (found, output[:500]),
            )
            run.check(
                "and nothing was created",
                docker(
                    "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"
                ).stdout.strip()
                == "",
                "",
            )
        else:
            run.check(
                "a Compose that cannot rehearse says so and does not pretend",
                not succeeded and "2.20" in output and version in output,
                output,
            )
            run.note("the words of a Compose with no --dry-run", output[:300])

        write_stack(folder, document())
        up(project, file)
        wait_for(
            lambda: docker(
                "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"
            ).stdout.strip()
            != "",
            20,
        )
        if can_rehearse:
            succeeded, output = rehearse_document(real, project, document(), folder)
            run.note("up --dry-run of a stack as it runs", output[:600])
            run.check(
                "the same document changes nothing",
                succeeded and not changed_lines(output),
                (changed_lines(output), output[:400]),
            )
            succeeded, output = rehearse_document(
                real, project, document(command="sleep 7200"), folder
            )
            run.note("up --dry-run of a changed command", output[:700])
            recreated = [
                row for row in effects(output) if row[0] == "container" and row[2] == "recreate"
            ]
            run.check(
                "a changed command recreates the container, and says so",
                succeeded and bool(recreated),
                (effects(output), output[:400]),
            )
            run.check(
                "and the adoption's reading sees it as a change",
                bool(changed_lines(output)),
                output[:400],
            )
            succeeded, output = rehearse_document(
                real, project, document(image=f"{SMALL}:1"), folder
            )
            run.check(
                "another image recreates it too",
                succeeded and any(row[2] == "recreate" for row in effects(output)),
                (effects(output), output[:400]),
            )
            run.check(
                "and the container still runs as it did",
                docker(
                    "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"
                ).stdout.strip()
                != "",
                "",
            )

        broken = "services:\n  web:\n    image: x\n     command: y\n"
        model, problem = resolve_document(real, project, broken, folder)
        run.note("a document that is not YAML", problem[:300])
        run.check(
            "a document Compose cannot read is refused with Compose's own words",
            model is None and bool(problem),
            problem,
        )
        run.check("and the line is in them", editor._LINE.search(problem) is not None, problem)
        model, problem = resolve_document(
            real, project, f"services:\n  web:\n    image: {BUSYBOX}\n    nope: 1\n", folder
        )
        run.note("a key Compose does not know", problem[:300])
        run.check("a key it does not know is refused", model is None and bool(problem), problem)
        model, problem = resolve_document(
            real,
            project,
            "services:\n  web:\n    image: ${W3RD_MISSING:?needs W3RD_MISSING}\n",
            folder,
        )
        run.note("a variable that is required", problem[:300])
        run.check(
            "a variable that is required and missing is refused",
            model is None and "W3RD_MISSING" in problem,
            problem,
        )
        model, problem = resolve_document(
            real, project, "include:\n  - other.yaml\nservices: {}\n", folder
        )
        run.note("an include", problem[:300] if model is None else "resolved")
        run.check(
            "an include is read by a Compose that has it, and refused with its words by one that does not",
            (model is not None) or (not can_rehearse and bool(problem)) or "other.yaml" in problem,
            problem,
        )
    finally:
        project_down(project, file)
        reset("compose_check")
        time.sleep(0)
        _ = (web_service, wait_for)
