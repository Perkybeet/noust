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
import { featureAnswer, isContainerHost, offers } from "./offers";
import type { FeatureAnswer, FeatureNeed } from "./offers";

export { CONTAINER_HOST, DATABASES_NEED, featureAnswer, isContainerHost, offers } from "./offers";
export type { FeatureAnswer, FeatureKey, FeatureNeed } from "./offers";

/** Whether this central deploys containers on its machine's Docker, kept current with the session. */
export function useContainerHost(): boolean {
  const { data } = useQuery(sessionQuery());
  return isContainerHost(data);
}

/** The filter as a function, stable between renders until the session changes: what the lists of the register take. */
export function useOffers(): (need: FeatureNeed | undefined) => boolean {
  const { data } = useQuery(sessionQuery());
  return useCallback((need) => offers(data, need), [data]);
}

/** Whether this Noust offers one function, kept current with the session. */
export function useFeature(need: FeatureNeed | undefined): boolean {
  const offered = useOffers();
  return offered(need);
}

/** The reason one function is not available here, for the place that says why; null when it is, or when nothing says. */
export function useFeatureReason(need: FeatureNeed | undefined): FeatureAnswer["reason"] {
  const { data } = useQuery(sessionQuery());
  if (need === undefined) return null;
  const answer = featureAnswer(data, need);
  return answer === null || answer.available ? null : answer.reason;
}
