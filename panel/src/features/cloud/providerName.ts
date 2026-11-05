import type { T } from "../../i18n";
import type { ProviderKind } from "./data";

/**
 * A provider in words; one a newer server knows and this console does not is shown as the server names it. It lives
 * apart from `data.ts`, which the fleet's pages import, so that those pages do not wait for the texts of the cloud's.
 */
export function providerName(t: T, provider: string): string {
  return provider === "hetzner" || provider === "digitalocean" || provider === "vultr" ? t(`cloud.provider.${provider satisfies ProviderKind}.name`) : provider;
}
