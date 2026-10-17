/**
 * An application that runs on several servers, as the central keeps it (`/api/fleet/placements`):
 * where it runs, how it looks across those servers, and the calls that record it, roll it out,
 * copy it and write its servers for a load balancer. The central asks the servers and compares
 * their answers; the console only draws them, so a server that is down is a row, never a blank page.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { BodyOf, ResponseOf } from "../../api/client";
import type { Status } from "../../components/ui/StatusPill";
import type { PlainKey } from "../../i18n";

export type PlacementList = ResponseOf<"/api/fleet/placements", "get">;
export type Placement = PlacementList["placements"][number];
export type PlacementDetail = ResponseOf<"/api/fleet/placements/{domain}", "get">;
export type PlacementServer = PlacementDetail["status"]["servers"][number];
export type PlacementBody = BodyOf<"/api/fleet/placements/{domain}", "put">;
export type RolloutBody = BodyOf<"/api/fleet/placements/{domain}/rollout", "post">;
export type CopyBody = BodyOf<"/api/fleet/placements/{domain}/copy", "post">;
export type ShipBody = BodyOf<"/api/fleet/placements/{domain}/ship", "post">;
export type BuilderList = ResponseOf<"/api/fleet/builders", "get">;
export type Builder = BuilderList["builders"][number];
export type Upstreams = ResponseOf<"/api/fleet/placements/{domain}/upstreams", "get">;
export type ActionAnswer = ResponseOf<"/api/fleet/placements/{domain}/rollout", "post">;

export const placementKeys = {
  all: ["fleet", "placements"] as const,
  list: ["fleet", "placements", "list"] as const,
  one: (domain: string) => ["fleet", "placements", "one", domain] as const,
  upstreams: (domain: string, format: string, port: number) => ["fleet", "placements", "upstreams", domain, format, port] as const,
  builders: ["fleet", "builders"] as const,
};

/** How often a placement's status is read again while it is on screen. */
export const STATUS_POLL_MS = 15_000;

/** The applications placed on several servers: the central's own record, which asks no server. */
export const placementsQuery = () =>
  queryOptions({
    queryKey: placementKeys.list,
    queryFn: ({ signal }) => request("get", "/api/fleet/placements", { signal }),
  });

/** One application across its servers: the central asks each and compares. */
export const placementQuery = (domain: string) =>
  queryOptions({
    queryKey: placementKeys.one(domain),
    queryFn: ({ signal }) => request("get", "/api/fleet/placements/{domain}", { params: { domain }, signal }),
    refetchInterval: STATUS_POLL_MS,
    retry: false,
  });

/** The servers of an application written for the load balancer in front of them. */
export const upstreamsQuery = (domain: string, format: UpstreamFormat, port: number) =>
  queryOptions({
    queryKey: placementKeys.upstreams(domain, format, port),
    queryFn: ({ signal }) => request("get", "/api/fleet/placements/{domain}/upstreams", { params: { domain }, query: { format, port }, signal }),
    retry: false,
  });

/** Which server builds which application: the central's own record, which asks no server. */
export const buildersQuery = () =>
  queryOptions({
    queryKey: placementKeys.builders,
    queryFn: ({ signal }) => request("get", "/api/fleet/builders", { signal }),
  });

export const UPSTREAM_FORMATS = ["nginx", "list", "json"] as const;
export type UpstreamFormat = (typeof UPSTREAM_FORMATS)[number];

/** Records where an application runs, or changes it. Touches no server. */
export function savePlacement(domain: string, body: PlacementBody): Promise<Placement> {
  return request("put", "/api/fleet/placements/{domain}", { params: { domain }, body });
}

/** Forgets where an application runs. The servers keep it. */
export function removePlacement(domain: string): Promise<Placement> {
  return request("delete", "/api/fleet/placements/{domain}", { params: { domain } });
}

/** Plans a rollout (`plan: true`), or queues it as a fleet job. Queuing may ask for sudo mode. */
export function runRollout(domain: string, body: RolloutBody): Promise<ActionAnswer> {
  return request("post", "/api/fleet/placements/{domain}/rollout", { params: { domain }, body });
}

/** Plans putting the application on the servers that lack it, or queues it. Needs sudo mode. */
export function runCopy(domain: string, body: CopyBody): Promise<ActionAnswer> {
  return request("post", "/api/fleet/placements/{domain}/copy", { params: { domain }, body });
}

/** Plans building on one server and running the build on the others (`plan: true`), or queues it. */
export function runShip(domain: string, body: ShipBody): Promise<ActionAnswer> {
  return request("post", "/api/fleet/placements/{domain}/ship", { params: { domain }, body });
}

/** Chooses the server that builds an application. Touches no server. */
export function setBuilder(domain: string, node: string): Promise<Builder> {
  return request("put", "/api/fleet/builders/{domain}", { params: { domain }, body: { node } });
}

/** Goes back to building an application where it runs. */
export function clearBuilder(domain: string): Promise<Builder> {
  return request("delete", "/api/fleet/builders/{domain}", { params: { domain } });
}

// ---------------------------------------------------------------------------------------
// Reading an answer

/** How one server stands next to the others, in the console's states: colour, shape and word. */
export const SYNC_VIEW: Readonly<Record<string, { state: Status; label: PlainKey }>> = {
  current: { state: "running", label: "placements.sync.current" },
  differs: { state: "warning", label: "placements.sync.differs" },
  unknown: { state: "unknown", label: "placements.sync.unknown" },
  missing: { state: "failed", label: "placements.sync.missing" },
  unreachable: { state: "failed", label: "placements.sync.unreachable" },
};

/** A server's sync, whatever the central said: a word this console does not know reads as unknown. */
export function syncView(sync: string): { state: Status; label: PlainKey } {
  return SYNC_VIEW[sync] ?? { state: "unknown", label: "placements.sync.unknown" };
}

/** The servers of a placement that lack the application: where a copy goes. */
export function lacking(servers: readonly PlacementServer[]): string[] {
  return servers.filter((server) => server.sync === "missing").map((server) => server.node);
}

/** The servers that have the application and answer: where a copy can come from. */
export function sources(servers: readonly PlacementServer[]): string[] {
  return servers.filter((server) => server.present && server.reachable).map((server) => server.node);
}

/** What was typed as the batch size: a count, a percentage, or nothing for the default. */
export function parseSerial(value: string): number | string | null | undefined {
  const text = value.trim();
  if (text === "") return null;
  if (/^\d{1,3}%$/.test(text)) {
    const percent = Number(text.slice(0, -1));
    return percent >= 1 && percent <= 100 ? text : undefined;
  }
  return /^\d{1,4}$/.test(text) && Number(text) >= 1 ? Number(text) : undefined;
}

/** How many failures stop a rollout, as typed; empty is the first one. */
export function parseFailures(value: string): number | null | undefined {
  const text = value.trim();
  if (text === "") return null;
  return /^\d{1,4}$/.test(text) ? Number(text) : undefined;
}

/** A domain as the central accepts it for a placement: the bare name. */
export const DOMAIN = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

/** A commit as the central accepts it: 7 to 40 hexadecimal characters. */
export const COMMIT = /^[0-9a-fA-F]{7,40}$/;

/** A registry tag. */
export const IMAGE_TAG = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;

/** A git tag, as the servers pass it on. */
export const GIT_TAG = /^[A-Za-z0-9][A-Za-z0-9._/+-]{0,127}$/;

/** The body of a shipment, from what the form holds; null for a value that is not valid. */
export function shipBody(input: {
  builder: string;
  targets: readonly string[];
  commit: string;
  force: boolean;
  schemaChangedOk: boolean;
  rollback: boolean;
  serial: string;
  failures: string;
  canary: string;
  plan: boolean;
}): ShipBody | null {
  const serial = parseSerial(input.serial);
  const failures = parseFailures(input.failures);
  const commit = input.commit.trim();
  if (serial === undefined || failures === undefined) return null;
  if (commit !== "" && !COMMIT.test(commit)) return null;
  if (input.builder === "" || input.targets.length === 0 || input.targets.includes(input.builder)) return null;
  return {
    builder: input.builder,
    to: [...input.targets],
    commit: commit === "" ? null : commit.toLowerCase(),
    keep: false,
    force: input.force,
    schema_changed_ok: input.schemaChangedOk,
    frozen_ok: false,
    rollback_on_failure: input.rollback,
    serial,
    max_failures: failures,
    canary: input.canary === "" ? null : input.canary,
    plan: input.plan,
  };
}

/** What a rollout deploys. */
export type RolloutWhat = "head" | "commit" | "tag" | "image";

/** The body of a rollout, from what the form holds; null for a value that is not valid. */
export function rolloutBody(input: {
  what: RolloutWhat;
  value: string;
  force: boolean;
  rollback: boolean;
  serial: string;
  failures: string;
  canary: string;
  plan: boolean;
}): RolloutBody | null {
  const serial = parseSerial(input.serial);
  const failures = parseFailures(input.failures);
  if (serial === undefined || failures === undefined) return null;
  const value = input.value.trim();
  const body: RolloutBody = {
    force: input.force,
    rollback_on_failure: input.rollback,
    serial,
    max_failures: failures,
    canary: input.canary === "" ? null : input.canary,
    plan: input.plan,
  };
  if (input.what === "commit") {
    if (!COMMIT.test(value)) return null;
    body.commit = value.toLowerCase();
  } else if (input.what === "tag") {
    if (!GIT_TAG.test(value) || value.includes("..")) return null;
    body.tag = value;
  } else if (input.what === "image") {
    if (!IMAGE_TAG.test(value)) return null;
    body.image_tag = value;
  }
  return body;
}
