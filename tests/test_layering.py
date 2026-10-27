# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The shape of the package: which layer imports which, what imports what in a circle, and how big
a module may be.

``tests/test_architecture.py`` guards what code is allowed to do; this guards where it may live.
The three rules have the same cause: nothing fixed them, so each one drifted a little with every
lane. ``core`` imports the managers that sit on top of it (29 places), the layers that serve the
console and the command line are imported from below (14 more), three groups of command modules
import each other and only work because of the order they are loaded in, and twenty modules are
past three thousand lines.

Each rule carries the list of places that still break it, as ``test_architecture.py`` does: the
test fails when a new one appears and when a listed one is fixed and not taken off, so the lists
can only get shorter. The import graph is read from the syntax tree
(``tests/import_graph_support.py``); a name used only for annotations (``if TYPE_CHECKING``) is
not an import at run time and does not count.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import noust
from tests import import_graph_support as graph
from tests.ratchet_support import check_ratchet

ROOT = Path(noust.__file__).resolve().parent
WHERE = "tests/test_layering.py"

#: Imports that run: when the module loads and when a function is called. Annotations do not.
RUNTIME = frozenset({graph.MODULE, graph.LAZY})

#: The base of the package may know nothing of what is built on it.
BASE_LAYERS = frozenset({"core", "validators"})
ABOVE_THE_BASE = frozenset(
    {
        "managers",
        "deployers",
        "web",
        "cli",
        "monitor",
        "fleet",
        "central",
        "terminal",
        "integrations",
    }
)

#: What does the work (deploy, manage, watch, ship) serves the console and the command line, and
#: is not served by them.
WORKING_LAYERS = frozenset({"managers", "deployers", "fleet", "monitor", "pushdeploy"})
SURFACES = frozenset({"web", "cli"})

# -- Where the rules are still broken -------------------------------------------------------------
#
# Each entry is "importer -> imported". All of them but one are imports inside a function, which is
# how a layer reaches for what it should have been given (a cycle avoided by hand). Taking one off
# means moving what is imported down to where both can reach it, or passing it in.

#: ``core`` and ``validators`` importing what is above them.
BASE_IMPORTS_UPWARD = {
    # Mail goes out through the monitor's email notifier, and federation reads the console's
    # permissions.
    "noust.core.accounts.federation -> noust.web.permissions.access",
    "noust.core.accounts.mail -> noust.monitor.email_notifier",
    "noust.core.notifier -> noust.monitor.email_notifier",
    # Application state and the helpers around it name the deployers' modules.
    "noust.core.app_compose_store -> noust.deployers.docker_compose",
    "noust.core.app_state -> noust.deployers.bluegreen",
    "noust.core.app_state -> noust.deployers.compose_reclaim",
    "noust.core.app_state -> noust.deployers.docker_compose",
    "noust.core.app_state -> noust.deployers.helpers.php_fpm",
    "noust.core.app_state -> noust.deployers.php_fpm",
    "noust.core.dependencies -> noust.deployers.helpers.php_fpm",
    "noust.core.redact -> noust.deployers.helpers.app_env",
    "noust.core.app_health_store -> noust.terminal.targets",
    # The only one that runs when the module loads and not inside a function.
    "noust.core.deploy_notifications -> noust.deployers.deploy_events",
    # Retention sweeps what the layers above keep.
    "noust.core.audit.retention -> noust.managers.backup_manager",
    "noust.core.audit.retention -> noust.monitor.observation_store",
    "noust.core.audit.retention -> noust.terminal.recording",
    "noust.core.audit.retention -> noust.terminal.settings",
    "noust.core.config -> noust.integrations.forges.hosts",
    "noust.core.config -> noust.managers.status_page",
    "noust.core.utils -> noust.managers.server.host",
    # The ENS profile's checks look at the running server and reach into every layer.
    "noust.core.ens.checks -> noust.central",
    "noust.core.ens.checks -> noust.central.setup",
    "noust.core.ens.checks -> noust.cli.commands.web",
    "noust.core.ens.checks -> noust.cli.web_state",
    "noust.core.ens.checks -> noust.deployers.helpers.sandbox",
    "noust.core.ens.checks -> noust.fleet.policy",
    "noust.core.ens.checks -> noust.managers.backup_manager",
    "noust.core.ens.checks -> noust.managers.server.security_checks",
    "noust.core.ens.checks -> noust.managers.service_manager",
}

#: The layers that do the work importing the console's or the command line's.
WORKING_LAYERS_IMPORT_A_SURFACE = {
    # The console's process is defined in a command module, and its sign-in classes and jobs
    # in ``web``.
    "noust.deployers.app_clone -> noust.web.jobs",
    "noust.deployers.helpers.env_manager -> noust.cli.prompts",
    "noust.fleet.placements -> noust.web.fleet_jobs",
    "noust.fleet.policy -> noust.web.auth",
    "noust.fleet.policy -> noust.web.permissions",
    "noust.fleet.policy -> noust.web.permissions.roles",
    "noust.managers.console_exposure -> noust.cli.commands.web",
    "noust.managers.console_exposure -> noust.web.auth",
    "noust.managers.name_migration -> noust.web.auth",
    "noust.managers.overview -> noust.web.machine",
    "noust.managers.previews -> noust.web.jobs",
    "noust.monitor.watch_tokens -> noust.cli.web_state",
    "noust.pushdeploy.repos -> noust.cli.commands.web",
}

#: The console importing the command line.
WEB_IMPORTS_THE_CLI = {
    "noust.web.api.ens -> noust.cli.commands.web",
}

#: Modules that import each other when they are loaded, whatever the order. Empty: a command that
#: attaches itself to a group imports the module that defines the group (a leaf of its own), and
#: nothing imports its siblings at the end of a file.
MODULES_IN_A_CYCLE: set[str] = set()

#: A module may not be longer than this. A new one past it is split before it is written; the
#: ones below were already past it, and may only get shorter.
MAX_MODULE_LINES = 3000

#: Modules past the limit, with the lines they have. The figure is lowered when the module shrinks
#: and the module leaves when it is under the limit.
OVERSIZE_MODULES = {
    "noust.cli.commands.db": 3237,
    "noust.cli.commands.web": 3481,
    "noust.core.config": 3745,
    "noust.core.runner": 4426,
    "noust.core.store": 5821,
    "noust.deployers.base": 5032,
    "noust.deployers.docker_compose": 4349,
    "noust.deployers.lifecycle": 4908,
    "noust.managers.backup_manager": 5249,
    "noust.managers.database.base": 3386,
    "noust.managers.database.postgres": 3296,
    "noust.managers.database.service": 3859,
    "noust.managers.database.settings": 3394,
    "noust.managers.previews": 3282,
    "noust.managers.service_manager": 3425,
    "noust.managers.source_manager": 4332,
    "noust.managers.webserver": 3710,
    "noust.web.api.apps": 3543,
    "noust.web.auth": 6229,
    "noust.web.jobs": 3308,
}


def upward_imports(
    edges: list[graph.Edge],
    modules: dict[str, Path],
    sources: frozenset[str],
    targets: frozenset[str],
) -> set[str]:
    """
    Find the imports that go from one set of layers to another.

    Args:
        edges: The package's import statements.
        modules: Module name to file, to know each one's layer.
        sources: Layers that must not import.
        targets: Layers that must not be imported.

    Returns:
        ``"importer -> imported"`` for each pair of modules, whatever the number of statements.
    """
    return {
        f"{edge.source} -> {edge.target}"
        for edge in edges
        if edge.kind in RUNTIME
        and graph.layer(edge.source, modules) in sources
        and graph.layer(edge.target, modules) in targets
    }


def modules_in_a_cycle(edges: list[graph.Edge]) -> set[str]:
    """
    Find the modules that import each other when they are loaded.

    Args:
        edges: The package's import statements.

    Returns:
        Every module of a group that loads itself, for every group.
    """
    return {
        module
        for group in graph.strongly_connected(edges, frozenset({graph.MODULE}))
        for module in group
    }


def oversize_modules(modules: dict[str, Path], limit: int) -> dict[str, int]:
    """
    Count the lines of the modules past a limit.

    Args:
        modules: Module name to file.
        limit: The longest a module may be.

    Returns:
        Module name to line count, for those over the limit.
    """
    counts = {name: path.read_bytes().count(b"\n") for name, path in modules.items()}
    return {name: lines for name, lines in counts.items() if lines > limit}


@pytest.fixture(scope="module")
def modules() -> dict[str, Path]:
    return graph.package_modules(ROOT)


@pytest.fixture(scope="module")
def edges() -> list[graph.Edge]:
    return graph.read_edges(ROOT)


class TestDirectionOfLayers:
    """What is built on a layer imports it; the layer does not import what is built on it."""

    def test_the_base_imports_nothing_above_it(self, edges, modules):
        found = upward_imports(edges, modules, BASE_LAYERS, ABOVE_THE_BASE)

        check_ratchet(found, BASE_IMPORTS_UPWARD, "core and validators import nothing above", WHERE)

    def test_the_layers_that_work_import_neither_the_console_nor_the_command_line(
        self, edges, modules
    ):
        found = upward_imports(edges, modules, WORKING_LAYERS, SURFACES)

        check_ratchet(
            found,
            WORKING_LAYERS_IMPORT_A_SURFACE,
            "managers, deployers, fleet, monitor and pushdeploy import neither web nor cli",
            WHERE,
        )

    def test_the_console_does_not_import_the_command_line(self, edges, modules):
        found = upward_imports(edges, modules, frozenset({"web"}), frozenset({"cli"}))

        check_ratchet(found, WEB_IMPORTS_THE_CLI, "web does not import cli", WHERE)


class TestNothingLoadsInACircle:
    """A cycle that works because of the order the modules load in breaks when the order changes."""

    def test_no_new_group_of_modules_imports_itself(self, edges):
        check_ratchet(
            modules_in_a_cycle(edges),
            MODULES_IN_A_CYCLE,
            "modules do not import each other when they load",
            WHERE,
        )


class TestNoModuleGrowsWithoutEnd:
    """The modules that are past the limit are where the lanes collide; they do not get longer."""

    def test_no_module_passes_the_limit_and_the_ones_that_did_do_not_grow(self, modules):
        found = oversize_modules(modules, MAX_MODULE_LINES)

        new = {name for name in found if name not in OVERSIZE_MODULES}
        assert not new, (
            f"New modules past {MAX_MODULE_LINES} lines:\n"
            + "\n".join(f"  {name}: {found[name]}" for name in sorted(new))
            + "\n\nSplit them. This list is not where they go."
        )
        grown = {
            name: (OVERSIZE_MODULES[name], lines)
            for name, lines in found.items()
            if lines > OVERSIZE_MODULES[name]
        }
        assert not grown, (
            "These modules grew past what was recorded:\n"
            + "\n".join(f"  {name}: {old} -> {now}" for name, (old, now) in sorted(grown.items()))
            + "\n\nPut the new code in a module of its own beside them."
        )

    def test_the_list_keeps_shrinking(self, modules):
        found = oversize_modules(modules, MAX_MODULE_LINES)

        gone = sorted(name for name in OVERSIZE_MODULES if name not in found)
        assert not gone, (
            f"These modules are within {MAX_MODULE_LINES} lines now or no longer exist:\n"
            + "\n".join(f"  {name}" for name in gone)
            + f"\n\nTake them off OVERSIZE_MODULES in {WHERE}."
        )
        shorter = {
            name: (OVERSIZE_MODULES[name], lines)
            for name, lines in found.items()
            if lines < OVERSIZE_MODULES[name]
        }
        assert not shorter, (
            "These modules got shorter: lower their figure so they cannot grow back:\n"
            + "\n".join(f'  "{name}": {now},' for name, (_, now) in sorted(shorter.items()))
        )


# -- The reading of the graph, on a package built for the purpose ---------------------------------


def _package(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "pkg"
    for relative, text in {"__init__.py": "", **files}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class TestReadingTheGraph:
    """The guards above are only as good as this reading, so it is held to what Python does."""

    def test_a_relative_import_is_resolved_from_the_package_of_the_module(self, tmp_path):
        root = _package(
            tmp_path,
            {
                "core/__init__.py": "",
                "core/store.py": "from . import paths\nfrom .paths import X\nfrom .. import top\n",
                "core/paths.py": "X = 1\n",
                "top.py": "",
            },
        )

        found = {(e.source, e.target) for e in graph.read_edges(root)}

        assert found == {
            ("pkg.core.store", "pkg.core.paths"),
            ("pkg.core.store", "pkg.top"),
        }

    def test_a_name_inside_a_module_is_an_edge_to_the_module(self, tmp_path):
        root = _package(
            tmp_path,
            {
                "a.py": "from pkg.b import thing\nimport pkg.b.c\n",
                "b/__init__.py": "",
                "b/c.py": "",
            },
        )

        found = {(e.source, e.target) for e in graph.read_edges(root)}

        assert found == {("pkg.a", "pkg.b"), ("pkg.a", "pkg.b.c")}

    def test_where_the_statement_sits_decides_when_it_runs(self, tmp_path):
        root = _package(
            tmp_path,
            {
                "a.py": (
                    "from typing import TYPE_CHECKING\n"
                    "import pkg.at_load\n"
                    "class K:\n"
                    "    import pkg.in_a_class\n"
                    "def f():\n"
                    "    import pkg.in_a_function\n"
                    "g = lambda: __import__('x')\n"
                    "if TYPE_CHECKING:\n"
                    "    import pkg.for_annotations\n"
                    "else:\n"
                    "    import pkg.when_not_checking\n"
                    "try:\n"
                    "    import pkg.optional\n"
                    "except ImportError:\n"
                    "    pass\n"
                ),
                "at_load.py": "",
                "in_a_class.py": "",
                "in_a_function.py": "",
                "for_annotations.py": "",
                "when_not_checking.py": "",
                "optional.py": "",
            },
        )

        kinds = {e.target: e.kind for e in graph.read_edges(root)}

        assert kinds == {
            "pkg.at_load": graph.MODULE,
            "pkg.in_a_class": graph.MODULE,
            "pkg.in_a_function": graph.LAZY,
            "pkg.for_annotations": graph.TYPING,
            "pkg.when_not_checking": graph.MODULE,
            "pkg.optional": graph.MODULE,
        }

    def test_what_is_not_in_the_package_is_not_an_edge(self, tmp_path):
        root = _package(
            tmp_path, {"a.py": "import os\nfrom pathlib import Path\nimport packaging.version\n"}
        )

        assert graph.read_edges(root) == []

    def test_a_cycle_of_three_is_found_and_a_lazy_import_does_not_make_one(self, tmp_path):
        root = _package(
            tmp_path,
            {
                "a.py": "import pkg.b\n",
                "b.py": "import pkg.c\n",
                "c.py": "import pkg.a\n",
                "x.py": "import pkg.y\n",
                "y.py": "def f():\n    import pkg.x\n",
            },
        )
        found = graph.read_edges(root)

        assert graph.strongly_connected(found, frozenset({graph.MODULE})) == [
            ["pkg.a", "pkg.b", "pkg.c"]
        ]
        assert len(graph.strongly_connected(found, RUNTIME)) == 2

    def test_a_layer_is_the_first_package_below_the_root(self, tmp_path):
        root = _package(tmp_path, {"core/__init__.py": "", "core/a.py": "", "main.py": ""})
        modules = graph.package_modules(root)

        assert graph.layer("pkg.core.a", modules) == "core"
        assert graph.layer("pkg.core", modules) == "core"
        assert graph.layer("pkg.main", modules) == ""
        assert graph.layer("pkg", modules) == ""

    def test_the_rules_see_an_upward_import_and_not_a_downward_one(self, tmp_path):
        root = _package(
            tmp_path,
            {
                "core/__init__.py": "",
                "core/a.py": "def f():\n    from pkg.managers import m\n",
                "managers/__init__.py": "",
                "managers/m.py": "from pkg.core import a\n",
            },
        )
        modules = graph.package_modules(root)
        found = graph.read_edges(root)

        assert upward_imports(found, modules, BASE_LAYERS, ABOVE_THE_BASE) == {
            "pkg.core.a -> pkg.managers.m"
        }
        assert upward_imports(found, modules, frozenset({"managers"}), BASE_LAYERS) == {
            "pkg.managers.m -> pkg.core.a"
        }

    def test_a_module_past_the_limit_is_counted_by_its_line_breaks(self, tmp_path):
        root = _package(tmp_path, {"long.py": "x = 1\n" * 12, "short.py": "x = 1\n" * 3})
        modules = graph.package_modules(root)

        assert oversize_modules(modules, 10) == {"pkg.long": 12}
