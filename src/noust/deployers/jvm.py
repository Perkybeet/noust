# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Java and Kotlin applications built with Maven or Gradle, run as the unit (checklist A-40).

Until 3.4 a repository with a ``pom.xml`` or a ``build.gradle`` was only ever a container. A JVM
application deploys like the compiled ones: the build tool packages it in the sandbox, the
runnable result is put at a path that does not change from one release to the next, and
systemd runs ``java -jar`` on it. A rollback is then a symlink to a release that carries its own
jar.

What is specific to the JVM:

- **A repository is a program or it is not.** A library has nothing to run, and the detector
  tells them apart by what the build file says: Spring Boot, Quarkus, Micronaut, Ktor, the
  ``application`` or a shadow plugin, a ``mainClass``. The jar that was built is then chosen
  by its manifest (it has a ``Main-Class``), not by its name, which carries the version.
- **The wrapper first.** ``mvnw`` and ``gradlew`` pin the tool the project was written for, so
  they win over the server's own ``mvn`` or ``gradle``; they run as ``sh <wrapper>`` because an
  archive may have lost the executable bit, and they download their distribution into the
  application's build cache.
- **Tests are not run.** ``-DskipTests`` for Maven; Gradle's ``assemble`` and ``bootJar`` do not
  depend on them. A deployment is not the place a test suite fails.
- **The dependency cache is the application's** (``~/.m2`` and ``GRADLE_USER_HOME`` live in
  ``/var/cache/noust/build/<app>``), so only what changed is downloaded.
- **Maven resolves dependencies in the install phase** for a single-module project
  (``dependency:go-offline``). A multi-module Maven build and any Gradle build resolve them
  while they build, so the ``--network strict`` profile, which builds without a network, needs
  ``build.install`` to fetch them first; the failure says so.
- **A Dockerfile means the container**, for the reason :mod:`noust.deployers.compiled` gives.

The JDK and the build tool are the server's own (distribution packages); the project's Java
version is checked against the JDK before anything is built.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from noust.core.exceptions import DeploymentError
from noust.core.fs import FileSystem
from noust.core.package_family import PackageFamily
from noust.core.runner import CommandRunner
from noust.deployers.base import BaseDeployer
from noust.deployers.compiled import has_container_file, install_suggestion_for
from noust.deployers.helpers.unit_launch import systemd_command
from noust.deployers.registry import DeployerRegistry

Tool = Literal["maven", "gradle"]
Flavour = Literal["spring", "quarkus", "micronaut", "ktor", "shadow", "application", "plain"]

#: Where the runnable jar is copied, relative to the project: a name that does not carry the
#: version, so the unit's command is the same for every release.
DIST_JAR = "dist/app.jar"

#: How a framework is told its port: on the command line, from ``${PORT}``, which systemd expands
#: from the unit's own ``PORT``. A port written into the unit's environment (``SERVER_PORT=3000``)
#: would be the same for the two instances of a zero-downtime application and for every copy of a
#: scaled one, and the second would fail to bind; ``PORT`` is the one variable each of them is given
#: its own value of. Spring Boot reads an argument of the program, Quarkus and Micronaut a system
#: property of the JVM. The rest (Ktor, Shadow, the application plugin) read ``PORT`` themselves.
_PORT_ARGUMENTS: dict[str, str] = {"spring": "--server.port=${PORT}"}
_PORT_PROPERTIES: dict[str, str] = {
    "quarkus": "-Dquarkus.http.port=${PORT}",
    "micronaut": "-Dmicronaut.server.port=${PORT}",
}

#: Jars that are never the program: their names say what they are.
_NOT_THE_PROGRAM = re.compile(
    r"(?:-sources|-javadoc|-tests?|-plain|-test-fixtures)\.jar$|^original-|\.jar\.original$"
)

#: Largest manifest read from a jar a build produced.
MAX_MANIFEST_BYTES = 64 * 1024

#: Largest build file read.
MAX_BUILD_FILE_BYTES = 512 * 1024

#: How many modules of a multi-module project are looked into, one level down.
MAX_MODULES = 24

_MAVEN_MARKERS: tuple[tuple[Flavour, re.Pattern[str]], ...] = (
    ("spring", re.compile(r"spring-boot-maven-plugin|spring-boot-starter-parent")),
    ("quarkus", re.compile(r"quarkus-maven-plugin")),
    ("micronaut", re.compile(r"micronaut-maven-plugin|micronaut-parent")),
    ("plain", re.compile(r"<mainClass>|Main-Class|vertx-maven-plugin")),
)
_GRADLE_MARKERS: tuple[tuple[Flavour, re.Pattern[str]], ...] = (
    ("spring", re.compile(r"""org\.springframework\.boot""")),
    ("quarkus", re.compile(r"""io\.quarkus""")),
    ("micronaut", re.compile(r"""io\.micronaut\.application""")),
    ("ktor", re.compile(r"""io\.ktor\.plugin""")),
    ("shadow", re.compile(r"""com\.github\.johnrengelman\.shadow|com\.gradleup\.shadow""")),
    (
        "application",
        re.compile(
            r"""\bid\s*\(?\s*["']application["']|apply\s+plugin\s*:\s*["']application["']"""
            r"""|(?m:^\s*application\s*$)|plugins\s*\{[^}]*\bapplication\b"""
        ),
    ),
    ("plain", re.compile(r"""\bmainClass(?:Name)?\b|Main-Class""")),
)

_MODULES_BLOCK = re.compile(r"<modules>")
_JAVA_VERSION_FILE = re.compile(r"^\s*(?:[A-Za-z]+-)?(\d+)(?:\.(\d+))?")
_SDKMAN = re.compile(r"(?m)^java\s*=\s*(\d+)(?:\.(\d+))?")
_TOOL_VERSIONS = re.compile(r"(?m)^java\s+(?:[A-Za-z]+-)?(\d+)(?:\.(\d+))?")
_POM_JAVA = re.compile(
    r"<(?:maven\.compiler\.release|maven\.compiler\.source|maven\.compiler\.target"
    r"|java\.version)>\s*(\d+)(?:\.(\d+))?\s*</"
)
_GRADLE_JAVA = re.compile(
    r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)|jvmToolchain\(\s*(\d+)\s*\)"
    r"|JavaVersion\.VERSION_(?:1_)?(\d+)|(?:source|target)Compatibility\s*=?\s*[\"']?(?:1\.)?(\d+)"
)
_GRADLE_TOOLCHAIN = re.compile(r"JavaLanguageVersion\.of|jvmToolchain")
_QUOTED_VERSION = re.compile(r'"(\d+)(?:\.(\d+))?')
_JAVAC_VERSION = re.compile(r"^javac\s+(\d+)(?:\.(\d+))?", re.MULTILINE)


@dataclass(frozen=True)
class BuildTool:
    """
    The tool a project is built with.

    Attributes:
        kind: ``maven`` or ``gradle``.
        wrapper: The project's ``mvnw`` or ``gradlew``, when it has a real file of that name.
    """

    kind: Tool
    wrapper: Path | None = None

    def command(self) -> list[str]:
        """
        Returns:
            The words that start the tool: the wrapper through ``sh`` (an archive may have lost
            its executable bit, and the sandbox looks programs up in a fixed ``PATH``), else the
            server's own.
        """
        if self.wrapper is not None:
            return ["sh", str(self.wrapper)]
        return ["mvn" if self.kind == "maven" else "gradle"]


def _read(path: Path) -> str:
    """
    Args:
        path: A file of an untrusted repository.

    Returns:
        Its text; empty for a link, a missing file or an unreadable one, and cut at
        :data:`MAX_BUILD_FILE_BYTES`.
    """
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            return handle.read(MAX_BUILD_FILE_BYTES)
    except OSError:
        return ""


def _module_directories(project: Path) -> list[Path]:
    """
    Args:
        project: The project.

    Returns:
        Its immediate subdirectories (real ones, sorted, at most :data:`MAX_MODULES`) that a
        multi-module build may keep modules in.
    """
    try:
        entries = sorted(project.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []
    found = [
        entry
        for entry in entries
        if entry.is_dir()
        and not entry.is_symlink()
        and not entry.name.startswith((".", "_"))
        and entry.name not in {"target", "build", "node_modules", "src"}
    ]
    return found[:MAX_MODULES]


def build_files(project: Path, tool: Tool) -> list[Path]:
    """
    List the build files of a project and of its modules.

    Args:
        project: The project.
        tool: ``maven`` or ``gradle``.

    Returns:
        The root build file(s) then those one level down; only files that exist.
    """
    names = ("pom.xml",) if tool == "maven" else ("build.gradle", "build.gradle.kts")
    found = [project / name for name in names if (project / name).is_file()]
    for module in _module_directories(project):
        found.extend(module / name for name in names if (module / name).is_file())
    return found


def flavour_of(project: Path, tool: Tool) -> Flavour | None:
    """
    Tell which kind of program a build describes.

    Args:
        project: The project.
        tool: ``maven`` or ``gradle``.

    Returns:
        The first framework the project's build files name, in the order of
        :data:`_MAVEN_MARKERS` or :data:`_GRADLE_MARKERS`; None when they name none (a
        library).
    """
    markers = _MAVEN_MARKERS if tool == "maven" else _GRADLE_MARKERS
    text = "\n".join(_read(path) for path in build_files(project, tool))
    for flavour, pattern in markers:
        if pattern.search(text):
            return flavour
    return None


def find_tool(project: Path) -> BuildTool | None:
    """
    Choose the tool a project is built with.

    Args:
        project: The project.

    Returns:
        Maven for a ``pom.xml``, Gradle for a ``build.gradle[.kts]``; when both are there, the
        one whose build file names a program. None for neither. The wrapper is the project's own
        file, never a link.
    """
    candidates: list[Tool] = []
    if (project / "pom.xml").is_file():
        candidates.append("maven")
    if any(
        (project / name).is_file()
        for name in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
    ):
        # A multi-project build often has no build file at its root, only the settings that
        # name the projects.
        candidates.append("gradle")
    if not candidates:
        return None
    chosen = next((kind for kind in candidates if flavour_of(project, kind)), candidates[0])
    wrapper_name = "mvnw" if chosen == "maven" else "gradlew"
    wrapper = project / wrapper_name
    real = wrapper.is_file() and not wrapper.is_symlink()
    return BuildTool(chosen, wrapper if real else None)


def is_multi_module(project: Path) -> bool:
    """
    Args:
        project: A Maven project.

    Returns:
        Whether the root ``pom.xml`` declares modules.
    """
    return _MODULES_BLOCK.search(_read(project / "pom.xml")) is not None


def _java_major(major: str, minor: str | None) -> int:
    """
    Args:
        major: The first number of a Java version.
        minor: The second, when there is one.

    Returns:
        The release: ``8`` for ``1.8``, ``21`` for ``21.0``.
    """
    return int(minor) if major == "1" and minor else int(major)


def requested_java(project: Path) -> tuple[int, bool] | None:
    """
    Read the Java release a project asks for.

    Args:
        project: The project.

    Returns:
        ``(major, toolchain)`` from ``.java-version``, ``.sdkmanrc``, ``.tool-versions``, the
        POM's properties or the Gradle build's compatibility, in that order; ``toolchain`` is
        True when Gradle is told to provision that JDK itself. None when it says nothing.
    """
    for name, pattern in (
        (".java-version", _JAVA_VERSION_FILE),
        (".sdkmanrc", _SDKMAN),
        (".tool-versions", _TOOL_VERSIONS),
        ("pom.xml", _POM_JAVA),
    ):
        found = pattern.search(_read(project / name))
        if found:
            return _java_major(found.group(1), found.group(2)), False
    for path in build_files(project, "gradle")[:1]:
        text = _read(path)
        found_gradle = _GRADLE_JAVA.search(text)
        if found_gradle:
            number = next(group for group in found_gradle.groups() if group)
            return _java_major(number, None), _GRADLE_TOOLCHAIN.search(text) is not None
    return None


def installed_java(output: str) -> int | None:
    """
    Read the major release of the JDK this server has.

    Args:
        output: What ``javac -version`` or ``java -version`` printed.

    Returns:
        The major (``21`` for ``21.0.4``, ``8`` for ``1.8.0_392``); None when unrecognisable.
    """
    found = _JAVAC_VERSION.search(output) or _QUOTED_VERSION.search(output)
    if not found:
        return None
    major, minor = int(found.group(1)), found.group(2)
    return int(minor) if major == 1 and minor else major


def main_class(jar: Path) -> str | None:
    """
    Read the ``Main-Class`` of a jar.

    Args:
        jar: A jar the build produced.

    Returns:
        The class the manifest names; None when it has no manifest or no such attribute (a
        library), or the file is not a readable archive. Only the manifest is read, bounded.
    """
    if jar.is_symlink() or not jar.is_file():
        return None
    try:
        with zipfile.ZipFile(jar) as archive:
            info = archive.getinfo("META-INF/MANIFEST.MF")
            if info.file_size > MAX_MANIFEST_BYTES:
                return None
            text = archive.read(info).decode("utf-8", errors="ignore")
    except (OSError, KeyError, zipfile.BadZipFile, NotImplementedError, RuntimeError):
        return None
    for line in text.replace("\r\n", "\n").split("\n"):
        key, _, value = line.partition(":")
        if key.strip().lower() == "main-class" and value.strip():
            return value.strip()
    return None


def _jars_in(directory: Path) -> list[Path]:
    """
    Args:
        directory: A build output directory (``target`` or ``build/libs``).

    Returns:
        The jars directly in it that can be the program, by name.
    """
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []
    return [
        entry
        for entry in entries
        if entry.name.endswith(".jar")
        and not entry.is_symlink()
        and entry.is_file()
        and not _NOT_THE_PROGRAM.search(entry.name)
    ]


def output_directories(project: Path, tool: Tool) -> list[Path]:
    """
    List where a build leaves its jars, in the project and in its modules.

    Args:
        project: The project.
        tool: ``maven`` or ``gradle``.

    Returns:
        ``target`` (or ``build/libs``) of the project, then of each module.
    """
    relative = Path("target") if tool == "maven" else Path("build") / "libs"
    return [project / relative, *(module / relative for module in _module_directories(project))]


def choose_jar(project: Path, tool: Tool, *, names: Sequence[str]) -> Path:
    """
    Pick the jar a build produced that is the program.

    Args:
        project: The project, built.
        tool: ``maven`` or ``gradle``.
        names: Names that point at the right module among several: the application's.

    Returns:
        The jar. Among those with a ``Main-Class``, a directory's largest (the fat one beside
        its thin variants); when several directories have one, the module named like the
        application.

    Raises:
        DeploymentError: There is none, or several modules produce one and nothing says which.
    """
    per_directory: dict[Path, Path] = {}
    for directory in output_directories(project, tool):
        runnable = [jar for jar in _jars_in(directory) if main_class(jar) is not None]
        if runnable:
            per_directory[directory] = max(runnable, key=lambda jar: jar.stat().st_size)
    if not per_directory:
        raise DeploymentError(
            "The build produced no jar that can run",
            details="A runnable jar has a Main-Class in its manifest. Add the Spring Boot, "
            "Quarkus or Shadow plugin (or a mainClass for the jar or shade plugin) to the "
            "build, or say how to start it: build.start: [java, -jar, path/to/app.jar].",
        )
    if len(per_directory) == 1:
        return next(iter(per_directory.values()))
    wanted = {name for name in names if name}
    for directory, jar in per_directory.items():
        module = directory.parent.name if tool == "maven" else directory.parent.parent.name
        if module in wanted:
            return jar
    found = sorted(str(jar.relative_to(project)) for jar in per_directory.values())
    raise DeploymentError(
        f"The build produced {len(found)} programs: {', '.join(found)}",
        details=f"Say which one runs in the build block: build.start: [java, -jar, {found[0]}].",
    )


def quarkus_launcher(project: Path, tool: Tool) -> Path | None:
    """
    Find a Quarkus build's launcher.

    Args:
        project: The project, built.
        tool: ``maven`` or ``gradle``.

    Returns:
        ``quarkus-app/quarkus-run.jar`` under the build output of the project or a module (it
        needs the ``lib`` directory beside it, so it is run where it is); None when absent.
    """
    root = "target" if tool == "maven" else "build"
    for base in (project, *_module_directories(project)):
        launcher = base / root / "quarkus-app" / "quarkus-run.jar"
        if launcher.is_file() and not launcher.is_symlink():
            return launcher
    return None


def installed_scripts(project: Path) -> list[Path]:
    """
    Find the start scripts Gradle's ``installDist`` produced.

    Args:
        project: The project, built.

    Returns:
        ``build/install/<name>/bin/<name>`` of the project and of each module that has one.
    """
    found: list[Path] = []
    for base in (project, *_module_directories(project)):
        install = base / "build" / "install"
        try:
            entries = sorted(install.iterdir(), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            script = entry / "bin" / entry.name
            if entry.is_dir() and not entry.is_symlink() and script.is_file():
                found.append(script)
    return found


class JavaDeployer(BaseDeployer):
    """
    Deployer for Java and Kotlin programs built with Maven or Gradle.

    The framework decides the Gradle task and the environment the unit sets; the program is the
    jar the build produced that has a ``Main-Class``, copied to a path without a version.
    """

    APP_TYPE = "java"
    DISPLAY_NAME = "Java / Kotlin (Maven, Gradle)"
    DETECTION_FILES: ClassVar[list[str]] = ["pom.xml", "build.gradle", "build.gradle.kts"]
    #: Read by :meth:`detect` and the helpers beyond the build files at the root.
    FRAMEWORK_CONFIG_FILES: ClassVar[list[str]] = [
        "settings.gradle",
        "settings.gradle.kts",
        "mvnw",
        "gradlew",
        ".java-version",
        ".sdkmanrc",
        ".tool-versions",
        "*/pom.xml",
        "*/build.gradle",
        "*/build.gradle.kts",
    ]
    DEFAULT_PORT = 8080

    #: With the other native stacks: below Python, above Node.
    DETECTION_PRIORITY = 45

    SYSTEM_DEPS: ClassVar[list[str]] = ["java"]

    #: ``runtime`` is for the languages Noust installs per application; the JDK is the server's.
    BUILD_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"base_directory", "install", "build", "start"}
    )

    #: The JDK (not the JRE: the build compiles) and Maven; Gradle comes with the project.
    TOOLCHAIN_PACKAGES: ClassVar[dict[PackageFamily, str]] = {
        PackageFamily.APT: "default-jdk-headless maven",
        PackageFamily.DNF: "java-21-openjdk-devel maven",
        PackageFamily.ZYPPER: "java-21-openjdk-devel maven",
    }

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ):
        """
        Args:
            verbose: Enable verbose logging.
            runner: Command runner used for installs and builds. Defaults to the
                process-wide runner.
            fs: Filesystem every change goes through. Defaults to the process-wide one.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        #: What the build produced and the unit runs: ``jar`` or ``script``, and its path
        #: relative to the project; None until built.
        self.launch: tuple[Literal["jar", "script"], str] | None = None

    # Detection -------------------------------------------------------------------------

    def detect(self, path: Path) -> bool:
        """
        Detect a Maven or Gradle project that builds a program.

        Args:
            path: Path to check.

        Returns:
            True for a ``pom.xml`` or ``build.gradle[.kts]`` that names a framework or a main
            class, and no Dockerfile: a library has nothing to run, and a Dockerfile is how
            the repository says it runs.
        """
        if has_container_file(path):
            return False
        tool = find_tool(path)
        return tool is not None and flavour_of(path, tool.kind) is not None

    def _tool(self) -> BuildTool:
        """
        Returns:
            The project's build tool.

        Raises:
            DeploymentError: The project has neither a ``pom.xml`` nor a Gradle build file.
        """
        tool = find_tool(self.build_path)
        if tool is None:
            raise DeploymentError(
                "This project has no pom.xml or build.gradle",
                details="Maven and Gradle projects are built from the directory that holds the "
                "build file; for one in a subdirectory set build.base_directory.",
            )
        return tool

    def _flavour(self) -> Flavour:
        """
        Returns:
            The kind of program the project is; ``plain`` when it names no framework (the build
            block then says what to do).
        """
        tool = find_tool(self.build_path)
        return (flavour_of(self.build_path, tool.kind) if tool else None) or "plain"

    # Commands --------------------------------------------------------------------------

    def get_install_command(self) -> list[str]:
        """
        Get the command that fetches the dependencies into the application's cache.

        Returns:
            ``dependency:go-offline`` for a single-module Maven project; nothing for a
            multi-module one (the reactor's own modules are not in any repository yet) and for
            Gradle, which resolves while it builds.
        """
        tool = find_tool(self.build_path)
        if tool is None or tool.kind != "maven" or is_multi_module(self.build_path):
            return []
        return [*tool.command(), "-B", "-ntp", "-DskipTests", "dependency:go-offline"]

    def _gradle_task(self) -> str:
        """
        Returns:
            The task that assembles the program for the project's framework.
        """
        return {
            "spring": "bootJar",
            "ktor": "buildFatJar",
            "application": "installDist",
        }.get(self._flavour(), "assemble")

    def get_build_command(self) -> list[str]:
        """
        Get the command that packages the program.

        Returns:
            ``mvn package`` without tests, or Gradle's task for the framework without a daemon
            (one would outlive the sandbox's unit). Nothing when the project has no build file;
            the deployment itself refuses that in :meth:`pre_install`.
        """
        tool = find_tool(self.build_path)
        if tool is None:
            return []
        if tool.kind == "maven":
            return [*tool.command(), "-B", "-ntp", "-DskipTests", "package"]
        return [*tool.command(), "--no-daemon", "--console=plain", self._gradle_task()]

    def get_start_command(self) -> str:
        """
        Get the command the unit runs.

        Returns:
            ``java -jar`` on the program the build produced, or its start script; before a
            build (``noust inspect``) the path the framework's build would use.
        """
        kind, relative = self.launch or self._expected_launch()
        path = str(self._at_runtime(self.build_path / relative))
        if kind != "jar":
            return systemd_command([path])
        flavour = self._flavour()
        properties = [_PORT_PROPERTIES[flavour]] if flavour in _PORT_PROPERTIES else []
        arguments = [_PORT_ARGUMENTS[flavour]] if flavour in _PORT_ARGUMENTS else []
        return systemd_command(["java", *properties, "-jar", path, *arguments])

    def _expected_launch(self) -> tuple[Literal["jar", "script"], str]:
        """
        Returns:
            What a build of this project would run, for a description made before building.
        """
        tool = find_tool(self.build_path)
        if self._flavour() == "quarkus":
            root = "target" if tool and tool.kind == "maven" else "build"
            return "jar", f"{root}/quarkus-app/quarkus-run.jar"
        return "jar", DIST_JAR

    def get_health_check(self) -> str:
        """
        Get the health check endpoint.

        Returns:
            ``/``: any answer but a server error means the program is up, so a service with no
            route there is not rolled back for it.
        """
        return "/"

    def _unit_environment(self) -> dict[str, str]:
        """
        Say what the unit sets: the port and the loopback address, in the framework's words.

        Returns:
            ``PORT`` and ``HOST`` for every program, plus the address variable its framework reads
            (Spring's ``SERVER_ADDRESS``, Quarkus' and Micronaut's). The port itself reaches the
            framework through the start command (:data:`_PORT_ARGUMENTS`), from ``PORT``.
        """
        environment = {"PORT": str(self.port), "HOST": "127.0.0.1"}
        flavour = self._flavour()
        if flavour == "spring":
            environment["SERVER_ADDRESS"] = "127.0.0.1"
        elif flavour == "quarkus":
            environment["QUARKUS_HTTP_HOST"] = "127.0.0.1"
        elif flavour == "micronaut":
            environment["MICRONAUT_SERVER_HOST"] = "127.0.0.1"
        return environment

    # Steps -----------------------------------------------------------------------------

    def pre_install(self) -> bool:
        """
        Check the JDK and the build tool, before anything is downloaded.

        Returns:
            True.

        Raises:
            DeploymentError: There is no JDK (only a JRE), no build tool, or the JDK is older
                than the project's Java.
        """
        tool = self._tool()
        if not self.runner.exists("javac"):
            raise DeploymentError(
                "This server has a Java runtime but no JDK (javac)",
                details=self._install_details(),
            )
        if tool.wrapper is None and not self.runner.exists(tool.command()[0]):
            raise DeploymentError(
                f"This project needs {tool.command()[0]} and the server does not have it",
                details=self._install_details()
                if tool.kind == "maven"
                else "Commit the Gradle wrapper (gradle wrapper) so the project brings its own "
                "Gradle, or install gradle on the server.",
            )
        self._check_java_version()
        return True

    def _install_details(self) -> str:
        """
        Returns:
            How to install the toolchain on this server.
        """
        hint = install_suggestion_for(self.TOOLCHAIN_PACKAGES, self.runner, sentence=False)
        return f"Install it with: {hint}" if hint else "Install a JDK and Maven system-wide."

    def _check_java_version(self) -> None:
        """
        Compare the project's Java with the server's JDK.

        Raises:
            DeploymentError: The JDK is older than the project asks for and the build is not
                told to provision a JDK itself (a Gradle toolchain).
        """
        wanted = requested_java(self.build_path)
        if wanted is None:
            return
        probe = self._run(["javac", "-version"], timeout=60)
        have = installed_java((probe.stdout or "") + (probe.stderr or ""))
        if have is None or have >= wanted[0]:
            return
        if wanted[1]:
            self.logger.substep(
                f"The build asks for Java {wanted[0]}; this server's JDK {have} is older and "
                "Gradle will provision that JDK itself"
            )
            return
        raise DeploymentError(
            f"The project asks for Java {wanted[0]} and this server's JDK is {have}",
            details="Install a newer JDK system-wide "
            f"({self._install_details().removeprefix('Install it with: ')}), or lower the Java "
            "version of the build.",
        )

    def post_build(self) -> bool:
        """
        Find the program the build produced and put it where the unit expects it.

        Returns:
            True.

        Raises:
            DeploymentError: The build produced no program, or several and nothing says which.
        """
        if self.build_config().build:
            return True
        tool = self._tool()
        project = self.build_path
        if not self._has_output():
            # A dry run builds nothing.
            return True
        quarkus = quarkus_launcher(project, tool.kind)
        if quarkus is not None:
            self.launch = ("jar", str(quarkus.relative_to(project)))
            return True
        if tool.kind == "gradle" and self._gradle_task() == "installDist":
            script = self._choose_script(installed_scripts(project))
            self.launch = ("script", str(script.relative_to(project)))
            return True
        jar = choose_jar(project, tool.kind, names=(self.app_name or "",))
        self.logger.substep(f"Program: {jar.relative_to(project)}")
        result = self._run(
            ["install", "-D", "-m", "0644", str(jar), str(project / DIST_JAR)], timeout=300
        )
        if not result.success:
            raise DeploymentError(
                f"The program could not be copied to {DIST_JAR}",
                details=(result.stderr or result.stdout or "No output.").strip(),
            )
        self.launch = ("jar", DIST_JAR)
        return True

    def _has_output(self) -> bool:
        """
        Returns:
            Whether a build left an output directory in the project or one of its modules.
        """
        return any(
            (base / part).exists()
            for base in (self.build_path, *_module_directories(self.build_path))
            for part in ("target", "build")
        )

    def _choose_script(self, scripts: Sequence[Path]) -> Path:
        """
        Pick the start script of an ``installDist`` among those found.

        Args:
            scripts: What :func:`installed_scripts` found.

        Returns:
            The script of the only distribution, or the one named like the application.

        Raises:
            DeploymentError: There is none, or several and nothing says which.
        """
        if not scripts:
            raise DeploymentError(
                "installDist produced no start script",
                details="The application plugin needs a mainClass. Add it to the build, or say "
                "how to start the program: build.start: [java, -jar, path/to/app.jar].",
            )
        if len(scripts) == 1:
            return scripts[0]
        for script in scripts:
            if script.name == self.app_name:
                return script
        listed = ", ".join(sorted(script.name for script in scripts))
        raise DeploymentError(
            f"installDist produced {len(scripts)} programs: {listed}",
            details="Say which one runs in the build block: "
            "build.start: [./build/install/NAME/bin/NAME].",
        )

    # Reporting -------------------------------------------------------------------------

    @classmethod
    def install_suggestion(cls, runner: CommandRunner) -> str | None:
        """
        Say how to get the toolchain, for ``noust inspect``.

        Args:
            runner: Used to tell which package manager this server has.

        Returns:
            The command, with the next step; None when no package is known for this server.
        """
        return install_suggestion_for(cls.TOOLCHAIN_PACKAGES, runner)

    def _missing_dependencies_issue(self, missing: list[str]) -> str:
        """
        Say which programs are missing and how to install them.

        Args:
            missing: The programs of :attr:`SYSTEM_DEPS` this server lacks.

        Returns:
            The pre-flight line, with the install command for this server's package manager.
        """
        line = super()._missing_dependencies_issue(missing)
        hint = install_suggestion_for(self.TOOLCHAIN_PACKAGES, self.runner, sentence=False)
        return f"{line}. Install it with: {hint}" if hint else line

    def get_template_context(self) -> dict[str, Any]:
        """
        Get the template context.

        Returns:
            The generic one, marked as a Java application.
        """
        context = super().get_template_context()
        context["is_java"] = True
        return context


DeployerRegistry.register(JavaDeployer)
