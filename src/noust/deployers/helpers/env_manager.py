# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Environment variable manager for Noust.

Handles discovering, prompting, and writing environment variables
for deployed applications, with support for .env.example parsing,
secret auto-generation, and interactive configuration.

Everything this module writes holds credentials: a deployed ``.env`` carries
``DATABASE_URL``, API keys and the secrets generated here, and
``.wasm/env-config.json`` records the same inventory. Both go out through
:mod:`noust.core.fs` with :data:`~noust.core.fs.SECRET_MODE`, so the mode is
applied by the ``os.open`` that creates the file rather than by a ``chmod``
afterwards, and the write lands on a temporary file that is renamed into place,
so a half-written ``.env`` never exists and a symlink planted at the destination
is replaced instead of followed. The application directory itself is left alone:
it is served by the web server and read by the service account, so tightening it
would break the deployment while doing nothing for the secrets, which are
protected by the file mode.
"""

import json
import os
import re
import secrets
import stat
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from noust.core.exceptions import NoustError, SecurityError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.secret_detection import (
    NAME_PATTERNS,
    URL_CREDENTIALS,
    classify,
    name_looks_secret,
    redact_url_credentials,
)

# URL_CREDENTIALS and redact_url_credentials moved to noust.core.secret_detection,
# which is the one place that now knows every shape a secret can take;
# re-exported here because noust.deployers.inspect and other callers already
# import them from this module and there is no reason to make them change.
__all__ = [
    "URL_CREDENTIALS",
    "EnvConfig",
    "EnvConfigError",
    "EnvManager",
    "EnvVariable",
    "is_secret_env_name",
    "redact_url_credentials",
]


#: The marker words of a Spanish ``.env.example`` (``genera-clave-hex-64-caracteres``,
#: ``CAMBIAR_ESTO``, ``tu-proveedor.com``, ``pon-aqui-el-valor``), matched against the
#: lower-cased default. Unlike the English substrings in
#: :attr:`EnvManager.PLACEHOLDER_PATTERNS` they must start a word: ``tu-`` is also the end
#: of ``virtu-al``, and a real value would be taken for a template.
_SPANISH_PLACEHOLDER = re.compile(r"(?<![a-z0-9])(?:(?:genera|generar|tu|pon)[-_]|cambiar)")

#: A key that says it is hexadecimal, and perhaps how long: ``hex-64``, ``hex_32_bytes``,
#: ``HEX_256_BITS``. Looked for in the placeholder and in the variable's name.
_HEX_HINT = re.compile(r"(?<![a-z])hex(?![a-z])(?:[-_ ]?(\d+)(?:[-_ ]?(bytes?|bits?))?)?")

#: What a key that says "hex" and not how long gets: 32 bytes, an AES-256 key.
_DEFAULT_HEX_LENGTH = 64

#: The lengths of a hex secret worth believing. A placeholder that says ``hex-999999`` is
#: a joke or a typo, and 1024 characters is already a key nobody uses.
_HEX_LENGTH_RANGE = range(8, 1025)


def _is_real_directory(path: Path) -> bool:
    """
    Report whether a path is a directory in its own right, not a link to one.

    Args:
        path: Path inside a checkout.

    Returns:
        True for a directory that is not a symlink.
    """
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


class EnvConfigError(NoustError):
    """Raised when environment configuration fails."""

    pass


@dataclass
class EnvVariable:
    """Represents a single environment variable."""

    name: str
    default: str = ""
    description: str = ""
    category: str = "General"
    required: bool = False
    secret: bool = False
    shared: bool = False
    value: str | None = None


@dataclass
class EnvConfig:
    """Environment configuration for an application."""

    variables: list[EnvVariable] = field(default_factory=list)
    files: dict[str, list[str]] = field(default_factory=dict)  # filename -> variable names

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "variables": [asdict(v) for v in self.variables],
            "files": self.files,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnvConfig":
        """
        Deserialize from dictionary.

        Args:
            data: Dictionary with variables and files keys.

        Returns:
            EnvConfig instance.
        """
        variables = [EnvVariable(**v) for v in data.get("variables", [])]
        return cls(variables=variables, files=data.get("files", {}))


class EnvManager:
    """
    Manager for application environment variables.

    Discovers variables from .env.example files, prompts users
    for values, auto-generates secrets, and writes .env files.
    """

    # Category detection by prefix
    CATEGORY_PREFIXES: ClassVar[dict[str, str]] = {
        "DATABASE": "Database",
        "DB_": "Database",
        "POSTGRES": "Database",
        "MYSQL": "Database",
        "MONGO": "Database",
        "REDIS": "Redis",
        "JWT": "Authentication",
        "AUTH": "Authentication",
        "SESSION": "Authentication",
        "OAUTH": "Authentication",
        "SMTP": "Email",
        "MAIL": "Email",
        "EMAIL": "Email",
        "AWS": "Cloud",
        "S3_": "Cloud",
        "GCP": "Cloud",
        "AZURE": "Cloud",
        "SENTRY": "Monitoring",
        "LOG": "Logging",
        "API": "API",
        "PORT": "Server",
        "HOST": "Server",
        "NODE_ENV": "Server",
        "APP_": "Application",
        "NEXT_PUBLIC": "Frontend",
        "VITE_": "Frontend",
        "ENCRYPTION": "Security",
        "CORS": "Security",
    }

    # Secret detection patterns, moved to noust.core.secret_detection so
    # discovery-time flagging and the full classifier share one list; kept as
    # a class attribute because callers already read it off the class.
    SECRET_PATTERNS: ClassVar[tuple[str, ...]] = NAME_PATTERNS

    #: Substrings that mark a default as a template placeholder rather than a
    #: real value, matched case-insensitively against the .env.example default.
    #: A secret whose default is a placeholder is regenerated: baking
    #: "your-secret-key-here" into a systemd unit as Environment= overrides the
    #: real .env the application reads at runtime, and the failure surfaces
    #: much later as an authentication error nobody connects to a deploy. The
    #: Spanish markers (``genera-``, ``cambiar``, ``tu-``, ``pon-``) are the
    #: regular expression :data:`_SPANISH_PLACEHOLDER`, because they need a word
    #: boundary these substrings do not.
    PLACEHOLDER_PATTERNS = [
        "your-",
        "your_",
        "yourapp",
        "youremail",
        "your.email",
        "user:password",
        "user:pass@",
        "username:password",
        "change-me",
        "changeme",
        "change_me",
        "replace-me",
        "replaceme",
        "replace_me",
        "<",
        "secret-key-here",
        "secret_key_here",
        "todo",
        "fixme",
        "placeholder",
    ]

    def __init__(self, verbose: bool = False, fs: FileSystem | None = None):
        """
        Args:
            verbose: Enable verbose logging.
            fs: Filesystem every write goes through. Defaults to the
                process-wide one, which is what makes ``--dry-run`` and the test
                doubles work without every call site knowing about them.
        """
        self.verbose = verbose
        self.logger = Logger(verbose=verbose)
        self._fs = fs

    @property
    def fs(self) -> FileSystem:
        """
        The filesystem every file this manager writes goes through.

        Returns:
            The injected filesystem, or the process-wide one.
        """
        return self._fs or get_fs()

    #: Example files read by :meth:`discover`, in precedence order.
    EXAMPLE_FILE_NAMES: ClassVar[tuple[str, ...]] = (".env.example", ".env.template", ".env.sample")

    #: Directories whose children :meth:`discover` also searches.
    WORKSPACE_PARENTS: ClassVar[tuple[str, ...]] = ("apps", "packages", "services")

    def discover(self, app_path: Path) -> list[EnvVariable]:
        """
        Discover environment variables from .env.example files.

        Scans root and subdirectories (apps/*, packages/*, services/*) for
        .env.example, .env.template and .env.sample files.

        The tree is a repository, which is untrusted input read as root, so
        nothing inside it is followed through a symlink: not an example file,
        not a workspace directory, not ``apps`` itself. A link to
        ``/etc/shadow`` or to another application's ``.env`` would otherwise
        come back as a list of defaults. ``app_path`` itself may be a link
        (``current`` on the releases layout is one Noust made).

        Args:
            app_path: Path to the application root.

        Returns:
            List of discovered environment variables.
        """
        variables = []
        seen_names = set()

        search_paths = [app_path]
        for subdir in self.WORKSPACE_PARENTS:
            sub_path = app_path / subdir
            if _is_real_directory(sub_path):
                for child in sorted(sub_path.iterdir()):
                    if _is_real_directory(child):
                        search_paths.append(child)

        for search_path in search_paths:
            for name in self.EXAMPLE_FILE_NAMES:
                content = self._read_example(search_path / name)
                if content is None:
                    continue
                for var in self._parse_env_content(content):
                    if var.name not in seen_names:
                        variables.append(var)
                        seen_names.add(var.name)

        return variables

    def _read_example(self, path: Path) -> str | None:
        """
        Read an example file only if it is a regular file, not a link to one.

        ``O_NOFOLLOW`` refuses a symlink at open time, so there is no window
        between checking the path and reading it; ``O_NONBLOCK`` keeps a FIFO
        planted under the name from hanging the open, and the ``fstat`` then
        turns it, or a directory, away.

        Args:
            path: Candidate example file.

        Returns:
            The file's text, or None when it is absent, a link, not a regular
            file, or unreadable.
        """
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except FileNotFoundError:
            return None
        except OSError as e:
            # ELOOP is how O_NOFOLLOW reports a symlink.
            self.logger.warning(f"Not reading {path}: {e.strerror}")
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                self.logger.warning(f"Not reading {path}: not a regular file")
                return None
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                return handle.read().decode("utf-8", errors="replace")
        except OSError as e:
            self.logger.warning(f"Could not read {path}: {e}")
            return None
        finally:
            if fd >= 0:
                os.close(fd)

    def _parse_env_content(self, content: str) -> list[EnvVariable]:
        """
        Parse the text of a .env.example file into EnvVariable objects.

        Supports comment-based descriptions and metadata:
            # Comment becomes description
            KEY=default_value
            # Required: true
            REQUIRED_KEY=

        Args:
            content: The file's text.

        Returns:
            List of parsed environment variables.
        """
        variables: list[EnvVariable] = []
        current_description = ""

        for line in content.splitlines():
            line = line.strip()

            # Skip empty lines
            if not line:
                current_description = ""
                continue

            # Collect comments as descriptions
            if line.startswith("#"):
                comment = line.lstrip("#").strip()
                if current_description:
                    current_description += " " + comment
                else:
                    current_description = comment
                continue

            # Parse KEY=VALUE
            if "=" not in line:
                current_description = ""
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            # Remove surrounding quotes from default value
            if len(value) >= 2:
                if (value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'"):
                    value = value[1:-1]

            var = EnvVariable(
                name=key,
                default=value,
                description=current_description,
                category=self._detect_category(key),
                required=not bool(value),
                secret=self._is_secret(key),
            )

            variables.append(var)
            current_description = ""

        return variables

    def _detect_category(self, name: str) -> str:
        """
        Detect the category of a variable by its name prefix.

        Args:
            name: Variable name.

        Returns:
            Category string.
        """
        upper_name = name.upper()
        for prefix, category in self.CATEGORY_PREFIXES.items():
            if upper_name.startswith(prefix):
                return category
        return "General"

    def _is_secret(self, name: str) -> bool:
        """
        Determine if a variable is a secret based on its name.

        Args:
            name: Variable name.

        Returns:
            True if the variable appears to be a secret.
        """
        upper_name = name.upper()
        return any(pattern in upper_name for pattern in self.SECRET_PATTERNS)

    def _is_placeholder(self, value: str) -> bool:
        """
        Determine if a default value looks like a template placeholder.

        Used to decide whether a default copied from .env.example is safe to
        keep: a secret that holds one is regenerated and anything else has to be
        supplied, because a template value deployed as if it were real is how
        ``genera-clave-hex-64-caracteres`` ended up as a key. Matching is
        case-insensitive, against PLACEHOLDER_PATTERNS and the Spanish markers.

        Args:
            value: Default value to inspect.

        Returns:
            True if the value matches a known placeholder pattern.
        """
        if not value:
            return False
        lower = value.lower()
        if any(pattern in lower for pattern in self.PLACEHOLDER_PATTERNS):
            return True
        return _SPANISH_PLACEHOLDER.search(lower) is not None

    @staticmethod
    def generate_secret(length: int = 32) -> str:
        """
        Generate a cryptographically secure random secret.

        Args:
            length: Length of the secret in bytes before encoding.

        Returns:
            URL-safe random string.
        """
        return secrets.token_urlsafe(length)

    @staticmethod
    def generate_hex_secret(length: int = _DEFAULT_HEX_LENGTH) -> str:
        """
        Generate a cryptographically secure random hexadecimal secret.

        Args:
            length: Number of hexadecimal characters.

        Returns:
            Exactly ``length`` characters of ``0-9a-f``.
        """
        return secrets.token_hex((length + 1) // 2)[:length]

    @staticmethod
    def hex_length(variable: EnvVariable) -> int | None:
        """
        Read from a variable whether its secret has to be hexadecimal, and how long.

        An application that decodes ``INTEGRATION_ENCRYPTION_KEY`` as 64 hex
        characters refuses to start on the url-safe text this module generates
        for every other secret, so the placeholder (``genera-clave-hex-64-caracteres``)
        and then the name (``WEBHOOK_SECRET_HEX_32``) are asked first.

        Args:
            variable: The variable, with the placeholder as its default.

        Returns:
            The number of hex characters, or None when neither says ``hex``.
            ``hex`` alone is 64; ``hex-32-bytes`` and ``hex-256-bits`` are counted
            in characters like ``hex-64``; a length nobody could mean is ignored.
        """
        for text in (variable.default.lower(), variable.name.lower()):
            found = _HEX_HINT.search(text)
            if not found:
                continue
            if found.group(1) is None:
                return _DEFAULT_HEX_LENGTH
            count, unit = int(found.group(1)), found.group(2) or ""
            length = count * 2 if unit.startswith("byte") else count // 4 if unit else count
            if length in _HEX_LENGTH_RANGE:
                return length
        return None

    def _generate_for(self, variable: EnvVariable) -> str:
        """
        Generate the secret a variable asks for, in the shape it asks for it.

        Args:
            variable: A secret whose default is empty or a placeholder.

        Returns:
            Hexadecimal when the variable says so, url-safe text otherwise.
        """
        length = self.hex_length(variable)
        return self.generate_hex_secret(length) if length else self.generate_secret()

    def prompt_variables(
        self,
        variables: list[EnvVariable],
        existing_values: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Interactively prompt for variable values grouped by category.

        Prompts come from :mod:`noust.cli.prompts`, falling back to input()
        when questionary is missing.

        Args:
            variables: List of variables to prompt for.
            existing_values: Existing values to use as defaults.

        Returns:
            Dictionary of variable name -> value.
        """
        existing = existing_values or {}
        result = {}
        unanswered: list[str] = []

        # Group by category
        categories: dict[str, list[EnvVariable]] = {}
        for var in variables:
            cat = var.category
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(var)

        from noust.cli import prompts

        for category, cat_vars in sorted(categories.items()):
            self.logger.info(f"\n  [{category}]")

            for var in cat_vars:
                # A template value is not offered as the one to accept: Enter on
                # "genera-clave-hex-64-caracteres" is how it reached a .env.
                template = self._is_placeholder(var.default)
                if var.name in existing:
                    current = existing[var.name]
                else:
                    current = "" if template else var.default

                # Auto-generate secrets if no existing value
                if var.secret and not current:
                    result[var.name] = self._generate_for(var)
                    self.logger.substep(f"{var.name} = [auto-generated]")
                    continue

                desc = f" ({var.description})" if var.description else ""
                prompt_msg = f"  {var.name}{desc}"

                if current:
                    prompt_msg += f" [{current}]"
                elif template:
                    prompt_msg += " [needs a value]"

                if prompts.AVAILABLE:
                    # A secret is not echoed. It ends up in a systemd unit and
                    # in a .env, and a shoulder is the cheapest way to lose one.
                    question = prompts.Password if var.secret else prompts.Text
                    answers = prompts.prompt(
                        [question("value", message=var.name, default=current or "")]
                    )
                    value = answers["value"] if answers else current
                else:
                    value = input(f"{prompt_msg}: ").strip()

                if template and not (value or current):
                    unanswered.append(var.name)
                    continue
                result[var.name] = value or current or ""

        if unanswered:
            self._warn_needs_value(unanswered)
        return result

    def prompt_non_interactive(
        self,
        variables: list[EnvVariable],
        supplied: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Fill variable values non-interactively.

        A template value from ``.env.example`` is never written as if it were
        real. For a secret (by its name), an empty default or a placeholder
        (``your-secret-key-here``, ``genera-clave-hex-64-caracteres``) is
        replaced by a generated value, hexadecimal when the placeholder or the
        name says so; a real default is kept. For anything else a placeholder
        default is left out, not invented: only the operator knows the SMTP host
        or the bucket, so it is named in one warning with the way to give it, and
        the application starts without the variable instead of with a lie.

        Args:
            variables: List of variables.
            supplied: Names the operator already gave a value for (``--env-file``,
                the create request); they are not reported as missing, and they
                are not written from here either, since the caller merges them.

        Returns:
            Dictionary of variable name -> value, without the variables that
            have to be supplied.
        """
        given = supplied or {}
        result = {}
        missing: list[str] = []
        for var in variables:
            default_is_placeholder = self._is_placeholder(var.default)

            if var.secret and (not var.default or default_is_placeholder):
                result[var.name] = self._generate_for(var)
                if default_is_placeholder:
                    self.logger.debug(
                        f"Regenerated secret for {var.name} (placeholder default detected)"
                    )
                continue

            if default_is_placeholder:
                if var.name not in given:
                    missing.append(var.name)
                continue

            result[var.name] = var.default

        if missing:
            self._warn_needs_value(missing)
        return result

    def _warn_needs_value(self, names: list[str]) -> None:
        """
        Tell the operator which variables were left out because they need a real value.

        Args:
            names: The variables, in the order the example declares them.
        """
        self.logger.warning(
            f"Not written, .env.example only gives a template value: {', '.join(names)}. "
            f"Pass them with --env-file, or run 'noust env configure <domain>' to be asked, "
            f"before the application needs them."
        )

    def write_env_files(
        self,
        app_path: Path,
        values: dict[str, str],
        file_mapping: dict[str, list[str]] | None = None,
    ) -> list[Path]:
        """
        Write environment variables to .env files.

        Args:
            app_path: Application root path.
            values: Variable name -> value mapping.
            file_mapping: Optional mapping of filename -> variable names.
                If None, writes all variables to a single .env file.

        Returns:
            List of written file paths.
        """
        written = []

        if file_mapping:
            for filename, var_names in file_mapping.items():
                file_path = app_path / filename
                file_values = {k: values[k] for k in var_names if k in values}
                self._write_single_env_file(file_path, file_values)
                written.append(file_path)
        else:
            env_path = app_path / ".env"
            self._write_single_env_file(env_path, values)
            written.append(env_path)

        return written

    def _write_single_env_file(self, path: Path, values: dict[str, str]) -> None:
        """
        Write a single .env file, readable by its owner only.

        A value is left bare unless that would change what
        :meth:`read_env_file` gives back for it: values written unquoted come
        back with surrounding whitespace stripped, and a value that itself
        starts and ends with the same quote character would have that pair
        read as delimiters and stripped too. Both are wrapped in double
        quotes, with the value copied through unescaped, because
        ``read_env_file`` unquotes by dropping exactly the first and last
        character rather than by scanning for an unescaped delimiter -
        wrapping never needs anything smarter than that to round-trip.

        A symlink at the destination is refused rather than written through.
        The seam would not follow it anyway, because it renames a temporary file
        into place, but a link that appears where an application's ``.env``
        belongs is someone trying to harvest credentials, and continuing past it
        as if it were an ordinary file hides that.

        Args:
            path: Path to write the .env file.
            values: Variable name -> value mapping.

        Raises:
            SecurityError: If the destination is a symlink.
            OSError: If the file cannot be created or written.
        """
        if path.is_symlink():
            raise SecurityError(
                f"Refusing to write secrets through the symlink {path}",
                details=(
                    "Something replaced the file with a symbolic link, which would "
                    "redirect the write. Inspect the directory, remove the link and "
                    "retry."
                ),
            )

        lines = []
        for key, value in sorted(values.items()):
            lines.append(f"{key}={self._quote_if_needed(value)}")
        self.fs.write_text(path, "\n".join(lines) + "\n", mode=SECRET_MODE)
        self.logger.debug(f"Wrote env file: {path}")

    @staticmethod
    def _quote_if_needed(value: str) -> str:
        """
        Quote a value only when writing it bare would change how it reads back.

        The file is read by systemd too, through ``EnvironmentFile=``, and
        systemd consumes a backslash in a bare or double-quoted value. Inside
        single quotes it is literal to systemd, to dotenv and to
        :meth:`read_env_file` alike, so a value holding one goes there - unless
        it also holds a single quote, which no quoting represents for all
        three.

        Args:
            value: The raw value to write.

        Returns:
            ``value`` unchanged, or wrapped in single or double quotes.
        """
        if "\\" in value and "'" not in value:
            return f"'{value}'"
        has_surrounding_whitespace = value != value.strip()
        looks_pre_quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'")
        if has_surrounding_whitespace or looks_pre_quoted:
            return f'"{value}"'
        return value

    def save_config(self, app_path: Path, config: EnvConfig) -> None:
        """
        Persist environment configuration to .wasm/env-config.json.

        The file records the variable inventory, defaults included, so it is
        written 0600 inside a 0700 directory. Nothing but Noust reads ``.wasm``,
        so tightening that directory costs the deployment nothing.

        Args:
            app_path: Application root path.
            config: Environment configuration to save.

        Raises:
            SecurityError: If the destination is a symlink.
            OSError: If the file cannot be created or written.
        """
        directory = app_path / ".wasm"
        # In place the application directory is a repository checkout, and a
        # committed ``.wasm -> /etc`` would have this tighten /etc to 0700
        # and write the inventory there.
        if directory.is_symlink():
            raise SecurityError(
                f"Refusing to write the environment inventory through the symlink {directory}",
                details="Remove .wasm from the repository; Noust keeps its own files there.",
            )
        self.fs.make_dir(directory, mode=SECRET_DIR_MODE, parents=True)
        # A .wasm left world readable by an older version is tightened; the
        # directory is ours alone, so there is nothing else to break.
        if directory.is_dir() and directory.stat().st_mode & 0o077:
            self.fs.chmod(directory, SECRET_DIR_MODE, follow_symlinks=False)

        target = directory / "env-config.json"
        if target.is_symlink():
            raise SecurityError(
                f"Refusing to write secrets through the symlink {target}",
                details=(
                    "Something replaced the file with a symbolic link, which would "
                    "redirect the write. Inspect the directory, remove the link and "
                    "retry."
                ),
            )

        self.fs.write_text(target, json.dumps(config.to_dict(), indent=2), mode=SECRET_MODE)

    def load_config(self, app_path: Path) -> EnvConfig | None:
        """
        Load persisted environment configuration.

        Args:
            app_path: Application root path.

        Returns:
            EnvConfig or None if not found.
        """
        config_file = app_path / ".wasm" / "env-config.json"
        if not config_file.exists():
            return None
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
            return EnvConfig.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            self.logger.warning(f"Failed to load env config: {e}")
            return None

    def mask_value(self, name: str, value: str) -> str:
        """
        Mask a value if it's a secret.

        Shows only the first 4 characters followed by asterisks. Uses the
        full classifier (:func:`~noust.core.secret_detection.classify`), not
        just the name-substring check :meth:`_is_secret` makes while parsing
        ``.env.example``, so a value that only looks secret on its own shape
        - a Stripe key behind an innocuous name - is masked here too.

        Args:
            name: Variable name.
            value: Variable value.

        Returns:
            Masked or original value.
        """
        if classify(name, value).secret and len(value) > 4:
            return value[:4] + "****"
        return value

    def get_current_values(self, app_path: Path) -> dict[str, str]:
        """
        Read the ``.env`` at the top of a directory.

        Only right for a directory that holds its own ``.env``: an in-place
        application, or ``shared/`` of a release one. Callers that have an
        application rather than a directory read through
        :func:`noust.deployers.helpers.app_env.read_app_env`, which knows which.

        Args:
            app_path: Directory holding the ``.env``.

        Returns:
            Dictionary of current environment variable values.
        """
        return self.read_env_file(app_path / ".env")

    def write_env_file(self, path: Path, values: dict[str, str]) -> None:
        """
        Write one environment file, readable by its owner only.

        Args:
            path: The file to write.
            values: Variable name to value.

        Raises:
            SecurityError: If the destination is a symlink.
            OSError: If the file cannot be created or written.
        """
        self._write_single_env_file(path, values)

    #: A leading ``export`` keyword, the way a shell (and dotenv) accepts it: the
    #: literal, lowercase word followed by at least one space or tab, consumed
    #: whole so ``export  FOO`` and ``export\tFOO`` both leave a clean key.
    #: Case-sensitive and requiring the whitespace is what keeps ``exported=yes``
    #: and ``EXPORT_DIR=...`` intact - they don't have a bare ``export`` word
    #: to strip.
    _EXPORT_PREFIX = re.compile(r"^export[ \t]+(.*)$")

    def read_env_file(self, env_file: Path) -> dict[str, str]:
        """
        Read the values of one environment file.

        Strips quotes from values for consistency, and a leading ``export``
        keyword from names, the way a shell sourcing the file (or Node's
        dotenv) would: ``export FOO=bar`` is read as ``FOO``.

        Args:
            env_file: The file to read.

        Returns:
            Variable name to value; empty when the file does not exist.
        """
        if not env_file.exists():
            return {}
        try:
            return self.parse_env_text(env_file.read_text(encoding="utf-8"))
        except OSError:
            return {}

    @classmethod
    def parse_env_text(cls, content: str) -> dict[str, str]:
        """
        Parse the text of an environment file.

        The one parser behind :meth:`read_env_file`, for a caller that has
        to open the file itself (without following links, with a size cap)
        and only then hand over what it read.

        Args:
            content: The file's text.

        Returns:
            Variable name to value, quotes and a leading ``export`` stripped.
        """
        values: dict[str, str] = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            exported = cls._EXPORT_PREFIX.match(key)
            if exported:
                key = exported.group(1)
            val = val.strip()
            if len(val) >= 2:
                if (val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'"):
                    val = val[1:-1]
            values[key] = val
        return values


def is_secret_env_name(name: str) -> bool:
    """
    Decide whether an environment variable's name marks its value as a secret.

    The name-only step of :func:`~noust.core.secret_detection.classify`,
    kept here under its established name for callers that have a name and
    nothing else - :mod:`noust.deployers.inspect` flags a discovered
    ``.env.example`` default this way before any value has been chosen for
    it. A caller that also has the value should call
    :func:`~noust.core.secret_detection.classify` directly instead: it also
    catches a secret-shaped value behind an innocuous name, and honours an
    operator's own mark.

    Args:
        name: Variable name.

    Returns:
        True if the name alone means the value behind it must not be shown
        in clear.
    """
    return name_looks_secret(name)
