import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

/** The GitLab connections this server holds, and the OAuth applications registered for them. */
export type GitLabStatus = ResponseOf<"/api/integrations/gitlab", "get">;
export type GitLabConnection = GitLabStatus["connections"][number];
export type GitLabOAuthApp = GitLabStatus["oauth_apps"][number];
export type GitLabProjects = ResponseOf<"/api/integrations/gitlab/connections/{name}/projects", "get">;
export type GitLabProject = GitLabProjects["items"][number];
export type GitLabGroups = ResponseOf<"/api/integrations/gitlab/connections/{name}/groups", "get">;
export type GitLabBranches = ResponseOf<"/api/integrations/gitlab/connections/{name}/projects/{project}/branches", "get">;
export type GitLabConnectBody = BodyOf<"/api/integrations/gitlab/connections", "post">;
export type GitLabOAuthAppBody = BodyOf<"/api/integrations/gitlab/oauth-apps", "put">;

export const gitlabKeys = {
  all: ["integrations", "gitlab"] as const,
  status: ["integrations", "gitlab", "status"] as const,
  groups: (connection: string) => ["integrations", "gitlab", "groups", connection] as const,
  projects: (connection: string, group: string, search: string) => ["integrations", "gitlab", "projects", connection, group, search] as const,
  branches: (connection: string, project: string) => ["integrations", "gitlab", "branches", connection, project] as const,
};

/** The connections and OAuth applications, and where GitLab sends the browser back. */
export const gitlabStatusQuery = () =>
  queryOptions({
    queryKey: gitlabKeys.status,
    queryFn: ({ signal }) => request("get", "/api/integrations/gitlab", { signal }),
  });

/** The groups a connection can see. Asks GitLab, so it is kept a while. */
export const gitlabGroupsQuery = (connection: string) =>
  queryOptions({
    queryKey: gitlabKeys.groups(connection),
    queryFn: ({ signal }) => request("get", "/api/integrations/gitlab/connections/{name}/groups", { params: { name: connection }, signal }),
    staleTime: 60_000,
  });

/** The projects a connection can see, optionally in one group and matching a search. */
export const gitlabProjectsQuery = (connection: string, group: string, search: string) =>
  queryOptions({
    queryKey: gitlabKeys.projects(connection, group, search),
    queryFn: ({ signal }) =>
      request("get", "/api/integrations/gitlab/connections/{name}/projects", {
        params: { name: connection },
        query: { ...(group === "" ? {} : { group }), ...(search === "" ? {} : { search }) },
        signal,
      }),
    staleTime: 30_000,
  });

/** A project's branches, the default one marked. `project` is its id or its full path. */
export const gitlabBranchesQuery = (connection: string, project: string) =>
  queryOptions({
    queryKey: gitlabKeys.branches(connection, project),
    queryFn: ({ signal }) =>
      request("get", "/api/integrations/gitlab/connections/{name}/projects/{project}/branches", { params: { name: connection, project }, signal }),
    staleTime: 60_000,
  });

/** Connects with an access token, checked against GitLab first. Needs "Confirm it's you". */
export function connectGitLab(body: GitLabConnectBody) {
  return request("post", "/api/integrations/gitlab/connections", { body });
}

/** Asks GitLab whether the connection still works and records the answer. Needs "Confirm it's you". */
export function testGitLabConnection(name: string) {
  return request("post", "/api/integrations/gitlab/connections/{name}/test", { params: { name } });
}

/** Forgets a connection and its tokens. Needs "Confirm it's you". */
export function removeGitLabConnection(name: string) {
  return request("delete", "/api/integrations/gitlab/connections/{name}", { params: { name } });
}

/** Records the OAuth application registered in GitLab for a host. Needs "Confirm it's you". */
export function saveGitLabOAuthApp(body: GitLabOAuthAppBody) {
  return request("put", "/api/integrations/gitlab/oauth-apps", { body });
}

/** Forgets a host's OAuth application. Needs "Confirm it's you". */
export function removeGitLabOAuthApp(host: string) {
  return request("delete", "/api/integrations/gitlab/oauth-apps/{host}", { params: { host } });
}

/** Starts signing in with GitLab: where to send the browser. Needs "Confirm it's you". */
export function startGitLabSignIn(name: string, host: string) {
  return request("post", "/api/integrations/gitlab/oauth/authorize", { body: { name, host } });
}

/** Finishes signing in with the `code` and `state` GitLab sent the browser back with. Needs "Confirm it's you". */
export function finishGitLabSignIn(code: string, state: string) {
  return request("post", "/api/integrations/gitlab/oauth/callback", { body: { code, state } });
}
