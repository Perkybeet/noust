# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Removing settings from ``config.yaml`` as text keeps everything else as it was.

The operator's comments, order and spacing are theirs: a reload-and-dump would
lose all three, which is why the file is edited line by line.
"""

from __future__ import annotations

import pytest
import yaml

from noust.core.config_text import CannotEditText, remove_keys

SAMPLE = """\
# Noust configuration for shop-01
webserver: nginx   # nginx or apache

# The old AI analysis. Nothing reads it any more.
monitor:
  enabled: true      # the monitor is on
  use_ai: true
  openai:
    # the key from 2024
    api_key: sk-live-0123456789abcdef
    model: gpt-4o-mini
  smtp:
    host: smtp.example.com

logging:
  level: debug
  file: /var/log/wasm/wasm.log

databases:
  backup_dir: /srv/old-backups
  credentials:
    mysql:
      user: root
"""


def test_removes_a_leaf_and_keeps_every_other_line() -> None:
    result = remove_keys(SAMPLE, ["monitor.use_ai"])

    assert "use_ai" not in result
    assert result == SAMPLE.replace("  use_ai: true\n", "")


def test_removes_a_section_with_everything_under_it_but_keeps_its_comments() -> None:
    result = remove_keys(SAMPLE, ["monitor.openai"])

    assert "sk-live" not in result
    assert "gpt-4o-mini" not in result
    assert "openai:" not in result
    # The comment written inside the removed section is not ours to delete.
    assert "# the key from 2024" in result
    assert yaml.safe_load(result)["monitor"] == {
        "enabled": True,
        "use_ai": True,
        "smtp": {"host": "smtp.example.com"},
    }


def test_a_parent_left_with_no_settings_goes_too() -> None:
    result = remove_keys(SAMPLE, ["logging.level", "logging.file"])

    assert "logging" not in result
    assert yaml.safe_load(result)["webserver"] == "nginx"


def test_a_parent_that_still_has_a_child_stays() -> None:
    result = remove_keys(SAMPLE, ["databases.backup_dir"])

    assert yaml.safe_load(result)["databases"] == {"credentials": {"mysql": {"user": "root"}}}


def test_standalone_comments_and_the_inline_comment_of_a_kept_line_survive() -> None:
    result = remove_keys(SAMPLE, ["monitor", "logging", "databases"])

    assert result.startswith(
        "# Noust configuration for shop-01\nwebserver: nginx   # nginx or apache"
    )
    assert "# The old AI analysis. Nothing reads it any more." in result


def test_a_list_under_a_removed_key_goes_with_it() -> None:
    text = "nodejs:\n  package_managers:\n    - npm\n    - pnpm\nwebserver: nginx\n"

    assert remove_keys(text, ["nodejs.package_managers"]) == "webserver: nginx\n"


def test_an_indentless_list_belongs_to_its_key() -> None:
    text = "nodejs:\n  package_managers:\n  - npm\n  - pnpm\n  use_nvm: true\n"

    assert remove_keys(text, ["nodejs.package_managers"]) == "nodejs:\n  use_nvm: true\n"


def test_quoted_keys_are_found() -> None:
    text = 'monitor:\n  "use_ai": true\n  enabled: true\n'

    assert remove_keys(text, ["monitor.use_ai"]) == "monitor:\n  enabled: true\n"


def test_a_key_the_file_lacks_changes_nothing() -> None:
    assert remove_keys(SAMPLE, ["monitor.nothing", "nope"]) == SAMPLE


def test_line_endings_are_kept() -> None:
    text = "webserver: nginx\r\nlogging:\r\n  level: info\r\n"

    assert remove_keys(text, ["logging"]) == "webserver: nginx\r\n"


def test_a_setting_written_as_a_multi_line_flow_mapping_is_refused() -> None:
    text = "logging: {\n  level: info,\n  file: /x\n}\nwebserver: nginx\n"

    with pytest.raises(CannotEditText):
        remove_keys(text, ["logging"])


def test_a_flow_mapping_on_one_line_is_removed_whole() -> None:
    text = "logging: {level: info, file: /x}\nwebserver: nginx\n"

    assert remove_keys(text, ["logging"]) == "webserver: nginx\n"


def test_a_key_inside_a_list_item_is_not_mistaken_for_a_setting() -> None:
    text = "audit:\n  syslog:\n    - address: 10.0.0.1\n      transport: udp\nlogging:\n  level: info\n"

    result = remove_keys(text, ["logging", "audit.address"])

    assert "address: 10.0.0.1" in result
    assert "logging" not in result
