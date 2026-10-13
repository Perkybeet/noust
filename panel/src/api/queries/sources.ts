import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

/** What an application's repository is reached with over SSH: its deploy key, never its private half. */
export type AppDeployKey = ResponseOf<"/api/apps/{domain}/deploy-key", "get">;
export type DeployKey = ResponseOf<"/api/deploy-keys", "get">["keys"][number];
export type DeployKeyList = ResponseOf<"/api/deploy-keys", "get">;
export type PinnedHostList = ResponseOf<"/api/deploy-keys/hosts", "get">;
export type PinnedHost = PinnedHostList["hosts"][number];
export type DeployKeyImportBody = BodyOf<"/api/apps/{domain}/deploy-key/import", "post">;
export type DeployKeyCreateBody = BodyOf<"/api/apps/{domain}/deploy-key", "post">;

/** How an application's code is fetched, and which pushes deploy it. */
export type SourceOptions = ResponseOf<"/api/apps/{domain}/source-options", "get">;
export type SourceOptionsBody = BodyOf<"/api/apps/{domain}/source-options", "put">;

/** What an application's repository is reached with over https: whether a credential is kept, never the token. */
export type GitCredential = ResponseOf<"/api/apps/{domain}/git-credential", "get">;
export type GitCredentialBody = BodyOf<"/api/apps/{domain}/git-credential", "put">;

/**
 * Under the app's own prefix (`["app", domain, ...]`), like everything read about one app, so
 * a job on the app finishing refreshes these with the rest. The server-wide list is its own
 * entry: a key made for one app shows up in it, and the other way round.
 */
export const sourceKeys = {
  deployKey: (domain: string) => ["app", domain, "deploy-key"] as const,
  gitCredential: (domain: string) => ["app", domain, "git-credential"] as const,
  sourceOptions: (domain: string) => ["app", domain, "source-options"] as const,
  deployKeys: ["deploy-keys"] as const,
  pinnedHosts: ["deploy-keys", "hosts"] as const,
};

/** The key an application's repository is reached with, the one it falls back to, and what to know about it. */
export const deployKeyQuery = (domain: string) =>
  queryOptions({
    queryKey: sourceKeys.deployKey(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/deploy-key", { params: { domain }, signal }),
  });

/** Whether a user and token are kept for the application's repository: the user, never the token. */
export const gitCredentialQuery = (domain: string) =>
  queryOptions({
    queryKey: sourceKeys.gitCredential(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/git-credential", { params: { domain }, signal }),
  });

/** Submodules, LFS, shallow clone, skip markers and watch paths of one application. */
export const sourceOptionsQuery = (domain: string) =>
  queryOptions({
    queryKey: sourceKeys.sourceOptions(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/source-options", { params: { domain }, signal }),
  });

/** Every deploy key on the server and the applications each reaches. */
export const deployKeysQuery = () =>
  queryOptions({
    queryKey: sourceKeys.deployKeys,
    queryFn: ({ signal }) => request("get", "/api/deploy-keys", { signal }),
  });

/** The host keys Noust pinned the first time it reached each code host. */
export const pinnedHostsQuery = () =>
  queryOptions({
    queryKey: sourceKeys.pinnedHosts,
    queryFn: ({ signal }) => request("get", "/api/deploy-keys/hosts", { signal }),
  });
