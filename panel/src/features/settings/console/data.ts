/**
 * What the Console access page works out before it asks the server: which accounts would stop
 * the console from being published (an account that can sign in with a password alone), and
 * what the form sends. The server judges again, with the master token's own second factor and
 * the rest; this says early, and by name, what it can know from the accounts list.
 */

import type { ExposureBody } from "../../../api/queries/consoleExposure";
import { parseAddresses } from "../../../lib/addresses";
import { accountStatus } from "../accounts/data";
import type { Account } from "../accounts/api";

/** The usernames that can sign in and hold no authenticator or passkey, alphabetically. */
export function accountsWithoutSecondFactor(accounts: readonly Pick<Account, "username" | "status" | "locked_until" | "mfa_enabled">[], now: number = Date.now()): string[] {
  return accounts
    .filter((account) => accountStatus(account, now) === "active" && !account.mfa_enabled)
    .map((account) => account.username)
    .sort((a, b) => a.localeCompare(b));
}

export interface PublishForm {
  domain: string;
  allow: string;
  skipDns: boolean;
  forceEns: boolean;
}

export const EMPTY_FORM: PublishForm = { domain: "", allow: "", skipDns: false, forceEns: false };

/** The request the form becomes: the name as typed (the server normalises it) and the valid addresses. */
export function bodyOf(form: PublishForm): ExposureBody {
  return {
    domain: form.domain.trim(),
    allow_ips: parseAddresses(form.allow).entries,
    force_ens: form.forceEns,
    skip_dns_check: form.skipDns,
  };
}

export type FormProblem = "domain" | "allow" | "ens";

/** What stops the form from going to the server, if anything: the first problem, with the entries at fault. */
export function formProblem(form: PublishForm, ensAllows: boolean): { problem: FormProblem; entries: string[] } | null {
  if (form.domain.trim() === "") return { problem: "domain", entries: [] };
  const parsed = parseAddresses(form.allow);
  if (parsed.invalid.length > 0) return { problem: "allow", entries: parsed.invalid };
  // The profile refuses the console beyond loopback unless forced, and forcing needs a list.
  if (!ensAllows && form.forceEns && parsed.entries.length === 0) return { problem: "ens", entries: [] };
  return null;
}
