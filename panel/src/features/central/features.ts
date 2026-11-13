/**
 * What this Noust can do, as the console asks it: one filter for everything that offers a place to go or a thing to do
 * (the main bar, the tabs, the second column, the palette, the shortcuts, the breadcrumbs, Home's widgets, the wizards).
 * The server publishes `session.central.features`, one entry for each function of its table
 * (`noust.central.features`), saying whether the function works on this machine now and, when it does not, the sentence
 * for why. A hub deploys nothing itself and a container has no systemd: both are answers of that one table, never a
 * condition written beside a screen.
 *
 * "If something cannot be done, do not give the option": a destination whose function is not available is absent from
 * every list. What is reached anyway (an address that was typed, an old bookmark) says why once, calmly, and offers
 * the way to a server that can (`FeatureGate`); it is never an error with "Try again".
 */

import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";

import { sessionQuery } from "../../api/queries/auth";
import type { SessionInfo } from "../../api/queries/auth";
import type { components } from "../../api/schema.gen";

/** A function of the server's table: the keys of the answer, so a mistyped one fails to compile. */
export type FeatureKey = keyof components["schemas"]["FeatureStates"];

/** What the server says of one function: whether it works here, and why not. */
export interface FeatureAnswer {
  available: boolean;
  /** The sentence for the first thing in the way, as the server says it (a code the console translates, and its English). */
  reason: components["schemas"]["FeatureReason"] | null;
}

/**
 * Whether this Noust offers a function. Offered when there is nothing to ask (no key), when the session says nothing
 * of functions (a server that has no such table offers what its role always allowed), and when the function is
 * available; absent when the server says it is not.
 *
 * @param session The signed-in session; `undefined` before it is read.
 * @param key The function a destination or an action needs; `undefined` for one that needs none.
 */
export function offers(session: SessionInfo | undefined, key: FeatureKey | undefined): boolean {
  if (key === undefined) return true;
  const states = session?.central?.features;
  if (states === undefined || states === null) return true;
  // A key the server's table does not know (an older server) is not a reason to hide anything.
  const state = (states as Record<string, { available: boolean } | undefined>)[key];
  return state === undefined || state.available;
}

/** The server's answer for one function, with the sentence for why not; null when the session says nothing of it. */
export function featureAnswer(session: SessionInfo | undefined, key: FeatureKey): FeatureAnswer | null {
  const states = session?.central?.features as Record<string, FeatureAnswer | undefined> | null | undefined;
  return states?.[key] ?? null;
}

/** The filter as a function, stable between renders until the session changes: what the lists of the register take. */
export function useOffers(): (key: FeatureKey | undefined) => boolean {
  const { data } = useQuery(sessionQuery());
  return useCallback((key) => offers(data, key), [data]);
}

/** Whether this Noust offers one function, kept current with the session. */
export function useFeature(key: FeatureKey | undefined): boolean {
  const offered = useOffers();
  return offered(key);
}

/** The reason one function is not available here, for the place that says why; null when it is, or when nothing says. */
export function useFeatureReason(key: FeatureKey | undefined): FeatureAnswer["reason"] {
  const { data } = useQuery(sessionQuery());
  if (key === undefined) return null;
  const answer = featureAnswer(data, key);
  return answer === null || answer.available ? null : answer.reason;
}
