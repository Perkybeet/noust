/**
 * The Docker zone of Server (`/api/docker`, docs/docker.md): everything Docker has on the machine with whose each thing is and what
 * the server says can be done to it (`can`), a container from the inside, and the cleanup as a plan. Every list answers
 * `available: false` with the engine's own sentences when Docker is missing, unreachable or too old, which is a state of the page
 * and never an error, so a machine without Docker draws what Docker is and how to install it.
 */

import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type DockerSummary = ResponseOf<"/api/docker/summary", "get">;
export type DockerAttention = NonNullable<DockerSummary["attention"]>[number];
export type DockerEngine = ResponseOf<"/api/docker/engine", "get">;
export type DockerSentence = DockerEngine["facts"][number];
export type DockerStacks = ResponseOf<"/api/docker/stacks", "get">;
export type DockerStack = NonNullable<DockerStacks["items"]>[number];
export type DockerStackDetail = ResponseOf<"/api/docker/stacks/{name}", "get">;
export type DockerContainers = ResponseOf<"/api/docker/containers", "get">;
export type DockerContainer = NonNullable<DockerContainers["items"]>[number];
export type DockerContainerDetail = ResponseOf<"/api/docker/containers/{ref}", "get">;
export type DockerContainerLogs = ResponseOf<"/api/docker/containers/{ref}/logs", "get">;
export type DockerImages = ResponseOf<"/api/docker/images", "get">;
export type DockerImage = NonNullable<DockerImages["items"]>[number];
export type DockerVolumes = ResponseOf<"/api/docker/volumes", "get">;
export type DockerVolume = NonNullable<DockerVolumes["items"]>[number];
export type DockerVolumeSizes = ResponseOf<"/api/docker/volumes/sizes", "get">;
export type DockerNetworks = ResponseOf<"/api/docker/networks", "get">;
export type DockerNetwork = NonNullable<DockerNetworks["items"]>[number];
export type DockerRestartLoss = ResponseOf<"/api/docker/restart-loss", "get">;
export type DockerCleanupPlan = ResponseOf<"/api/docker/cleanup/plan", "get">;
export type DockerPlanItem = NonNullable<DockerCleanupPlan["items"]>[number];
export type DockerOwner = DockerContainer["owner"];
export type DockerCan = DockerContainer["can"];
export type DockerAction = ResponseOf<"/api/docker/containers/{ref}/start", "post">;

/** What the lists are filtered by, which is what the page keeps in its address. */
export interface DockerListFilter {
  owner?: string | undefined;
  search?: string | undefined;
  app?: string | undefined;
  state?: string | undefined;
  project?: string | undefined;
}

export const dockerKeys = {
  all: ["docker"] as const,
  summary: ["docker", "summary"] as const,
  engine: ["docker", "engine"] as const,
  restartLoss: ["docker", "restart-loss"] as const,
  stacks: (filter: DockerListFilter) => ["docker", "stacks", filter] as const,
  stack: (name: string) => ["docker", "stack", name] as const,
  containers: (filter: DockerListFilter) => ["docker", "containers", filter] as const,
  container: (ref: string) => ["docker", "container", ref] as const,
  logs: (ref: string, tail: number) => ["docker", "container", ref, "logs", tail] as const,
  images: (filter: DockerListFilter) => ["docker", "images", filter] as const,
  volumes: (filter: DockerListFilter) => ["docker", "volumes", filter] as const,
  volumeSizes: ["docker", "volume-sizes"] as const,
  networks: (filter: DockerListFilter) => ["docker", "networks", filter] as const,
  cleanup: ["docker", "cleanup-plan"] as const,
};

/** How often what is on screen is read again: Docker has no event stream here, and a container's state is what the page is for. */
export const DOCKER_POLL_MS = 10_000;

/** The filters Docker takes as parameters, without the ones the page did not set. */
function queryOf(filter: DockerListFilter): Record<string, string> {
  return Object.fromEntries(Object.entries(filter).filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1] !== ""));
}

const poll = { refetchInterval: DOCKER_POLL_MS, placeholderData: keepPreviousData } as const;

export const dockerSummaryQuery = () =>
  queryOptions({
    queryKey: dockerKeys.summary,
    queryFn: ({ signal }) => request("get", "/api/docker/summary", { signal }),
    ...poll,
  });

export const dockerEngineQuery = () =>
  queryOptions({
    queryKey: dockerKeys.engine,
    queryFn: ({ signal }) => request("get", "/api/docker/engine", { signal }),
  });

export const dockerRestartLossQuery = () =>
  queryOptions({
    queryKey: dockerKeys.restartLoss,
    queryFn: ({ signal }) => request("get", "/api/docker/restart-loss", { signal }),
  });

export const dockerStacksQuery = (filter: DockerListFilter = {}) =>
  queryOptions({
    queryKey: dockerKeys.stacks(filter),
    queryFn: ({ signal }) => request("get", "/api/docker/stacks", { query: queryOf(filter), signal }),
    ...poll,
  });

export const dockerStackQuery = (name: string) =>
  queryOptions({
    queryKey: dockerKeys.stack(name),
    queryFn: ({ signal }) => request("get", "/api/docker/stacks/{name}", { params: { name }, signal }),
    ...poll,
  });

export const dockerContainersQuery = (filter: DockerListFilter = {}) =>
  queryOptions({
    queryKey: dockerKeys.containers(filter),
    queryFn: ({ signal }) => request("get", "/api/docker/containers", { query: queryOf(filter), signal }),
    ...poll,
  });

export const dockerContainerQuery = (ref: string) =>
  queryOptions({
    queryKey: dockerKeys.container(ref),
    queryFn: ({ signal }) => request("get", "/api/docker/containers/{ref}", { params: { ref }, signal }),
    ...poll,
  });

/** The last lines, for the first paint of the live log and for a container that is not running; the follow is `/ws/docker/logs/{ref}`. */
export const dockerLogsQuery = (ref: string, tail = 200) =>
  queryOptions({
    queryKey: dockerKeys.logs(ref, tail),
    queryFn: ({ signal }) => request("get", "/api/docker/containers/{ref}/logs", { params: { ref }, query: { tail }, signal }),
  });

export const dockerImagesQuery = (filter: DockerListFilter = {}) =>
  queryOptions({
    queryKey: dockerKeys.images(filter),
    queryFn: ({ signal }) => request("get", "/api/docker/images", { query: queryOf(filter), signal }),
    ...poll,
  });

export const dockerVolumesQuery = (filter: DockerListFilter = {}) =>
  queryOptions({
    queryKey: dockerKeys.volumes(filter),
    queryFn: ({ signal }) => request("get", "/api/docker/volumes", { query: queryOf(filter), signal }),
    ...poll,
  });

/** What each volume takes: apart from the listing because Docker is slow to say it. Asked once the list is on screen. */
export const dockerVolumeSizesQuery = () =>
  queryOptions({
    queryKey: dockerKeys.volumeSizes,
    queryFn: ({ signal }) => request("get", "/api/docker/volumes/sizes", { signal }),
    staleTime: 60_000,
  });

export const dockerNetworksQuery = (filter: DockerListFilter = {}) =>
  queryOptions({
    queryKey: dockerKeys.networks(filter),
    queryFn: ({ signal }) => request("get", "/api/docker/networks", { query: queryOf(filter), signal }),
    ...poll,
  });

/** The cleanup as a plan: what would go, whose it is and how much it holds. Read when the operator asks for it, never kept: the token is of this reading. */
export const dockerCleanupPlanQuery = () =>
  queryOptions({
    queryKey: dockerKeys.cleanup,
    queryFn: ({ signal }) => request("get", "/api/docker/cleanup/plan", { signal }),
    staleTime: 0,
    gcTime: 0,
  });
