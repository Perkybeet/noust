import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type GitHubStatus = ResponseOf<"/api/integrations/github", "get">;
export type GitHubInstallation = GitHubStatus["installations"][number];
export type GitHubManifest = ResponseOf<"/api/integrations/github/manifest", "post">;
export type GitHubManifestBody = BodyOf<"/api/integrations/github/manifest", "post">;
export type GitHubRepositories = ResponseOf<"/api/integrations/github/repositories", "get">;
export type GitHubRepository = GitHubRepositories["items"][number];
export type GitHubBranches = ResponseOf<"/api/integrations/github/repositories/{owner}/{repo}/branches", "get">;
export type GitHubBranch = GitHubBranches["items"][number];

export const githubKeys = {
  all: ["integrations", "github"] as const,
  status: ["integrations", "github", "status"] as const,
  repositories: ["integrations", "github", "repositories"] as const,
  branches: (owner: string, repo: string) => ["integrations", "github", "branches", owner, repo] as const,
};

/** This server's GitHub App, its installations and whether GitHub can deliver its events. */
export const githubStatusQuery = () =>
  queryOptions({
    queryKey: githubKeys.status,
    queryFn: ({ signal }) => request("get", "/api/integrations/github", { signal }),
  });

/** Every repository the App's installations cover. Asks GitHub, so it is kept a while. */
export const githubRepositoriesQuery = () =>
  queryOptions({
    queryKey: githubKeys.repositories,
    queryFn: ({ signal }) => request("get", "/api/integrations/github/repositories", { signal }),
    staleTime: 60_000,
  });

/** A repository's branches, as GitHub orders them. */
export const githubBranchesQuery = (owner: string, repo: string) =>
  queryOptions({
    queryKey: githubKeys.branches(owner, repo),
    queryFn: ({ signal }) =>
      request("get", "/api/integrations/github/repositories/{owner}/{repo}/branches", { params: { owner, repo }, signal }),
    staleTime: 60_000,
  });

/** Starts creating the App: its manifest and the GitHub URL to post it to. Needs "Confirm it's you". */
export function startGitHubManifest(body: GitHubManifestBody) {
  return request("post", "/api/integrations/github/manifest", { body });
}

/** Finishes creating the App with the one-time code GitHub sent back. Needs "Confirm it's you". */
export function convertGitHubManifest(code: string, state: string) {
  return request("post", "/api/integrations/github/manifest/conversions", { body: { code, state } });
}

/** Records an installation GitHub's setup callback named. Needs "Confirm it's you". */
export function addGitHubInstallation(installationId: number) {
  return request("post", "/api/integrations/github/installations", { body: { installation_id: installationId } });
}

/** Makes the stored installations exactly those GitHub lists for the App. */
export function syncGitHubInstallations() {
  return request("post", "/api/integrations/github/installations/sync");
}

/** Forgets the App on this server; GitHub keeps it until it is deleted there. Needs "Confirm it's you". */
export function removeGitHubApp() {
  return request("delete", "/api/integrations/github");
}
