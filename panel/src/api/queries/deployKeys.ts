import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type ServerKey = ResponseOf<"/api/deploy-keys/server", "get">;

export const deployKeyKeys = {
  server: ["deploy-keys", "server"] as const,
};

/**
 * The public half of the key this server signs in to code hosts with, for a repository that has
 * no deploy key of its own. Read while a private repository is being connected, before the
 * application it will belong to exists.
 */
export const serverKeyQuery = () =>
  queryOptions({
    queryKey: deployKeyKeys.server,
    queryFn: ({ signal }) => request("get", "/api/deploy-keys/server", { signal }),
    staleTime: 30_000,
  });

/**
 * Makes the server's key when it has none (sudo mode: it writes the server's SSH identity), and
 * answers with the key, which a server that already has one gets back unchanged.
 */
export function createServerKey(): Promise<ServerKey> {
  return request("post", "/api/deploy-keys/server");
}
