/**
 * What a certificate's expiry means to the operator, decided once for the certificates table,
 * its drawer and an app's Domains tab. Pure, so every threshold is tested without a page.
 */

import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";
import type { Locale } from "../../i18n";
import type { CertEntry } from "../../api/queries/certs";
import type { Job } from "../../api/queries/jobs";
import { CERT_WARNING_DAYS } from "../overview/attention";

export { CERT_WARNING_DAYS };

export type CertTone = "ok" | "warn" | "fail" | "idle" | "busy";

export interface CertificateView {
  tone: CertTone;
  /** The state in words, for a table cell: "Valid for 46 days", "Expires in 12 days". */
  label: string;
  /** True when the operator should act: renew soon or now. */
  attention: boolean;
}

function days(count: number, locale: Locale): string {
  return translate(locale, "domains.certificates.day", { count });
}

/** The state of a certificate from the days it has left. */
export function certificateView(cert: Pick<CertEntry, "days_remaining">, locale: Locale = getLocale()): CertificateView {
  const left = cert.days_remaining;
  if (left === null || left === undefined) return { tone: "idle", label: translate(locale, "domains.certificates.expiryUnknown"), attention: false };
  if (left < 0) {
    return {
      tone: "fail",
      label:
        left === -1
          ? translate(locale, "domains.certificates.expiredYesterday")
          : translate(locale, "domains.certificates.expiredAgo", { days: days(-left, locale) }),
      attention: true,
    };
  }
  if (left === 0) return { tone: "warn", label: translate(locale, "domains.certificates.expiresToday"), attention: true };
  if (left < CERT_WARNING_DAYS) return { tone: "warn", label: translate(locale, "domains.certificates.expiresIn", { days: days(left, locale) }), attention: true };
  return { tone: "ok", label: translate(locale, "domains.certificates.validFor", { days: days(left, locale) }), attention: false };
}

/** Most urgent first: expired, then the fewest days left; unknown expiry last. */
export function byUrgency(a: Pick<CertEntry, "days_remaining">, b: Pick<CertEntry, "days_remaining">): number {
  const left = (cert: Pick<CertEntry, "days_remaining">): number => cert.days_remaining ?? Number.POSITIVE_INFINITY;
  return left(a) - left(b);
}

/** The certificate jobs the backend runs (its JobType values). */
export const CERT_JOB_TYPES: ReadonlySet<string> = new Set(["cert_create", "cert_renew"]);

const RUNNING = new Set(["pending", "running"]);

/**
 * The certificate job queued or running for a certificate name, if any. Jobs name their
 * certificate in `metadata.domain`; renewing every certificate names "all".
 */
export function certificateJobFor(jobs: readonly Job[] | undefined, name: string): Job | null {
  return (
    jobs?.find(
      (job) =>
        CERT_JOB_TYPES.has(job.type) &&
        RUNNING.has(job.status) &&
        (job.metadata?.["domain"] === name || job.metadata?.["domain"] === "all"),
    ) ?? null
  );
}

/**
 * A certificate's issuer, the way an operator reads it, from the distinguished name openssl
 * prints ("C = US, O = Let's Encrypt, CN = R11"): the organisation and common name, in that
 * order ("Let's Encrypt R11"). Falls back to the raw string when it does not parse as a DN, so
 * nothing is ever hidden.
 */
export function issuerName(distinguishedName: string): string {
  const fields = new Map<string, string>();
  for (const part of distinguishedName.split(",")) {
    const at = part.indexOf("=");
    if (at === -1) continue;
    const key = part.slice(0, at).trim().toUpperCase();
    const value = part.slice(at + 1).trim();
    if (key !== "" && value !== "") fields.set(key, value);
  }
  const named = [fields.get("O"), fields.get("CN")].filter((part): part is string => part !== undefined);
  return named.length > 0 ? named.join(" ") : distinguishedName;
}

/** Whether a certificate lists a name among the ones it covers. */
export function covers(cert: Pick<CertEntry, "domain" | "domains"> | null | undefined, name: string): boolean {
  if (!cert) return false;
  return cert.domain === name || cert.domains.includes(name);
}

/**
 * What the certificate does for one name of an app: covers it (with the certificate's own state),
 * does not (the name is served over HTTP until the certificate is extended), or is being
 * extended to it. `lineage` is the app's certificate: undefined while unknown, null when it has
 * none.
 */
export function coverageOf(
  name: string,
  lineage: CertEntry | null | undefined,
  extending: boolean,
  locale: Locale = getLocale(),
): { tone: CertTone; label: string } {
  if (lineage === undefined) return { tone: "idle", label: translate(locale, "domains.certificates.checking") };
  if (lineage === null) return { tone: "idle", label: translate(locale, "domains.certificates.noCertificateHttpOnly") };
  if (!covers(lineage, name)) {
    return extending
      ? { tone: "busy", label: translate(locale, "domains.certificates.extendingCertificate") }
      : { tone: "warn", label: translate(locale, "domains.certificates.notCovered") };
  }
  const view = certificateView(lineage, locale);
  return { tone: view.tone, label: view.tone === "ok" ? translate(locale, "domains.certificates.covered") : view.label };
}
