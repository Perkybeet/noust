# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the EnvManager helper.

Two properties beyond the parsing surface. Everything this manager writes is a
credential store, so it goes out through the :mod:`noust.core.fs` seam with
:data:`~noust.core.fs.SECRET_MODE`, which is what makes ``--dry-run`` honest and
the file 0600. And a password hidden inside a connection string is redacted
whether or not the URL names a user: ``redis://:password@host`` is the canonical
Redis form and used to come out in clear.
"""

import ast
import re
import types
from pathlib import Path

import pytest

from noust.core.exceptions import SecurityError
from noust.core.fs import (
    SECRET_DIR_MODE,
    SECRET_MODE,
    DryRunFileSystem,
    RecordingFileSystem,
)
from noust.deployers.helpers import env_manager as env_manager_module
from noust.deployers.helpers.env_manager import (
    EnvConfig,
    EnvManager,
    EnvVariable,
    is_secret_env_name,
    redact_url_credentials,
)


@pytest.fixture
def env_manager():
    """Create an EnvManager instance."""
    return EnvManager(verbose=False)


@pytest.fixture
def warnings(env_manager, monkeypatch):
    """What the manager warns the operator about, instead of printing it."""
    messages: list[str] = []
    monkeypatch.setattr(env_manager.logger, "warning", messages.append)
    return messages


@pytest.fixture
def temp_app(tmp_path):
    """Create a temporary application directory with .env.example."""
    env_example = tmp_path / ".env.example"
    env_example.write_text(
        "# Database connection URL\n"
        "DATABASE_URL=postgresql://localhost:5432/mydb\n"
        "\n"
        "# JWT secret for authentication\n"
        "JWT_SECRET=\n"
        "\n"
        "# Application port\n"
        "PORT=3000\n"
        "\n"
        "# API key for external service\n"
        "API_KEY=\n"
        "\n"
        "NODE_ENV=production\n"
    )
    return tmp_path


class TestEnvManagerParsing:
    """Tests for .env.example parsing."""

    def test_parse_env_example(self, env_manager, temp_app):
        """Test parsing a basic .env.example file."""
        variables = env_manager.discover(temp_app)
        assert len(variables) == 5

        names = [v.name for v in variables]
        assert "DATABASE_URL" in names
        assert "JWT_SECRET" in names
        assert "PORT" in names
        assert "API_KEY" in names
        assert "NODE_ENV" in names

    def test_defaults_parsed(self, env_manager, temp_app):
        """Test that default values are parsed correctly."""
        variables = env_manager.discover(temp_app)
        by_name = {v.name: v for v in variables}

        assert by_name["DATABASE_URL"].default == "postgresql://localhost:5432/mydb"
        assert by_name["PORT"].default == "3000"
        assert by_name["NODE_ENV"].default == "production"
        assert by_name["JWT_SECRET"].default == ""

    def test_descriptions_parsed(self, env_manager, temp_app):
        """Test that comments become descriptions."""
        variables = env_manager.discover(temp_app)
        by_name = {v.name: v for v in variables}

        assert "Database" in by_name["DATABASE_URL"].description
        assert "JWT" in by_name["JWT_SECRET"].description

    def test_quoted_defaults(self, env_manager, tmp_path):
        """Test parsing quoted default values."""
        env_file = tmp_path / ".env.example"
        env_file.write_text(
            "QUOTED_DOUBLE=\"hello world\"\nQUOTED_SINGLE='hello world'\nUNQUOTED=hello\n"
        )

        variables = env_manager.discover(tmp_path)
        by_name = {v.name: v for v in variables}

        assert by_name["QUOTED_DOUBLE"].default == "hello world"
        assert by_name["QUOTED_SINGLE"].default == "hello world"
        assert by_name["UNQUOTED"].default == "hello"

    def test_empty_file(self, env_manager, tmp_path):
        """Test parsing an empty file."""
        env_file = tmp_path / ".env.example"
        env_file.write_text("")
        variables = env_manager.discover(tmp_path)
        assert len(variables) == 0

    def test_no_env_example(self, env_manager, tmp_path):
        """Test when no .env.example exists."""
        variables = env_manager.discover(tmp_path)
        assert len(variables) == 0

    def test_subdirectory_scanning(self, env_manager, tmp_path):
        """Test that subdirectories are scanned."""
        apps_dir = tmp_path / "apps" / "backend"
        apps_dir.mkdir(parents=True)

        (tmp_path / ".env.example").write_text("ROOT_VAR=1\n")
        (apps_dir / ".env.example").write_text("BACKEND_VAR=2\n")

        variables = env_manager.discover(tmp_path)
        names = [v.name for v in variables]
        assert "ROOT_VAR" in names
        assert "BACKEND_VAR" in names

    def test_deduplication(self, env_manager, tmp_path):
        """Test that duplicate variable names are deduplicated."""
        apps_dir = tmp_path / "apps" / "api"
        apps_dir.mkdir(parents=True)

        (tmp_path / ".env.example").write_text("DATABASE_URL=pg://root\n")
        (apps_dir / ".env.example").write_text("DATABASE_URL=pg://api\n")

        variables = env_manager.discover(tmp_path)
        db_vars = [v for v in variables if v.name == "DATABASE_URL"]
        assert len(db_vars) == 1
        # Root takes precedence
        assert db_vars[0].default == "pg://root"


class TestCategoryDetection:
    """Tests for variable category detection."""

    def test_database_category(self, env_manager):
        assert env_manager._detect_category("DATABASE_URL") == "Database"
        assert env_manager._detect_category("DB_HOST") == "Database"
        assert env_manager._detect_category("POSTGRES_PASSWORD") == "Database"

    def test_auth_category(self, env_manager):
        assert env_manager._detect_category("JWT_SECRET") == "Authentication"
        assert env_manager._detect_category("AUTH_PROVIDER") == "Authentication"
        assert env_manager._detect_category("SESSION_TIMEOUT") == "Authentication"

    def test_email_category(self, env_manager):
        assert env_manager._detect_category("SMTP_HOST") == "Email"
        assert env_manager._detect_category("MAIL_FROM") == "Email"

    def test_server_category(self, env_manager):
        assert env_manager._detect_category("PORT") == "Server"
        assert env_manager._detect_category("HOST") == "Server"
        assert env_manager._detect_category("NODE_ENV") == "Server"

    def test_unknown_category(self, env_manager):
        assert env_manager._detect_category("CUSTOM_THING") == "General"


class TestDiscoveryStaysInsideTheCheckout:
    """
    A repository is untrusted input, and discovery reads it as root.

    A ``.env.example`` that is a symlink to ``/etc/shadow`` or to another
    application's ``.env`` would otherwise have its lines parsed into
    variables whose defaults go back to whoever asked for the inspection.
    """

    def test_a_symlinked_example_outside_the_checkout_is_skipped(self, env_manager, tmp_path):
        outside = tmp_path / "outside.env"
        outside.write_text("STOLEN_VALUE=hunter2\n")
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / ".env.example").symlink_to(outside)

        assert env_manager.discover(checkout) == []

    def test_a_symlinked_example_inside_the_checkout_is_skipped_too(self, env_manager, tmp_path):
        # Refusing every link is simpler to reason about than resolving and
        # comparing, and an example file never needs to be one.
        (tmp_path / "real.env").write_text("LINKED=1\n")
        (tmp_path / ".env.template").symlink_to(tmp_path / "real.env")
        (tmp_path / ".env.example").write_text("PLAIN=1\n")

        names = [v.name for v in env_manager.discover(tmp_path)]

        assert names == ["PLAIN"]

    def test_a_symlinked_workspace_directory_is_not_followed(self, env_manager, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / ".env.example").write_text("FOREIGN=1\n")
        checkout = tmp_path / "checkout"
        (checkout / "apps").mkdir(parents=True)
        (checkout / "apps" / "web").symlink_to(elsewhere, target_is_directory=True)

        assert env_manager.discover(checkout) == []

    def test_a_symlinked_workspace_parent_is_not_followed(self, env_manager, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "api").mkdir(parents=True)
        (elsewhere / "api" / ".env.example").write_text("FOREIGN=1\n")
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "packages").symlink_to(elsewhere, target_is_directory=True)

        assert env_manager.discover(checkout) == []

    def test_a_root_reached_through_a_symlink_still_works(self, env_manager, tmp_path):
        # On the releases layout the running code is app_path/current, a
        # link WASM made itself: the root may be one, only its content may not.
        release = tmp_path / "releases" / "1"
        release.mkdir(parents=True)
        (release / ".env.example").write_text("PORT=3000\n")
        (tmp_path / "current").symlink_to(release, target_is_directory=True)

        names = [v.name for v in env_manager.discover(tmp_path / "current")]

        assert names == ["PORT"]

    def test_a_directory_named_like_an_example_is_ignored(self, env_manager, tmp_path):
        (tmp_path / ".env.example").mkdir()

        assert env_manager.discover(tmp_path) == []


class TestTheOneSecretNameClassifier:
    """``is_secret_env_name`` is the deployer's patterns plus the config's words."""

    @pytest.mark.parametrize(
        "name",
        ["DB_PASSWORD", "ADMIN_PASS", "STRIPE_API_KEY", "JWT_SECRET", "GITHUB_TOKEN"],
    )
    def test_the_deployer_patterns_are_secrets(self, name):
        assert is_secret_env_name(name) is True

    @pytest.mark.parametrize("name", ["AUTH", "SLACK_WEBHOOK", "AWS_CREDENTIALS", "apiKey"])
    def test_the_config_words_are_secrets(self, name):
        # None of these contains a SECRET_PATTERNS substring; every one is a
        # word config.redact_secrets hides.
        assert not any(p in name.upper() for p in EnvManager.SECRET_PATTERNS)
        assert is_secret_env_name(name) is True

    @pytest.mark.parametrize("name", ["PORT", "NODE_ENV", "PUBLIC_URL", "KEYBOARD_LAYOUT"])
    def test_ordinary_names_are_not(self, name):
        assert is_secret_env_name(name) is False


class TestSecretDetection:
    """Tests for secret detection."""

    def test_password_detected(self, env_manager):
        assert env_manager._is_secret("DATABASE_PASSWORD") is True
        assert env_manager._is_secret("ADMIN_PASSWORD") is True

    def test_secret_detected(self, env_manager):
        assert env_manager._is_secret("JWT_SECRET") is True
        assert env_manager._is_secret("CLIENT_SECRET") is True

    def test_token_detected(self, env_manager):
        assert env_manager._is_secret("ACCESS_TOKEN") is True
        assert env_manager._is_secret("REFRESH_TOKEN") is True

    def test_api_key_detected(self, env_manager):
        assert env_manager._is_secret("API_KEY") is True
        assert env_manager._is_secret("STRIPE_API_KEY") is True

    def test_non_secret(self, env_manager):
        assert env_manager._is_secret("PORT") is False
        assert env_manager._is_secret("NODE_ENV") is False
        assert env_manager._is_secret("DATABASE_URL") is False


class TestSecretGeneration:
    """Tests for secret generation."""

    def test_generates_string(self, env_manager):
        secret = env_manager.generate_secret()
        assert isinstance(secret, str)
        assert len(secret) > 0

    def test_unique_secrets(self, env_manager):
        secrets = {env_manager.generate_secret() for _ in range(10)}
        assert len(secrets) == 10

    def test_custom_length(self, env_manager):
        short = env_manager.generate_secret(8)
        long = env_manager.generate_secret(64)
        assert len(short) < len(long)


class TestNonInteractivePrompt:
    """Tests for non-interactive variable filling."""

    def test_uses_defaults(self, env_manager):
        variables = [
            EnvVariable(name="PORT", default="3000"),
            EnvVariable(name="NODE_ENV", default="production"),
        ]
        result = env_manager.prompt_non_interactive(variables)
        assert result["PORT"] == "3000"
        assert result["NODE_ENV"] == "production"

    def test_generates_secrets(self, env_manager):
        variables = [
            EnvVariable(name="JWT_SECRET", secret=True, default=""),
        ]
        result = env_manager.prompt_non_interactive(variables)
        assert result["JWT_SECRET"] != ""
        assert len(result["JWT_SECRET"]) > 10

    def test_keeps_default_for_secrets_with_default(self, env_manager):
        variables = [
            EnvVariable(name="JWT_SECRET", secret=True, default="my-fixed-secret"),
        ]
        result = env_manager.prompt_non_interactive(variables)
        assert result["JWT_SECRET"] == "my-fixed-secret"

    def test_regenerates_secret_with_your_prefix_placeholder(self, env_manager):
        variables = [
            EnvVariable(
                name="NEXTAUTH_SECRET",
                secret=True,
                default="your-secret-key-here",
            ),
        ]
        result = env_manager.prompt_non_interactive(variables)
        assert result["NEXTAUTH_SECRET"] != "your-secret-key-here"
        assert len(result["NEXTAUTH_SECRET"]) > 10

    def test_regenerates_secret_with_angle_bracket_placeholder(self, env_manager):
        variables = [
            EnvVariable(
                name="CLIENT_SECRET",
                secret=True,
                default="<your-client-secret>",
            ),
        ]
        result = env_manager.prompt_non_interactive(variables)
        assert result["CLIENT_SECRET"] != "<your-client-secret>"
        assert "<" not in result["CLIENT_SECRET"]

    def test_regenerates_secret_with_changeme_placeholder(self, env_manager):
        variables = [
            EnvVariable(name="API_TOKEN", secret=True, default="changeme"),
        ]
        result = env_manager.prompt_non_interactive(variables)
        assert result["API_TOKEN"] != "changeme"

    def test_a_non_secret_placeholder_is_not_written_and_is_named(self, env_manager, warnings):
        """A template value is not a value: the operator has to give a real one."""
        variables = [
            EnvVariable(
                name="DATABASE_URL",
                secret=False,
                default="mysql://user:password@localhost:3306/db",
            ),
        ]
        result = env_manager.prompt_non_interactive(variables)
        assert "DATABASE_URL" not in result
        assert any("DATABASE_URL" in message for message in warnings)


class TestPlaceholderDetection:
    """Tests for placeholder default detection."""

    def test_your_prefix_is_placeholder(self, env_manager):
        assert env_manager._is_placeholder("your-secret") is True
        assert env_manager._is_placeholder("YOUR-SECRET") is True
        assert env_manager._is_placeholder("your_app_password") is True

    def test_angle_brackets_are_placeholder(self, env_manager):
        assert env_manager._is_placeholder("<token>") is True

    def test_changeme_is_placeholder(self, env_manager):
        assert env_manager._is_placeholder("changeme") is True
        assert env_manager._is_placeholder("change-me") is True
        assert env_manager._is_placeholder("CHANGE_ME") is True

    def test_user_password_url_is_placeholder(self, env_manager):
        url = "mysql://user:password@localhost:3306/db"
        assert env_manager._is_placeholder(url) is True

    def test_empty_value_is_not_placeholder(self, env_manager):
        assert env_manager._is_placeholder("") is False

    def test_real_value_is_not_placeholder(self, env_manager):
        assert env_manager._is_placeholder("3000") is False
        assert env_manager._is_placeholder("production") is False
        assert env_manager._is_placeholder("my-fixed-secret") is False
        assert env_manager._is_placeholder("postgresql://app:K8j2x@db.example.com/app") is False


class TestSpanishPlaceholders:
    """The marker words of a Spanish ``.env.example`` are markers too."""

    @pytest.mark.parametrize(
        "value",
        [
            "genera-clave-hex-64-caracteres",
            "genera-password-seguro-aqui",
            "GENERA-SECRETO",
            "generar-un-secreto",
            "cambiar",
            "CAMBIAR_ESTO",
            "cambiar-por-tu-clave",
            "tu-api-key-google-maps",
            "tu-usuario-smtp",
            "smtp.tu-proveedor.com",
            "noreply@tu-dominio.com",
            "pon-aqui-el-valor",
            "PON-TU-TOKEN",
        ],
    )
    def test_is_a_placeholder(self, env_manager, value):
        assert env_manager._is_placeholder(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "general-purpose",
            "generated-by-ci",
            "virtu-al.example.com",
            "kitu-1",
            "caponi-x",
            "stu-dio",
            "3000",
            "proggest_dev_password",
            "postgresql://proggest:K8j2x@db.example.com/proggest",
        ],
    )
    def test_a_real_value_that_contains_the_letters_is_not(self, env_manager, value):
        assert env_manager._is_placeholder(value) is False


class TestPlaceholdersAreNeverWrittenVerbatim:
    """What ``.env.example`` writes down as a to-do is never deployed as a value."""

    def test_a_secret_with_a_spanish_placeholder_is_generated(self, env_manager):
        variables = [
            EnvVariable(name="REDIS_PASSWORD", secret=True, default="genera-password-seguro-aqui"),
            EnvVariable(name="JWT_SECRET", secret=True, default="CAMBIAR_ESTO"),
        ]

        result = env_manager.prompt_non_interactive(variables)

        assert not result["REDIS_PASSWORD"].startswith("genera")
        assert len(result["REDIS_PASSWORD"]) > 10
        assert result["JWT_SECRET"] != "CAMBIAR_ESTO"

    def test_a_key_the_placeholder_says_is_64_hex_characters_is_64_hex_characters(
        self, env_manager
    ):
        """The case that took Proggest's backend down: the key was written as the text."""
        variables = [
            EnvVariable(
                name="INTEGRATION_ENCRYPTION_KEY",
                secret=True,
                default="genera-clave-hex-64-caracteres",
            ),
            EnvVariable(
                name="PERSONNEL_ENCRYPTION_KEY",
                secret=True,
                default="genera-clave-hex-64-caracteres",
            ),
        ]

        result = env_manager.prompt_non_interactive(variables)

        for name in ("INTEGRATION_ENCRYPTION_KEY", "PERSONNEL_ENCRYPTION_KEY"):
            assert re.fullmatch(r"[0-9a-f]{64}", result[name]), result[name]
        assert result["INTEGRATION_ENCRYPTION_KEY"] != result["PERSONNEL_ENCRYPTION_KEY"]

    @pytest.mark.parametrize(
        ("default", "length"),
        [
            ("genera-clave-hex-32", 32),
            ("change-me-hex-128-chars", 128),
            ("genera-clave-hex-32-bytes", 64),
            ("change-me-hex-256-bits", 64),
            ("cambiar-hex_48", 48),
        ],
    )
    def test_the_length_is_read_from_the_placeholder(self, env_manager, default, length):
        variables = [EnvVariable(name="SIGNING_KEY", secret=True, default=default)]

        value = env_manager.prompt_non_interactive(variables)["SIGNING_KEY"]

        assert re.fullmatch(rf"[0-9a-f]{{{length}}}", value), value

    def test_the_name_can_say_it_is_hex_too(self, env_manager):
        variables = [
            EnvVariable(name="WEBHOOK_SECRET_HEX_32", secret=True, default=""),
            EnvVariable(name="APP_ENCRYPTION_KEY_HEX", secret=True, default="CAMBIAR_X"),
        ]

        result = env_manager.prompt_non_interactive(variables)

        assert re.fullmatch(r"[0-9a-f]{32}", result["WEBHOOK_SECRET_HEX_32"])
        # No length said anywhere: 32 bytes, the size of an AES-256 key.
        assert re.fullmatch(r"[0-9a-f]{64}", result["APP_ENCRYPTION_KEY_HEX"])

    def test_a_secret_that_says_nothing_about_hex_is_not_hex(self, env_manager):
        variables = [EnvVariable(name="JWT_SECRET", secret=True, default="genera-secreto-jwt-aqui")]

        value = env_manager.prompt_non_interactive(variables)["JWT_SECRET"]

        assert not re.fullmatch(r"[0-9a-f]+", value)

    def test_a_hex_length_nobody_could_mean_is_ignored(self, env_manager):
        variables = [EnvVariable(name="X_KEY", secret=True, default="change-me-hex-999999")]

        value = env_manager.prompt_non_interactive(variables)["X_KEY"]

        assert len(value) < 100

    @pytest.mark.parametrize(
        ("name", "default"),
        [
            ("SMTP_HOST", "smtp.tu-proveedor.com"),
            ("MAIL_FROM", "noreply@tu-dominio.com"),
            ("AWS_S3_BUCKET", "tu-bucket-s3"),
            ("SMTP_USER", "tu-usuario-smtp"),
            ("DATABASE_URL", "postgresql://user:password@localhost:5432/db"),
            ("SMTP_HOST_EN", "smtp.your-provider.com"),
        ],
    )
    def test_a_placeholder_that_is_not_a_secret_has_to_be_supplied(
        self, env_manager, warnings, name, default
    ):
        variables = [
            EnvVariable(name="PORT", default="3000"),
            EnvVariable(name=name, default=default, required=False),
        ]

        result = env_manager.prompt_non_interactive(variables)

        assert result == {"PORT": "3000"}
        (message,) = warnings
        assert name in message
        assert "noust env" in message  # how to give it

    def test_a_value_supplied_by_the_operator_is_not_reported_missing(self, env_manager, warnings):
        variables = [EnvVariable(name="SMTP_HOST", default="smtp.tu-proveedor.com")]

        result = env_manager.prompt_non_interactive(variables, supplied={"SMTP_HOST": "mx.acme.io"})

        assert result == {}
        assert warnings == []

    def test_one_warning_names_every_variable_that_is_missing(self, env_manager, warnings):
        variables = [
            EnvVariable(name="SMTP_HOST", default="smtp.tu-proveedor.com"),
            EnvVariable(name="SMTP_USER", default="tu-usuario-smtp"),
        ]

        env_manager.prompt_non_interactive(variables)

        (message,) = warnings
        assert "SMTP_HOST" in message
        assert "SMTP_USER" in message

    def test_real_defaults_and_empty_secrets_behave_as_before(self, env_manager, warnings):
        variables = [
            EnvVariable(name="PORT", default="3000"),
            EnvVariable(name="JWT_SECRET", secret=True, default=""),
            EnvVariable(name="API_TOKEN", secret=True, default="my-fixed-token"),
            EnvVariable(name="OPTIONAL", default=""),
        ]

        result = env_manager.prompt_non_interactive(variables)

        assert result["PORT"] == "3000"
        assert len(result["JWT_SECRET"]) > 10
        assert result["API_TOKEN"] == "my-fixed-token"
        assert result["OPTIONAL"] == ""
        assert warnings == []

    def test_the_example_of_a_real_project_comes_out_without_a_single_marker(
        self, env_manager, tmp_path, warnings
    ):
        (tmp_path / ".env.example").write_text(
            "# Cifra las credenciales por empresa\n"
            "INTEGRATION_ENCRYPTION_KEY=genera-clave-hex-64-caracteres\n"
            "REDIS_PASSWORD=genera-password-seguro-aqui\n"
            "STORAGE_TYPE=s3\n"
            "SMTP_HOST=smtp.tu-proveedor.com\n"
            "AWS_ACCESS_KEY_ID=tu-access-key-id\n"
        )

        result = env_manager.prompt_non_interactive(env_manager.discover(tmp_path))

        assert re.fullmatch(r"[0-9a-f]{64}", result["INTEGRATION_ENCRYPTION_KEY"])
        assert result["STORAGE_TYPE"] == "s3"
        assert "SMTP_HOST" not in result
        assert all(not env_manager._is_placeholder(v) for v in result.values()), result


class TestAskingForVariables:
    """The interactive path never offers a placeholder as the value to accept."""

    @pytest.fixture
    def typed(self, env_manager, monkeypatch):
        """Answer the prompts with these lines and keep what was asked."""
        from noust.cli import prompts

        monkeypatch.setattr(prompts, "AVAILABLE", False)
        answers: list[str] = []
        asked: list[str] = []

        def fake_input(message: str = "") -> str:
            asked.append(message)
            return answers.pop(0)

        monkeypatch.setattr("builtins.input", fake_input)
        return types.SimpleNamespace(answers=answers, asked=asked)

    def test_a_secret_placeholder_is_generated_without_asking(self, env_manager, typed):
        variables = [
            EnvVariable(name="PERSONNEL_ENCRYPTION_KEY", secret=True, default="genera-hex-64")
        ]

        result = env_manager.prompt_variables(variables)

        assert re.fullmatch(r"[0-9a-f]{64}", result["PERSONNEL_ENCRYPTION_KEY"])
        assert typed.asked == []

    def test_the_placeholder_is_not_offered_as_the_default(self, env_manager, typed):
        typed.answers.append("mx.acme.io")
        variables = [EnvVariable(name="SMTP_HOST", default="smtp.tu-proveedor.com")]

        result = env_manager.prompt_variables(variables)

        assert result == {"SMTP_HOST": "mx.acme.io"}
        assert "smtp.tu-proveedor.com" not in typed.asked[0]

    def test_an_empty_answer_leaves_the_variable_out_and_says_so(
        self, env_manager, typed, warnings
    ):
        typed.answers.append("")
        variables = [EnvVariable(name="SMTP_HOST", default="smtp.tu-proveedor.com")]

        result = env_manager.prompt_variables(variables)

        assert result == {}
        assert any("SMTP_HOST" in message for message in warnings)

    def test_what_the_application_already_has_wins_over_the_example(self, env_manager, typed):
        typed.answers.append("")
        variables = [EnvVariable(name="SMTP_HOST", default="smtp.tu-proveedor.com")]

        result = env_manager.prompt_variables(variables, {"SMTP_HOST": "mx.acme.io"})

        assert result == {"SMTP_HOST": "mx.acme.io"}

    def test_a_real_default_is_still_offered_and_accepted(self, env_manager, typed):
        typed.answers.append("")
        variables = [EnvVariable(name="PORT", default="3000")]

        result = env_manager.prompt_variables(variables)

        assert result == {"PORT": "3000"}
        assert "[3000]" in typed.asked[0]


class TestEnvFileWriting:
    """Tests for .env file writing."""

    def test_write_env_file(self, env_manager, tmp_path):
        values = {"PORT": "3000", "NODE_ENV": "production"}
        written = env_manager.write_env_files(tmp_path, values)

        assert len(written) == 1
        content = written[0].read_text()
        assert "NODE_ENV=production" in content
        assert "PORT=3000" in content

    def test_write_with_mapping(self, env_manager, tmp_path):
        values = {
            "PORT": "3000",
            "DATABASE_URL": "pg://localhost",
            "API_KEY": "secret123",
        }
        mapping = {
            ".env": ["PORT", "DATABASE_URL"],
            "apps/api/.env": ["API_KEY"],
        }

        written = env_manager.write_env_files(tmp_path, values, mapping)
        assert len(written) == 2

    def test_values_sorted(self, env_manager, tmp_path):
        values = {"ZZZ": "last", "AAA": "first", "MMM": "middle"}
        env_manager.write_env_files(tmp_path, values)

        content = (tmp_path / ".env").read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert lines[0] == "AAA=first"
        assert lines[-1] == "ZZZ=last"


class TestMasking:
    """Tests for value masking."""

    def test_mask_secret(self, env_manager):
        assert env_manager.mask_value("API_KEY", "sk_live_abc123def456") == "sk_l****"

    def test_no_mask_non_secret(self, env_manager):
        assert env_manager.mask_value("PORT", "3000") == "3000"

    def test_short_secret_not_masked(self, env_manager):
        assert env_manager.mask_value("API_KEY", "abc") == "abc"


class TestEnvConfig:
    """Tests for EnvConfig serialization."""

    def test_save_and_load(self, env_manager, tmp_path):
        config = EnvConfig(
            variables=[
                EnvVariable(name="PORT", default="3000", category="Server"),
            ],
            files={".env": ["PORT"]},
        )

        env_manager.save_config(tmp_path, config)
        loaded = env_manager.load_config(tmp_path)

        assert loaded is not None
        assert len(loaded.variables) == 1
        assert loaded.variables[0].name == "PORT"
        assert loaded.files == {".env": ["PORT"]}

    def test_load_missing(self, env_manager, tmp_path):
        assert env_manager.load_config(tmp_path) is None

    def test_load_invalid_json(self, env_manager, tmp_path):
        wasm_dir = tmp_path / ".wasm"
        wasm_dir.mkdir()
        (wasm_dir / "env-config.json").write_text("invalid json")
        assert env_manager.load_config(tmp_path) is None


class TestCurrentValues:
    """Tests for reading current .env values."""

    def test_read_env(self, env_manager, tmp_path):
        (tmp_path / ".env").write_text("PORT=3000\nNODE_ENV=production\n")
        values = env_manager.get_current_values(tmp_path)
        assert values == {"PORT": "3000", "NODE_ENV": "production"}

    def test_read_quoted_values(self, env_manager, tmp_path):
        (tmp_path / ".env").write_text('DB_URL="postgres://localhost"\n')
        values = env_manager.get_current_values(tmp_path)
        assert values["DB_URL"] == "postgres://localhost"

    def test_skip_comments(self, env_manager, tmp_path):
        (tmp_path / ".env").write_text("# comment\nPORT=3000\n")
        values = env_manager.get_current_values(tmp_path)
        assert "PORT" in values
        assert len(values) == 1

    def test_missing_env(self, env_manager, tmp_path):
        values = env_manager.get_current_values(tmp_path)
        assert values == {}


class TestEnvFilePermissions:
    """A deployed .env carries DATABASE_URL, API keys and generated secrets."""

    def test_env_file_is_owner_only(self, tmp_path):
        """The mode comes from the creating open(), not from a later chmod."""
        manager = EnvManager(fs=RecordingFileSystem())

        written = manager.write_env_files(tmp_path, {"DATABASE_URL": "postgres://s3cret"})

        assert written[0].stat().st_mode & 0o777 == SECRET_MODE

    def test_an_env_left_lax_by_an_older_version_is_repaired(self, tmp_path):
        """Rewriting must not preserve a world-readable mode."""
        env_file = tmp_path / ".env"
        env_file.write_text("OLD=1\n")
        env_file.chmod(0o644)

        EnvManager(fs=RecordingFileSystem()).write_env_files(tmp_path, {"API_KEY": "sk-live"})

        assert env_file.stat().st_mode & 0o777 == SECRET_MODE

    def test_the_application_directory_is_not_tightened(self, tmp_path):
        """The tree is served by the web server; only the file is private."""
        app_path = tmp_path / "app"
        app_path.mkdir(mode=0o755)
        app_path.chmod(0o755)

        EnvManager(fs=RecordingFileSystem()).write_env_files(app_path, {"API_KEY": "sk-live"})

        assert app_path.stat().st_mode & 0o777 == 0o755

    def test_env_config_json_is_private_and_so_is_its_directory(self, tmp_path):
        """.wasm records the inventory; nothing but WASM reads it."""
        manager = EnvManager(fs=RecordingFileSystem())

        manager.save_config(tmp_path, EnvConfig(variables=[EnvVariable(name="API_KEY")]))

        saved = tmp_path / ".wasm" / "env-config.json"
        assert saved.stat().st_mode & 0o777 == SECRET_MODE
        assert saved.parent.stat().st_mode & 0o777 == SECRET_DIR_MODE

    def test_a_wasm_directory_left_lax_is_tightened(self, tmp_path):
        """An older version created it 0755 next to a 0600 file."""
        wasm_dir = tmp_path / ".wasm"
        wasm_dir.mkdir(mode=0o755)
        wasm_dir.chmod(0o755)

        EnvManager(fs=RecordingFileSystem()).save_config(tmp_path, EnvConfig())

        assert wasm_dir.stat().st_mode & 0o777 == SECRET_DIR_MODE

    def test_writing_through_a_planted_symlink_is_refused(self, tmp_path):
        """A link where an app's .env belongs is someone harvesting secrets."""
        victim = tmp_path / "victim.txt"
        victim.write_text("original\n")
        (tmp_path / ".env").symlink_to(victim)

        with pytest.raises(SecurityError):
            EnvManager(fs=RecordingFileSystem()).write_env_files(tmp_path, {"API_KEY": "sk-live"})

        assert victim.read_text() == "original\n"

    def test_saving_the_config_through_a_planted_symlink_is_refused(self, tmp_path):
        """The inventory file is a credential store too."""
        victim = tmp_path / "victim.txt"
        victim.write_text("original\n")
        (tmp_path / ".wasm").mkdir()
        (tmp_path / ".wasm" / "env-config.json").symlink_to(victim)

        with pytest.raises(SecurityError):
            EnvManager(fs=RecordingFileSystem()).save_config(tmp_path, EnvConfig())

        assert victim.read_text() == "original\n"

    def test_every_written_file_goes_through_the_seam(self, tmp_path):
        """The monorepo path writes several files; all of them are secrets."""
        recorder = RecordingFileSystem()

        written = EnvManager(fs=recorder).write_env_files(
            tmp_path,
            {"API_KEY": "sk-live", "PORT": "3000"},
            file_mapping={"apps/web/.env": ["API_KEY"], ".env": ["PORT"]},
        )

        assert [path for kind, path in recorder.changes if kind == "write"] == written
        for path in written:
            assert path.stat().st_mode & 0o077 == 0, f"{path} is not private"


class TestEnvWritesUnderADryRun:
    """A rehearsal that writes half the deployment is worse than no rehearsal."""

    def test_no_env_file_is_created(self, tmp_path):
        """The .env is what carries the credentials into the filesystem."""
        dry = DryRunFileSystem()

        EnvManager(fs=dry).write_env_files(tmp_path, {"API_KEY": "sk-live"})

        assert not (tmp_path / ".env").exists()
        assert list(tmp_path.iterdir()) == []
        assert any(".env" in line for line in dry.skipped)

    def test_an_existing_env_file_survives_untouched(self, tmp_path):
        """Overwriting is destructive: the previous secrets are gone."""
        env_file = tmp_path / ".env"
        env_file.write_text("API_KEY=the-old-one\n")

        EnvManager(fs=DryRunFileSystem()).write_env_files(tmp_path, {"API_KEY": "the-new-one"})

        assert env_file.exists()
        assert env_file.read_text() == "API_KEY=the-old-one\n"

    def test_the_mapped_subdirectories_are_not_created(self, tmp_path):
        """A monorepo rehearsal must not leave apps/web behind."""
        EnvManager(fs=DryRunFileSystem()).write_env_files(
            tmp_path, {"API_KEY": "sk-live"}, file_mapping={"apps/web/.env": ["API_KEY"]}
        )

        assert not (tmp_path / "apps").exists()

    def test_saving_the_config_creates_neither_file_nor_directory(self, tmp_path):
        """save_config used to create .wasm on the way to writing the file."""
        EnvManager(fs=DryRunFileSystem()).save_config(tmp_path, EnvConfig())

        assert not (tmp_path / ".wasm").exists()

    def test_an_existing_config_survives_untouched(self, tmp_path):
        """The stored inventory must come out of a rehearsal unchanged."""
        wasm_dir = tmp_path / ".wasm"
        wasm_dir.mkdir()
        config_file = wasm_dir / "env-config.json"
        config_file.write_text('{"variables": [], "files": {}}')

        EnvManager(fs=DryRunFileSystem()).save_config(
            tmp_path, EnvConfig(variables=[EnvVariable(name="NEW")])
        )

        assert config_file.read_text() == '{"variables": [], "files": {}}'

    def test_a_lax_wasm_directory_is_not_chmodded(self, tmp_path):
        """A chmod changes this machine, so a rehearsal must skip it too."""
        wasm_dir = tmp_path / ".wasm"
        wasm_dir.mkdir(mode=0o755)
        wasm_dir.chmod(0o755)

        EnvManager(fs=DryRunFileSystem()).save_config(tmp_path, EnvConfig())

        assert wasm_dir.stat().st_mode & 0o777 == 0o755


class TestConnectionStringRedaction:
    """A password inside a value that no name marks as a secret."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # The finding: the canonical Redis URL has no user name, and a
            # pattern that requires one printed the password in clear.
            ("redis://:password@host:6379", "redis://:***@host:6379"),
            ("rediss://:p4ss@host:6380/0", "rediss://:***@host:6380/0"),
            ("mongodb://:password@host:27017", "mongodb://:***@host:27017"),
            ("postgres://user:pass@host:5432/db", "postgres://user:***@host:5432/db"),
            (
                "postgresql://wasm:s3cr3t@127.0.0.1:5432/app?sslmode=require",
                "postgresql://wasm:***@127.0.0.1:5432/app?sslmode=require",
            ),
            ("amqp://guest:guest@rabbit:5672/%2f", "amqp://guest:***@rabbit:5672/%2f"),
            ("amqps://:only-a-pass@broker", "amqps://:***@broker"),
            ("mysql+pymysql://root:toor@db/app", "mysql+pymysql://root:***@db/app"),
            ("smtp://:pw@mail.example.com:465", "smtp://:***@mail.example.com:465"),
            # An empty password still says "there is a credential here".
            ("redis://:@host:6379", "redis://:***@host:6379"),
        ],
    )
    def test_credentials_inside_urls_are_replaced(self, value, expected):
        assert redact_url_credentials(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "redis://host:6379",
            "redis://localhost:6379/0",
            "postgres://localhost:5432/mydb",
            "https://api.example.com/v1/things",
            "http://127.0.0.1:8080/health",
            "amqp://rabbit:5672",
            "mongodb://replica-a:27017,replica-b:27017/app",
            "3000",
            "",
            "production",
            # A bare address is not a URL, and a mail address is not a password.
            "alerts@example.com",
            # The '@' is in the query string, past the end of the authority.
            "http://host:8080/redirect?to=user@example.com",
            # The '@' is in the path, likewise past the authority.
            "https://cdn.example.com:443/pkg/@scope/name",
        ],
    )
    def test_values_without_credentials_are_left_alone(self, value):
        assert redact_url_credentials(value) == value

    def test_several_urls_in_one_value_are_all_redacted(self):
        value = "redis://:a@one,postgres://u:b@two"

        assert redact_url_credentials(value) == "redis://:***@one,postgres://u:***@two"

    def test_the_placeholder_is_configurable(self):
        assert redact_url_credentials("redis://:pw@host", placeholder="X") == "redis://:X@host"

    def test_the_cli_prints_a_userless_redis_url_redacted(self):
        """The finding was reported against ``wasm env show``: prove it there."""
        from noust.cli.commands.env import _redact

        redacted = _redact({"REDIS_URL": "redis://:hunter2@cache:6379"})

        assert "hunter2" not in redacted["REDIS_URL"]
        assert redacted["REDIS_URL"] == "redis://:***@cache:6379"

    def test_the_cli_keeps_no_second_copy_of_the_pattern(self):
        """Two copies of a security pattern is how one of them stays wrong."""
        from noust.cli.commands import env as env_cli

        assert not hasattr(env_cli, "_URL_CREDENTIALS")

    def test_the_replacement_never_leaks_the_password_length(self):
        short = redact_url_credentials("redis://:a@host")
        long = redact_url_credentials("redis://:" + "z" * 64 + "@host")

        assert short == long


class TestNoMutationEscapesTheSeam:
    """
    The guard that stops the defect from coming back.

    ``--dry-run`` announced that nothing would change and then wrote the .env,
    because a write is a ``Path.write_text`` and never goes near a subprocess.
    Reads are deliberately not covered: they change nothing.
    """

    #: Calls that change the filesystem. Names only, because that is what
    #: survives an alias, a re-import or a helper variable.
    MUTATING = frozenset(
        {
            "chmod",
            "chown",
            "copy",
            "copy2",
            "copyfile",
            "copymode",
            "copystat",
            "copytree",
            "hardlink_to",
            "lchmod",
            "lchown",
            "link",
            "link_to",
            "makedirs",
            "mkdir",
            "mkdtemp",
            "mkstemp",
            "mknod",
            "move",
            "open",
            "remove",
            "removedirs",
            "rename",
            "renames",
            "replace",
            "rmdir",
            "rmtree",
            "symlink",
            "symlink_to",
            "touch",
            "truncate",
            "unlink",
            "utime",
            "write_bytes",
            "write_text",
            "writelines",
            "NamedTemporaryFile",
            "TemporaryDirectory",
            "TemporaryFile",
        }
    )

    #: Expressions that are the seam itself, written as source text so a new
    #: spelling has to be added here on purpose rather than by accident.
    SEAM = frozenset({"fs", "self.fs", "self._fs", "filesystem", "self.filesystem"})

    #: ``os.open`` flags that can change a file. A call naming none of them
    #: and naming ``O_RDONLY`` only reads.
    WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "O_TMPFILE")

    def _is_read_only_os_open(self, node: ast.Call, name: str, receiver: str) -> bool:
        """
        Recognise ``os.open(path, os.O_RDONLY | ...)`` with no write flag.

        Discovery reads example files with ``O_NOFOLLOW``, which only
        ``os.open`` offers; reading is not a mutation the seam has to see.

        Args:
            node: The call.
            name: The called attribute or function name.
            receiver: The expression it is called on.

        Returns:
            True for a read-only ``os.open``.
        """
        if name != "open" or receiver != "os" or len(node.args) < 2:
            return False
        flags = ast.unparse(node.args[1])
        return "O_RDONLY" in flags and not any(flag in flags for flag in self.WRITE_FLAGS)

    def _offenders(self, path: Path) -> list[str]:
        """
        Collect every mutating call in a module that bypasses the seam.

        Args:
            path: Module to inspect.

        Returns:
            One ``name (line N)`` entry per offending call.
        """
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func
            if isinstance(func, ast.Attribute):
                name, receiver = func.attr, ast.unparse(func.value)
            elif isinstance(func, ast.Name):
                name, receiver = func.id, ""
            else:
                continue

            if self._is_read_only_os_open(node, name, receiver):
                continue

            if name in self.MUTATING and receiver not in self.SEAM:
                found.append(f"{name} on {receiver or '<bare call>'} (line {node.lineno})")

        return found

    def test_the_env_manager_never_writes_outside_the_seam(self):
        """Every .env and every directory goes through noust.core.fs."""
        module = Path(env_manager_module.__file__)

        assert self._offenders(module) == []

    def test_the_guard_notices_a_direct_mutation(self, tmp_path):
        """A guard that cannot fail protects nothing."""
        sample = tmp_path / "sample.py"
        sample.write_text("from pathlib import Path\nPath('/x/.env').write_text('K=v')\n")

        assert self._offenders(sample) != []

    def test_the_guard_notices_a_writing_os_open(self, tmp_path):
        """Only a read-only os.open is let through."""
        sample = tmp_path / "sample.py"
        sample.write_text("import os\nos.open('/x/.env', os.O_RDONLY | os.O_CREAT)\n")

        assert self._offenders(sample) != []

    def test_the_guard_accepts_the_seam(self, tmp_path):
        """The same call through the seam is exactly what we want to see."""
        sample = tmp_path / "sample.py"
        sample.write_text("def f(self):\n    self.fs.write_text(self.path, 'K=v')\n")

        assert self._offenders(sample) == []
