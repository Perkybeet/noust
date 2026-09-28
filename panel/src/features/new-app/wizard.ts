/**
 * The new-app wizard's logic, apart from any page: what the operator pointed at, the form the
 * inspection proposes, what is wrong with it, and the request it becomes. Pure, so every rule
 * is tested without rendering a step.
 */

import { isApiError } from "../../api/client";
import type { BodyOf, ResponseOf } from "../../api/client";
import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";
import type { Locale, PlainKey } from "../../i18n";
import { draftOf, parseLimits } from "../app/settings/limits";
import type { LimitsDraft } from "../app/settings/limits";
import { domainProblem, normalizeDomain } from "../domains/names";
import { generateSecret } from "./secrets";

export type Inspection = ResponseOf<"/api/apps/inspect", "post">;
export type EnvKey = Inspection["env_keys"][number];
export type CreateAppBody = BodyOf<"/api/apps", "post">;
export type AppTypeOption = ResponseOf<"/api/apps/types", "get">["types"][number];
export type PlatformProposal = NonNullable<Inspection["platform_proposal"]>;

export type Step = "source" | "review" | "deploy";

export const STEPS: readonly { id: Step; label: PlainKey }[] = [
  { id: "source", label: "newApp.steps.source" },
  { id: "review", label: "newApp.steps.review" },
  { id: "deploy", label: "newApp.steps.deploy" },
];

/** A list of names as the language joins them: "Node.js and Vite", "Node.js y Vite". */
export function joinList(items: readonly string[], locale: Locale = getLocale()): string {
  return new Intl.ListFormat(locale, { style: "long", type: "conjunction" }).format(items);
}

// ---------------------------------------------------------------------------------------
// The source

export type SourceKind = "github" | "git" | "archive" | "local" | "unknown";

/**
 * `github:owner/repo`: a repository this server's GitHub App reaches, the spelling the
 * repository picker and `wasm create --source` share (wasm.validators.source).
 */
const GITHUB_SHORTHAND = /^github:[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\/[A-Za-z0-9._-]{1,100}$/;

/**
 * What a source looks like, for the hint under the field. The server decides for real
 * (`validate_source`); this only tells the operator what WASM will do with it.
 */
export function sourceKind(value: string): SourceKind {
  const source = value.trim();
  if (source === "") return "unknown";
  if (GITHUB_SHORTHAND.test(source)) return "github";
  if (source.startsWith("/") || source.startsWith("~/") || source.startsWith("./") || source.startsWith("../")) return "local";
  if (/^(?:https?|ftp):\/\/\S+\.(?:zip|tar\.gz|tgz|tar\.bz2|tar\.xz|tar)(?:\?\S*)?$/i.test(source)) return "archive";
  if (/^(?:https?|ssh|git):\/\/\S+$/i.test(source) || /^[\w.-]+@[\w.-]+:\S+$/.test(source)) return "git";
  return "unknown";
}

/**
 * A source in a few characters, for places too narrow for all of it: the end of a path
 * (`src/storefront`), the repository of a URL (`you/app`).
 */
export function shortSource(value: string): string {
  const source = value.trim().replace(/\/+$/, "").replace(/\.git$/, "");
  const parts = source.split(/[/:]/).filter((part) => part !== "");
  return parts.slice(-2).join("/") || source;
}

export const SOURCE_WORDS: Record<SourceKind, PlainKey> = {
  github: "newApp.source.kinds.github",
  git: "newApp.source.kinds.git",
  archive: "newApp.source.kinds.archive",
  local: "newApp.source.kinds.local",
  unknown: "newApp.source.kinds.unknown",
};

export interface SourceForm {
  source: string;
  branch: string;
  /**
   * The GitHub App installation that reaches the repository, as the repository picker listed
   * it. Only a repository chosen there has one; a typed source never does.
   */
  installationId?: number;
}

/** Whether two sources would be inspected, and deployed, the same way. */
export function sameSource(a: SourceForm, b: SourceForm): boolean {
  return a.source.trim() === b.source.trim() && a.branch.trim() === b.branch.trim() && a.installationId === b.installationId;
}

export type InspectBody = BodyOf<"/api/apps/inspect", "post">;

/** The request `POST /api/apps/inspect` takes for a source. */
export function inspectBody(form: SourceForm): InspectBody {
  const branch = form.branch.trim();
  return {
    source: form.source.trim(),
    ...(branch !== "" && sourceKind(form.source) !== "local" ? { branch } : {}),
    ...(form.installationId !== undefined ? { github_installation_id: form.installationId } : {}),
  };
}

export type SourceErrors = Partial<Record<"source" | "branch", string>>;

export function sourceProblems(form: SourceForm): SourceErrors {
  const errors: SourceErrors = {};
  const locale = getLocale();
  if (form.source.trim() === "") errors.source = translate(locale, "newApp.validation.sourceEmpty");
  else if (sourceKind(form.source) === "unknown") errors.source = translate(locale, "newApp.validation.sourceUnknown");
  if (form.branch.trim() !== "" && !/^[\w./-]+$/.test(form.branch.trim())) {
    errors.branch = translate(locale, "newApp.validation.branch");
  }
  return errors;
}

/**
 * What the Review step starts from when detection found nothing the operator wants: no type,
 * no commands, no variables. The type is then chosen by hand, and its deployer decides the
 * rest when it runs.
 */
export function manualInspection(source: SourceForm): Inspection {
  return {
    app_type: "",
    detected_types: [],
    package_manager: null,
    install_command: [],
    build_command: [],
    start_command: "",
    default_port: 3000,
    env_keys: [],
    branch: source.branch.trim(),
    commit: "",
  };
}

// ---------------------------------------------------------------------------------------
// The review

/**
 * The registry's display name (`DISPLAY_NAME`) for a type, from `GET /api/apps/types` - the
 * one source of truth for what WASM can deploy (`available_types`). Falls back to the raw
 * identifier while the list has not loaded yet, or for a type the wizard has not seen.
 */
export function typeName(types: readonly AppTypeOption[], type: string): string {
  return types.find((entry) => entry.type === type)?.name ?? type;
}

/**
 * Every type the operator can choose, the detected ones first in the order the registry
 * matched them, then the rest as the API ordered them (alphabetical, `auto` last). The first
 * is what WASM would deploy as.
 */
export function typeOptions(types: readonly AppTypeOption[], detected: readonly string[]): { value: string; label: string; hint?: string }[] {
  const rest = types.filter((entry) => !detected.includes(entry.type));
  return [
    ...detected.map((type, index) => ({
      value: type,
      label: typeName(types, type),
      hint: translate(getLocale(), index === 0 ? "newApp.review.typeDetected" : "newApp.review.typeAlso"),
    })),
    ...rest.map((entry) => ({ value: entry.type, label: entry.name })),
  ];
}

/** Types that run no process of their own, so they have no port. */
export function hasPort(type: string): boolean {
  return type !== "static";
}

export interface EnvRow {
  /** Stable across edits, for React keys and error names. */
  id: string;
  name: string;
  value: string;
  secret: boolean;
  required: boolean;
  /** Declared in .env.example: its name is fixed, only its value is edited. */
  declared: boolean;
  /** The value .env.example gave it, if any. */
  example: string | null;
  /**
   * Proposed by another platform's configuration (`platform_proposal`) rather than declared in
   * .env.example; `generated` when that platform generates it, so one was generated here.
   */
  proposed?: { generated: boolean; note: string | null };
}

export type Layout = "releases" | "inplace";
export type WebServer = "nginx" | "apache";

export interface PathRow {
  /** Stable across edits, for React keys and error names. */
  id: string;
  value: string;
}

export interface ReviewForm {
  appType: string;
  domain: string;
  /** Also answer on www.<domain>, as a redirect to it. Only offered where that means anything. */
  includeWww: boolean;
  webserver: WebServer;
  ssl: boolean;
  port: string;
  layout: Layout;
  /** Releases only: paths kept in shared/ and linked into every release. */
  persistentPaths: PathRow[];
  limits: LimitsDraft;
  env: EnvRow[];
  /**
   * Whether what another platform's configuration proposes (`platform_proposal`) is filled in
   * when there is one. On until the operator turns it off.
   */
  useProposal: boolean;
}

/**
 * Whether "Also serve www" would do anything for this domain: `should_include_www` on the
 * server folds it to false for a subdomain or a name that already is `www.*`, and offering a
 * toggle that a deploy would silently ignore is worse than not offering it.
 */
export function canIncludeWww(domain: string): boolean {
  const parts = normalizeDomain(domain).split(".");
  return parts.length === 2 && parts[0] !== "www";
}

export function envRowsFrom(keys: readonly EnvKey[]): EnvRow[] {
  return keys.map((key) => ({
    id: `declared:${key.name}`,
    name: key.name,
    value: key.default ?? "",
    secret: key.secret,
    required: key.required,
    declared: true,
    example: key.default ?? null,
  }));
}

/**
 * The port to propose: the type's own default, or the first one after it no app on this
 * machine has taken. Proposing a port another app holds would only earn an error.
 */
export function proposedPort(preferred: number, taken: ReadonlyMap<number, string>): number {
  let port = preferred;
  while (taken.has(port) && port < 65535) port += 1;
  return port;
}

/**
 * The form an inspection proposes. When the operator inspects again (another branch, a fixed
 * repository), what they already typed is kept: the domain, the choices, and the value of
 * every variable that is still declared. Nothing is asked twice.
 */
export function initialReview(
  inspection: Inspection,
  defaults: { webserver: WebServer; taken: ReadonlyMap<number, string> },
  previous?: ReviewForm | null,
): ReviewForm {
  const env = envRowsFrom(inspection.env_keys);
  if (!previous) {
    const form: ReviewForm = {
      appType: inspection.app_type,
      domain: "",
      includeWww: false,
      webserver: defaults.webserver,
      ssl: true,
      port: String(proposedPort(inspection.default_port, defaults.taken)),
      layout: "releases",
      persistentPaths: [],
      limits: draftOf({}),
      env,
      useProposal: true,
    };
    return withProposal(form, inspection, defaults.taken, true);
  }
  const typed = new Map(previous.env.map((row) => [row.name, row]));
  const kept = env.map((row) => {
    const before = typed.get(row.name);
    return before ? { ...row, value: before.value } : row;
  });
  const extra = previous.env.filter((row) => !row.declared && !env.some((declared) => declared.name === row.name));
  const proposed = previous.env.filter((row) => row.proposed !== undefined);
  const again: ReviewForm = {
    ...previous,
    appType: inspection.detected_types.includes(previous.appType) ? previous.appType : inspection.app_type,
    env: [...kept, ...proposed, ...extra],
  };
  // A proposal is used on the new inspection as the operator left it on the previous one.
  return withProposal(again, inspection, defaults.taken, previous.useProposal);
}

// ---------------------------------------------------------------------------------------
// Another platform's configuration

const PLATFORM_NAMES: Readonly<Record<string, string>> = {
  vercel: "Vercel",
  railway: "Railway",
  render: "Render",
  heroku: "Heroku",
};

/** A platform's own name (a product name, never translated). */
export function platformName(platform: string): string {
  return PLATFORM_NAMES[platform] ?? platform;
}

/** The id of a row a proposal added, so turning the proposal off finds exactly those. */
const PROPOSED = "proposed:";

/** The port a proposal asks for, moved to the next free one when another app has it. */
function proposalPort(proposal: PlatformProposal, taken: ReadonlyMap<number, string>): string | null {
  return proposal.port !== null && proposal.port !== undefined ? String(proposedPort(proposal.port, taken)) : null;
}

/**
 * Fills in, or takes back out, what another platform's configuration proposes: its type, its
 * port, its variables (the ones .env.example does not already declare; a variable the platform
 * generates gets a random value here), and its persistent paths. Commands and the health check
 * are not part of the form: WASM runs the type's own commands, and the health check is set on
 * the application once it exists. Taking it out keeps whatever the operator changed since.
 */
export function withProposal(form: ReviewForm, inspection: Inspection, taken: ReadonlyMap<number, string>, on: boolean): ReviewForm {
  const proposal = inspection.platform_proposal ?? null;
  const typed = new Map(form.env.filter((row) => row.proposed !== undefined).map((row) => [row.name, row.value]));
  const env = form.env.filter((row) => row.proposed === undefined);
  const persistentPaths = form.persistentPaths.filter((row) => !row.id.startsWith(PROPOSED));
  if (proposal === null) return { ...form, env, persistentPaths, useProposal: on };

  const port = proposalPort(proposal, taken);
  const detectedPort = String(proposedPort(inspection.default_port, taken));
  const proposedType = proposal.app_type ?? null;
  if (!on) {
    return {
      ...form,
      env,
      persistentPaths,
      useProposal: false,
      port: port !== null && form.port === port ? detectedPort : form.port,
      appType: proposedType !== null && form.appType === proposedType ? inspection.app_type : form.appType,
    };
  }

  const declared = new Set(env.map((row) => row.name));
  const added: EnvRow[] = (proposal.env ?? [])
    .filter((variable) => !declared.has(variable.name))
    .map((variable) => {
      const given = variable.value ?? null;
      return {
        id: `${PROPOSED}${variable.name}`,
        name: variable.name,
        value: typed.get(variable.name) ?? given ?? (variable.generated ? generateSecret() : ""),
        secret: variable.secret || variable.generated,
        required: variable.required && !variable.generated,
        declared: true,
        example: given,
        proposed: { generated: variable.generated, note: variable.note ?? null },
      };
    });
  const kept = new Set(persistentPaths.map((row) => row.value.trim()));
  const paths = (proposal.persistent_paths ?? []).filter((value) => !kept.has(value)).map((value) => ({ id: `${PROPOSED}${value}`, value }));
  return {
    ...form,
    useProposal: true,
    appType: proposedType ?? form.appType,
    port: port ?? form.port,
    env: [...env.filter((row) => row.declared), ...added, ...env.filter((row) => !row.declared)],
    persistentPaths: [...persistentPaths, ...paths],
  };
}

/**
 * What is wrong with a domain for a new application, or null: not a domain the server takes,
 * or one already deployed here. The same check for every way of starting an application.
 */
export function domainError(value: string, deployed: ReadonlySet<string>): string | null {
  const domain = normalizeDomain(value);
  const bad = domainProblem(domain);
  if (bad !== null) return bad;
  if (deployed.has(domain)) return translate(getLocale(), "newApp.validation.domainTaken", { domain });
  return null;
}

export interface ReviewContext {
  /** Domains already deployed on this machine. */
  domains: ReadonlySet<string>;
  /** Ports already taken by an app, and by which. */
  ports: ReadonlyMap<number, string>;
  /** CPUs of this machine, for the CPU quota's upper bound; null while unknown. */
  cores: number | null;
}

export type ReviewErrors = Record<string, string>;

const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** Field name of a variable's value in ReviewErrors. */
export function envField(row: Pick<EnvRow, "id">): string {
  return `env:${row.id}`;
}

/** Field name of an added variable's name in ReviewErrors. */
export function envNameField(row: Pick<EnvRow, "id">): string {
  return `env-name:${row.id}`;
}

/** Field name of a persistent path in ReviewErrors. */
export function pathField(row: Pick<PathRow, "id">): string {
  return `path:${row.id}`;
}

/** Field name of a resource limit in ReviewErrors. */
export function limitField(name: keyof LimitsDraft): string {
  return `limit:${name}`;
}

/**
 * A persistent path from the operator, or why the deployer would refuse it - the same check
 * `wasm.deployers.releases.persistent_path` runs when the deploy links `shared/`, run here
 * first so a typo is caught before the build rather than after.
 */
export function persistentPathProblem(raw: string): string | null {
  const value = raw.trim();
  if (value === "") return translate(getLocale(), "newApp.validation.pathEmpty");
  if (value.startsWith("/") || value.split("/").some((part) => part === "..")) {
    return translate(getLocale(), "newApp.validation.pathRelative");
  }
  return null;
}

/** The port the operator typed, or why it is not one the server will take. */
export function portProblem(value: string, taken: ReadonlyMap<number, string>): string | null {
  const text = value.trim();
  const locale = getLocale();
  if (!/^\d+$/.test(text)) return translate(locale, "newApp.validation.portNumber");
  const port = Number(text);
  if (port < 1 || port > 65535) return translate(locale, "newApp.validation.portRange");
  if (port < 1024 && port !== 80 && port !== 443) return translate(locale, "newApp.validation.portReserved");
  const owner = taken.get(port);
  if (owner !== undefined) return translate(locale, "newApp.validation.portTaken", { port: text, owner });
  return null;
}

export function reviewProblems(form: ReviewForm, context: ReviewContext): ReviewErrors {
  const errors: ReviewErrors = {};
  const locale = getLocale();
  if (form.appType === "") errors["appType"] = translate(locale, "newApp.validation.appType");
  const domain = domainError(form.domain, context.domains);
  if (domain !== null) errors["domain"] = domain;

  if (hasPort(form.appType)) {
    const port = portProblem(form.port, context.ports);
    if (port !== null) errors["port"] = port;
  }

  const names = new Set<string>();
  for (const row of form.env) {
    const name = row.name.trim();
    if (!row.declared) {
      if (name === "" && row.value === "") continue;
      if (!ENV_NAME.test(name)) {
        errors[envNameField(row)] = translate(locale, "newApp.validation.envName");
        continue;
      }
    }
    if (names.has(name)) errors[envNameField(row)] = translate(locale, "newApp.validation.envTwice", { name });
    names.add(name);
    if (row.required && row.value.trim() === "") {
      errors[envField(row)] = translate(locale, row.proposed !== undefined ? "newApp.validation.envRequiredProposal" : "newApp.validation.envRequired");
    }
  }

  if (form.layout === "releases") {
    const paths = new Set<string>();
    for (const row of form.persistentPaths) {
      const value = row.value.trim();
      if (value === "") continue;
      const problem = persistentPathProblem(value);
      if (problem !== null) {
        errors[pathField(row)] = problem;
        continue;
      }
      if (paths.has(value)) {
        errors[pathField(row)] = translate(locale, "newApp.validation.pathTwice", { path: value });
        continue;
      }
      paths.add(value);
    }
  }

  const limitErrors = parseLimits(form.limits, context.cores).errors;
  for (const name of Object.keys(limitErrors) as (keyof LimitsDraft)[]) {
    const message = limitErrors[name];
    if (message !== undefined) errors[limitField(name)] = message;
  }

  return errors;
}

/** The request `POST /api/apps` takes, from what the operator reviewed. */
export function createAppBody(source: SourceForm, form: ReviewForm): CreateAppBody {
  const env: Record<string, string> = {};
  for (const row of form.env) {
    const name = row.name.trim();
    if (name === "") continue;
    env[name] = row.value;
  }
  const branch = source.branch.trim();
  const paths = form.layout === "releases" ? form.persistentPaths.map((row) => row.value.trim()).filter((value) => value !== "") : [];
  const limits = parseLimits(form.limits, null).values;
  return {
    domain: normalizeDomain(form.domain),
    source: source.source.trim(),
    ...(branch !== "" && sourceKind(source.source) !== "local" ? { branch } : {}),
    ...(source.installationId !== undefined ? { github_installation_id: source.installationId } : {}),
    app_type: form.appType,
    ...(hasPort(form.appType) ? { port: Number(form.port.trim()) } : {}),
    webserver: form.webserver,
    ssl: form.ssl,
    layout: form.layout,
    include_www: form.includeWww && canIncludeWww(form.domain),
    ...(paths.length > 0 ? { persistent_paths: paths } : {}),
    memory_max_mb: limits.memory_max_mb,
    cpu_quota_percent: limits.cpu_quota_percent,
    tasks_max: limits.tasks_max,
    env_vars: env,
    skip_database: false,
  };
}

// ---------------------------------------------------------------------------------------
// Refusals

export interface Refusal {
  /** The step the operator has to go back to. */
  step: Step;
  /** Messages per field of that step. Empty when the refusal is about no field in particular. */
  fields: Record<string, string>;
}

/** Fields of the request, by the step that asks for them. */
const SOURCE_FIELDS = new Set(["source", "branch", "github_installation_id"]);
const REVIEW_FIELDS: Readonly<Record<string, string>> = {
  domain: "domain",
  port: "port",
  app_type: "appType",
  webserver: "webserver",
  ssl: "ssl",
  layout: "layout",
  include_www: "includeWww",
  persistent_paths: "persistentPaths",
  memory_max_mb: limitField("memory"),
  cpu_quota_percent: limitField("cpu"),
  tasks_max: limitField("tasks"),
};

/**
 * Where a refusal of `POST /api/apps` sends the operator: to the step holding the fields a 422
 * names, to the domain for a clash or a bad domain, to the port for a port the machine refuses,
 * to the source when the source is the problem. Anything else stays on the Deploy step.
 */
export function refusalOf(error: unknown): Refusal | null {
  if (!isApiError(error)) return null;
  if (error.fields !== null) {
    const source: Record<string, string> = {};
    const review: Record<string, string> = {};
    for (const [name, message] of Object.entries(error.fields)) {
      // The installation is part of the chosen repository, so its complaint is the source's.
      if (SOURCE_FIELDS.has(name)) source[name === "github_installation_id" ? "source" : name] = message;
      else review[REVIEW_FIELDS[name] ?? name] = message;
    }
    if (Object.keys(source).length > 0) return { step: "source", fields: source };
    return { step: "review", fields: review };
  }
  if (error.status === 409 || error.error === "domainerror") return { step: "review", fields: { domain: error.detail } };
  if (error.error === "porterror") return { step: "review", fields: { port: error.detail } };
  if (error.error === "sourceerror") return { step: "source", fields: { source: error.detail } };
  return null;
}
