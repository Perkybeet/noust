/**
 * How a container application is built and run, as a form: what the operator typed, what is
 * wrong with it before the server is asked, and the request it becomes (for a new application
 * and for a change to an existing one). Pure, like the wizard's own logic, so every rule is
 * tested without drawing a field.
 *
 * Nothing here ever holds a secret longer than the form does: the registry's password and the
 * value of a build secret are typed, sent once and never read back. What the server keeps of
 * them is only that they exist (`registry_password_set`, the ids of the build secrets).
 */

import type { BodyOf, ResponseOf } from "../../api/client";
import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";
import type { PlainKey } from "../../i18n";

export type ContainerSettings = ResponseOf<"/api/apps/{domain}/container", "get">;
export type ContainerRequest = NonNullable<BodyOf<"/api/apps", "post">["container"]>;
export type ContainerChange = BodyOf<"/api/apps/{domain}/container", "put">;

/** How the image comes to exist: built from a Dockerfile, built by Railpack, or pulled. */
export type Method = "dockerfile" | "railpack" | "image";

export const METHODS: readonly Method[] = ["dockerfile", "railpack", "image"];

/** Docker's own restart policies, as the server allows them. */
export const RESTART_POLICIES = ["unless-stopped", "always", "on-failure", "no"] as const;

export type RestartPolicy = (typeof RESTART_POLICIES)[number];

/** The catalog key of each restart policy's name, one literal per case. */
export const RESTART_LABELS: Readonly<Record<RestartPolicy, PlainKey>> = {
  "unless-stopped": "containers.restart.unlessStopped",
  always: "containers.restart.always",
  "on-failure": "containers.restart.onFailure",
  no: "containers.restart.no",
};

/** Where the process listens when nothing says, by method: the server's own defaults, for the hint. */
export const DEFAULT_PORTS: Readonly<Record<Method, number>> = { dockerfile: 8080, railpack: 3000, image: 80 };

/** A build argument being edited: stable across edits, for React keys and error names. */
export interface ArgRow {
  id: string;
  name: string;
  value: string;
}

/**
 * A build secret being edited. One the server already keeps is `stored`: its id is shown, its
 * value never is, and `remove` takes it out on save. One that is new has the value typed here.
 */
export interface SecretRow {
  id: string;
  secretId: string;
  value: string;
  stored: boolean;
  remove: boolean;
}

/** A container path being edited (a volume). */
export interface VolumeRow {
  id: string;
  value: string;
}

export interface ContainerDraft {
  method: Method;
  /** The registry reference, for an image. */
  image: string;
  /** Relative to the repository; empty finds `Dockerfile` or `Containerfile`. */
  dockerfile: string;
  /** Relative to the repository; empty is its root. */
  context: string;
  target: string;
  buildArgs: ArgRow[];
  buildSecrets: SecretRow[];
  /** Where the process listens in the container; empty is the method's default. */
  containerPort: string;
  volumes: VolumeRow[];
  restartPolicy: RestartPolicy;
  registryServer: string;
  registryUsername: string;
  /** Typed, sent once, never shown again. */
  registryPassword: string;
  /** A password is kept on the server for the account: what the settings page can say of it. */
  registryPasswordSet: boolean;
  /** Take the registry account out (settings only). */
  clearRegistry: boolean;
}

let counter = 0;

/** A fresh id for a row, unique for the life of the page. */
export function rowId(prefix: string): string {
  counter += 1;
  return `${prefix}:${String(counter)}`;
}

/** The form of a new container application: nothing set, the method's own defaults to apply. */
export function emptyDraft(method: Method = "dockerfile", port = ""): ContainerDraft {
  return {
    method,
    image: "",
    dockerfile: "",
    context: "",
    target: "",
    buildArgs: [],
    buildSecrets: [],
    containerPort: port,
    volumes: [],
    restartPolicy: "unless-stopped",
    registryServer: "",
    registryUsername: "",
    registryPassword: "",
    registryPasswordSet: false,
    clearRegistry: false,
  };
}

/** The form of an existing application, from what the server says it is. */
export function draftOf(settings: ContainerSettings): ContainerDraft {
  const method = METHODS.find((candidate) => candidate === settings.method) ?? "dockerfile";
  const policy = RESTART_POLICIES.find((candidate) => candidate === settings.restart_policy) ?? "unless-stopped";
  return {
    method,
    image: settings.image ?? "",
    dockerfile: settings.dockerfile ?? "",
    context: settings.context === "." ? "" : settings.context,
    target: settings.target ?? "",
    buildArgs: Object.entries(settings.build_args).map(([name, value]) => ({ id: rowId("arg"), name, value })),
    buildSecrets: settings.build_secrets.map((secretId) => ({ id: rowId("secret"), secretId, value: "", stored: true, remove: false })),
    containerPort: String(settings.container_port),
    volumes: settings.volumes.map((value) => ({ id: rowId("volume"), value })),
    restartPolicy: policy,
    registryServer: settings.registry_server ?? "",
    registryUsername: settings.registry_username ?? "",
    registryPassword: "",
    registryPasswordSet: settings.registry_password_set,
    clearRegistry: false,
  };
}

// ---------------------------------------------------------------------------------------
// What is wrong with a draft

export type ContainerErrors = Partial<Record<string, string>>;

const ARG_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;
const SECRET_ID = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/;

/** The port a draft names, or null when empty (the method's default), or NaN when it is not one. */
export function portOf(draft: Pick<ContainerDraft, "containerPort">): number | null {
  const text = draft.containerPort.trim();
  if (text === "") return null;
  return /^\d+$/.test(text) ? Number(text) : Number.NaN;
}

/**
 * What is wrong with an image reference before it is sent: the shapes the server would refuse
 * with certainty. The grammar of a reference is the server's to judge (`check_image_reference`),
 * so a reference this lets through can still be refused, with the server's own words.
 */
export function imageProblem(value: string): string | null {
  const reference = value.trim();
  const locale = getLocale();
  if (reference === "") return translate(locale, "containers.validation.imageEmpty");
  if (/\s/.test(reference) || reference.startsWith("-") || reference.includes("://")) return translate(locale, "containers.validation.imageShape");
  return null;
}

/** An absolute path inside the container, as a volume. */
export function volumeProblem(value: string): string | null {
  const path = value.trim();
  const locale = getLocale();
  if (path === "") return translate(locale, "containers.validation.volumeEmpty");
  if (!path.startsWith("/") || path === "/" || path.split("/").includes("..")) return translate(locale, "containers.validation.volumeAbsolute");
  return null;
}

export function argField(row: Pick<ArgRow, "id">): string {
  return `build_arg:${row.id}`;
}

export function argNameField(row: Pick<ArgRow, "id">): string {
  return `build_arg_name:${row.id}`;
}

export function secretField(row: Pick<SecretRow, "id">): string {
  return `build_secret:${row.id}`;
}

export function secretIdField(row: Pick<SecretRow, "id">): string {
  return `build_secret_id:${row.id}`;
}

export function volumeField(row: Pick<VolumeRow, "id">): string {
  return `volume:${row.id}`;
}

/**
 * What is wrong with a draft, by field: the ones the server would refuse for certain, said
 * beside the field before anything is sent. The server decides the rest and its refusal is shown
 * on the same fields (`image`, `dockerfile`, `build_args`...).
 *
 * @param existing Whether this is a form of an existing application: a stored build secret has
 *   no value to ask for, and the registry password is only needed with a new account.
 * @param withImage Whether the image reference is checked here: the wizard asks for it on its
 *   source step and checks it there.
 */
export function containerProblems(draft: ContainerDraft, { existing = false, withImage = true }: { existing?: boolean; withImage?: boolean } = {}): ContainerErrors {
  const errors: ContainerErrors = {};
  const locale = getLocale();
  if (draft.method === "image" && withImage) {
    const image = imageProblem(draft.image);
    if (image !== null) errors["image"] = image;
  }
  const port = portOf(draft);
  if (port !== null && (Number.isNaN(port) || port < 1 || port > 65535)) errors["container_port"] = translate(locale, "containers.validation.port");

  if (draft.method !== "image") {
    const names = new Set<string>();
    for (const row of draft.buildArgs) {
      const name = row.name.trim();
      if (name === "" && row.value === "") continue;
      if (!ARG_NAME.test(name)) {
        errors[argNameField(row)] = translate(locale, "containers.validation.argName");
        continue;
      }
      if (names.has(name)) errors[argNameField(row)] = translate(locale, "containers.validation.argTwice", { name });
      names.add(name);
    }
    const ids = new Set<string>();
    for (const row of draft.buildSecrets) {
      if (row.stored) continue;
      const id = row.secretId.trim();
      if (id === "" && row.value === "") continue;
      if (!SECRET_ID.test(id)) {
        errors[secretIdField(row)] = translate(locale, "containers.validation.secretId");
        continue;
      }
      if (ids.has(id) || draft.buildSecrets.some((other) => other.stored && !other.remove && other.secretId === id)) {
        errors[secretIdField(row)] = translate(locale, "containers.validation.secretTwice", { id });
        continue;
      }
      ids.add(id);
      if (row.value === "") errors[secretField(row)] = translate(locale, "containers.validation.secretEmpty", { id });
    }
  }

  const volumes = new Set<string>();
  for (const row of draft.volumes) {
    if (row.value.trim() === "") continue;
    const problem = volumeProblem(row.value);
    if (problem !== null) {
      errors[volumeField(row)] = problem;
      continue;
    }
    const path = row.value.trim().replace(/\/+$/, "");
    if (volumes.has(path)) errors[volumeField(row)] = translate(locale, "containers.validation.volumeTwice", { path });
    volumes.add(path);
  }

  if (draft.method === "image" && !draft.clearRegistry) {
    const user = draft.registryUsername.trim();
    const password = draft.registryPassword !== "";
    const server = draft.registryServer.trim();
    if (server !== "" && user === "") errors["registry_username"] = translate(locale, "containers.validation.registryUser");
    if (user !== "" && !password && !(existing && draft.registryPasswordSet)) errors["registry_password"] = translate(locale, "containers.validation.registryPassword");
    if (password && user === "") errors["registry_username"] = translate(locale, "containers.validation.registryUser");
  }
  return errors;
}

// ---------------------------------------------------------------------------------------
// The request

/** The build arguments of a draft as the server takes them: the rows that have a name. */
function argsOf(draft: ContainerDraft): Record<string, string> {
  const args: Record<string, string> = {};
  for (const row of draft.buildArgs) {
    const name = row.name.trim();
    if (name !== "") args[name] = row.value;
  }
  return args;
}

/** The volumes of a draft, in order, without a trailing slash: what the server stores. */
function volumesOf(draft: ContainerDraft): string[] {
  return draft.volumes.map((row) => row.value.trim().replace(/\/+$/, "")).filter((path) => path !== "");
}

/**
 * The `container` of `POST /api/apps` for a new application: only what the operator set, so
 * the server's defaults (the Dockerfile it finds, the context at the root, the method's port,
 * unless-stopped) stay the server's. The password and the values of the build secrets travel
 * here and nowhere else.
 */
export function containerBody(draft: ContainerDraft): ContainerRequest {
  const port = portOf(draft);
  const secrets: Record<string, string> = {};
  for (const row of draft.buildSecrets) {
    if (!row.stored && row.secretId.trim() !== "") secrets[row.secretId.trim()] = row.value;
  }
  const args = argsOf(draft);
  const volumes = volumesOf(draft);
  const builds = draft.method !== "image";
  return {
    clear_registry: false,
    method: draft.method,
    ...(draft.method === "image" ? { image: draft.image.trim() } : {}),
    ...(draft.method === "dockerfile" && draft.dockerfile.trim() !== "" ? { dockerfile: draft.dockerfile.trim() } : {}),
    ...(builds && draft.context.trim() !== "" ? { context: draft.context.trim() } : {}),
    ...(draft.method === "dockerfile" && draft.target.trim() !== "" ? { target: draft.target.trim() } : {}),
    ...(builds && Object.keys(args).length > 0 ? { build_args: args } : {}),
    ...(builds && Object.keys(secrets).length > 0 ? { build_secrets: secrets } : {}),
    ...(port !== null && !Number.isNaN(port) ? { container_port: port } : {}),
    ...(volumes.length > 0 ? { volumes } : {}),
    restart_policy: draft.restartPolicy,
    ...(draft.method === "image" && draft.registryServer.trim() !== "" ? { registry_server: draft.registryServer.trim() } : {}),
    ...(draft.method === "image" && draft.registryUsername.trim() !== "" ? { registry_username: draft.registryUsername.trim() } : {}),
    ...(draft.method === "image" && draft.registryPassword !== "" ? { registry_password: draft.registryPassword } : {}),
  };
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

function sameArgs(a: Readonly<Record<string, string>>, b: Readonly<Record<string, string>>): boolean {
  const left = Object.entries(a);
  return left.length === Object.keys(b).length && left.every(([name, value]) => b[name] === value);
}

export interface PlannedChange {
  /** The `PUT /api/apps/{domain}/container` body: only what changed. */
  body: ContainerChange;
  /** The names of the settings it changes, for the save bar's count. */
  fields: string[];
}

/**
 * What saving a draft changes of an existing application, against what the server holds: only
 * the settings that differ, a setting emptied as `clear` (the server's way to go back to its
 * default), a secret as itself and never compared, since there is nothing to compare it to.
 */
export function plannedChange(draft: ContainerDraft, stored: ContainerSettings): PlannedChange {
  const fields: string[] = [];
  const body: ContainerChange = { clear_registry: false };
  const clear: string[] = [];
  const settings = stored;

  if (draft.method !== settings.method && draft.method !== "image" && settings.method !== "image") {
    body.method = draft.method;
    fields.push("method");
  }
  if (draft.method === "image") {
    if (draft.image.trim() !== (settings.image ?? "")) {
      body.image = draft.image.trim();
      fields.push("image");
    }
  } else {
    const dockerfile = draft.dockerfile.trim();
    if (dockerfile !== (settings.dockerfile ?? "")) {
      if (dockerfile === "") clear.push("dockerfile");
      else body.dockerfile = dockerfile;
      fields.push("dockerfile");
    }
    const context = draft.context.trim() === "" ? "." : draft.context.trim();
    if (context !== settings.context) {
      body.context = context;
      fields.push("context");
    }
    const target = draft.target.trim();
    if (target !== (settings.target ?? "")) {
      if (target === "") clear.push("target");
      else body.target = target;
      fields.push("target");
    }
    const args = argsOf(draft);
    if (!sameArgs(args, settings.build_args)) {
      body.build_args = args;
      fields.push("build_args");
    }
    const secrets: Record<string, string | null> = {};
    for (const row of draft.buildSecrets) {
      if (row.stored && row.remove) secrets[row.secretId] = null;
      else if (!row.stored && row.secretId.trim() !== "") secrets[row.secretId.trim()] = row.value;
    }
    if (Object.keys(secrets).length > 0) {
      body.build_secrets = secrets;
      fields.push("build_secrets");
    }
  }

  const port = portOf(draft);
  if (port === null) {
    // Emptied: back to the method's default, which is what the server then says it is.
    if (settings.container_port !== DEFAULT_PORTS[draft.method]) {
      clear.push("container_port");
      fields.push("container_port");
    }
  } else if (!Number.isNaN(port) && port !== settings.container_port) {
    body.container_port = port;
    fields.push("container_port");
  }
  const volumes = volumesOf(draft);
  if (!sameList(volumes, settings.volumes)) {
    body.volumes = volumes;
    fields.push("volumes");
  }
  if (draft.restartPolicy !== settings.restart_policy) {
    body.restart_policy = draft.restartPolicy;
    fields.push("restart_policy");
  }

  if (draft.method === "image") {
    if (draft.clearRegistry) {
      if (settings.registry_username !== null && settings.registry_username !== undefined) {
        body.clear_registry = true;
        fields.push("registry");
      }
    } else {
      const server = draft.registryServer.trim();
      if (server !== (settings.registry_server ?? "")) {
        if (server === "") clear.push("registry_server");
        else body.registry_server = server;
        fields.push("registry_server");
      }
      const user = draft.registryUsername.trim();
      if (user !== (settings.registry_username ?? "")) {
        body.registry_username = user;
        fields.push("registry_username");
      }
      if (draft.registryPassword !== "") {
        body.registry_password = draft.registryPassword;
        fields.push("registry_password");
      }
    }
  }
  if (clear.length > 0) body.clear = clear;
  return { body, fields };
}

/** Whether the draft holds a registry account that would be sent (a new one, or a new password). */
export function hasRegistryAccount(draft: ContainerDraft): boolean {
  return draft.registryUsername.trim() !== "" || draft.registryPassword !== "";
}
