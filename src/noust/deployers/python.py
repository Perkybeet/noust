"""
Python deployer for Noust.
"""

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner
from noust.deployers.base import BaseDeployer
from noust.deployers.registry import DeployerRegistry

#: Directories that hold other people's Python, never the application's: a
#: ``wsgi.py`` found in the virtual environment is Django's own.
_NOT_THE_APPLICATION = frozenset(
    {
        "venv",
        ".venv",
        "env",
        "node_modules",
        ".git",
        "__pycache__",
        "site-packages",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
    }
)

#: The workers an application starts with. ``WEB_CONCURRENCY``, which gunicorn
#: reads when it is given no ``-w``, is set to this in the unit, and a value
#: in the application's ``.env`` replaces it.
DEFAULT_WORKERS = 4

#: Where Django's settings may say ``STATIC_ROOT``.
_SETTINGS_FILES = 3
_STATIC_ROOT = re.compile(r"^\s*STATIC_ROOT\s*=", re.MULTILINE)


class PythonDeployer(BaseDeployer):
    """
    Deployer for Python web applications.

    Handles deployment of Django, Flask, FastAPI, and other
    Python web frameworks, started with Gunicorn (or Hypercorn, when the
    project depends on it and not on Gunicorn) bound to the loopback address
    nginx proxies to. A project that starts differently says how with ``start``
    in its build block.
    """

    APP_TYPE = "python"
    DISPLAY_NAME = "Python (Django/Flask/FastAPI)"

    DETECTION_FILES: ClassVar[list[str]] = [
        "requirements.txt",
        "pyproject.toml",
        "setup.py",
        "Pipfile",
    ]

    DEFAULT_PORT = 8000

    # See DEFAULT_DETECTION_PRIORITY in interface.py for the full order.
    DETECTION_PRIORITY = 50

    SYSTEM_DEPS: ClassVar[list[str]] = ["python3", "pip3"]

    RUNTIME_LANGUAGES: ClassVar[frozenset[str]] = frozenset({"python"})
    PRIMARY_RUNTIME: ClassVar[str | None] = "python"

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ):
        """
        Initialize the Python deployer.

        Args:
            verbose: Enable verbose logging.
            runner: Command runner used for installs and builds. Defaults to the
                process-wide runner.
            fs: Filesystem every change goes through. Defaults to the
                process-wide one.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        self.framework = "generic"
        self.wsgi_app: str | None = None
        self.asgi_app: str | None = None
        #: Where the Django project is when ``manage.py`` is not at the root.
        self.django_dir: str | None = None
        self.use_poetry = False
        self.use_pipenv = False
        self.venv_path: Path = Path()

    def detect(self, path: Path) -> bool:
        """Detect if path contains a Python project."""
        for f in self.DETECTION_FILES:
            if (path / f).exists():
                return True
        return False

    def _dependency_text(self) -> str:
        """
        Read what the project declares it depends on, lower-cased.

        Returns:
            ``requirements.txt``, else ``pyproject.toml``, else ``Pipfile``;
            empty when the project has none of them.
        """
        for name in ("requirements.txt", "pyproject.toml", "Pipfile"):
            path = self.build_path / name
            if path.is_file():
                try:
                    return path.read_text().lower()
                except (OSError, UnicodeDecodeError) as exc:
                    self.logger.debug(f"Could not read {name}: {exc}")
        return ""

    def _detect_framework(self) -> str:
        """Detect the Python framework used."""
        deps_content = self._dependency_text()

        if "django" in deps_content:
            return "django"
        elif "fastapi" in deps_content:
            return "fastapi"
        elif "flask" in deps_content:
            return "flask"
        elif "starlette" in deps_content:
            return "starlette"

        return "generic"

    def _walk(self, top: Path) -> Iterator[tuple[Path, list[str]]]:
        """
        Walk the application's own directories, shallowest first.

        Args:
            top: Where to start.

        Yields:
            ``(directory, file names)`` of every directory that is not the
            virtual environment, a dependency tree or a build output.
        """
        for raw_root, dirs, files in os.walk(top):
            dirs[:] = sorted(d for d in dirs if d not in _NOT_THE_APPLICATION)
            yield Path(raw_root), files

    def _find_django(self) -> tuple[Path, str | None, str | None]:
        """
        Find the Django project and its WSGI and ASGI entry points.

        Returns:
            The directory of ``manage.py`` (the root when there is none), and
            the ``wsgi.py`` and ``asgi.py`` module paths relative to it. The
            shallowest of each wins: a project's own, not a vendored one.
        """
        root = self.build_path
        for directory, files in self._walk(root):
            if "manage.py" in files:
                root = directory
                break
        wsgi = asgi = None
        for directory, files in self._walk(root):
            relative = directory.relative_to(root)
            module = ".".join(relative.parts)
            if wsgi is None and "wsgi.py" in files:
                wsgi = f"{module}.wsgi:application" if module else "wsgi:application"
            if asgi is None and "asgi.py" in files:
                asgi = f"{module}.asgi:application" if module else "asgi:application"
        return root, wsgi, asgi

    def _detect_app_module(self) -> tuple[str | None, str | None]:
        """Detect the WSGI/ASGI application module."""
        wsgi_app = None
        asgi_app = None
        self.django_dir = None

        # Check for common patterns
        if self.framework == "django":
            root, wsgi_app, asgi_app = self._find_django()
            if root != self.build_path:
                self.django_dir = str(root.relative_to(self.build_path))

        elif self.framework == "flask":
            # Check for app.py, main.py, or __init__.py
            for filename in ["app.py", "main.py", "application.py"]:
                if (self.build_path / filename).exists():
                    content = (self.build_path / filename).read_text()
                    # Look for Flask app instance
                    match = re.search(r"(\w+)\s*=\s*Flask\(", content)
                    if match:
                        app_var = match.group(1)
                        module = filename.replace(".py", "")
                        wsgi_app = f"{module}:{app_var}"
                        break

        elif self.framework == "fastapi":
            # Check for main.py or app.py
            for filename in ["main.py", "app.py", "application.py"]:
                if (self.build_path / filename).exists():
                    content = (self.build_path / filename).read_text()
                    match = re.search(r"(\w+)\s*=\s*FastAPI\(", content)
                    if match:
                        app_var = match.group(1)
                        module = filename.replace(".py", "")
                        asgi_app = f"{module}:{app_var}"
                        break

        return wsgi_app, asgi_app

    def _detect_python_package_manager(self) -> tuple[bool, bool]:
        """
        Detect which Python dependency manager the project uses.

        Renamed from _detect_package_manager, which overrode a base method of
        the same name that returns the *Node* package manager as a string. The
        base class called it during pre_install and got a tuple back.

        Returns:
            Tuple of (uses_poetry, uses_pipenv).
        """
        use_poetry = (self.build_path / "poetry.lock").exists()
        use_pipenv = (self.build_path / "Pipfile.lock").exists()
        return use_poetry, use_pipenv

    def _tool_dirs(self) -> list[Path]:
        """
        List the directory of the programs the virtual environment installed.

        Returns:
            ``venv/bin``: where ``gunicorn``, ``django-admin`` and the
            project's own entry points are once its dependencies are in.
        """
        return [self.build_path / "venv" / "bin"]

    def _interpret(self, path: str) -> list[str]:
        """
        Start a script of the virtual environment through its interpreter.

        An entry point's first line names the interpreter of the release it
        was installed in, which pruning eventually deletes; the interpreter
        found through ``current`` runs the script file instead, and resolves
        the environment of whichever release is active.

        Args:
            path: The program of a start command, absolute.

        Returns:
            The interpreter and the script for a script of ``venv/bin``; the
            program alone for anything else.
        """
        bin_dir = self.build_path / "venv" / "bin"
        script = bin_dir / Path(path).name
        if path != str(self._runtime_equivalent(script)) or script.name.startswith("python"):
            return [path]
        try:
            with script.open("rb") as handle:
                if handle.read(2) != b"#!":
                    return [path]
        except OSError:
            return [path]
        return [str(self._runtime_equivalent(bin_dir / "python")), path]

    def get_install_command(self) -> list[str]:
        """
        Get dependency installation command.

        Returns:
            Poetry's or Pipenv's own install for a project with their lock;
            otherwise ``pip install -r requirements.txt``, or ``pip install .``
            for a project that only has a ``pyproject.toml`` or a ``setup.py``.
        """
        if self.use_poetry:
            return ["poetry", "install", "--without", "dev"]
        elif self.use_pipenv:
            return ["pipenv", "install", "--deploy"]
        elif not (self.build_path / "requirements.txt").exists() and (
            (self.build_path / "pyproject.toml").exists() or (self.build_path / "setup.py").exists()
        ):
            return [*self._pip(), "install", "."]
        else:
            return [*self._pip(), "install", "-r", "requirements.txt"]

    def _django_static_root(self) -> bool:
        """
        Tell whether the Django project says where ``collectstatic`` puts files.

        Returns:
            True when a settings module sets ``STATIC_ROOT``: without it the
            command fails, and a project that serves its static files some
            other way (a CDN, WhiteNoise from the source tree) has no use for it.
        """
        root = self.build_path / (self.django_dir or "")
        for directory, files in self._walk(root):
            for name in files:
                is_settings = name.endswith(".py") and (
                    name.startswith("settings") or directory.name == "settings"
                )
                if not is_settings:
                    continue
                try:
                    if _STATIC_ROOT.search((directory / name).read_text(errors="ignore")):
                        return True
                except OSError:
                    continue
        return False

    def _has_migrations(self) -> bool:
        """
        Tell whether the Django project has migrations of its own.

        Returns:
            True when an application of the project (not the virtual
            environment's) has a ``migrations`` package.
        """
        root = self.build_path / (self.django_dir or "")
        return any(
            directory.name == "migrations" and "__init__.py" in files
            for directory, files in self._walk(root)
        )

    def _say_migrations_are_a_decision(self) -> None:
        """
        Tell the deployment's log that a Django project's migrations are not run for it.

        A schema change is a decision (it decides whether going back is safe),
        so it is declared, as a ``pre_deploy`` hook, and not guessed at. Said
        once the project is known to be Django and has migrations, and only
        while no hook says otherwise.
        """
        if self.framework != "django" or not self._has_migrations():
            return
        if self._hook_set().declared:
            return
        self.logger.substep(
            "Django migrations are not run for you: declare them as a pre_deploy hook "
            "(run: ./venv/bin/python manage.py migrate --noinput, migrates: true); see docs/build.md"
        )

    def get_build_command(self) -> list[str]:
        """
        Get the build command: Django's ``collectstatic``, when it has a ``STATIC_ROOT``.

        Returns:
            The command; nothing for a project that is not Django or has no
            ``STATIC_ROOT``. Migrations are not run here: a schema change is
            a decision, declared as a ``pre_deploy`` hook (see docs/build.md).
        """
        if self.framework == "django" and self._django_static_root():
            venv_python = self.venv_path / "bin" / "python"
            manage = f"{self.django_dir}/manage.py" if self.django_dir else "manage.py"
            return [str(venv_python), manage, "collectstatic", "--noinput"]
        return []

    def _pip(self) -> list[str]:
        """
        Return the argv prefix that runs pip inside the virtual environment.

        On releases pip runs as ``python -m pip``. A virtual environment's
        scripts carry the absolute path of the interpreter it was created
        with in their first line, and a venv reused from the previous release
        still names that release: its ``bin/pip`` would install into the
        release that is serving, not the one being built.

        Returns:
            The argv prefix.
        """
        if self.uses_releases:
            return [str(self.venv_path / "bin" / "python"), "-m", "pip"]
        return [str(self.venv_path / "bin" / "pip")]

    def _server(self) -> str:
        """
        Pick the server the default start command runs.

        Returns:
            ``hypercorn`` when the project depends on it and not on Gunicorn,
            ``gunicorn`` otherwise (which Noust installs when the project
            does not).
        """
        text = self._dependency_text()
        if "hypercorn" in text and "gunicorn" not in text:
            return "hypercorn"
        return "gunicorn"

    def _launcher(self, module: str) -> str:
        """
        Return how the unit starts a server installed in the virtual environment.

        On releases through ``current`` and as ``python -m <module>``, for the
        reason :meth:`_pip` gives: the interpreter found through ``current``
        resolves the venv of whichever release is active, and a script's
        first line would pin the release it was installed in, which pruning
        eventually deletes.

        Args:
            module: ``gunicorn`` or ``hypercorn``.

        Returns:
            The executable part of ExecStart.
        """
        if self.uses_releases:
            return f"{self._at_runtime(self.venv_path / 'bin' / 'python')} -m {module}"
        return str(self.venv_path / "bin" / module)

    def _gunicorn(self) -> str:
        """
        Return how the unit starts gunicorn.

        Returns:
            The executable part of ExecStart (see :meth:`_launcher`).
        """
        return self._launcher("gunicorn")

    def get_start_command(self) -> str:
        """
        Get start command: Gunicorn (or Hypercorn) on the loopback address.

        The server binds ``127.0.0.1``, where nginx proxies to: bound to
        every interface, the application's port answers the network too
        whenever the firewall does not close it. Workers come from
        ``WEB_CONCURRENCY`` in the unit's environment, which the application's
        ``.env`` may replace.
        """
        bind = f"127.0.0.1:{self.port}"
        chdir = f" --chdir {self.django_dir}" if self.django_dir else ""
        if self._server() == "hypercorn":
            target = self.asgi_app or self.wsgi_app or "app:app"
            return f"{self._launcher('hypercorn')} {target} -b {bind} -w ${{WEB_CONCURRENCY}}"
        venv_gunicorn = self._gunicorn()

        if self.asgi_app:
            # Use uvicorn workers for ASGI
            return (
                f"{venv_gunicorn}{chdir} {self.asgi_app} -k uvicorn.workers.UvicornWorker -b {bind}"
            )
        elif self.wsgi_app:
            return f"{venv_gunicorn}{chdir} {self.wsgi_app} -b {bind}"
        else:
            # Fallback
            return f"{venv_gunicorn} app:app -b {bind}"

    def _unit_environment(self) -> dict[str, str]:
        """
        Say what the unit sets: the port, and how many workers to start.

        Returns:
            The generic variables, and ``WEB_CONCURRENCY``.
        """
        environment = super()._unit_environment()
        environment["WEB_CONCURRENCY"] = str(DEFAULT_WORKERS)
        return environment

    def pre_install(self) -> bool:
        """Pre-installation hook - create virtual environment."""
        self.framework = self._detect_framework()
        self.use_poetry, self.use_pipenv = self._detect_python_package_manager()

        self.logger.debug(f"Framework: {self.framework}")
        self.logger.debug(f"Poetry: {self.use_poetry}, Pipenv: {self.use_pipenv}")

        # Create virtual environment
        self.venv_path = self.build_path / "venv"

        if not self.use_poetry and not self.use_pipenv:
            self.logger.substep("Creating virtual environment...")
            result = self._run(
                ["python3", "-m", "venv", str(self.venv_path)],
                cwd=self.build_path,
            )
            if not result.success:
                self.logger.warning("Failed to create virtual environment")
                return False

        return True

    def post_install(self) -> bool:
        """Post-installation hook - detect app module and install the server."""
        # Detect application module
        self.wsgi_app, self.asgi_app = self._detect_app_module()
        self.logger.debug(f"WSGI app: {self.wsgi_app}")
        self.logger.debug(f"ASGI app: {self.asgi_app}")
        self._say_migrations_are_a_decision()

        if self.build_config().start:
            # The project starts itself the way it said: what it needs to do
            # that is in its own dependencies.
            return True

        # Install the server
        packages = [self._server()]

        if self.asgi_app and self._server() == "gunicorn":
            packages.append("uvicorn")

        self.logger.substep(f"Installing {' and '.join(packages)}...")
        result = self._run([*self._pip(), "install", *packages])
        if not result.success:
            self.logger.warning(f"Failed to install {' and '.join(packages)}")

        return True

    def get_template_context(self) -> dict:
        """Get template context with Python specifics."""
        context = super().get_template_context()
        context.update(
            {
                "is_python": True,
                "framework": self.framework,
                "venv_path": str(self._at_runtime(self.venv_path)),
            }
        )
        return context


# Register the deployer
DeployerRegistry.register(PythonDeployer)
