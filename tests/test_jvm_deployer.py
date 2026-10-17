"""
Java and Kotlin applications built with Maven or Gradle (checklist A-40).

A repository with a ``pom.xml`` or a ``build.gradle`` used to be only ever a container. The
tests pin what a JVM deployment turns on: which projects are programs (a library is not), which
tool builds them and how it is started, which jar is the program (by its manifest, not its
name), that the program is copied to a path without a version, what the unit is given per
framework, and the JDK check before anything is downloaded.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.deployers.jvm import (
    DIST_JAR,
    JavaDeployer,
    choose_jar,
    find_tool,
    flavour_of,
    installed_java,
    is_multi_module,
    main_class,
    quarkus_launcher,
    requested_java,
)
from noust.deployers.registry import DeployerRegistry, detect_app_type

DOMAIN = "api.example.com"

SPRING_POM = """\
<project>
  <parent><artifactId>spring-boot-starter-parent</artifactId></parent>
  <properties><java.version>21</java.version></properties>
</project>
"""
LIBRARY_POM = "<project><artifactId>util</artifactId><packaging>jar</packaging></project>\n"
SPRING_GRADLE = """\
plugins {
    id 'java'
    id 'org.springframework.boot' version '3.3.0'
}
java { toolchain { languageVersion = JavaLanguageVersion.of(21) } }
"""


def write(root: Path, name: str, text: str = "") -> Path:
    """
    Args:
        root: The project.
        name: A path inside it.
        text: Its content.

    Returns:
        The file.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def jar(root: Path, name: str, *, main: str | None = None, padding: int = 0) -> Path:
    """
    Args:
        root: The project.
        name: The jar's path inside it.
        main: Its ``Main-Class``; none for a library.
        padding: Bytes of content to add, to make it larger than another.

    Returns:
        The jar.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = "Manifest-Version: 1.0\r\n"
    if main:
        manifest += f"Main-Class: {main}\r\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", manifest + "\r\n")
        if padding:
            archive.writestr("data.bin", "x" * padding)
    return path


def deployer_for(root: Path, runner: FakeRunner | None = None) -> JavaDeployer:
    """
    Args:
        root: The project.
        runner: What runs its commands.

    Returns:
        A configured deployer.
    """
    instance = JavaDeployer(runner=runner or FakeRunner())
    instance.configure(DOMAIN, "src", app_path=root)
    return instance


def spring_maven(root: Path) -> Path:
    """
    Args:
        root: Where to make the project.

    Returns:
        ``root``, a Spring Boot project built with Maven.
    """
    write(root, "pom.xml", SPRING_POM)
    return root


def spring_gradle(root: Path) -> Path:
    """
    Args:
        root: Where to make the project.

    Returns:
        ``root``, a Spring Boot project built with Gradle.
    """
    write(root, "build.gradle", SPRING_GRADLE)
    write(root, "settings.gradle", "rootProject.name = 'api'\n")
    return root


class TestDetection:
    def test_it_sits_between_python_and_node(self) -> None:
        order = [d.APP_TYPE for d in DeployerRegistry.in_detection_order()]
        assert order.index("python") < order.index("java") < order.index("nodejs")

    def test_a_spring_boot_application_built_with_maven(self, tmp_path: Path) -> None:
        spring_maven(tmp_path)
        assert JavaDeployer().detect(tmp_path) is True
        assert detect_app_type(tmp_path) == "java"

    def test_a_spring_boot_application_built_with_gradle(self, tmp_path: Path) -> None:
        spring_gradle(tmp_path)
        assert JavaDeployer().detect(tmp_path) is True

    def test_a_kotlin_gradle_build_file_is_read_too(self, tmp_path: Path) -> None:
        write(tmp_path, "build.gradle.kts", 'plugins { id("io.ktor.plugin") version "2.3.0" }\n')
        assert JavaDeployer().detect(tmp_path) is True

    @pytest.mark.parametrize(
        ("name", "text", "expected"),
        [
            ("pom.xml", "<build><plugin>quarkus-maven-plugin</plugin></build>", "quarkus"),
            ("pom.xml", "<parent>micronaut-parent</parent>", "micronaut"),
            ("pom.xml", "<mainClass>com.acme.Main</mainClass>", "plain"),
            ("build.gradle", "plugins { id 'io.quarkus' }", "quarkus"),
            ("build.gradle", "plugins { id 'io.micronaut.application' }", "micronaut"),
            ("build.gradle", "plugins { id 'application' }\n", "application"),
            ("build.gradle.kts", "plugins {\n    application\n}\n", "application"),
            ("build.gradle", "apply plugin: 'application'\n", "application"),
            ("build.gradle", "mainClassName = 'com.acme.Main'\n", "plain"),
            ("build.gradle", "plugins { id 'com.github.johnrengelman.shadow' }", "shadow"),
        ],
    )
    def test_what_the_build_file_says_the_program_is(
        self, tmp_path: Path, name: str, text: str, expected: str
    ) -> None:
        write(tmp_path, name, text)
        tool = find_tool(tmp_path)
        assert tool is not None
        assert flavour_of(tmp_path, tool.kind) == expected

    def test_a_library_has_nothing_to_run(self, tmp_path: Path) -> None:
        write(tmp_path, "pom.xml", LIBRARY_POM)
        assert JavaDeployer().detect(tmp_path) is False
        write(tmp_path, "pom.xml", "")
        write(tmp_path, "build.gradle", "plugins { id 'java-library' }\n")
        assert JavaDeployer().detect(tmp_path) is False

    def test_a_multi_project_gradle_build_is_read_one_level_down(self, tmp_path: Path) -> None:
        write(tmp_path, "settings.gradle", "include 'api', 'core'\n")
        write(tmp_path, "core/build.gradle", "plugins { id 'java-library' }\n")
        assert JavaDeployer().detect(tmp_path) is False
        write(tmp_path, "api/build.gradle", "plugins { id 'org.springframework.boot' }\n")
        assert JavaDeployer().detect(tmp_path) is True

    def test_a_multi_module_maven_build_is_read_one_level_down(self, tmp_path: Path) -> None:
        write(tmp_path, "pom.xml", "<project><modules><module>api</module></modules></project>")
        write(tmp_path, "api/pom.xml", SPRING_POM)
        assert JavaDeployer().detect(tmp_path) is True
        assert is_multi_module(tmp_path) is True

    def test_a_dockerfile_keeps_it_a_container(self, tmp_path: Path) -> None:
        spring_maven(tmp_path)
        write(tmp_path, "Dockerfile", "FROM eclipse-temurin:21\n")
        assert JavaDeployer().detect(tmp_path) is False
        assert detect_app_type(tmp_path) == "container"

    def test_a_build_file_that_is_a_link_is_not_read(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-pom.xml"
        outside.write_text(SPRING_POM)
        (tmp_path / "pom.xml").symlink_to(outside)
        assert JavaDeployer().detect(tmp_path) is False

    def test_the_wrapper_is_the_projects_own_file_and_never_a_link(self, tmp_path: Path) -> None:
        spring_maven(tmp_path)
        assert find_tool(tmp_path).wrapper is None  # type: ignore[union-attr]
        wrapper = write(tmp_path, "mvnw", "#!/bin/sh\n")
        assert find_tool(tmp_path).wrapper == wrapper  # type: ignore[union-attr]
        wrapper.unlink()
        outside = tmp_path.parent / f"{tmp_path.name}-mvnw"
        outside.write_text("#!/bin/sh\n")
        wrapper.symlink_to(outside)
        assert find_tool(tmp_path).wrapper is None  # type: ignore[union-attr]

    def test_when_both_tools_are_there_the_one_that_names_a_program_wins(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "pom.xml", LIBRARY_POM)
        write(tmp_path, "build.gradle", SPRING_GRADLE)
        tool = find_tool(tmp_path)
        assert tool is not None and tool.kind == "gradle"


class TestJavaVersions:
    def test_the_version_files_come_first(self, tmp_path: Path) -> None:
        write(tmp_path, "pom.xml", SPRING_POM)
        assert requested_java(tmp_path) == (21, False)
        write(tmp_path, ".java-version", "17.0.2\n")
        assert requested_java(tmp_path) == (17, False)

    @pytest.mark.parametrize(
        ("name", "text", "expected"),
        [
            (".java-version", "temurin-21\n", 21),
            (".java-version", "1.8\n", 8),
            (".sdkmanrc", "java=21.0.2-tem\n", 21),
            (".tool-versions", "nodejs 22.1.0\njava temurin-17.0.9\n", 17),
            ("pom.xml", "<maven.compiler.release>17</maven.compiler.release>", 17),
            ("pom.xml", "<maven.compiler.source>1.8</maven.compiler.source>", 8),
        ],
    )
    def test_each_place_a_project_says_it(
        self, tmp_path: Path, name: str, text: str, expected: int
    ) -> None:
        write(tmp_path, name, text)
        assert requested_java(tmp_path) == (expected, False)

    def test_gradle_says_it_in_its_dsl_and_a_toolchain_provisions_itself(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "build.gradle", SPRING_GRADLE)
        assert requested_java(tmp_path) == (21, True)
        write(tmp_path, "build.gradle", "sourceCompatibility = JavaVersion.VERSION_17\n")
        assert requested_java(tmp_path) == (17, False)
        write(tmp_path, "build.gradle", "sourceCompatibility = '1.8'\n")
        assert requested_java(tmp_path) == (8, False)
        write(tmp_path, "build.gradle", "kotlin { jvmToolchain(11) }\n")
        assert requested_java(tmp_path) == (11, True)

    def test_a_project_that_says_nothing(self, tmp_path: Path) -> None:
        write(tmp_path, "pom.xml", LIBRARY_POM)
        assert requested_java(tmp_path) is None

    def test_what_the_jdk_prints(self) -> None:
        assert installed_java("javac 21.0.4") == 21
        assert installed_java("javac 1.8.0_392") == 8
        assert installed_java('openjdk version "17.0.11" 2024-04-16') == 17
        assert installed_java('java version "1.8.0_392"') == 8
        assert installed_java("nonsense") is None


class TestTheJars:
    def test_a_manifest_names_the_main_class(self, tmp_path: Path) -> None:
        assert main_class(jar(tmp_path, "a.jar", main="com.acme.Main")) == "com.acme.Main"
        assert main_class(jar(tmp_path, "b.jar")) is None

    def test_what_is_not_a_jar_has_no_main_class(self, tmp_path: Path) -> None:
        write(tmp_path, "bad.jar", "not a zip")
        assert main_class(tmp_path / "bad.jar") is None
        assert main_class(tmp_path / "missing.jar") is None

    def test_a_link_is_not_followed(self, tmp_path: Path) -> None:
        real = jar(tmp_path.parent, f"{tmp_path.name}-real.jar", main="x.Main")
        (tmp_path / "link.jar").symlink_to(real)
        assert main_class(tmp_path / "link.jar") is None

    def test_the_program_is_the_jar_with_a_main_class_not_the_one_named_like_it(
        self, tmp_path: Path
    ) -> None:
        jar(tmp_path, "target/api-1.0.0.jar", main="org.springframework.boot.loader.JarLauncher")
        jar(tmp_path, "target/api-1.0.0.jar.original")
        jar(tmp_path, "target/api-1.0.0-sources.jar", main="x.Main")
        jar(tmp_path, "target/original-api-1.0.0.jar", main="x.Main")
        chosen = choose_jar(tmp_path, "maven", names=("api",))
        assert chosen == tmp_path / "target" / "api-1.0.0.jar"

    def test_among_the_jars_of_a_directory_the_fat_one_wins(self, tmp_path: Path) -> None:
        jar(tmp_path, "build/libs/api-1.0.jar", main="x.Main")
        jar(tmp_path, "build/libs/api-1.0-all.jar", main="x.Main", padding=5000)
        assert choose_jar(tmp_path, "gradle", names=()) == tmp_path / "build/libs/api-1.0-all.jar"

    def test_spring_boots_plain_jar_is_not_the_program(self, tmp_path: Path) -> None:
        jar(tmp_path, "build/libs/api-1.0.jar", main="x.Main")
        jar(tmp_path, "build/libs/api-1.0-plain.jar", main="x.Main", padding=9000)
        assert choose_jar(tmp_path, "gradle", names=()) == tmp_path / "build/libs/api-1.0.jar"

    def test_a_multi_module_build_has_its_program_in_one_module(self, tmp_path: Path) -> None:
        jar(tmp_path, "core/target/core-1.0.jar")
        jar(tmp_path, "web/target/web-1.0.jar", main="x.Main")
        assert choose_jar(tmp_path, "maven", names=("shop",)) == tmp_path / "web/target/web-1.0.jar"

    def test_two_modules_with_a_program_are_told_apart_by_the_applications_name(
        self, tmp_path: Path
    ) -> None:
        jar(tmp_path, "web/target/web-1.0.jar", main="x.Main")
        jar(tmp_path, "admin/target/admin-1.0.jar", main="x.Main")
        assert choose_jar(tmp_path, "maven", names=("admin",)) == (
            tmp_path / "admin/target/admin-1.0.jar"
        )

    def test_two_modules_and_nothing_to_tell_them_apart_is_refused(self, tmp_path: Path) -> None:
        jar(tmp_path, "web/target/web-1.0.jar", main="x.Main")
        jar(tmp_path, "admin/target/admin-1.0.jar", main="x.Main")
        with pytest.raises(DeploymentError) as caught:
            choose_jar(tmp_path, "maven", names=("shop",))
        assert "2 programs" in str(caught.value)
        assert "build.start" in (caught.value.details or "")

    def test_no_runnable_jar_says_what_to_add(self, tmp_path: Path) -> None:
        jar(tmp_path, "target/util-1.0.jar")
        with pytest.raises(DeploymentError) as caught:
            choose_jar(tmp_path, "maven", names=())
        assert "Main-Class" in (caught.value.details or "")

    def test_quarkus_runs_where_it_was_built_because_it_needs_its_lib_directory(
        self, tmp_path: Path
    ) -> None:
        launcher = write(tmp_path, "target/quarkus-app/quarkus-run.jar", "x")
        assert quarkus_launcher(tmp_path, "maven") == launcher
        assert quarkus_launcher(tmp_path, "gradle") is None


class TestCommands:
    def test_maven_fetches_dependencies_then_packages_without_tests(self, tmp_path: Path) -> None:
        deployer = deployer_for(spring_maven(tmp_path))
        assert deployer.get_install_command() == [
            "mvn",
            "-B",
            "-ntp",
            "-DskipTests",
            "dependency:go-offline",
        ]
        assert deployer.get_build_command() == ["mvn", "-B", "-ntp", "-DskipTests", "package"]

    def test_a_multi_module_maven_build_resolves_while_it_builds(self, tmp_path: Path) -> None:
        write(tmp_path, "pom.xml", "<project><modules><module>api</module></modules></project>")
        write(tmp_path, "api/pom.xml", SPRING_POM)
        assert deployer_for(tmp_path).get_install_command() == []

    def test_the_wrapper_is_run_through_sh(self, tmp_path: Path) -> None:
        spring_maven(tmp_path)
        wrapper = write(tmp_path, "mvnw")
        deployer = deployer_for(tmp_path)
        assert deployer.get_build_command()[:2] == ["sh", str(wrapper)]

    def test_gradle_builds_spring_with_bootjar_and_never_starts_a_daemon(
        self, tmp_path: Path
    ) -> None:
        deployer = deployer_for(spring_gradle(tmp_path))
        assert deployer.get_install_command() == []
        assert deployer.get_build_command() == [
            "gradle",
            "--no-daemon",
            "--console=plain",
            "bootJar",
        ]

    @pytest.mark.parametrize(
        ("text", "task"),
        [
            ("plugins { id 'io.ktor.plugin' }", "buildFatJar"),
            ("plugins { id 'application' }\n", "installDist"),
            ("plugins { id 'io.quarkus' }", "assemble"),
            ("plugins { id 'com.github.johnrengelman.shadow' }", "assemble"),
        ],
    )
    def test_each_frameworks_gradle_task(self, tmp_path: Path, text: str, task: str) -> None:
        write(tmp_path, "build.gradle", text)
        assert deployer_for(tmp_path).get_build_command()[-1] == task

    def test_the_start_command_is_the_copied_jar_through_current(self, tmp_path: Path) -> None:
        write(tmp_path, "pom.xml", "<mainClass>x.Main</mainClass>")
        deployer = deployer_for(tmp_path)
        assert deployer.get_start_command() == f"java -jar {tmp_path}/{DIST_JAR}"

    def test_spring_is_given_its_port_from_the_units_port_variable(self, tmp_path: Path) -> None:
        # A SERVER_PORT written into the unit was the same for the two instances of a
        # zero-downtime application, and the second could not bind: the first real Spring Boot
        # build answered "did not answer on the green instance". ${PORT} is each instance's own.
        deployer = deployer_for(spring_maven(tmp_path))
        assert deployer.get_start_command() == (
            f"java -jar {tmp_path}/{DIST_JAR} --server.port=${{PORT}}"
        )

    def test_a_quarkus_build_is_started_where_it_was_built(self, tmp_path: Path) -> None:
        write(tmp_path, "pom.xml", "<plugin>quarkus-maven-plugin</plugin>")
        assert deployer_for(tmp_path).get_start_command() == (
            f"java -Dquarkus.http.port=${{PORT}} -jar {tmp_path}/target/quarkus-app/quarkus-run.jar"
        )

    def test_micronaut_is_given_its_port_as_a_system_property(self, tmp_path: Path) -> None:
        write(tmp_path, "pom.xml", "<parent>micronaut-parent</parent>")
        assert deployer_for(tmp_path).get_start_command() == (
            f"java -Dmicronaut.server.port=${{PORT}} -jar {tmp_path}/{DIST_JAR}"
        )

    def test_the_build_block_replaces_the_commands(self, tmp_path: Path) -> None:
        spring_maven(tmp_path)
        write(
            tmp_path,
            "noust.yaml",
            "build:\n  build: [mvn, package, -Pprod]\n  start: [java, -jar, target/app.jar]\n",
        )
        deployer = deployer_for(tmp_path)
        assert deployer.build_command() == ["mvn", "package", "-Pprod"]
        assert deployer.build_config().start == ("java", "-jar", "target/app.jar")

    def test_the_unit_is_told_the_port_in_the_frameworks_own_words(self, tmp_path: Path) -> None:
        deployer = deployer_for(spring_maven(tmp_path))
        port = str(deployer.port)
        # The port is the start command's (from ${PORT}); the unit only says where to listen.
        assert deployer._unit_environment() == {
            "PORT": port,
            "HOST": "127.0.0.1",
            "SERVER_ADDRESS": "127.0.0.1",
        }
        write(tmp_path, "pom.xml", "<plugin>quarkus-maven-plugin</plugin>")
        assert deployer._unit_environment()["QUARKUS_HTTP_HOST"] == "127.0.0.1"
        assert "QUARKUS_HTTP_PORT" not in deployer._unit_environment()
        write(tmp_path, "pom.xml", "<parent>micronaut-parent</parent>")
        assert deployer._unit_environment()["MICRONAUT_SERVER_HOST"] == "127.0.0.1"
        write(tmp_path, "pom.xml", "<mainClass>x.Main</mainClass>")
        assert set(deployer._unit_environment()) == {"PORT", "HOST"}


class TestBeforeAnythingIsDownloaded:
    def test_a_server_with_a_jre_but_no_jdk_is_told_to_install_one(self, tmp_path: Path) -> None:
        runner = FakeRunner().only_knows("java", "mvn", "apt-get")
        deployer = deployer_for(spring_maven(tmp_path), runner)
        with pytest.raises(DeploymentError) as caught:
            deployer.pre_install()
        assert "no JDK" in str(caught.value)
        assert "apt install default-jdk-headless maven" in (caught.value.details or "")

    def test_a_server_without_maven_is_told_to_install_it(self, tmp_path: Path) -> None:
        runner = FakeRunner().only_knows("java", "javac", "apt-get")
        deployer = deployer_for(spring_maven(tmp_path), runner)
        with pytest.raises(DeploymentError) as caught:
            deployer.pre_install()
        assert "needs mvn" in str(caught.value)

    def test_a_wrapper_needs_no_maven_on_the_server(self, tmp_path: Path) -> None:
        spring_maven(tmp_path)
        write(tmp_path, "mvnw")
        runner = FakeRunner().only_knows("java", "javac").script(["javac"], stdout="javac 21.0.4")
        assert deployer_for(tmp_path, runner).pre_install() is True

    def test_a_gradle_project_without_a_wrapper_is_told_to_commit_one(self, tmp_path: Path) -> None:
        runner = FakeRunner().only_knows("java", "javac")
        deployer = deployer_for(spring_gradle(tmp_path), runner)
        with pytest.raises(DeploymentError) as caught:
            deployer.pre_install()
        assert "gradle wrapper" in (caught.value.details or "")

    def test_a_jdk_older_than_the_projects_java_is_refused_with_the_way_out(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner().script(["javac", "-version"], stdout="javac 17.0.11")
        deployer = deployer_for(spring_maven(tmp_path), runner)
        with pytest.raises(DeploymentError) as caught:
            deployer.pre_install()
        assert "asks for Java 21 and this server's JDK is 17" in str(caught.value)

    def test_a_gradle_toolchain_provisions_its_own_jdk(self, tmp_path: Path) -> None:
        write(tmp_path, "gradlew")
        spring_gradle(tmp_path)
        runner = FakeRunner().script(["javac", "-version"], stdout="javac 17.0.11")
        deployer = deployer_for(tmp_path, runner)
        lines: list[str] = []
        deployer.logger.attach_sink(lines.append)

        assert deployer.pre_install() is True

        assert any("Gradle will provision that JDK itself" in line for line in lines)

    def test_a_newer_jdk_builds_an_older_project(self, tmp_path: Path) -> None:
        write(tmp_path, ".java-version", "17\n")
        spring_maven(tmp_path)
        runner = FakeRunner().script(["javac", "-version"], stdout="javac 21.0.4")
        assert deployer_for(tmp_path, runner).pre_install() is True

    def test_a_directory_with_no_build_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(DeploymentError, match=r"no pom\.xml"):
            deployer_for(tmp_path).pre_install()

    def test_the_preflight_line_says_how_to_install_it(self, tmp_path: Path) -> None:
        deployer = deployer_for(spring_maven(tmp_path))
        deployer._runner = FakeRunner().only_knows("dnf")
        assert deployer._missing_dependencies_issue(["java"]) == (
            "Missing system dependencies: java. Install it with: dnf install java-21-openjdk-devel maven"
        )


class TestAfterTheBuild:
    def test_the_program_is_copied_to_a_path_without_a_version(self, tmp_path: Path) -> None:
        spring_maven(tmp_path)
        jar(tmp_path, "target/api-1.4.2.jar", main="x.Main")
        runner = FakeRunner()
        deployer = deployer_for(tmp_path, runner)

        deployer.post_build()

        assert runner.calls_to("install") == [
            (
                "install",
                "-D",
                "-m",
                "0644",
                str(tmp_path / "target/api-1.4.2.jar"),
                str(tmp_path / DIST_JAR),
            )
        ]
        assert deployer.launch == ("jar", DIST_JAR)
        assert deployer.get_start_command() == (
            f"java -jar {tmp_path}/{DIST_JAR} --server.port=${{PORT}}"
        )

    def test_a_failed_copy_says_what_the_copy_said(self, tmp_path: Path) -> None:
        spring_maven(tmp_path)
        jar(tmp_path, "target/api-1.0.jar", main="x.Main")
        runner = FakeRunner().script(["install"], stderr="No space left", exit_code=1)
        with pytest.raises(DeploymentError) as caught:
            deployer_for(tmp_path, runner).post_build()
        assert "No space left" in (caught.value.details or "")

    def test_quarkus_is_run_where_it_is(self, tmp_path: Path) -> None:
        write(tmp_path, "pom.xml", "<plugin>quarkus-maven-plugin</plugin>")
        write(tmp_path, "target/quarkus-app/quarkus-run.jar", "x")
        runner = FakeRunner()
        deployer = deployer_for(tmp_path, runner)

        deployer.post_build()

        assert runner.calls_to("install") == []
        assert deployer.launch == ("jar", "target/quarkus-app/quarkus-run.jar")

    def test_the_application_plugin_is_run_through_its_start_script(self, tmp_path: Path) -> None:
        write(tmp_path, "build.gradle", "plugins { id 'application' }\n")
        script = write(tmp_path, "build/install/api/bin/api", "#!/bin/sh\n")
        deployer = deployer_for(tmp_path)

        deployer.post_build()

        assert deployer.launch == ("script", "build/install/api/bin/api")
        assert deployer.get_start_command() == str(script)

    def test_an_install_dist_with_several_programs_is_told_apart_by_the_applications_name(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "build.gradle", "plugins { id 'application' }\n")
        write(tmp_path, "build/install/other/bin/other")
        write(tmp_path, "build/install/api-example-com/bin/api-example-com")
        deployer = deployer_for(tmp_path)
        deployer.post_build()
        assert deployer.launch == (
            "script",
            "build/install/api-example-com/bin/api-example-com",
        )

    def test_an_install_dist_with_several_and_no_hint_is_refused(self, tmp_path: Path) -> None:
        write(tmp_path, "build.gradle", "plugins { id 'application' }\n")
        write(tmp_path, "build/install/one/bin/one")
        write(tmp_path, "build/install/two/bin/two")
        with pytest.raises(DeploymentError, match="2 programs"):
            deployer_for(tmp_path).post_build()

    def test_a_build_block_that_builds_leaves_the_program_to_its_start(
        self, tmp_path: Path
    ) -> None:
        spring_maven(tmp_path)
        write(
            tmp_path,
            "noust.yaml",
            "build:\n  build: [mvn, package]\n  start: [java, -jar, a.jar]\n",
        )
        runner = FakeRunner()
        assert deployer_for(tmp_path, runner).post_build() is True
        assert runner.calls_to("install") == []

    def test_a_dry_run_has_nothing_to_choose_from(self, tmp_path: Path) -> None:
        deployer = deployer_for(spring_maven(tmp_path))
        assert deployer.post_build() is True
        assert deployer.launch is None
