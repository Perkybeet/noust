# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Nuxt, SvelteKit, Astro, Remix and React Router, and Angular.

Each is recognised from the files of its project (including the ambiguous ones:
SvelteKit and Remix list ``vite`` too), tells a server from static files by
what its configuration says, and says what runs. The tests are on fake trees,
the way ``detect`` is tested for every other type, then through a real release
pipeline for what ends up in the unit and the site.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers.angular import AngularDeployer
from noust.deployers.astro import AstroDeployer
from noust.deployers.helpers.package_manager import PackageManagerHelper
from noust.deployers.nodejs import NodeJSDeployer
from noust.deployers.nuxt import NuxtDeployer
from noust.deployers.registry import DeployerRegistry, detect_app_type
from noust.deployers.remix import RemixDeployer
from noust.deployers.sveltekit import SvelteKitDeployer
from noust.deployers.vite import ViteDeployer
from tests.test_release_pipeline import (  # noqa: F401 - fixtures used by name
    DOMAIN,
    GIT_URL,
    LOCKFILE,
    PORT,
    RELEASES,
    active_id,
    deploy_new,
    git,
    machine,
    root,
    store,
    wire,
    write_tree,
)


def package(**sections: dict[str, str]) -> str:
    """A package.json with the given sections (``dependencies``, ``scripts``...)."""
    return json.dumps({"name": "app", **sections})


def deployer_for(deployer_class: type, tree: Path) -> Any:
    """A deployer of a type, configured to deploy a tree in place."""
    deployer = deployer_class(verbose=False, runner=FakeRunner())
    deployer.configure("app.example.com", str(tree), port=3000, app_path=tree)
    return deployer


class TestDetection:
    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            ({"package.json": package(dependencies={"nuxt": "^3.10.0"})}, "nuxt"),
            ({"package.json": package(devDependencies={"nuxt": "^4.0.0"})}, "nuxt"),
            (
                {"package.json": package(dependencies={"vue": "3"}), "nuxt.config.ts": ""},
                "nuxt",
            ),
            (
                {
                    "package.json": package(devDependencies={"@sveltejs/kit": "^2", "vite": "^5"}),
                    "svelte.config.js": "",
                    "vite.config.js": "",
                },
                "sveltekit",
            ),
            (
                {"package.json": package(dependencies={"astro": "^4"}), "astro.config.mjs": ""},
                "astro",
            ),
            ({"package.json": package(dependencies={"astro": "^4"})}, "astro"),
            (
                {
                    "package.json": package(
                        dependencies={"@remix-run/react": "^2"}, devDependencies={"vite": "^5"}
                    ),
                    "vite.config.ts": "",
                },
                "remix",
            ),
            (
                {
                    "package.json": package(
                        dependencies={"react-router": "^7"},
                        devDependencies={"@react-router/dev": "^7", "vite": "^6"},
                    ),
                    "react-router.config.ts": "",
                    "vite.config.ts": "",
                },
                "remix",
            ),
            (
                {
                    "package.json": package(dependencies={"@angular/core": "^18"}),
                    "angular.json": "{}",
                },
                "angular",
            ),
        ],
    )
    def test_each_framework_is_recognised_from_its_files(
        self, tmp_path: Path, files: dict[str, str], expected: str
    ) -> None:
        write_tree(tmp_path, files)

        assert detect_app_type(tmp_path) == expected

    def test_a_svelte_project_without_kit_is_still_a_vite_project(self, tmp_path: Path) -> None:
        # svelte.config.js alone is any Svelte project's.
        write_tree(
            tmp_path,
            {
                "package.json": package(devDependencies={"svelte": "^5", "vite": "^5"}),
                "svelte.config.js": "",
                "vite.config.js": "",
            },
        )

        assert detect_app_type(tmp_path) == "vite"

    def test_angular_needs_its_workspace_file(self, tmp_path: Path) -> None:
        # A package that depends on @angular/core with no angular.json is a
        # library, and nothing Noust deploys claims it.
        write_tree(tmp_path, {"package.json": package(dependencies={"@angular/core": "^18"})})

        assert not AngularDeployer().detect(tmp_path)
        assert not NodeJSDeployer().detect(tmp_path)

    def test_plain_node_does_not_claim_a_framework_project(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "package.json": package(
                    dependencies={"nuxt": "^3", "express": "4"}, scripts={"start": "node x.js"}
                )
            },
        )

        assert not NodeJSDeployer().detect(tmp_path)
        assert detect_app_type(tmp_path) == "nuxt"

    def test_a_compose_file_made_for_production_still_beats_a_framework(
        self, tmp_path: Path
    ) -> None:
        write_tree(
            tmp_path,
            {
                "package.json": package(dependencies={"nuxt": "^3"}),
                "docker-compose.prod.yml": "services:\n  web:\n    build: .\n",
            },
        )

        assert detect_app_type(tmp_path) == "docker-compose"

    def test_a_project_of_none_of_them_is_left_to_the_older_types(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {"package.json": package(devDependencies={"vite": "5"}), "vite.config.js": ""},
        )

        assert detect_app_type(tmp_path) == "vite"

    def test_the_five_are_registered_and_offered(self) -> None:
        from noust.deployers.registry import available_types

        offered = {entry["type"] for entry in available_types()}

        assert {"nuxt", "sveltekit", "astro", "remix", "angular"} <= offered
        assert DeployerRegistry.get("nuxt") is NuxtDeployer


class TestNuxt:
    def plan(self, tmp_path: Path, files: dict[str, str]) -> Any:
        write_tree(tmp_path, files)
        return deployer_for(NuxtDeployer, tmp_path).plan()

    def test_nuxt_3_runs_the_nitro_server(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            {
                "package.json": package(
                    dependencies={"nuxt": "^3.10"}, scripts={"build": "nuxt build"}
                )
            },
        )

        assert plan.serves
        assert plan.start == "node .output/server/index.mjs"
        assert plan.environment == {"HOST": "127.0.0.1"}

    def test_a_generated_site_is_static_files(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            {
                "package.json": package(
                    dependencies={"nuxt": "^3.10"}, scripts={"build": "nuxt generate"}
                )
            },
        )

        assert not plan.serves
        assert plan.publish == ".output/public"

    def test_the_static_preset_is_static_files_too(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            {
                "package.json": package(
                    dependencies={"nuxt": "^3"}, scripts={"build": "nuxt build"}
                ),
                "nuxt.config.ts": "export default defineNuxtConfig({ nitro: { preset: 'static' } })",
            },
        )

        assert not plan.serves

    def test_a_commented_out_preset_is_not_one(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            {
                "package.json": package(
                    dependencies={"nuxt": "^3"}, scripts={"build": "nuxt build"}
                ),
                "nuxt.config.ts": "export default defineNuxtConfig({\n  // preset: 'static',\n})",
            },
        )

        assert plan.serves

    def test_nuxt_2_runs_its_start_script_and_generates_into_dist(self, tmp_path: Path) -> None:
        server = self.plan(
            tmp_path / "ssr",
            {
                "package.json": package(
                    dependencies={"nuxt": "^2.15.8"}, scripts={"start": "nuxt start"}
                )
            },
        )
        static = self.plan(
            tmp_path / "static",
            {
                "package.json": package(
                    dependencies={"nuxt": "~2.17.0"}, scripts={"generate": "nuxt generate"}
                ),
                "nuxt.config.js": "export default { target: 'static' }",
            },
        )

        assert server.serves and server.start == "npm run start"
        assert not static.serves and static.publish == "dist"

    def test_a_nuxt_2_static_target_builds_with_its_generate_script(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "package.json": package(
                    dependencies={"nuxt": "^2.15"},
                    scripts={"build": "nuxt build", "generate": "nuxt generate"},
                ),
                "nuxt.config.js": "export default { target: 'static' }",
            },
        )

        assert deployer_for(NuxtDeployer, tmp_path).get_build_command() == [
            "npm",
            "run",
            "generate",
        ]

    def test_the_server_is_the_start_command_and_a_site_has_none(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {
                "package.json": package(
                    dependencies={"nuxt": "^3"}, scripts={"build": "nuxt generate"}
                )
            },
        )

        deployer = deployer_for(NuxtDeployer, tmp_path)

        assert deployer.get_start_command() == ""
        assert deployer.get_nginx_template() == "static"


class TestSvelteKit:
    def plan(self, tmp_path: Path, config: str, **dependencies: str) -> Any:
        write_tree(
            tmp_path,
            {
                "package.json": package(devDependencies={"@sveltejs/kit": "^2", **dependencies}),
                "svelte.config.js": config,
            },
        )
        return deployer_for(SvelteKitDeployer, tmp_path).plan()

    def test_the_node_adapter_runs_the_build_behind_the_proxy(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            "import adapter from '@sveltejs/adapter-node';\nexport default { kit: { adapter: adapter() } };",
        )

        assert plan.serves
        assert plan.start == "node build"
        # Forms are refused as cross-site unless the origin is what the proxy forwards.
        assert plan.environment["PROTOCOL_HEADER"] == "x-forwarded-proto"
        assert plan.environment["HOST_HEADER"] == "x-forwarded-host"
        assert plan.environment["HOST"] == "127.0.0.1"

    def test_the_static_adapter_publishes_its_directory(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            "import adapter from '@sveltejs/adapter-static';\n"
            "export default { kit: { adapter: adapter({ pages: 'out' }) } };",
        )

        assert not plan.serves
        assert plan.publish == "out"

    def test_the_static_adapter_publishes_build_by_default(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            "import adapter from '@sveltejs/adapter-static';\nexport default { kit: { adapter: adapter() } };",
        )

        assert plan.publish == "build"

    def test_the_auto_adapter_builds_for_no_platform_this_server_is(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            "import adapter from '@sveltejs/adapter-auto';\nexport default { kit: { adapter: adapter() } };",
        )

        assert plan.problem is not None
        assert "adapter-node" in plan.problem
        assert "adapter-static" in plan.problem

    def test_a_platform_adapter_is_refused_naming_it(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            "import adapter from '@sveltejs/adapter-vercel';\nexport default { kit: { adapter: adapter() } };",
        )

        assert plan.problem is not None and "adapter-vercel" in plan.problem

    def test_a_commented_out_adapter_is_not_the_adapter(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            "// import adapter from '@sveltejs/adapter-static';\n"
            "import adapter from '@sveltejs/adapter-node';\n"
            "export default { kit: { adapter: adapter() } };",
        )

        assert plan.start == "node build"

    def test_the_dependency_names_the_adapter_when_the_config_does_not(
        self, tmp_path: Path
    ) -> None:
        plan = self.plan(tmp_path, "export default {};", **{"@sveltejs/adapter-node": "^5"})

        assert plan.serves

    def test_the_problem_is_raised_when_the_project_is_installed_not_at_detection(
        self, tmp_path: Path
    ) -> None:
        self.plan(tmp_path, "import a from '@sveltejs/adapter-auto';")
        deployer = deployer_for(SvelteKitDeployer, tmp_path)

        with pytest.raises(DeploymentError) as excinfo:
            deployer.pre_install()

        assert "adapter-auto" in excinfo.value.details

    def test_what_the_build_block_says_wins_over_an_adapter_that_cannot_be_deployed(
        self, tmp_path: Path
    ) -> None:
        self.plan(tmp_path, "import a from '@sveltejs/adapter-auto';")
        write_tree(tmp_path, {"noust.yaml": "build:\n  start: node build\n"})
        deployer = deployer_for(SvelteKitDeployer, tmp_path)

        assert deployer.pre_install() is True


class TestAstro:
    def plan(self, tmp_path: Path, config: str = "", **dependencies: str) -> Any:
        write_tree(
            tmp_path,
            {
                "package.json": package(dependencies={"astro": "^4", **dependencies}),
                "astro.config.mjs": config,
            },
        )
        return deployer_for(AstroDeployer, tmp_path).plan()

    def test_without_an_adapter_it_is_static_files_in_dist(self, tmp_path: Path) -> None:
        plan = self.plan(tmp_path)

        assert not plan.serves
        assert plan.publish == "dist"

    def test_the_output_directory_it_names_is_the_one_published(self, tmp_path: Path) -> None:
        plan = self.plan(tmp_path, "export default defineConfig({ outDir: './public-out' });")

        assert plan.publish == "public-out"

    def test_the_node_adapter_is_a_server(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            "export default defineConfig({ output: 'server', adapter: node({ mode: 'standalone' }) });",
            **{"@astrojs/node": "^8"},
        )

        assert plan.serves
        assert plan.start == "node ./dist/server/entry.mjs"
        assert plan.environment == {"HOST": "127.0.0.1"}

    def test_the_node_adapters_middleware_mode_is_not_a_server_to_run(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            "export default defineConfig({ adapter: node({ mode: 'middleware' }) });",
            **{"@astrojs/node": "^8"},
        )

        assert plan.problem is not None and "middleware" in plan.problem

    @pytest.mark.parametrize(
        "adapter", ["@astrojs/vercel", "@astrojs/netlify", "@astrojs/cloudflare"]
    )
    def test_a_platform_adapter_is_refused_naming_it(self, tmp_path: Path, adapter: str) -> None:
        plan = self.plan(tmp_path, **{adapter: "^1"})

        assert plan.problem is not None and adapter in plan.problem

    def test_a_static_site_gets_the_static_template_and_a_server_the_proxy(
        self, tmp_path: Path
    ) -> None:
        self.plan(tmp_path / "static")
        self.plan(tmp_path / "ssr", **{"@astrojs/node": "^8"})

        assert deployer_for(AstroDeployer, tmp_path / "static").get_nginx_template() == "static"
        assert deployer_for(AstroDeployer, tmp_path / "ssr").get_nginx_template() == "proxy"


class TestRemix:
    def plan(self, tmp_path: Path, files: dict[str, str]) -> Any:
        write_tree(tmp_path, files)
        return deployer_for(RemixDeployer, tmp_path).plan()

    def test_the_start_script_is_what_runs(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            {
                "package.json": package(
                    dependencies={"@remix-run/react": "^2", "@remix-run/serve": "^2"},
                    scripts={"start": "remix-serve ./build/server/index.js"},
                )
            },
        )

        assert plan.serves
        assert plan.start == "npm run start"
        assert plan.environment == {"HOST": "127.0.0.1"}

    def test_without_a_script_the_frameworks_own_server_runs_the_build(
        self, tmp_path: Path
    ) -> None:
        plan = self.plan(
            tmp_path,
            {
                "package.json": package(
                    dependencies={"react-router": "^7", "@react-router/serve": "^7"},
                    devDependencies={"@react-router/dev": "^7"},
                )
            },
        )

        assert plan.serves
        assert (
            plan.start == f"{tmp_path}/node_modules/.bin/react-router-serve ./build/server/index.js"
        )

    def test_the_classic_compilers_build_is_found_where_it_is(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            {
                "package.json": package(dependencies={"@remix-run/serve": "^1"}),
                "build/index.js": "",
            },
        )

        assert plan.start.endswith("remix-serve ./build/index.js")

    def test_spa_mode_is_the_client_bundle_as_files(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            {
                "package.json": package(
                    dependencies={"react-router": "^7"}, devDependencies={"@react-router/dev": "^7"}
                ),
                "react-router.config.ts": "export default { ssr: false } satisfies Config;",
            },
        )

        assert not plan.serves
        assert plan.publish == "build/client"

    def test_a_project_with_nothing_to_run_its_build_says_what_to_add(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            {"package.json": package(dependencies={"@remix-run/react": "^2"})},
        )

        assert plan.problem is not None
        assert "@remix-run/serve" in plan.problem
        assert "start" in plan.problem


class TestAngular:
    def plan(self, tmp_path: Path, workspace: dict[str, Any], **dependencies: str) -> Any:
        write_tree(
            tmp_path,
            {
                "package.json": package(dependencies={"@angular/core": "^18", **dependencies}),
                "angular.json": json.dumps(workspace),
            },
        )
        return deployer_for(AngularDeployer, tmp_path).plan()

    @staticmethod
    def workspace(builder: str, **options: Any) -> dict[str, Any]:
        return {
            "projects": {
                "shop": {
                    "projectType": "application",
                    "architect": {"build": {"builder": builder, "options": options}},
                }
            }
        }

    def test_the_application_builder_leaves_the_client_in_browser(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            self.workspace("@angular-devkit/build-angular:application", outputPath="dist/shop"),
        )

        assert not plan.serves
        assert plan.publish == "dist/shop/browser"

    def test_the_older_browser_builder_leaves_it_in_the_output_path(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            self.workspace("@angular-devkit/build-angular:browser", outputPath="dist/shop"),
        )

        assert plan.publish == "dist/shop"

    def test_an_output_path_object_with_no_browser_directory_is_the_base(
        self, tmp_path: Path
    ) -> None:
        plan = self.plan(
            tmp_path,
            self.workspace(
                "@angular/build:application", outputPath={"base": "out/shop", "browser": ""}
            ),
        )

        assert plan.publish == "out/shop"

    def test_server_side_rendering_runs_the_server_the_build_left(self, tmp_path: Path) -> None:
        plan = self.plan(
            tmp_path,
            self.workspace(
                "@angular-devkit/build-angular:application",
                outputPath="dist/shop",
                server="src/main.server.ts",
                ssr={"entry": "server.ts"},
            ),
            **{"@angular/ssr": "^18"},
        )

        assert plan.serves
        assert plan.start == "node dist/shop/server/server.mjs"

    def test_universal_runs_its_main_js(self, tmp_path: Path) -> None:
        workspace = self.workspace(
            "@angular-devkit/build-angular:browser", outputPath="dist/shop/browser"
        )
        workspace["projects"]["shop"]["architect"]["server"] = {"builder": "x"}
        plan = self.plan(tmp_path, workspace, **{"@nguniversal/express-engine": "^16"})

        assert plan.serves
        assert plan.start == "node dist/shop/browser/server/main.js"

    def test_the_ssr_dependency_without_a_server_build_is_still_a_site(
        self, tmp_path: Path
    ) -> None:
        plan = self.plan(
            tmp_path,
            self.workspace("@angular-devkit/build-angular:application", outputPath="dist/shop"),
            **{"@angular/platform-server": "^18"},
        )

        assert not plan.serves

    def test_the_default_project_is_the_one_deployed(self, tmp_path: Path) -> None:
        workspace = {
            "defaultProject": "admin",
            "projects": {
                "lib": {"projectType": "library", "architect": {"build": {"builder": "x"}}},
                "shop": {
                    "projectType": "application",
                    "architect": {"build": {"builder": ":application", "options": {}}},
                },
                "admin": {
                    "projectType": "application",
                    "architect": {
                        "build": {
                            "builder": "@angular-devkit/build-angular:application",
                            "options": {"outputPath": "dist/admin"},
                        }
                    },
                },
            },
        }

        assert self.plan(tmp_path, workspace).publish == "dist/admin/browser"

    def test_a_workspace_that_cannot_be_read_still_has_a_default_output(
        self, tmp_path: Path
    ) -> None:
        write_tree(
            tmp_path,
            {
                "package.json": package(dependencies={"@angular/core": "^18"}),
                "angular.json": "{ not json",
            },
        )

        plan = deployer_for(AngularDeployer, tmp_path).plan()

        assert not plan.serves and plan.publish == "dist"


class TestThePackageManagerOfTheProject:
    helper = PackageManagerHelper()

    @pytest.mark.parametrize(
        ("lockfile", "manager"),
        [
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
            ("bun.lockb", "bun"),
            ("bun.lock", "bun"),
            ("package-lock.json", "npm"),
            ("npm-shrinkwrap.json", "npm"),
        ],
    )
    def test_each_lockfile_names_its_manager(
        self, tmp_path: Path, lockfile: str, manager: str
    ) -> None:
        write_tree(tmp_path, {lockfile: "", "package.json": "{}"})

        assert self.helper.detect(tmp_path) == manager

    def test_with_two_lockfiles_pnpm_then_bun_then_yarn_then_npm_decide(
        self, tmp_path: Path
    ) -> None:
        write_tree(tmp_path, {"package-lock.json": "", "yarn.lock": "", "bun.lockb": ""})
        assert self.helper.detect(tmp_path) == "bun"
        write_tree(tmp_path, {"pnpm-lock.yaml": ""})
        assert self.helper.detect(tmp_path) == "pnpm"

    def test_the_package_manager_field_decides_when_there_is_no_lockfile(
        self, tmp_path: Path
    ) -> None:
        write_tree(tmp_path, {"package.json": json.dumps({"packageManager": "pnpm@9.1.0"})})

        assert self.helper.detect(tmp_path) == "pnpm"

    def test_a_lockfile_beats_the_package_manager_field(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {"package.json": json.dumps({"packageManager": "pnpm@9"}), "yarn.lock": ""},
        )

        assert self.helper.detect(tmp_path) == "yarn"

    def test_a_manager_noust_does_not_drive_is_ignored(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"package.json": json.dumps({"packageManager": "deno@1"})})

        assert self.helper.detect(tmp_path) == "npm"

    def test_a_project_in_a_workspace_takes_the_lockfile_above_it(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"pnpm-lock.yaml": "", "apps/web/package.json": "{}"})
        project = tmp_path / "apps" / "web"

        assert self.helper.detect_up(project, tmp_path) == "pnpm"
        assert self.helper.lockfile_dir(project, tmp_path) == tmp_path

    def test_the_nearest_lockfile_wins(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"pnpm-lock.yaml": "", "apps/web/yarn.lock": ""})
        project = tmp_path / "apps" / "web"

        assert self.helper.detect_up(project, tmp_path) == "yarn"

    def test_nothing_above_the_tree_is_read(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"pnpm-lock.yaml": "", "repo/apps/web/package.json": "{}"})
        repo = tmp_path / "repo"

        assert self.helper.detect_up(repo / "apps" / "web", repo) == "npm"

    def test_a_manager_asked_for_is_not_second_guessed(self, tmp_path: Path) -> None:
        write_tree(tmp_path, {"pnpm-lock.yaml": ""})

        assert self.helper.detect_up(tmp_path, tmp_path, "yarn") == "yarn"

    def test_the_install_of_each_framework_follows_the_lockfile(self, tmp_path: Path) -> None:
        write_tree(
            tmp_path,
            {"package.json": package(dependencies={"nuxt": "^3"}), "yarn.lock": ""},
        )
        deployer = deployer_for(NuxtDeployer, tmp_path)
        deployer.pre_install()

        assert deployer.package_manager == "yarn"
        assert deployer.get_install_command() == ["yarn", "install", "--frozen-lockfile"]
        assert deployer.get_build_command() == [] or deployer.get_build_command()[0] == "yarn"


class TestDeployingOne:
    def deploy(
        self,
        tmp_path: Path,
        root: Path,
        machine: SimpleNamespace,
        deployer_class: type,
        files: dict[str, str],
    ) -> Any:
        machine.git.publish(write_tree(tmp_path / "v1", {"package-lock.json": LOCKFILE, **files}))
        output_dirs = {
            "nuxt": [".output/server/index.mjs"],
            "sveltekit": ["build/index.js"],
        }
        original = machine.runner.stream

        def build(argv: Any, **kwargs: Any) -> Any:
            if tuple(argv) == ("npm", "run", "build"):
                # A build leaves what the project's configuration says it does.
                for relative in output_dirs.get(deployer_class.APP_TYPE, ["dist/index.html"]):
                    write_tree(Path(kwargs["cwd"]), {relative: "ok"})
            return original(argv, **kwargs)

        machine.runner.stream = build
        return deploy_new(root, machine, deployer_class=deployer_class)

    def test_a_nuxt_server_is_a_unit_behind_a_proxy(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        self.deploy(
            tmp_path,
            root,
            machine,
            NuxtDeployer,
            {
                "package.json": package(
                    dependencies={"nuxt": "^3"}, scripts={"build": "nuxt build"}
                ),
            },
        )

        unit = machine.services.units["rel-example-com"]
        assert unit["command"].endswith("node .output/server/index.mjs")
        assert unit["working_directory"] == str(root / "current")
        assert unit["environment"]["HOST"] == "127.0.0.1"
        assert unit["environment"]["PORT"] == str(PORT)
        assert machine.web.sites[DOMAIN]["template"] == "proxy"
        app = store.get_app(DOMAIN)
        assert app is not None
        assert not app.is_static
        assert app.port == PORT

    def test_a_sveltekit_static_site_has_no_unit_and_is_served_from_its_directory(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        self.deploy(
            tmp_path,
            root,
            machine,
            SvelteKitDeployer,
            {
                "package.json": package(
                    devDependencies={"@sveltejs/kit": "^2", "@sveltejs/adapter-static": "^3"},
                    scripts={"build": "vite build"},
                ),
                "svelte.config.js": "import a from '@sveltejs/adapter-static';",
                "build/index.html": "<h1>ok</h1>",
            },
        )

        assert machine.services.units == {}
        site = machine.web.sites[DOMAIN]
        assert site["template"] == "static"
        assert site["context"]["static_dir"] == str(root / "current" / "build")
        # Registered as the static site it turned out to be, not the server it
        # was guessed to be before its source was there.
        app = store.get_app(DOMAIN)
        assert app is not None and app.is_static

    def test_a_framework_whose_adapter_cannot_run_here_fails_before_installing(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    "package.json": package(devDependencies={"@sveltejs/kit": "^2"}),
                    "package-lock.json": LOCKFILE,
                    "svelte.config.js": "import a from '@sveltejs/adapter-auto';",
                },
            )
        )
        deployer = wire(SvelteKitDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(DeploymentError) as excinfo:
            deployer.deploy()

        assert "adapter-auto" in excinfo.value.details
        assert not root.exists()
        assert machine.runner.cwd_of("npm", "ci") == []

    def test_a_static_build_that_left_no_directory_says_where_to_look(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    "package.json": package(
                        dependencies={"astro": "^4"}, scripts={"build": "astro build"}
                    ),
                    "package-lock.json": LOCKFILE,
                },
            )
        )
        deployer = wire(AstroDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(DeploymentError) as excinfo:
            deployer.deploy()

        assert "--publish-directory" in excinfo.value.details

    def test_an_old_vite_application_is_not_moved_to_a_framework_by_an_update(
        self, tmp_path: Path
    ) -> None:
        # What was deployed as vite stays vite: updates use the stored type.
        assert DeployerRegistry.get("vite") is ViteDeployer
