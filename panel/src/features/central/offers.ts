/**
 * The pure half of the console's one filter of what this Noust can do: the keys, the answers and the question `offers` answers from
 * a session. No hooks and nothing that reads the server, so a test outside the browser (the end-to-end suite walks the register
 * with it) reads the filter without loading the console.
 */

import type { SessionInfo } from "../../api/queries/auth";
import type { components } from "../../api/schema.gen";

/**
 * A function the console derives from what the table publishes, for the one page the table has no key of its own for: turning
 * on the machine's Docker is something a hub is asked, and neither a server nor a central that deploys already is. It reads the
 * table's own answer (`offers`), never the role, and it is the only one: when the table has a key for it, this goes.
 */
export type DerivedKey = "deploy_here";

/** A function of the server's table: the keys of the answer, so a mistyped one fails to compile; and the one derived from it. */
export type FeatureKey = keyof components["schemas"]["FeatureStates"] | DerivedKey;

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
  // Before the session is read nothing is hidden, as for every other function.
  if (key === "deploy_here") return session === undefined || canBeToldToDeployHere(session);
  const states = session?.central?.features;
  if (states === undefined || states === null) return true;
  // A key the server's table does not know (an older server) is not a reason to hide anything.
  const state = (states as Record<string, { available: boolean } | undefined>)[key];
  return state === undefined || state.available;
}

/** What this central was told, outside the console, to deploy on its machine's own Docker: the setting the session carries. */
export const CONTAINER_HOST = "containers";

/**
 * Whether this central deploys containers on its machine's Docker (it was told to, in its compose file). The words that count
 * "services" count containers there, and the page that says how it was turned on says it is on.
 */
export function isContainerHost(session: SessionInfo | undefined): boolean {
  return session?.central?.deploy_here === CONTAINER_HOST;
}

/**
 * Whether the page that turns on the machine's Docker has anything to say here: this central deploys on it already (so the page is
 * where it is checked again), or the table says the one thing in the way is that it is a hub (`features.hub_role` on what an
 * application needs). A server deploys without being asked and a node that publishes no table has no such page.
 */
function canBeToldToDeployHere(session: SessionInfo): boolean {
  if (isContainerHost(session)) return true;
  const answer = featureAnswer(session, "applications");
  return answer !== null && !answer.available && answer.reason?.code === "features.hub_role";
}

/** The server's answer for one function, with the sentence for why not; null when the session says nothing of it. */
export function featureAnswer(session: SessionInfo | undefined, key: FeatureKey): FeatureAnswer | null {
  const states = session?.central?.features as Record<string, FeatureAnswer | undefined> | null | undefined;
  return states?.[key] ?? null;
}
