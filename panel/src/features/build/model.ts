/**
 * An application's build block as a form: the six things an operator may say about how a project
 * is installed, built, started and where it lives (docs/build.md), which of them a type of
 * application can honour, what is wrong with them before the server is asked, and the request
 * saving becomes. Pure, so each rule is tested without drawing a field.
 *
 * The block has two owners and one precedence rule: the operator's own, stored on the server,
 * wins whole over the repository's `noust.yaml`, and over the type's own commands; nothing is
 * ever merged. A form that starts from the repository's values and is saved makes them the
 * operator's, so what it does not carry is what falls back to the type, not to the repository.
 */

import type { BodyOf, ResponseOf } from "../../api/client";
import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";

export type AppBuild = ResponseOf<"/api/apps/{domain}/build", "get">;
export type BuildBody = BodyOf<"/api/apps/{domain}/build", "put">;

/** The keys of a block, in the order the form and the CLI list them. */
export const BUILD_KEYS = ["base_directory", "install", "build", "start", "publish_directory", "runtime"] as const;

export type BuildKey = (typeof BUILD_KEYS)[number];

/** Where the block that applies comes from. */
export type BuildSource = "operator" | "repo" | "none";

/** What the form holds: every key as the text typed, empty meaning "not set". */
export type BuildDraft = Record<BuildKey, string>;

export type BuildErrors = Partial<Record<BuildKey | "form", string>>;

/** The languages a type of application runs on, for the runtimes it may be pinned to. */
type Language = "node" | "python" | "php";

const NODE_KEYS: readonly BuildKey[] = ["base_directory", "install", "build", "start", "runtime"];
const NODE_STATIC_KEYS: readonly BuildKey[] = ["base_directory", "install", "build", "start", "publish_directory", "runtime"];

/**
 * What each type can honour, as docs/build.md's table says it: a key a type cannot honour is
 * refused by the server with what to do instead, and so is not offered. A type this table does
 * not know (a deployer added later) is offered every key, and the server decides.
 */
const KEYS_BY_TYPE: Readonly<Record<string, { keys: readonly BuildKey[]; languages: readonly Language[] }>> = {
  nodejs: { keys: NODE_KEYS, languages: ["node"] },
  nextjs: { keys: NODE_KEYS, languages: ["node"] },
  nuxt: { keys: NODE_STATIC_KEYS, languages: ["node"] },
  sveltekit: { keys: NODE_STATIC_KEYS, languages: ["node"] },
  astro: { keys: NODE_STATIC_KEYS, languages: ["node"] },
  remix: { keys: NODE_STATIC_KEYS, languages: ["node"] },
  angular: { keys: NODE_STATIC_KEYS, languages: ["node"] },
  vite: { keys: NODE_STATIC_KEYS, languages: ["node"] },
  static: { keys: ["base_directory", "install", "build", "publish_directory", "runtime"], languages: ["node", "python"] },
  python: { keys: ["base_directory", "install", "build", "start", "runtime"], languages: ["python"] },
  "php-fpm": { keys: ["install", "build", "runtime"], languages: ["php"] },
  // Built with the server's own toolchain: no runtime to pin, and nothing to publish.
  go: { keys: ["base_directory", "install", "build", "start"], languages: [] },
  rust: { keys: ["base_directory", "install", "build", "start"], languages: [] },
  ruby: { keys: ["base_directory", "install", "build", "start"], languages: [] },
  java: { keys: ["base_directory", "install", "build", "start"], languages: [] },
  dotnet: { keys: ["base_directory", "install", "build", "start"], languages: [] },
  elixir: { keys: ["base_directory", "install", "build", "start"], languages: [] },
  deno: { keys: ["base_directory", "install", "build", "start"], languages: [] },
};

/** Types whose build is not a block's to shape: they are built by their own configuration. */
export const NOT_BUILT_BY_BLOCK: ReadonlySet<string> = new Set(["monorepo", "docker-compose", "container"]);

/** Whether a type of application has a build block at all (a Compose stack or a container has not). */
export function hasBuildBlock(appType: string | null | undefined): boolean {
  return appType !== null && appType !== undefined && appType !== "" && !NOT_BUILT_BY_BLOCK.has(appType);
}

/** The keys a type of application can honour. */
export function allowedKeys(appType: string): readonly BuildKey[] {
  return KEYS_BY_TYPE[appType]?.keys ?? BUILD_KEYS;
}

/** The languages a type may be pinned to (`node@22`), none when the table does not know the type. */
export function runtimeLanguages(appType: string): readonly string[] {
  return KEYS_BY_TYPE[appType]?.languages ?? ["node", "python", "php"];
}

/**
 * Runtimes worth offering as a start, for what a type runs on: the releases in support. They are
 * suggestions typed into the field, not the list of what is installed on the server (no endpoint
 * says that), and any major, minor or full version, `lts` or `latest` is accepted.
 */
export function runtimePresets(appType: string): string[] {
  const presets: Record<string, readonly string[]> = {
    node: ["node@22", "node@20", "node@lts"],
    python: ["python@3.13", "python@3.12"],
    php: ["php@8.4", "php@8.3"],
  };
  return runtimeLanguages(appType).flatMap((language) => presets[language] ?? []);
}

/** A runtime as the server reads it: a language, `@`, and a version or `lts` or `latest`. */
const RUNTIME = /^(node|python|php)@(\d+(?:\.\d+){0,2}|lts|latest)$/;

export function runtimeProblem(value: string, appType: string): string | null {
  const text = value.trim();
  if (text === "") return null;
  const locale = getLocale();
  const match = RUNTIME.exec(text);
  if (match === null) return translate(locale, "appBuild.validation.runtime");
  const language = match[1] ?? "";
  const accepted = runtimeLanguages(appType);
  if (!accepted.includes(language)) return translate(locale, "appBuild.validation.runtimeType", { language, languages: accepted.join(", ") });
  return null;
}

/** A path inside the repository as the server takes it: relative, no `..`, no `~`, no spaces or quotes. */
export function pathProblem(value: string): string | null {
  const text = value.trim();
  if (text === "") return null;
  const locale = getLocale();
  if (text.startsWith("/") || text.startsWith("~") || text.includes("\\") || /[\s"']/.test(text) || text.split("/").includes("..")) {
    return translate(locale, "appBuild.validation.path");
  }
  return null;
}

/**
 * Whether a command has something in it a shell would have taken for an operator: a pipe, `&&`,
 * a redirection, `;` or a substitution. Noust runs a command as an argument vector and never
 * through a shell, so each of these is an ordinary argument; the form says so beside the field
 * rather than letting a person find out from a failed build.
 */
export function looksLikeShell(command: string): boolean {
  return /(\|\||&&|\||;|>|<|`|\$\()/.test(command);
}

/** A draft of an application: what applies now, each key as text. */
export function draftOfBuild(build: AppBuild): BuildDraft {
  return {
    base_directory: build.base_directory ?? "",
    install: build.install ?? "",
    build: build.build ?? "",
    start: build.start ?? "",
    publish_directory: build.publish_directory ?? "",
    runtime: build.runtime ?? "",
  };
}

/** The keys of a draft that differ from what is stored, as the text typed (trimmed). */
export function changedKeys(draft: BuildDraft, stored: BuildDraft, appType: string): BuildKey[] {
  return allowedKeys(appType).filter((key) => draft[key].trim() !== stored[key].trim());
}

/** The keys of a draft that say something: the ones the type can honour and the field holds. */
export function setKeys(draft: BuildDraft, appType: string): BuildKey[] {
  return allowedKeys(appType).filter((key) => draft[key].trim() !== "");
}

/**
 * What is wrong with a draft before the server is asked: what it would refuse for certain. The
 * server's own validation (`build_config`) is the authority and its refusal, naming the field, is
 * shown on the same field; this only saves a round trip for the obvious.
 */
export function buildProblems(draft: BuildDraft, appType: string): BuildErrors {
  const errors: BuildErrors = {};
  const locale = getLocale();
  for (const key of ["base_directory", "publish_directory"] as const) {
    const problem = pathProblem(draft[key]);
    if (problem !== null) errors[key] = problem;
  }
  const runtime = runtimeProblem(draft.runtime, appType);
  if (runtime !== null) errors.runtime = runtime;
  const keys = allowedKeys(appType);
  if (keys.includes("start") && keys.includes("publish_directory") && draft.start.trim() !== "" && draft.publish_directory.trim() !== "") {
    errors.publish_directory = translate(locale, "appBuild.validation.startAndPublish");
  }
  if (setKeys(draft, appType).length === 0) errors.form = translate(locale, "appBuild.validation.nothing");
  return errors;
}

/** `PUT /api/apps/{domain}/build`: only the keys the type honours and the draft sets, trimmed. */
export function buildBody(draft: BuildDraft, appType: string): BuildBody {
  const body: BuildBody = {};
  for (const key of setKeys(draft, appType)) body[key] = draft[key].trim();
  return body;
}

/**
 * Where the commands of the next deployment come from, as the one word the page says it by: the
 * operator's own block (which wins whole), the repository's `noust.yaml`, or neither (the type's
 * own commands).
 */
export function sourceOf(build: Pick<AppBuild, "source">): BuildSource {
  return build.source === "operator" || build.source === "repo" ? build.source : "none";
}

/** What the unit was last written from, as the rows the page shows: nothing for a unit no block shaped. */
export interface AppliedUnit {
  start: string | null;
  workingDirectory: string | null;
  runtime: string | null;
}

export function appliedUnit(applied: AppBuild["applied"]): AppliedUnit | null {
  if (applied === null || applied === undefined) return null;
  const runtime = applied["runtime"];
  const text = (value: unknown): string | null => (typeof value === "string" && value !== "" ? value : null);
  const language = typeof runtime === "object" && runtime !== null ? (runtime as Record<string, unknown>)["language"] : null;
  const version = typeof runtime === "object" && runtime !== null ? (runtime as Record<string, unknown>)["version"] : null;
  return {
    start: text(applied["start"]),
    workingDirectory: text(applied["working_directory"]),
    runtime: typeof language === "string" && typeof version === "string" ? `${language} ${version}` : null,
  };
}
