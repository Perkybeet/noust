/**
 * What the tests of the Docker zone share: a signed-in session on a machine where Docker is at hand, the engine's answers, and one
 * builder for each kind of thing Docker has, so each test says only what is particular to it. Nothing here is drawn.
 */

import type { SessionInfo } from "../../api/queries/auth";
import type { DockerCan, DockerContainer, DockerImage, DockerNetwork, DockerOwner, DockerStack, DockerVolume } from "../../api/queries/docker";
import { SESSION, json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

export const NOBODY: DockerOwner = { kind: "nobody", subject: "", project: "" };
export const SHOP: DockerOwner = { kind: "app", subject: "shop.example.com", project: "shop-example-com" };
export const PG16: DockerOwner = { kind: "database", subject: "postgresql@pg16", project: "" };
export const NOUST: DockerOwner = { kind: "noust", subject: "buildkit", project: "" };

/** What the server says can be done to what nobody of Noust made, and to what an application owns. */
export const CAN_ALL: DockerCan = { start: true, stop: true, restart: true, remove: true, console: true, via: "", blocked: "" };
export const CAN_OWNED: DockerCan = {
  start: false,
  stop: false,
  restart: false,
  remove: false,
  console: false,
  via: "Do it to the whole application: noust stop shop.example.com.",
  blocked: "owner",
};

/** A session of a server whose function table says Docker is at hand (or not). */
export function dockerSession(docker = true, role = "server"): SessionInfo {
  const available = { available: docker, ...(docker ? {} : { reason: { code: "features.needs_docker", text: "Docker is not installed", params: {}, hint: null } }) };
  return { ...SESSION, central: { role, sealed: false, locked: false, features: { docker_zone: available } as unknown as NonNullable<NonNullable<SessionInfo["central"]>["features"]> } };
}

export const ENGINE_OK = {
  ok: true,
  state: "ok",
  usable: true,
  facts: [{ code: "docker.engine.version", params: { version: "29.1.3", api: "1.52" }, text: "Docker 29.1.3 (API 1.52)", hint: null }],
  limits: [],
  info: {
    installed: true,
    reachable: true,
    client_version: "29.1.3",
    server_version: "29.1.3",
    client_api: "1.52",
    server_api: "1.52",
    min_api: "1.44",
    api: "1.52",
    compose_version: "2.40.3",
    compose_v1: false,
    image_store: "containerd",
    storage_driver: "overlayfs",
    rootless: false,
    host: "",
    socket: "/var/run/docker.sock",
    socket_owner: "root",
    socket_group: "docker",
    socket_mode: "0660",
    root_dir: "/var/lib/docker",
    os: "Ubuntu 24.04",
    error: "",
  },
};

export const ENGINE_MISSING = {
  ok: false,
  state: "missing",
  usable: false,
  facts: [],
  limits: [{ code: "docker.engine.missing", params: { command: "noust setup init" }, text: "Docker is not installed on this server", hint: "Install it from Server > Software" }],
  info: { ...ENGINE_OK.info, installed: false, reachable: false, server_version: "" },
};

export function container(name: string, owner: DockerOwner, more: Partial<DockerContainer> = {}): DockerContainer {
  return {
    id: name.padEnd(12, "0"),
    name,
    image: "nginx:1.27",
    state: "running",
    status: "Up 2 hours",
    health: "",
    created: "2026-11-12T08:00:00Z",
    ports: "",
    mounts: [],
    networks: [],
    project: owner.project,
    service: "",
    working_dir: "",
    config_files: [],
    oneoff: false,
    instance: "",
    helper: "",
    state_kind: "running",
    problem: false,
    exit_code: null,
    owner,
    can: owner.kind === "nobody" ? CAN_ALL : CAN_OWNED,
    ...more,
  };
}

export function volume(name: string, owner: DockerOwner, more: Partial<DockerVolume> = {}): DockerVolume {
  return {
    name,
    driver: "local",
    scope: "local",
    mountpoint: `/var/lib/docker/volumes/${name}/_data`,
    project: owner.project,
    key: "",
    instance: "",
    size_bytes: 3_650_722_201,
    used_by: [],
    anonymous: false,
    owner,
    can: owner.kind === "nobody" ? CAN_ALL : CAN_OWNED,
    ...more,
  };
}

export function image(id: string, tags: string[], owner: DockerOwner, more: Partial<DockerImage> = {}): DockerImage {
  return { id, tags, created: "2026-11-01T10:00:00Z", size_bytes: 400_000_000, used_by: [], way_back: false, dangling: tags.length === 0, owner, can: owner.kind === "nobody" ? CAN_ALL : CAN_OWNED, update: null, ...more };
}

export function network(name: string, owner: DockerOwner, more: Partial<DockerNetwork> = {}): DockerNetwork {
  return { id: name.padEnd(12, "0"), name, driver: "bridge", scope: "local", internal: false, project: owner.project, created: "2026-11-01T10:00:00Z", containers: [], builtin: false, owner, can: owner.kind === "nobody" ? CAN_ALL : CAN_OWNED, ...more };
}

export function stack(name: string, owner: DockerOwner, more: Partial<DockerStack> = {}): DockerStack {
  return { name, working_dir: `/srv/${name}`, config_files: [], containers: [], total: 3, running: 3, workers: 3, state: "running", owner, can: owner.kind === "nobody" ? CAN_ALL : CAN_OWNED, ...more };
}

/** A list the way the server answers it: everything, with the engine that gave it. */
export function listOf<T>(items: readonly T[]): { available: true; engine: typeof ENGINE_OK; total: number; offset: number; limit: null; can_manage: true; items: readonly T[] } {
  return { available: true, engine: ENGINE_OK, total: items.length, offset: 0, limit: null, can_manage: true, items };
}

/** The routes of the zone for a machine with Docker, each overridable by a test. */
export function dockerRoutes(more: { containers?: DockerContainer[]; images?: DockerImage[]; volumes?: DockerVolume[]; networks?: DockerNetwork[]; stacks?: DockerStack[] } & Record<string, unknown> = {}): Record<string, RouteHandler> {
  const { containers = [], images = [], volumes = [], networks = [], stacks = [], ...rest } = more;
  return {
    "GET /api/docker/summary": () =>
      json(200, {
        available: true,
        can_manage: true,
        engine: ENGINE_OK,
        counts: { containers: { total: containers.length, running: containers.length, failing: 0, stopped: 0 }, stacks: { total: stacks.length, unowned: 0 }, images: { total: images.length }, volumes: { total: volumes.length }, networks: { total: networks.length } },
        attention: [],
        disk: { docker_bytes: 28_120_000_000, reclaimable_bytes: 0, free_bytes: null, total_bytes: null, path: "/var/lib/docker" },
      }),
    "GET /api/docker/engine": () => json(200, ENGINE_OK),
    "GET /api/docker/containers": () => json(200, listOf(containers)),
    "GET /api/docker/images": () => json(200, listOf(images)),
    "GET /api/docker/volumes": () => json(200, listOf(volumes)),
    "GET /api/docker/volumes/sizes": () => json(200, { sizes: {} }),
    "GET /api/docker/networks": () => json(200, listOf(networks)),
    "GET /api/docker/stacks": () => json(200, listOf(stacks)),
    ...(rest as Record<string, RouteHandler>),
  };
}
