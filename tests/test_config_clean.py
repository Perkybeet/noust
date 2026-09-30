# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Configuration hygiene (3.1, backlog 50): what no version reads any more.

``noust config show`` must not present settings of removed features as
settings; ``noust config clean`` removes them from the file as text, keeping
the operator's comments, with an owner-only dated copy first, and deletes the
OpenAI key an old AI monitor stored - from the copy too. The reference in
``docs/CONFIG.md`` gives every setting a meaning.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click
import pytest
import yaml
from click.testing import CliRunner

from noust.cli import app as app_module
from noust.cli.app import cli as root_cli
from noust.cli.commands import config as config_cmd
from noust.core.config import (
    DEFAULT_CONFIG,
    OBSOLETE_KEYS,
    REMOVED_KEYS,
    Config,
    find_obsolete,
)
from noust.core.exceptions import ConfigError
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.logger import Logger

REPO = Path(__file__).resolve().parent.parent

#: A file the way a 1.x server left it after years of upgrades: the operator's
#: own comments, the removed AI monitor, the old logging block, a key nobody
#: reads.
OLD_FILE = """\
# Noust configuration for shop-01. Edited by hand: keep the comments.
webserver: apache   # this box runs apache

ssl:
  email: ops@example.com

logging:
  level: debug
  file: /var/log/wasm/wasm.log

nodejs:
  default_version: "18"
  use_nvm: true

# The monitor mails ops
monitor:
  enabled: true
  use_ai: true
  ai_interval: 1800
  auto_terminate: true
  openai:
    api_key: sk-old-live-0123456789abcdef0123456789abcdef
    model: gpt-4o-mini
  smtp:
    host: smtp.example.com

databases:
  backup_dir: /srv/old-backups
  auto_start: true
  credentials:
    mysql:
      user: root
      password: hunter2
"""


class _TestLogger(Logger):
    """A logger that writes wherever stdout points when it is built."""

    def __init__(
        self,
        verbose: bool = False,
        no_color: bool = False,
        log_file: Path | None = None,
        stream: Any = None,
    ):
        super().__init__(
            verbose=verbose,
            no_color=no_color,
            log_file=log_file,
            stream=stream if stream is not None else sys.stdout,
        )


@pytest.fixture(autouse=True)
def _readable_output(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    Make what a command prints readable by the test, on the real filesystem.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        Nothing; restores the filesystem seam afterwards.
    """
    monkeypatch.setattr(app_module, "Logger", _TestLogger)
    monkeypatch.setattr(config_cmd, "Logger", _TestLogger)
    set_fs(None)
    try:
        yield
    finally:
        set_fs(None)


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """
    Point the real configuration at a sandbox holding the old file.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The path of the sandbox ``config.yaml``, already written.
    """
    path = tmp_path / "etc" / "noust" / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(OLD_FILE, encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    monkeypatch.setattr(config_cmd, "DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


def noust(*args: str, input: str | None = None) -> Any:
    """
    Run the real command tree.

    Args:
        *args: Arguments after the program name.
        input: What to pipe to standard input.

    Returns:
        Click's result.
    """
    return CliRunner().invoke(root_cli, list(args), input=input)


def backups(path: Path) -> list[Path]:
    """
    Find the dated copies ``clean`` made beside a file.

    Args:
        path: The configuration file.

    Returns:
        The copies.
    """
    return sorted(path.parent.glob(f"{path.name}.bak-*"))


# -- what is obsolete ----------------------------------------------------------------


def test_nothing_the_defaults_ship_is_obsolete() -> None:
    """A default that is also obsolete would ship a setting nothing reads."""
    for key in (*OBSOLETE_KEYS, *REMOVED_KEYS):
        node: Any = DEFAULT_CONFIG
        present = True
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                present = False
                break
            node = node[part]
        assert not present, f"{key} is in DEFAULT_CONFIG and in the obsolete list"


def test_every_obsolete_entry_says_why() -> None:
    for key, reason in OBSOLETE_KEYS.items():
        assert reason.strip().endswith("."), key


def test_find_obsolete_names_sections_and_leaves_and_marks_credentials() -> None:
    found = {item.key: item for item in find_obsolete(yaml.safe_load(OLD_FILE))}

    assert set(found) == {
        "monitor.use_ai",
        "monitor.ai_interval",
        "monitor.openai",
        "databases.backup_dir",
        "databases.auto_start",
        "logging",
        "nodejs",
        "monitor.auto_terminate",
    }
    assert found["monitor.openai"].secret is True
    assert found["logging"].secret is False


def test_find_obsolete_ignores_what_is_not_a_mapping() -> None:
    assert find_obsolete(None) == []
    assert find_obsolete(["monitor"]) == []


# -- the configuration in effect -----------------------------------------------------


def test_obsolete_settings_are_not_part_of_the_configuration_in_effect(config_path: Path) -> None:
    config = Config()

    tree = config.to_dict()
    assert "logging" not in tree
    assert "nodejs" not in tree
    assert "openai" not in tree["monitor"]
    assert "use_ai" not in tree["monitor"]
    assert "backup_dir" not in tree["databases"]
    # What is still a setting keeps its value.
    assert tree["webserver"] == "apache"
    assert tree["monitor"]["smtp"]["host"] == "smtp.example.com"
    assert tree["databases"]["credentials"]["mysql"]["password"] == "hunter2"


def test_a_stale_full_put_cannot_bring_them_back(config_path: Path) -> None:
    config = Config()
    stale = yaml.safe_load(OLD_FILE)

    config.replace(stale)

    assert "logging" not in config.to_dict()
    assert "openai" not in config.to_dict()["monitor"]


def test_set_refuses_an_obsolete_key_and_a_key_under_an_obsolete_section(
    config_path: Path,
) -> None:
    config = Config()

    for key in ("monitor.openai.api_key", "monitor.use_ai", "logging.level", "nodejs"):
        with pytest.raises(ConfigError, match="obsolete"):
            config.set(key, "x")


def test_setting_a_whole_section_drops_the_obsolete_keys_it_carries(config_path: Path) -> None:
    config = Config()

    config.set("databases", {"backup_dir": "/x", "credentials": {"mysql": {"user": "app"}}})

    databases = config.to_dict()["databases"]
    assert "backup_dir" not in databases
    assert databases["credentials"]["mysql"]["user"] == "app"


# -- clean ---------------------------------------------------------------------------


def test_clean_removes_the_obsolete_settings_and_only_those(config_path: Path) -> None:
    result = Config().clean()

    assert result["cleaned"] is True
    assert set(result["removed"]) == {
        "monitor.use_ai",
        "monitor.ai_interval",
        "monitor.openai",
        "databases.backup_dir",
        "databases.auto_start",
        "logging",
        "nodejs",
        "monitor.auto_terminate",
    }
    assert yaml.safe_load(config_path.read_text()) == {
        "webserver": "apache",
        "ssl": {"email": "ops@example.com"},
        "monitor": {"enabled": True, "smtp": {"host": "smtp.example.com"}},
        "databases": {"credentials": {"mysql": {"user": "root", "password": "hunter2"}}},
    }


def test_clean_keeps_every_comment_and_the_layout(config_path: Path) -> None:
    Config().clean()

    text = config_path.read_text()
    assert text.startswith(
        "# Noust configuration for shop-01. Edited by hand: keep the comments.\n"
    )
    assert "webserver: apache   # this box runs apache" in text
    assert "# The monitor mails ops" in text
    assert "ssl:\n  email: ops@example.com\n" in text


def test_clean_keeps_a_dated_owner_only_copy_that_has_no_openai_key(config_path: Path) -> None:
    result = Config().clean()

    (backup,) = backups(config_path)
    assert result["backup"] == str(backup)
    assert (backup.stat().st_mode & 0o777) == 0o600
    copy = backup.read_text()
    # It is the way back, so everything else is still there...
    assert "logging:" in copy
    assert "use_ai: true" in copy
    assert "hunter2" in copy
    # ...but the credential of the removed feature is in neither file.
    assert "sk-old-live" not in copy
    assert "sk-old-live" not in config_path.read_text()
    assert result["secrets_deleted"] == ["monitor.openai.api_key"]


def test_clean_leaves_the_file_owner_only(config_path: Path) -> None:
    config_path.chmod(0o644)

    Config().clean()

    assert (config_path.stat().st_mode & 0o777) == 0o600


def test_clean_twice_finds_nothing_and_makes_no_second_copy(config_path: Path) -> None:
    Config().clean()
    before = config_path.read_text()

    again = Config().clean()

    assert again == {"removed": [], "secrets_deleted": [], "cleaned": False, "backup": None}
    assert config_path.read_text() == before
    assert len(backups(config_path)) == 1


def test_clean_of_a_file_with_nothing_obsolete_writes_nothing(config_path: Path) -> None:
    config_path.write_text("# mine\nwebserver: nginx\n", encoding="utf-8")

    result = Config().clean()

    assert result["cleaned"] is False
    assert backups(config_path) == []
    assert config_path.read_text() == "# mine\nwebserver: nginx\n"


def test_clean_without_a_configuration_file_is_not_an_error(
    config_path: Path,
) -> None:
    config_path.unlink()

    assert Config().clean() == {
        "removed": [],
        "secrets_deleted": [],
        "cleaned": False,
        "backup": None,
    }


def test_clean_removes_a_parent_it_empties(config_path: Path) -> None:
    config_path.write_text("webserver: nginx\nmonitor:\n  use_ai: true\n", encoding="utf-8")

    Config().clean()

    assert config_path.read_text() == "webserver: nginx\n"


def test_two_cleans_in_the_same_second_keep_both_copies(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Config().clean()
    config_path.write_text("logging:\n  level: info\n", encoding="utf-8")

    Config().clean()

    assert len(backups(config_path)) == 2


def test_clean_refuses_a_layout_it_cannot_edit_and_writes_nothing(config_path: Path) -> None:
    text = "webserver: nginx\nlogging: {\n  level: info,\n  file: /x\n}\n"
    config_path.write_text(text, encoding="utf-8")

    result = Config().clean()

    assert result["cleaned"] is False
    assert "by hand" in result["error"]
    assert "logging" in result["error"]
    assert config_path.read_text() == text
    assert backups(config_path) == []


def test_clean_refuses_a_file_that_is_not_yaml(config_path: Path) -> None:
    config_path.write_text("webserver: [unclosed\n", encoding="utf-8")

    result = Config().clean()

    assert result["cleaned"] is False
    assert "error" in result
    assert backups(config_path) == []


def test_clean_under_dry_run_changes_nothing(config_path: Path) -> None:
    set_fs(DryRunFileSystem())

    result = Config().clean()

    assert result["removed"]
    assert config_path.read_text() == OLD_FILE
    assert backups(config_path) == []


def test_the_cleaned_file_loads_with_the_same_settings(config_path: Path) -> None:
    before = Config().to_dict()
    Config.reset_instance()

    Config().clean()
    Config.reset_instance()

    assert Config().to_dict() == before


# -- noust config show / clean -------------------------------------------------------


def test_show_lists_obsolete_settings_apart_and_never_prints_their_values(
    config_path: Path,
) -> None:
    result = noust("config", "show")

    assert result.exit_code == 0, result.output
    assert "sk-old-live" not in result.output
    assert "gpt-4o-mini" not in result.output
    assert "obsolete" in result.output.lower()
    assert "monitor.openai" in result.output
    assert "noust config clean" in result.output
    # Not in the tree above the list either.
    tree = result.output.split("obsolete setting(s)")[0]
    assert "openai" not in tree
    assert "use_ai" not in tree


def test_show_json_carries_the_obsolete_list(config_path: Path) -> None:
    result = noust("config", "show", "--json")

    payload = json.loads(result.output)
    assert "openai" not in payload["config"]["monitor"]
    keys = {item["key"]: item for item in payload["obsolete"]}
    assert keys["monitor.openai"]["secret"] is True
    assert "sk-old-live" not in result.output


def test_show_of_a_clean_file_lists_nothing_obsolete(config_path: Path) -> None:
    config_path.write_text("webserver: nginx\n", encoding="utf-8")

    result = noust("config", "show", "--json")

    assert json.loads(result.output)["obsolete"] == []


def test_the_clean_command_reports_what_it_removed_and_where_the_copy_is(
    config_path: Path,
) -> None:
    result = noust("config", "clean")

    assert result.exit_code == 0, result.output
    assert "monitor.openai" in result.output
    assert "logging" in result.output
    assert "Backup" in result.output
    assert "not kept in the backup" in result.output
    assert "sk-old-live" not in result.output
    assert len(backups(config_path)) == 1


def test_the_clean_command_quiet_says_nothing(config_path: Path) -> None:
    result = noust("config", "clean", "--quiet")

    assert result.exit_code == 0
    assert result.output == ""
    assert "logging" not in config_path.read_text()


def test_the_clean_command_dry_run_says_what_would_go_and_writes_nothing(
    config_path: Path,
) -> None:
    result = noust("config", "clean", "--dry-run")

    assert result.exit_code == 0, result.output
    assert "Would remove" in result.output
    assert config_path.read_text() == OLD_FILE
    assert backups(config_path) == []


def test_the_clean_command_fails_when_it_cannot_edit_the_file(config_path: Path) -> None:
    config_path.write_text("logging: {\n  level: info\n}\n", encoding="utf-8")

    result = noust("config", "clean")

    assert result.exit_code == 1
    assert "by hand" in result.output


def test_the_clean_command_says_when_there_is_nothing_to_do(config_path: Path) -> None:
    config_path.write_text("webserver: nginx\n", encoding="utf-8")

    result = noust("config", "clean")

    assert result.exit_code == 0
    assert "No obsolete settings" in result.output


def test_clean_has_no_json_flag() -> None:
    command = config_cmd.cli.get_command(click.Context(config_cmd.cli), "clean")
    assert command is not None
    assert "--json" not in {opt for param in command.params for opt in param.opts}


# -- noust config set: secrets without argv, lists ----------------------------------


def test_set_reads_a_secret_from_a_pipe_when_asked_to(config_path: Path) -> None:
    result = noust("config", "set", "monitor.smtp.password", "--stdin", input="s3cret-from-stdin\n")

    assert result.exit_code == 0, result.output
    assert "s3cret-from-stdin" not in result.output
    assert yaml.safe_load(config_path.read_text())["monitor"]["smtp"]["password"] == (
        "s3cret-from-stdin"
    )


def test_set_without_a_value_asks_hidden_on_a_terminal(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_cmd, "_at_a_terminal", lambda: True)
    prompts: list[dict[str, Any]] = []

    def fake_prompt(text: str, **kwargs: Any) -> str:
        prompts.append(kwargs)
        return "typed-secret"

    monkeypatch.setattr(config_cmd.click, "prompt", fake_prompt)

    result = noust("config", "set", "notifications.channels.telegram.bot_token")

    assert result.exit_code == 0, result.output
    assert prompts == [{"hide_input": True, "confirmation_prompt": True}]
    assert "typed-secret" not in result.output
    stored = yaml.safe_load(config_path.read_text())
    assert stored["notifications"]["channels"]["telegram"]["bot_token"] == "typed-secret"


def test_set_without_a_value_never_blanks_a_secret_from_an_empty_pipe(config_path: Path) -> None:
    """Without a terminal and without --stdin nothing is read: an empty pipe would blank it."""
    result = noust("config", "set", "monitor.smtp.password", input="")

    assert result.exit_code == 2
    assert yaml.safe_load(config_path.read_text()).get("monitor", {}).get("smtp") == {
        "host": "smtp.example.com"
    }


def test_set_without_a_value_still_refuses_a_key_that_is_not_a_secret(config_path: Path) -> None:
    result = noust("config", "set", "webserver")

    assert result.exit_code == 2
    assert "VALUE" in result.output


def test_email_recipients_typed_as_a_comma_list_store_a_list(config_path: Path) -> None:
    result = noust("config", "set", "monitor.email_recipients", "a@example.com,b@example.com")

    assert result.exit_code == 0, result.output
    stored = yaml.safe_load(config_path.read_text())
    assert stored["monitor"]["email_recipients"] == ["a@example.com", "b@example.com"]


# -- web.allowed_hosts ---------------------------------------------------------------


def test_allowed_hosts_defaults_to_allowing_any_host() -> None:
    assert DEFAULT_CONFIG["web"]["allowed_hosts"] == []


def test_allowed_hosts_are_normalised(config_path: Path) -> None:
    config = Config()

    config.set("web.allowed_hosts", ["Console.Example.COM.", "*.example.org", "10.0.0.5", ""])

    assert config.get("web.allowed_hosts") == ["console.example.com", "*.example.org", "10.0.0.5"]


def test_allowed_hosts_accepts_the_comma_separated_form_of_the_command_line(
    config_path: Path,
) -> None:
    result = noust("config", "set", "web.allowed_hosts", "a.example.com, b.example.com")

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(config_path.read_text())["web"]["allowed_hosts"] == [
        "a.example.com",
        "b.example.com",
    ]


@pytest.mark.parametrize(
    "entry",
    ["https://console.example.com", "console.example.com:8443", "console/x", "*", "a b", "-x.com"],
)
def test_allowed_hosts_refuses_what_could_never_match(config_path: Path, entry: str) -> None:
    with pytest.raises(ConfigError, match=r"web\.allowed_hosts"):
        Config().set("web.allowed_hosts", [entry])


def test_allowed_hosts_refuses_a_value_that_is_not_a_list(config_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config().set("web.allowed_hosts", {"a": 1})


# -- the reference ------------------------------------------------------------------


def _leaves(tree: dict[str, Any], prefix: str = "") -> Iterator[str]:
    """
    List the dotted paths of a configuration tree's leaves.

    Args:
        tree: The tree.
        prefix: Dotted path of ``tree``.

    Yields:
        Each leaf's dotted path; an empty mapping counts as a leaf.
    """
    for key, value in tree.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            yield from _leaves(value, f"{name}.")
        else:
            yield name


def test_the_reference_documents_every_setting_the_defaults_carry() -> None:
    """A key shown by 'noust config show' must have a meaning written down."""
    text = (REPO / "docs" / "CONFIG.md").read_text(encoding="utf-8")

    missing = [key for key in _leaves(DEFAULT_CONFIG) if f"`{key}`" not in text]

    assert missing == [], f"docs/CONFIG.md does not document: {missing}"


def test_the_reference_documents_the_settings_read_without_a_default() -> None:
    text = (REPO / "docs" / "CONFIG.md").read_text(encoding="utf-8")
    extra = [
        "backup.directory",
        "backup.max_per_app",
        "central.name",
        "monitor.max_observations",
        "monitor.notify",
        "monitor.retention_days",
        "monitor.smtp.timeout",
        "monitor.watch_units",
        "notifications.allow_private_hosts",
    ]

    assert [key for key in extra if f"`{key}`" not in text] == []


def test_the_reference_lists_every_obsolete_setting() -> None:
    text = (REPO / "docs" / "CONFIG.md").read_text(encoding="utf-8")

    assert [key for key in (*OBSOLETE_KEYS, *REMOVED_KEYS) if f"`{key}`" not in text] == []


def test_the_packaged_reference_configuration_names_no_obsolete_setting() -> None:
    packaged = yaml.safe_load((REPO / "obs" / "noust.default.yaml").read_text(encoding="utf-8"))

    assert [item.key for item in find_obsolete(packaged)] == []


@pytest.mark.parametrize("script", ["obs/debian.postinst", "rpm/noust.spec"])
def test_the_package_upgrade_cleans_the_configuration_after_upgrading_it(script: str) -> None:
    text = (REPO / script).read_text(encoding="utf-8")

    assert "config upgrade" in text
    assert "config clean" in text
    assert text.index("config upgrade") < text.index("config clean")
