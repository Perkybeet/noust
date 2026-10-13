/**
 * Shared variables (`/api/teams/{team}/variables`, `.../projects/{project}/variables`,
 * `.../environments/{environment}/variables` and `/api/variables/server`) and the secret
 * managers they can refer to (`/api/secret-providers`).
 *
 * The four scopes answer with one shape and take one body, so the console holds them as one
 * thing: a `VariableScope` says whose they are, and `variablesApi` is the only place that knows
 * which URL each scope lives at. Every rule (who may read a scope, what a reference may name,
 * whether a secret is shown) is the server's.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type SharedVariables = ResponseOf<"/api/teams/{team}/variables", "get">;
export type SharedVariable = SharedVariables["variables"][number];
export type SharedVariableBody = BodyOf<"/api/teams/{team}/variables/{name}", "put">;
export type VariableUsage = ResponseOf<"/api/teams/{team}/variables/{name}/usage", "get">;
export type VariableDeleted = ResponseOf<"/api/teams/{team}/variables/{name}", "delete">;

export type SecretProviders = ResponseOf<"/api/secret-providers", "get">;
export type SecretProvider = SecretProviders["providers"][number];
export type SecretProviderKinds = ResponseOf<"/api/secret-providers/kinds", "get">;
export type SecretProviderKind = SecretProviderKinds["kinds"][number];
export type SecretProviderBody = BodyOf<"/api/secret-providers/{name}", "put">;

/**
 * Whose variables: the server's, a team's, a project's, one of its environments', or the set
 * an application defines for its previews alone (the same rows and the same dialog, at other
 * URLs; it has no references to other scopes and nobody refers to it).
 */
export type VariableScope =
  | { kind: "preview"; domain: string }
  | { kind: "server" }
  | { kind: "team"; team: string }
  | { kind: "project"; team: string; project: string }
  | { kind: "environment"; team: string; project: string; environment: string };

/** Which part of a deployment a variable is given to. */
export type VariableAudience = "both" | "build" | "runtime";

export const AUDIENCES: readonly VariableAudience[] = ["both", "build", "runtime"];

/** The audience a server answered, or "both" for a word this console does not know. */
export function audienceOf(word: string | null | undefined): VariableAudience {
  return word === "build" || word === "runtime" ? word : "both";
}

/** Keys of the shared variable queries, one entry per scope so a change reads only that scope again. */
export const variableKeys = {
  all: ["variables"] as const,
  scope: (scope: VariableScope) => ["variables", scope] as const,
  of: (scope: VariableScope, unmask = false) => ["variables", scope, { unmask }] as const,
  usage: (scope: VariableScope, name: string) => ["variables", scope, "usage", name] as const,
  providers: ["secret-providers"] as const,
  kinds: ["secret-providers", "kinds"] as const,
};

/**
 * The calls on one scope. A function per verb rather than a table of paths: the typed client
 * wants each path as a literal.
 */
export function variablesApi(scope: VariableScope) {
  switch (scope.kind) {
    case "preview": {
      const { domain } = scope;
      const asShared = (answer: ResponseOf<"/api/apps/{domain}/previews/variables", "get">, unmasked: boolean): SharedVariables => ({
        scope: "preview",
        label: answer.domain,
        unmasked,
        syntax: "{{app.URL}}",
        variables: answer.variables.map((variable) => ({
          name: variable.name,
          value: variable.value,
          secrecy: { secret: variable.secret, marked: variable.marked ?? false, reason: "" },
          audience: variable.audience,
          references: [],
          updated_at: variable.updated_at ?? "",
          updated_by: null,
        })),
      });
      return {
        list: async (unmask: boolean, signal?: AbortSignal) =>
          asShared(await request("get", "/api/apps/{domain}/previews/variables", { params: { domain }, query: { unmask }, ...(signal ? { signal } : {}) }), unmask),
        set: async (name: string, body: SharedVariableBody) =>
          asShared(await request("put", "/api/apps/{domain}/previews/variables/{name}", { params: { domain, name }, body }), false),
        remove: async (name: string): Promise<VariableDeleted> => {
          await request("delete", "/api/apps/{domain}/previews/variables/{name}", { params: { domain, name } });
          return { name, applications: [] };
        },
        // Nobody refers to a preview's variable: they are copied, not referenced.
        usage: async (name: string): Promise<VariableUsage> => ({ name, applications: [] }),
      };
    }
    case "server":
      return {
        list: (unmask: boolean, signal?: AbortSignal) => request("get", "/api/variables/server", { query: { unmask }, ...(signal ? { signal } : {}) }),
        set: (name: string, body: SharedVariableBody) => request("put", "/api/variables/server/{name}", { params: { name }, body }),
        remove: (name: string) => request("delete", "/api/variables/server/{name}", { params: { name } }),
        usage: (name: string, signal?: AbortSignal) => request("get", "/api/variables/server/{name}/usage", { params: { name }, ...(signal ? { signal } : {}) }),
      };
    case "team": {
      const { team } = scope;
      return {
        list: (unmask: boolean, signal?: AbortSignal) => request("get", "/api/teams/{team}/variables", { params: { team }, query: { unmask }, ...(signal ? { signal } : {}) }),
        set: (name: string, body: SharedVariableBody) => request("put", "/api/teams/{team}/variables/{name}", { params: { team, name }, body }),
        remove: (name: string) => request("delete", "/api/teams/{team}/variables/{name}", { params: { team, name } }),
        usage: (name: string, signal?: AbortSignal) => request("get", "/api/teams/{team}/variables/{name}/usage", { params: { team, name }, ...(signal ? { signal } : {}) }),
      };
    }
    case "project": {
      const { team, project } = scope;
      return {
        list: (unmask: boolean, signal?: AbortSignal) =>
          request("get", "/api/teams/{team}/projects/{project}/variables", { params: { team, project }, query: { unmask }, ...(signal ? { signal } : {}) }),
        set: (name: string, body: SharedVariableBody) => request("put", "/api/teams/{team}/projects/{project}/variables/{name}", { params: { team, project, name }, body }),
        remove: (name: string) => request("delete", "/api/teams/{team}/projects/{project}/variables/{name}", { params: { team, project, name } }),
        usage: (name: string, signal?: AbortSignal) =>
          request("get", "/api/teams/{team}/projects/{project}/variables/{name}/usage", { params: { team, project, name }, ...(signal ? { signal } : {}) }),
      };
    }
    case "environment": {
      const { team, project, environment } = scope;
      return {
        list: (unmask: boolean, signal?: AbortSignal) =>
          request("get", "/api/teams/{team}/projects/{project}/environments/{environment}/variables", {
            params: { team, project, environment },
            query: { unmask },
            ...(signal ? { signal } : {}),
          }),
        set: (name: string, body: SharedVariableBody) =>
          request("put", "/api/teams/{team}/projects/{project}/environments/{environment}/variables/{name}", { params: { team, project, environment, name }, body }),
        remove: (name: string) =>
          request("delete", "/api/teams/{team}/projects/{project}/environments/{environment}/variables/{name}", { params: { team, project, environment, name } }),
        usage: (name: string, signal?: AbortSignal) =>
          request("get", "/api/teams/{team}/projects/{project}/environments/{environment}/variables/{name}/usage", {
            params: { team, project, environment, name },
            ...(signal ? { signal } : {}),
          }),
      };
    }
  }
}

/** A scope's variables, secrets masked. */
export const sharedVariablesQuery = (scope: VariableScope) =>
  queryOptions({
    queryKey: variableKeys.of(scope),
    queryFn: ({ signal }) => variablesApi(scope).list(false, signal),
  });

/** The applications whose variables refer to one of a scope's, among those the principal sees. */
export const variableUsageQuery = (scope: VariableScope, name: string) =>
  queryOptions({
    queryKey: variableKeys.usage(scope, name),
    queryFn: ({ signal }) => variablesApi(scope).usage(name, signal),
    staleTime: 0,
  });

/** The secret managers this server fetches from. Tokens are never in the answer. */
export const secretProvidersQuery = () =>
  queryOptions({
    queryKey: variableKeys.providers,
    queryFn: ({ signal }) => request("get", "/api/secret-providers", { signal }),
  });

/** The kinds of secret manager Noust can fetch from, and the settings each takes. */
export const secretProviderKindsQuery = () =>
  queryOptions({
    queryKey: variableKeys.kinds,
    queryFn: ({ signal }) => request("get", "/api/secret-providers/kinds", { signal }),
    staleTime: Infinity,
  });
