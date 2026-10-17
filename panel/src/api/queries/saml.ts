/**
 * The SAML identity providers people sign in with (`/api/auth/saml/config`): who they are, the
 * certificates that verify them, and what a first sign-in does. Nothing in a provider is secret
 * (its certificates are the public half of the keys that sign), and every rule is the server's: a
 * refusal is shown as it worded it.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type SamlProviders = ResponseOf<"/api/auth/saml/config", "get">;
export type SamlProvider = SamlProviders["providers"][number];
export type SamlSettings = BodyOf<"/api/auth/saml/config/{provider}", "put">;
export type SamlTest = ResponseOf<"/api/auth/saml/config/{provider}/test", "post">;

export const samlKeys = {
  all: ["auth", "saml"] as const,
  providers: ["auth", "saml", "providers"] as const,
};

/** The providers, with their settings, the certificates described, and the address to register. */
export const samlProvidersQuery = () =>
  queryOptions({
    queryKey: samlKeys.providers,
    queryFn: ({ signal }) => request("get", "/api/auth/saml/config", { signal }),
  });

/** Creates a provider or changes the settings named; what is left out stays. Needs "Confirm it's you". */
export function saveSamlProvider(provider: string, body: SamlSettings) {
  return request("put", "/api/auth/saml/config/{provider}", { params: { provider }, body });
}

/** Removes a provider. The server refuses while that would leave nobody able to sign in. */
export function removeSamlProvider(provider: string) {
  return request("delete", "/api/auth/saml/config/{provider}", { params: { provider } });
}

/** Looks at the provider's certificates and addresses the way a sign-in would; signs nobody in. */
export function testSamlProvider(provider: string) {
  return request("post", "/api/auth/saml/config/{provider}/test", { params: { provider } });
}
