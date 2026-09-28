import { useEffect, useRef } from "react";

import { announce } from "../../app/Announcer";
import { getLocale } from "../../app/locale";
import { appStatus } from "../../components/page/status";
import type { Locale } from "../../i18n";
import type { AppInfo } from "./data";

/**
 * What a reading remembers of an app's state: its English word, so a language switch between
 * two readings is not mistaken for every app changing state.
 */
function stateWord(status: string | null | undefined): string {
  return appStatus(status, "en").label;
}

/**
 * The sentence that tells a screen reader which apps changed state between two readings of
 * the list, or null when none did. Apps that appeared or disappeared are not transitions.
 * `before` holds each app's English state word, as `useStateTransitions` records it.
 */
export function describeTransitions(
  before: ReadonlyMap<string, string>,
  after: readonly Pick<AppInfo, "domain" | "status">[],
  locale: Locale = getLocale(),
): { message: string; failed: boolean } | null {
  const changes: string[] = [];
  let failed = false;
  for (const app of after) {
    const was = before.get(app.domain);
    if (was === undefined || was === stateWord(app.status)) continue;
    const view = appStatus(app.status, locale);
    changes.push(`${app.domain}: ${view.label}`);
    if (view.state === "failed") failed = true;
  }
  return changes.length === 0 ? null : { message: `${changes.join(". ")}.`, failed };
}

/**
 * Announces apps changing state in a live list (the `app` events refresh it): politely, or
 * assertively when one failed. The first reading is the page loading, not a transition, and
 * says nothing.
 */
export function useStateTransitions(apps: readonly AppInfo[] | undefined): void {
  const previous = useRef<Map<string, string> | null>(null);
  useEffect(() => {
    if (apps === undefined) return;
    const before = previous.current;
    previous.current = new Map(apps.map((app) => [app.domain, stateWord(app.status)]));
    if (before === null) return;
    const change = describeTransitions(before, apps);
    if (change !== null) announce(change.message, change.failed ? "assertive" : "polite");
  }, [apps]);
}
