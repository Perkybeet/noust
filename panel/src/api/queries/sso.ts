import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type SsoProviders = ResponseOf<"/api/auth/oidc/providers", "get">;
export type SsoProvider = SsoProviders["providers"][number];
export type SsoCompleteBody = BodyOf<"/api/auth/oidc/complete", "post">;
export type SsoCompleted = ResponseOf<"/api/auth/oidc/complete", "post">;

export const ssoKeys = {
  providers: ["auth", "sso", "providers"] as const,
};

const NONE: SsoProviders = { providers: [] };

/**
 * The identity providers the sign-in page offers, answered before anyone is signed in. A server
 * that cannot say (an old one, one that is unreachable) offers none: the page's own session
 * request is what reports an unreachable server, and a missing button must not hide the form.
 */
export const ssoProvidersQuery = () =>
  queryOptions({
    queryKey: ssoKeys.providers,
    queryFn: ({ signal }) => request("get", "/api/auth/oidc/providers", { signal }).catch((): SsoProviders => NONE),
    staleTime: 60_000,
  });

/**
 * Where the button goes: a plain navigation, because the browser leaves for the provider and
 * comes back to the server, which sends it to the sign-in page again with what to do next.
 */
export function ssoStartHref(provider: string, next: string): string {
  const query = new URLSearchParams({ provider });
  if (next !== "/") query.set("next", next);
  return `/api/auth/oidc/start?${query.toString()}`;
}

/** Collects the single-use ticket the provider's answer left: the sign-in goes on from there. */
export function completeSso(body: SsoCompleteBody) {
  return request("post", "/api/auth/oidc/complete", { body });
}
