/**
 * What the Accounts page reads beyond the raw API shape: an account's state in the console's
 * words, its second factor, the list's filters (kept in the URL) and the people whose
 * accounts hold roles that should not go together.
 */

import type { Status } from "../../../components/ui/StatusPill";
import type { T } from "../../../i18n";
import { formatClock } from "../../../lib/format";
import type { Account } from "./api";
import { isRole } from "./roles";
import type { Role } from "./roles";

export type AccountStatus = "active" | "locked" | "disabled" | "invited";

export interface AccountsSearch {
  q?: string;
  role?: Role;
  state?: AccountStatus;
}

const STATES: readonly AccountStatus[] = ["active", "locked", "disabled", "invited"];

function isState(value: unknown): value is AccountStatus {
  return typeof value === "string" && (STATES as readonly string[]).includes(value);
}

export function validateAccountsSearch(search: Record<string, unknown>): AccountsSearch {
  const q = typeof search["q"] === "string" && search["q"] !== "" ? search["q"] : undefined;
  const role = typeof search["role"] === "string" && isRole(search["role"]) ? search["role"] : undefined;
  const state = isState(search["state"]) ? search["state"] : undefined;
  return { ...(q !== undefined ? { q } : {}), ...(role !== undefined ? { role } : {}), ...(state !== undefined ? { state } : {}) };
}

export function isFiltered(search: AccountsSearch): boolean {
  return search.q !== undefined || search.role !== undefined || search.state !== undefined;
}

/** The state an account is in right now: a lock that has run out is not a lock. */
export function accountStatus(account: Pick<Account, "status" | "locked_until">, now: number = Date.now()): AccountStatus {
  if (account.status === "locked" && account.locked_until !== null && account.locked_until !== undefined && account.locked_until * 1000 <= now) return "active";
  return isState(account.status) ? account.status : "active";
}

export function filterAccounts(accounts: readonly Account[], search: AccountsSearch, now: number = Date.now()): Account[] {
  const needle = search.q?.trim().toLowerCase() ?? "";
  return accounts
    .filter((account) => (search.role === undefined ? true : account.role === search.role))
    .filter((account) => (search.state === undefined ? true : accountStatus(account, now) === search.state))
    .filter((account) =>
      needle === ""
        ? true
        : [account.username, account.display_name, account.person_ref ?? ""].some((value) => value.toLowerCase().includes(needle)),
    )
    .sort((a, b) => a.username.localeCompare(b.username));
}

export interface StateView {
  /** A pill state, or null for "active": being able to sign in is not a running state. */
  state: Status | null;
  label: string;
}

/** An account's state in the state language: waiting, a problem, stopped - or plainly active. */
export function stateView(t: T, account: Pick<Account, "status" | "locked_until">, now: number = Date.now()): StateView {
  switch (accountStatus(account, now)) {
    case "invited":
      return { state: "queued", label: t("accounts.state.invited") };
    case "locked":
      return {
        state: "warning",
        label:
          account.locked_until !== null && account.locked_until !== undefined
            ? t("accounts.state.lockedUntil", { time: formatClock(new Date(account.locked_until * 1000), t.locale) })
            : t("accounts.state.locked"),
      };
    case "disabled":
      return { state: "stopped", label: t("accounts.state.disabled") };
    case "active":
      return { state: null, label: t("accounts.state.active") };
  }
}

/**
 * The second factor, in words: "Authenticator app", "2 passkeys", "Not set up". With passkeys,
 * whether an authenticator is also enrolled is not in the list's answer; the passkeys are what
 * it says for certain.
 */
export function factorLabel(t: T, account: Pick<Account, "mfa_enabled" | "passkeys">): string {
  const { passkeys } = account;
  if (!account.mfa_enabled) return t("accounts.factor.none");
  return passkeys > 0 ? t("accounts.factor.passkeys", { count: passkeys }) : t("accounts.factor.app");
}

/** Accounts that belong to the same person as this one, by `person_ref`. */
export function samePerson(accounts: readonly Account[], account: Pick<Account, "username" | "person_ref">): Account[] {
  const person = account.person_ref?.trim().toLowerCase() ?? "";
  if (person === "") return [];
  return accounts.filter((other) => other.username !== account.username && (other.person_ref?.trim().toLowerCase() ?? "") === person);
}
