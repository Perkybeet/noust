/**
 * The address the console tells an operator an application answers on.
 *
 * An application keeps the name it was made with (its unit, its folder and its user are named
 * after it), so one made on a temporary address (`shop.203-0-113-10.sslip.io`) stays that app
 * after the operator adds their own name as an alias. What the page says is the name a person
 * would use: the first alias that is not temporary and that the certificate presenting it covers (the
 * application's, or the one a name holds of its own), else the name the application has. A name that was never temporary is the application's own and is
 * never replaced by an alias: an alias is another face of the same app, not a better name.
 */

import type { CertList } from "../../api/queries/certs";
import { certificateOf, covers } from "../domains/certificates";
import { findCertificate } from "./lookups";

/** The names an application answers on, as `GET /api/apps/{domain}/domains` lists them. */
export interface AnsweredName {
  domain: string;
  /** `primary`, `alias` or `redirect`. */
  kind: string;
  /** `own` when the name holds a certificate of its own; absent when the application's presents it. */
  certificate?: string | undefined;
}

export interface Address {
  /** The name to say. */
  name: string;
  /** Whether a certificate covers it: it is `https://`. */
  secure: boolean;
}

/** The suffix of the temporary names Noust offers (see `deployers/temporary_domain.py`). */
const TEMPORARY_SUFFIX = ".sslip.io";

/** Whether a name is a temporary address, made of the server's own IP address. */
export function isTemporaryName(name: string): boolean {
  return name.toLowerCase().endsWith(TEMPORARY_SUFFIX);
}

/**
 * The address to say for an application.
 *
 * @param domain The application's own name.
 * @param names The names it answers on; undefined while they are read.
 * @param certificates The certificates of the machine; undefined while they are read.
 */
export function displayAddress(domain: string, names: readonly AnsweredName[] | undefined, certificates: CertList | undefined): Address {
  const lineage = findCertificate(certificates, domain);
  // The certificate that presents a name is the app's, or the lineage kept under the name for one that holds its own.
  const secure = (entry: AnsweredName): boolean => covers(certificateOf(entry, lineage, (name) => findCertificate(certificates, name)), entry.domain);
  if (isTemporaryName(domain) && names !== undefined) {
    const own = names.find((entry) => entry.kind === "alias" && !isTemporaryName(entry.domain) && secure(entry));
    if (own !== undefined) return { name: own.domain, secure: true };
  }
  // Until the certificates are read, HTTPS: how Noust deploys by default.
  const itself = names?.find((entry) => entry.domain === domain) ?? { domain, kind: "primary" };
  return { name: domain, secure: certificates === undefined || secure(itself) };
}
