# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What CodeQL is told about Noust is true of Noust (item 87).

``.github/codeql/extensions/noust-python`` says that a call to a function of ``validators/`` (and a
few others) is where a value stops being the request's: CodeQL stops following it there. That is
a statement about the code, and a statement nobody checks is how a sanitizer that does not
sanitize ends up hiding a real alert. So each row of the model must name a function that exists
and that refuses a hostile name, and a row without such a proof here fails.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.exceptions import NoustError

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / ".github" / "codeql" / "extensions" / "noust-python"

HOSTILE = "../../etc/passwd\n"

#: For each function the model trusts, a call with hostile input that it must refuse.
PROOFS: dict[str, Callable[[Any], Any]] = {
    "validators.domain.validate_domain": lambda f: f(HOSTILE),
    "validators.names.resolve_within": lambda f: f(Path("/var/www/apps"), HOSTILE),
    "validators.source.check_upload_id": lambda f: f(HOSTILE),
    "validators.source.upload_tree": lambda f: f(HOSTILE),
    "validators.replica.check_replica_name": lambda f: f(HOSTILE),
    "validators.db_instance.check_instance_name": lambda f: f(HOSTILE),
    "fleet.models.validate_node_name": lambda f: f(HOSTILE),
    "managers.database.imports.resolve_server_path": lambda f: f("../../etc/passwd", restrict=True),
    "managers.database.instance_layout.directory": lambda f: f(HOSTILE),
}

ROW = re.compile(r"^(?:Member\[(\w+)\]\.)+(?:Member\[(\w+)\])\.ReturnValue$")


def rows() -> list[tuple[str, str, str]]:
    """The (type, access path, kind) of every row of the model files."""
    found: list[tuple[str, str, str]] = []
    for file in sorted((PACK / "models").glob("*.yml")):
        document = yaml.safe_load(file.read_text(encoding="utf-8"))
        for extension in document["extensions"]:
            for type_, path, kind in extension["data"]:
                found.append((type_, path, kind))
    return found


def dotted(path: str) -> str:
    """``Member[a].Member[b].ReturnValue`` as ``a.b``."""
    assert path.endswith(".ReturnValue"), path
    return ".".join(re.findall(r"Member\[(\w+)\]", path))


def resolve(name: str) -> Callable[..., Any]:
    """Import the function a row names."""
    module_name, _, function = f"noust.{name}".rpartition(".")
    return getattr(importlib.import_module(module_name), function)


class TestThePack:
    def test_it_is_a_model_pack_for_the_python_queries(self) -> None:
        pack = yaml.safe_load((PACK / "codeql-pack.yml").read_text(encoding="utf-8"))

        assert pack["name"] == "noust/python-models"
        assert pack["library"] is True
        assert pack["extensionTargets"] == {"codeql/python-all": "*"}
        assert pack["dataExtensions"] == ["models/**/*.yml"]
        assert "dependencies" not in pack

    def test_every_row_is_a_barrier_of_a_kind_the_python_queries_read(self) -> None:
        for file in (PACK / "models").glob("*.yml"):
            document = yaml.safe_load(file.read_text(encoding="utf-8"))
            for extension in document["extensions"]:
                assert extension["addsTo"] == {
                    "pack": "codeql/python-all",
                    "extensible": "barrierModel",
                }
        assert {kind for _, _, kind in rows()} <= {"path-injection"}

    def test_the_rows_name_what_the_package_has_and_only_that(self) -> None:
        for type_, _, _ in rows():
            assert type_ == "noust"


class TestEveryRowIsTrue:
    def test_each_function_it_trusts_has_its_proof_here_and_each_proof_a_row(self) -> None:
        named = {dotted(path) for _, path, _ in rows()}

        assert named == set(PROOFS), (
            f"in the model only: {sorted(named - set(PROOFS))}; "
            f"in the proofs only: {sorted(set(PROOFS) - named)}"
        )

    @pytest.mark.parametrize("name", sorted(PROOFS))
    def test_the_function_exists_and_refuses_a_hostile_name(self, name: str) -> None:
        function = resolve(name)

        with pytest.raises((NoustError, ValueError)):
            PROOFS[name](function)
