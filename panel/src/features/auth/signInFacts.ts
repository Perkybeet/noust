/**
 * "Your last sign-in was ... and N attempts failed since": said once, right after a person
 * signs in (ENS op.acc.6.r5.2), so someone else's use of their account does not go unnoticed.
 *
 * The sign-in answer carries the facts; the console is not on screen yet (the checks after
 * sign-in may come first), so they wait here, in memory, until the console shows them once.
 */

import { useEffect } from "react";

import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatMoment, parseTimestamp } from "../../lib/format";

/** The members of a sign-in answer (`LoginResponse`) this needs. */
export interface SignInFacts {
  account?: { username: string; display_name: string } | null;
  previous_login_at?: string | null;
  previous_login_ip?: string | null;
  failures_since?: number | null;
  last_failure_at?: string | null;
  last_failure_ip?: string | null;
}

let pending: SignInFacts | null = null;

/** Keeps a person's sign-in facts for the console to say once. The master token has none. */
export function rememberSignIn(answer: SignInFacts): void {
  pending = answer.account ? answer : null;
}

/** Takes the facts, once. */
export function takeSignInFacts(): SignInFacts | null {
  const facts = pending;
  pending = null;
  return facts;
}

function moment(t: T, value: string | null | undefined): string | null {
  const date = parseTimestamp(value ?? null);
  return date === null ? null : formatMoment(date, t.locale);
}

/** The toast's words, for a test to read without a toast. */
export function describeSignIn(t: T, facts: SignInFacts): { kind: "info" | "warning"; title: string; description: string } {
  const when = moment(t, facts.previous_login_at);
  const last =
    when === null
      ? t("auth.facts.firstSignIn")
      : t("auth.facts.lastSignIn", { when, address: facts.previous_login_ip ?? t("auth.facts.unknownAddress") });
  const failures = facts.failures_since ?? 0;
  if (failures > 0) {
    const failedAt = moment(t, facts.last_failure_at);
    return {
      kind: "warning",
      title: t("auth.facts.failures", { count: failures }),
      description: `${last} ${
        failedAt === null
          ? t("auth.facts.failuresHint")
          : t("auth.facts.lastFailure", { when: failedAt, address: facts.last_failure_ip ?? t("auth.facts.unknownAddress") })
      }`,
    };
  }
  const display = facts.account?.display_name ?? "";
  const name = display !== "" ? display : (facts.account?.username ?? "");
  return { kind: "info", title: t("auth.facts.signedInAs", { name }), description: last };
}

/**
 * Says the facts of the sign-in that just happened, once: a warning that stays until closed
 * when attempts failed in between, an ordinary note otherwise.
 */
export function useSignInFacts(): void {
  const t = useT();
  useEffect(() => {
    const facts = takeSignInFacts();
    if (facts === null) return;
    const { kind, title, description } = describeSignIn(t, facts);
    if (kind === "warning") toast.warning(title, { description });
    else toast.info(title, { description });
    // Once per sign-in, whatever language the console switches to afterwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
