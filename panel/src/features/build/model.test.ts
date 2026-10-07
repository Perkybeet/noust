import { describe, expect, it } from "vitest";

import {
  allowedKeys,
  appliedUnit,
  buildBody,
  buildProblems,
  changedKeys,
  draftOfBuild,
  hasBuildBlock,
  looksLikeShell,
  pathProblem,
  runtimePresets,
  runtimeProblem,
  setKeys,
  sourceOf,
} from "./model";
import type { AppBuild, BuildDraft } from "./model";

const REPO: AppBuild = {
  domain: "web.example.com",
  source: "repo",
  base_directory: "apps/web",
  install: "pnpm install --frozen-lockfile",
  build: "pnpm --filter web build",
  start: "node dist/server.js --port ${PORT}",
  publish_directory: null,
  runtime: "node@22",
  document: null,
  repository_error: null,
  applied: null,
};

const EMPTY: BuildDraft = { base_directory: "", install: "", build: "", start: "", publish_directory: "", runtime: "" };

describe("the build block as a form", () => {
  it("starts from what applies now, empty for a key nothing sets", () => {
    expect(draftOfBuild(REPO)).toEqual({
      base_directory: "apps/web",
      install: "pnpm install --frozen-lockfile",
      build: "pnpm --filter web build",
      start: "node dist/server.js --port ${PORT}",
      publish_directory: "",
      runtime: "node@22",
    });
    expect(draftOfBuild({ ...REPO, source: "none", base_directory: null, install: null, build: null, start: null, runtime: null })).toEqual(EMPTY);
  });

  it("knows where the commands come from: the operator's own, the repository's, or the type's", () => {
    expect(sourceOf({ source: "operator" })).toBe("operator");
    expect(sourceOf({ source: "repo" })).toBe("repo");
    expect(sourceOf({ source: "none" })).toBe("none");
    expect(sourceOf({ source: "something new" })).toBe("none");
  });

  it("offers each type what it can honour, as the table of the build guide says", () => {
    expect(allowedKeys("nextjs")).not.toContain("publish_directory");
    expect(allowedKeys("nuxt")).toContain("publish_directory");
    expect(allowedKeys("static")).not.toContain("start");
    expect(allowedKeys("static")).toContain("publish_directory");
    expect(allowedKeys("php-fpm")).toEqual(["install", "build", "runtime"]);
    expect(allowedKeys("python")).toContain("start");
    // A type the table does not know is offered everything: the server decides.
    expect(allowedKeys("something-new")).toHaveLength(6);
  });

  it("has no block for what is built by its own configuration", () => {
    for (const type of ["monorepo", "docker-compose", "container", "", null, undefined]) expect(hasBuildBlock(type), String(type)).toBe(false);
    for (const type of ["nodejs", "nextjs", "static", "python", "php-fpm", "vite"]) expect(hasBuildBlock(type), type).toBe(true);
  });

  it("counts the keys that differ from what is stored, only among the ones the type honours", () => {
    const stored = draftOfBuild(REPO);
    expect(changedKeys(stored, stored, "nextjs")).toEqual([]);
    expect(changedKeys({ ...stored, start: "node server.js", publish_directory: "dist" }, stored, "nextjs")).toEqual(["start"]);
    expect(changedKeys({ ...stored, install: " pnpm install --frozen-lockfile " }, stored, "nextjs")).toEqual([]);
  });

  it("sends only the keys the draft sets and the type honours, trimmed: a key left out is not set", () => {
    const draft: BuildDraft = { ...EMPTY, base_directory: " apps/web ", build: "npm run build", publish_directory: "dist", runtime: "node@22" };
    expect(buildBody(draft, "nextjs")).toEqual({ base_directory: "apps/web", build: "npm run build", runtime: "node@22" });
    expect(buildBody(draft, "static")).toEqual({ base_directory: "apps/web", build: "npm run build", publish_directory: "dist", runtime: "node@22" });
    expect(setKeys(EMPTY, "nextjs")).toEqual([]);
  });
});

describe("what is wrong with a block before the server is asked", () => {
  it("wants something to be set: an empty block would be refused", () => {
    expect(buildProblems(EMPTY, "nextjs").form).toMatch(/at least one/);
    expect(buildProblems({ ...EMPTY, build: "make" }, "nextjs")).toEqual({});
  });

  it("wants paths inside the repository", () => {
    for (const bad of ["/abs", "~/x", "..", "a/../b", "a\\b", "has space", 'q"uote', "it's"]) {
      expect(pathProblem(bad), bad).not.toBeNull();
    }
    for (const good of ["", "apps/web", "dist", "packages/api-v2"]) expect(pathProblem(good), good).toBeNull();
    const errors = buildProblems({ ...EMPTY, base_directory: "../x", publish_directory: "/y", build: "make" }, "static");
    expect(Object.keys(errors).sort()).toEqual(["base_directory", "publish_directory"]);
  });

  it("wants a runtime of a language the type runs on, with a major, a version, lts or latest", () => {
    for (const good of ["node@22", "node@22.11", "node@22.11.0", "node@lts", "python@3.12", "python@latest", ""]) {
      expect(runtimeProblem(good, "static"), good).toBeNull();
    }
    expect(runtimeProblem("php@8.3", "php-fpm")).toBeNull();
    for (const bad of ["node", "node@", "node@^22", "ruby@3", "Node@22", "node @22", "node@22.x"]) {
      expect(runtimeProblem(bad, "nodejs"), bad).not.toBeNull();
    }
    // A runtime of another language than the type's is refused, naming the ones it takes.
    expect(runtimeProblem("python@3.12", "nextjs")).toMatch(/node/);
    expect(runtimeProblem("php@8.3", "python")).toMatch(/python/);
    expect(runtimeProblem("node@22", "php-fpm")).toMatch(/php/);
  });

  it("does not take a start command and a publish directory together", () => {
    const errors = buildProblems({ ...EMPTY, start: "node server.js", publish_directory: "dist" }, "vite");
    expect(errors.publish_directory).toMatch(/not both/);
    // A type with no publish directory has nothing to say about it.
    expect(buildProblems({ ...EMPTY, start: "node server.js", publish_directory: "dist" }, "nodejs")).toEqual({});
  });

  it("offers runtimes for what the type runs on, never a list of what is installed", () => {
    expect(runtimePresets("nextjs")).toEqual(["node@22", "node@20", "node@lts"]);
    expect(runtimePresets("python")).toEqual(["python@3.13", "python@3.12"]);
    expect(runtimePresets("php-fpm")).toEqual(["php@8.4", "php@8.3"]);
    expect(runtimePresets("static")).toContain("python@3.12");
  });

  it("notices what only a shell would understand, which Noust never gives a command to", () => {
    for (const shell of ["npm ci && npm run build", "make | tee log", "echo hi > out", "echo $(date)", "a; b", "echo `x`"]) {
      expect(looksLikeShell(shell), shell).toBe(true);
    }
    for (const plain of ["pnpm --filter web build", "node dist/server.js --port ${PORT}", "make 'all targets'", "./bin/server --listen 127.0.0.1:${PORT}"]) {
      expect(looksLikeShell(plain), plain).toBe(false);
    }
  });
});

describe("what the unit was last written from", () => {
  it("says the command, the directory and the runtime, and nothing for a unit no block shaped", () => {
    expect(appliedUnit(null)).toBeNull();
    expect(appliedUnit(undefined)).toBeNull();
    expect(
      appliedUnit({ start: "/var/www/apps/web/current/bin/api", working_directory: "/var/www/apps/web/current/apps/api", path: "/opt/noust/runtimes/node/22/bin", runtime: { language: "node", version: "22.11.0" } }),
    ).toEqual({ start: "/var/www/apps/web/current/bin/api", workingDirectory: "/var/www/apps/web/current/apps/api", runtime: "node 22.11.0" });
    expect(appliedUnit({ start: "", working_directory: 3, runtime: null })).toEqual({ start: null, workingDirectory: null, runtime: null });
  });
});
