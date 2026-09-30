/**
 * The search params of the Domains and certificates tabs, apart from the pages themselves so a
 * route's `validateSearch` does not pull in a whole tab - importing anything from a module runs
 * the whole of it, heavy imports included.
 */

export type CertificateFilter = "attention";

export interface CertificatesSearch {
  /**
   * What the certificates are filtered by: a link from a health report or an alert about one
   * certificate lands on that certificate alone (`/domains?q=example.com`).
   */
  q?: string;
  /** Only the certificates that expire soon or have expired. */
  show?: CertificateFilter;
  /**
   * 3.0's tab parameter, which links from 3.0 and the CLI still carry: `sites` now has an
   * address of its own, which the route redirects to; `certificates` is this tab.
   */
  tab?: "sites" | "certificates";
}

export interface SitesSearch {
  q?: string;
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, 200);
}

/** The certificates tab's params; `tab=certificates`, 3.0's default, simply lands here. */
export function validateCertificatesSearch(search: Record<string, unknown>): CertificatesSearch {
  const q = text(search["q"]);
  return {
    ...(q !== undefined ? { q } : {}),
    ...(search["show"] === "attention" ? { show: "attention" as const } : {}),
    ...(search["tab"] === "sites" || search["tab"] === "certificates" ? { tab: search["tab"] } : {}),
  };
}

export function validateSitesSearch(search: Record<string, unknown>): SitesSearch {
  const q = text(search["q"]);
  return q !== undefined ? { q } : {};
}
