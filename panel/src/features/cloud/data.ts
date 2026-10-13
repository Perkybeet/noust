/**
 * Servers created at a cloud provider: what the console reads of `/api/fleet/cloud`, and the
 * pure rules of the form that creates one (which plans a location sells, which images fit a
 * plan, the request the form becomes). Nothing here renders; the dialogs and the page do.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { BodyOf, ResponseOf } from "../../api/client";
import type { Status } from "../../components/ui/StatusPill";
import { NODE_NAME } from "../fleet/nodes";

export type CloudProvider = ResponseOf<"/api/fleet/cloud/providers", "get">[number];
export type CloudAccount = ResponseOf<"/api/fleet/cloud/accounts", "get">[number];
export type CloudCatalog = ResponseOf<"/api/fleet/cloud/accounts/{name}/catalog", "get">;
export type CloudChoice = CloudCatalog["plans"][number];
export type CloudScript = ResponseOf<"/api/fleet/cloud/scripts", "get">[number];
export type CloudScriptText = ResponseOf<"/api/fleet/cloud/scripts/{name}", "get">;
export type CloudMachine = ResponseOf<"/api/fleet/cloud/servers", "get">[number];
export type ProvisionBody = BodyOf<"/api/fleet/cloud/servers", "post">;

/** What the provider's token gives: the key of a provider, as the API spells it. */
export type ProviderKind = "hetzner" | "digitalocean" | "vultr";

export const PROVIDER_KINDS: readonly ProviderKind[] = ["hetzner", "digitalocean", "vultr"];

export const cloudKeys = {
  all: ["fleet", "cloud"] as const,
  providers: ["fleet", "cloud", "providers"] as const,
  accounts: ["fleet", "cloud", "accounts"] as const,
  catalog: (account: string) => ["fleet", "cloud", "catalog", account] as const,
  scripts: ["fleet", "cloud", "scripts"] as const,
  script: (name: string) => ["fleet", "cloud", "scripts", name] as const,
  machines: ["fleet", "cloud", "servers"] as const,
};

export const cloudProvidersQuery = () =>
  queryOptions({
    queryKey: cloudKeys.providers,
    queryFn: ({ signal }) => request("get", "/api/fleet/cloud/providers", { signal }),
    // The providers are the server's own code, not configuration.
    staleTime: Infinity,
  });

export const cloudAccountsQuery = () =>
  queryOptions({
    queryKey: cloudKeys.accounts,
    queryFn: ({ signal }) => request("get", "/api/fleet/cloud/accounts", { signal }),
  });

/** What an account can create now: asks the provider, so it is read once per dialog, not polled. */
export const cloudCatalogQuery = (account: string) =>
  queryOptions({
    queryKey: cloudKeys.catalog(account),
    queryFn: ({ signal }) => request("get", "/api/fleet/cloud/accounts/{name}/catalog", { params: { name: account }, signal }),
    enabled: account !== "",
    staleTime: 5 * 60_000,
    retry: false,
  });

export const cloudScriptsQuery = () =>
  queryOptions({
    queryKey: cloudKeys.scripts,
    queryFn: ({ signal }) => request("get", "/api/fleet/cloud/scripts", { signal }),
  });

export const cloudScriptQuery = (name: string) =>
  queryOptions({
    queryKey: cloudKeys.script(name),
    queryFn: ({ signal }) => request("get", "/api/fleet/cloud/scripts/{name}", { params: { name }, signal }),
    enabled: name !== "",
  });

export const cloudMachinesQuery = () =>
  queryOptions({
    queryKey: cloudKeys.machines,
    queryFn: ({ signal }) => request("get", "/api/fleet/cloud/servers", { signal }),
    // A machine being created changes by the minute; the page that shows it asks again.
    refetchInterval: (query) => ((query.state.data ?? []).some((machine) => IN_PROGRESS.has(machine.state)) ? 5_000 : false),
  });

/** The states a machine passes through before it is in the fleet. */
const IN_PROGRESS: ReadonlySet<string> = new Set(["creating", "booting", "enrolling"]);

/** How a machine's state reads as a status: the shape and colour say the same as the word. */
export function machineStatus(state: string): Status {
  switch (state) {
    case "ready":
      return "running";
    case "failed":
      return "failed";
    case "deleted":
      return "stopped";
    case "creating":
    case "booting":
    case "enrolling":
      return "deploying";
    default:
      return "unknown";
  }
}

/** The kinds a library entry can be. */
export type ScriptKind = "script" | "cloud-config";

/** What a script's name may be: the API's own rule. */
export const SCRIPT_NAME = /^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$/;

/** What an account's name may be: the API's own rule. */
export const ACCOUNT_NAME = /^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$/;

/** The shape of a token worth sending: no spaces or line breaks, long enough to be one. */
export function plausibleToken(text: string): boolean {
  const token = text.trim();
  return token.length >= 16 && token.length <= 512 && !/\s/.test(token);
}

/** Guess a library entry's kind from its first line, the way the CLI does. */
export function guessKind(content: string): ScriptKind {
  return content.startsWith("#cloud-config") ? "cloud-config" : "script";
}

// -- The form that creates a server ----------------------------------------------------------

export type Access = "admin" | "deploy" | "read";

export interface CreateForm {
  name: string;
  account: string;
  location: string;
  plan: string;
  image: string;
  access: Access;
  hostAccess: boolean;
  sshKeys: string[];
  firewalls: string[];
  networks: string[];
  ipv4: boolean;
  ipv6: boolean;
  backups: boolean;
  scripts: string[];
  deleteOnFailure: boolean;
}

export const EMPTY_CREATE: CreateForm = {
  name: "",
  account: "",
  location: "",
  plan: "",
  image: "",
  access: "admin",
  hostAccess: false,
  sshKeys: [],
  firewalls: [],
  networks: [],
  ipv4: true,
  ipv6: true,
  backups: false,
  scripts: [],
  deleteOnFailure: false,
};

export type CreateProblem = "name" | "account" | "location" | "plan" | "image" | "addresses";

/** The first thing wrong with the form, in the order the steps ask for things; null when it can be sent. */
export function createProblem(form: CreateForm): CreateProblem | null {
  if (!NODE_NAME.test(form.name.trim())) return "name";
  if (form.account === "") return "account";
  if (form.location === "") return "location";
  if (form.plan === "") return "plan";
  if (form.image === "") return "image";
  if (!form.ipv4 && !form.ipv6) return "addresses";
  return null;
}

/** The plans an account sells in a location, the cheapest first, those with no price last. */
export function plansIn(catalog: CloudCatalog, location: string): CloudChoice[] {
  const sold = catalog.plans.filter((plan) => plan.locations === undefined || plan.locations === null || plan.locations.length === 0 || plan.locations.includes(location));
  return [...sold].sort((a, b) => (a.monthly ?? Number.POSITIVE_INFINITY) - (b.monthly ?? Number.POSITIVE_INFINITY) || a.name.localeCompare(b.name));
}

/** The images that run on a plan's processor; all of them when either side does not say. */
export function imagesFor(catalog: CloudCatalog, plan: CloudChoice | undefined): CloudChoice[] {
  const arch = plan?.arch;
  const fitting = catalog.images.filter((image) => arch === undefined || arch === null || image.archs === undefined || image.archs === null || image.archs.length === 0 || image.archs.includes(arch));
  return [...fitting].sort((a, b) => a.name.localeCompare(b.name));
}

/** Debian and Ubuntu LTS releases Noust ships packages for, best first: what a new server starts from. */
const PREFERRED_IMAGES = [/ubuntu.*24[.-]?04/i, /debian.*13/i, /debian.*12/i, /ubuntu.*22[.-]?04/i];

/** The image to preselect: the first of Noust's supported releases the list has, else none. */
export function preferredImage(images: readonly CloudChoice[]): string {
  for (const pattern of PREFERRED_IMAGES) {
    const found = images.find((image) => pattern.test(image.id) || pattern.test(image.name));
    if (found !== undefined) return found.id;
  }
  return "";
}

/** A plan's price as the provider quotes it, with no currency (the provider's account decides it). */
export function priceOf(plan: CloudChoice | undefined): string | null {
  const monthly = plan?.monthly;
  if (monthly === undefined || monthly === null) return null;
  return monthly.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** The request the form becomes. */
export function createBody(form: CreateForm, capabilities: Record<string, boolean>): ProvisionBody {
  return {
    name: form.name.trim(),
    account: form.account,
    location: form.location,
    plan: form.plan,
    image: form.image,
    access: form.access,
    allow_host_access: form.hostAccess,
    ssh_keys: form.sshKeys,
    firewalls: capabilities["firewalls"] === true ? form.firewalls : [],
    networks: capabilities["networks"] === true ? form.networks : [],
    ipv4: form.ipv4,
    ipv6: capabilities["ipv6"] === true ? form.ipv6 : false,
    backups: capabilities["backups"] === true ? form.backups : false,
    scripts: form.scripts,
    delete_on_failure: form.deleteOnFailure,
  };
}
