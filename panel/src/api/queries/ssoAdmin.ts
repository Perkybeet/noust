/**
 * Administering single sign-on (`/api/auth/oidc/config`, `/api/auth/oidc/identities`,
 * `/api/auth/oidc/local-login`): the providers an operator configures, the people linked to
 * them, and whether the password and passkey sign-in stays on beside them. Every rule - which
 * accounts must keep a password, what a provider may let in - is the server's; the console asks
 * and shows the answer, and a refusal is shown as the server worded it.
 *
 * The sign-in page's own, anonymous read of the providers is `sso.ts`.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type SsoConfig = ResponseOf<"/api/auth/oidc/config", "get">;
export type SsoProviderInfo = SsoConfig["providers"][number];
export type SsoProviderSettings = BodyOf<"/api/auth/oidc/config/{provider}", "put">;
export type SsoGroupMapping = NonNullable<SsoProviderSettings["group_mappings"]>[number];
export type SsoTestResult = ResponseOf<"/api/auth/oidc/config/{provider}/test", "post">;
export type SsoIdentities = ResponseOf<"/api/auth/oidc/identities", "get">;
export type SsoIdentity = SsoIdentities["identities"][number];
export type LocalSignIn = ResponseOf<"/api/auth/oidc/local-login", "get">;
export type LocalSignInBody = BodyOf<"/api/auth/oidc/local-login", "put">;

export const ssoAdminKeys = {
  all: ["auth", "sso-admin"] as const,
  config: ["auth", "sso-admin", "config"] as const,
  identities: ["auth", "sso-admin", "identities"] as const,
  localSignIn: ["auth", "sso-admin", "local-sign-in"] as const,
};

/** The providers, with their settings (never the secret: only whether one is kept), and the address to register. */
export const ssoConfigQuery = () =>
  queryOptions({
    queryKey: ssoAdminKeys.config,
    queryFn: ({ signal }) => request("get", "/api/auth/oidc/config", { signal }),
  });

/** Every person linked to a provider, by the pair the provider guarantees. */
export const ssoIdentitiesQuery = () =>
  queryOptions({
    queryKey: ssoAdminKeys.identities,
    queryFn: ({ signal }) => request("get", "/api/auth/oidc/identities", { signal }),
  });

/** Whether passwords and passkeys sign accounts in, who keeps them if not, and who could still administer. */
export const localSignInQuery = () =>
  queryOptions({
    queryKey: ssoAdminKeys.localSignIn,
    queryFn: ({ signal }) => request("get", "/api/auth/oidc/local-login", { signal }),
  });

/** Creates a provider or changes the settings named; what is left out stays. Needs "Confirm it's you". */
export function saveProvider(provider: string, body: SsoProviderSettings) {
  return request("put", "/api/auth/oidc/config/{provider}", { params: { provider }, body });
}

/** Removes a provider. The server refuses while that would leave nobody able to sign in. */
export function removeProvider(provider: string) {
  return request("delete", "/api/auth/oidc/config/{provider}", { params: { provider } });
}

/** Reads the provider's discovery document and keys; signs nobody in. */
export function testProvider(provider: string) {
  return request("post", "/api/auth/oidc/config/{provider}/test", { params: { provider } });
}

/** Links a person at a provider to an account by hand. */
export function linkIdentity(body: BodyOf<"/api/auth/oidc/identities", "post">) {
  return request("post", "/api/auth/oidc/identities", { body });
}

/** Unlinks a person: their sign-ins through the provider end at once. */
export function unlinkIdentity(id: number) {
  return request("delete", "/api/auth/oidc/identities/{identity_id}", { params: { identity_id: id } });
}

/** Turns the password and passkey sign-in off or on, and names the accounts that keep it. */
export function setLocalSignIn(body: LocalSignInBody) {
  return request("put", "/api/auth/oidc/local-login", { body });
}
