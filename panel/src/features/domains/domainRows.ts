/**
 * The Domains list: one row for each name a web server site answers, with the application that
 * owns the site and the certificate that covers the name. Derived from the two lists the page
 * already reads (sites and certificates), so there is no third source to keep in step with them.
 */

import type { CertEntry } from "../../api/queries/certs";
import type { SiteEntry } from "../../api/queries/sites";

export interface DomainRow {
  /** The name, as the web server answers it (`*.example.com` for a wildcard). */
  name: string;
  /** The site file that answers it. */
  site: string;
  /** The application whose site it is; null for a site somebody wrote by hand for no application. */
  app: string | null;
  enabled: boolean;
  /** The site serves TLS, whether or not a certificate of the list covers this name. */
  secure: boolean;
  /** The certificate that covers the name, when one of the list does. */
  cert: CertEntry | null;
}

/** A name nginx writes for "any other name": it answers nothing a person would call a domain. */
const CATCH_ALL = new Set(["_", "", "default_server"]);

/** Whether a wildcard certificate name covers a host: one label under it, never deeper and never the bare name. */
export function wildcardCovers(pattern: string, host: string): boolean {
  if (!pattern.startsWith("*.")) return false;
  const suffix = pattern.slice(1);
  if (!host.endsWith(suffix)) return false;
  const label = host.slice(0, host.length - suffix.length);
  return label !== "" && !label.includes(".");
}

function days(cert: CertEntry): number {
  return cert.days_remaining ?? Number.NEGATIVE_INFINITY;
}

/**
 * The certificate of the list that covers a name: one that lists it, else one whose wildcard
 * does. When several cover it, the one that lasts longest, because that is the one worth showing.
 */
export function certificateCovering(certs: readonly CertEntry[], name: string): CertEntry | null {
  const lists = (cert: CertEntry): boolean => cert.domain === name || cert.domains.includes(name);
  const wild = (cert: CertEntry): boolean => cert.domains.some((entry) => wildcardCovers(entry, name)) || wildcardCovers(cert.domain, name);
  let best: CertEntry | null = null;
  for (const cert of certs) {
    if (!lists(cert) && !wild(cert)) continue;
    if (best === null) {
      best = cert;
      continue;
    }
    // A certificate that lists the name outranks a wildcard; between equals, the longer-lived.
    const prefer = lists(cert) !== lists(best) ? lists(cert) : days(cert) > days(best);
    if (prefer) best = cert;
  }
  return best;
}

/** One row for each name the sites answer, by name; an enabled site wins a name two sites claim. */
export function domainRows(sites: readonly SiteEntry[], certs: readonly CertEntry[]): DomainRow[] {
  const byName = new Map<string, DomainRow>();
  for (const site of sites) {
    for (const raw of site.server_names) {
      const name = raw.trim().toLowerCase();
      if (CATCH_ALL.has(name)) continue;
      const kept = byName.get(name);
      if (kept !== undefined && (kept.enabled || !site.enabled)) continue;
      byName.set(name, {
        name,
        site: site.name,
        app: site.app ?? null,
        enabled: site.enabled,
        secure: site.has_ssl,
        cert: certificateCovering(certs, name),
      });
    }
  }
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** Whether a row answers a search: its name, its application or its site's file, whatever the case. */
export function matchesDomain(row: DomainRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (needle === "") return true;
  return row.name.includes(needle) || (row.app?.toLowerCase().includes(needle) ?? false) || row.site.toLowerCase().includes(needle);
}
