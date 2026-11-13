/**
 * What changes Docker through Noust (`/api/docker`, docs/docker.md): the new versions of the images in use and updating them with a net,
 * bringing a stack that nobody of Noust made to Noust, and the Compose document of a stack with its history. Nothing is asked of a
 * registry unless the operator asks, so the check is a job that a button starts.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type DockerImageUpdate = ResponseOf<"/api/docker/images/{image_id}/update", "get">;
export type DockerBring = ResponseOf<"/api/docker/stacks/{name}/bring", "post">;
export type DockerCompose = ResponseOf<"/api/docker/stacks/{name}/compose", "get">;
export type DockerComposeCheck = ResponseOf<"/api/docker/stacks/{name}/compose/check", "post">;
export type DockerComposeVersions = ResponseOf<"/api/docker/stacks/{name}/compose/versions", "get">;
export type DockerComposeVersion = NonNullable<DockerComposeVersions["items"]>[number];

export const dockerChangeKeys = {
  update: (id: string) => ["docker", "image", id, "update"] as const,
  compose: (name: string) => ["docker", "stack", name, "compose"] as const,
  versions: (name: string) => ["docker", "stack", name, "compose", "versions"] as const,
};

/** What updating one image would do, said before anything is: the plan the dialog shows. Read when asked, never kept. */
export const dockerImageUpdateQuery = (id: string) =>
  queryOptions({
    queryKey: dockerChangeKeys.update(id),
    queryFn: ({ signal }) => request("get", "/api/docker/images/{image_id}/update", { params: { image_id: id }, signal }),
    staleTime: 0,
    gcTime: 0,
  });

/**
 * A stack's Compose document. Reading it is a reveal (it can hold a value somebody typed into it) and is recorded, so it is asked for
 * when the editor opens and never kept: it asks again each time.
 */
export const dockerComposeQuery = (name: string) =>
  queryOptions({
    queryKey: dockerChangeKeys.compose(name),
    queryFn: ({ signal }) => request("get", "/api/docker/stacks/{name}/compose", { params: { name }, signal }),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });

/** The versions saved of a stack's document, without their text. */
export const dockerComposeVersionsQuery = (name: string) =>
  queryOptions({
    queryKey: dockerChangeKeys.versions(name),
    queryFn: ({ signal }) => request("get", "/api/docker/stacks/{name}/compose/versions", { params: { name }, signal }),
  });

/** Starts the look at the registries. It is a job, and it is the operator's to ask: nothing here asks a registry by itself. */
export function checkImages(pull: boolean) {
  return request("post", "/api/docker/images/check", { body: { pull } });
}
