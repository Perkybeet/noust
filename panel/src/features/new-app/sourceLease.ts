/**
 * How a repository of the central's GitHub App reaches a server of the fleet (docs/sources.md, owner item 90).
 *
 * The App lives on the central: it lists the repositories and its private key never leaves it. A server that
 * deploys one of them clones it with a token the central leases to it, for the repositories the server serves,
 * good for an hour. A lease exists once the server has an application from that repository; for the first one
 * the central has nothing yet to lease against, so the console asks it to lease now, for exactly this server and
 * this repository, before the application is created (`POST /api/fleet/sources/leases`). Pure here, so each rule
 * is tested without a page; the wizard makes the call.
 */

import type { BodyOf } from "../../api/client";
import type { CreateAppBody } from "./wizard";

export type LeaseBody = BodyOf<"/api/fleet/sources/leases", "post">;

/** `owner/repo` of a source the repository list gave (`github:owner/repo`), or null for any other. */
export function repositoryOf(source: string): string | null {
  const match = /^github:([\w.-]+\/[\w.-]+)$/.exec(source.trim());
  return match?.[1] ?? null;
}

/**
 * What to ask the central to lease before an application is made on `node` from `source`: only for a server
 * of the fleet (the central's own clones with its App directly) and only for a repository the App listed.
 * Null when nothing needs leasing.
 */
export function leaseFor(node: string | null, source: string, listed: boolean): LeaseBody | null {
  const repository = repositoryOf(source);
  if (node === null || !listed || repository === null) return null;
  return { force: false, nodes: [node], repositories: [repository] };
}

/**
 * A request about a repository the central's App listed, as the server it is for gets it: without the
 * installation's number, which is the central's. The server clones with its lease, by repository; an
 * installation it has never heard of is a refusal.
 */
export function forServer<T extends object>(body: T, node: string | null, listed: boolean): T {
  if (node === null || !listed || !("github_installation_id" in body)) return body;
  const rest: Record<string, unknown> = { ...body };
  delete rest["github_installation_id"];
  return rest as T;
}

/** The create request with what belongs to the central's App taken out for a server of the fleet. */
export function createBodyFor(body: CreateAppBody, node: string | null, listed: boolean): CreateAppBody {
  return forServer(body, node, listed);
}
