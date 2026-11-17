/**
 * The Docker zone of the whole fleet (`GET /api/views/docker/*`): every server answers for its own Docker, with its own permissions and
 * its own idea of whose each object is, and the central puts the answers side by side with the server on each row. A server that runs
 * a Noust older than 4.3 does not count Docker: its row of `nodes` says `unsupported`, and the others' rows are not an error for it.
 */

import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";
import type { DockerAttention, DockerContainer, DockerEngine, DockerImage, DockerNetwork, DockerStack, DockerVolume } from "./docker";

export type DockerView = ResponseOf<"/api/views/docker/containers", "get">;

/** Where a row came from, as the central says it: the server, whether it is the central itself, its page there and through this central. */
export interface FleetRow {
  node: string;
  local: boolean;
  page: string;
  href: string;
}

export type FleetContainer = DockerContainer & FleetRow;
export type FleetStack = DockerStack & FleetRow;
export type FleetImage = DockerImage & FleetRow;
export type FleetVolume = DockerVolume & FleetRow;
export type FleetNetwork = DockerNetwork & FleetRow;
export type FleetAttention = DockerAttention & FleetRow;
/** One server's engine, or its summary: the counters and what asks for attention there. */
export type FleetEngine = DockerEngine & FleetRow;
export type FleetSummary = FleetRow & { available: boolean; engine?: DockerEngine | null; counts?: Record<string, Record<string, number>>; attention?: DockerAttention[]; disk?: { free_bytes?: number | null; total_bytes?: number | null } | null };

export type DockerViewKind = "summary" | "attention" | "engine" | "stacks" | "containers" | "images" | "volumes" | "networks";

export const dockerViewKeys = {
  all: ["views", "docker"] as const,
  of: (kind: DockerViewKind) => ["views", "docker", kind] as const,
};

/** How often a view is read again while it is on screen: the central caches each server's answer about as long. */
export const DOCKER_VIEW_POLL_MS = 15_000;

/** One view of the fleet's Docker. A refresh that fails keeps the last answer, and a view never retries on its own. */
export const dockerViewQuery = (kind: DockerViewKind) =>
  queryOptions({
    queryKey: dockerViewKeys.of(kind),
    queryFn: ({ signal }) => {
      switch (kind) {
        case "summary":
          return request("get", "/api/views/docker/summary", { signal });
        case "attention":
          return request("get", "/api/views/docker/attention", { signal });
        case "engine":
          return request("get", "/api/views/docker/engine", { signal });
        case "stacks":
          return request("get", "/api/views/docker/stacks", { signal });
        case "containers":
          return request("get", "/api/views/docker/containers", { signal });
        case "images":
          return request("get", "/api/views/docker/images", { signal });
        case "volumes":
          return request("get", "/api/views/docker/volumes", { signal });
        case "networks":
          return request("get", "/api/views/docker/networks", { signal });
      }
    },
    refetchInterval: DOCKER_VIEW_POLL_MS,
    placeholderData: keepPreviousData,
    retry: false,
  });

/** What the look for new versions on every server is asked: which servers (none named is all of them), and whether to download what moved. */
export type FleetLookBody = BodyOf<"/api/fleet/docker/images/check", "post">;
export type FleetLookAnswer = ResponseOf<"/api/fleet/docker/images/check", "post">;

/**
 * Looks for new versions of the images in use on every server of the fleet but this central, which is looked at on its own page. With
 * `plan: true` it only says what it would do, which servers take part and which are left out and why; with `plan: false` it queues one
 * job of the central that asks each server for its own look. Nothing is asked of a registry unless a person pressed the button.
 */
export function lookAcrossFleet(body: FleetLookBody): Promise<FleetLookAnswer> {
  return request("post", "/api/fleet/docker/images/check", { body });
}

/** A view with the kind of its rows written on it: the contract leaves them open, and the type says which they are (nothing of it exists at run time). */
export type ViewRows<T> = Pick<DockerView, "items"> & { readonly rows?: T };

/**
 * The rows of a view, typed by the kind of thing it lists: each server's own API decides their fields.
 *
 * @param view - What the central answered, or nothing while it is read.
 * @returns The rows, or nothing while the view is read.
 */
export function rowsOf<T extends FleetRow>(view: ViewRows<T> | undefined): T[] | undefined {
  return view === undefined ? undefined : (view.items as unknown as T[]);
}
