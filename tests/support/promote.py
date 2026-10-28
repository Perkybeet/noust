# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_promote.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.support.release_pipeline import TreeRunner


class SearchingRunner(TreeRunner):
    """A runner whose ``grep -rIlZ -F`` really searches the tree it is given."""

    def run(self, argv: Any, **kwargs: Any) -> Any:
        if list(argv[:4]) == ["grep", "-rIlZ", "-F", "--"]:
            needle, base = argv[4], Path(argv[5])
            found = []
            for path in sorted(base.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    data = path.read_bytes()
                    if b"\x00" not in data and needle.encode() in data:
                        found.append(str(path))
            self.calls.append(tuple(argv))
            self.envs.append(kwargs.get("env"))
            from noust.core.runner import CommandResult

            return CommandResult(
                argv=tuple(argv),
                exit_code=0 if found else 1,
                stdout="".join(name + "\0" for name in found),
                stderr="",
            )
        return super().run(argv, **kwargs)
