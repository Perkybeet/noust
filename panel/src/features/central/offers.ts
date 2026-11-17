/**
 * The pure half of the console's one filter of what this Noust can do: the keys, the answers and the question `offers` answers from
 * a session. No hooks and nothing that reads the server, so a test outside the browser (the end-to-end suite walks the register
 * with it) reads the filter without loading the console.
 */

import type { SessionInfo } from "../../api/queries/auth";
import type { components } from "../../api/schema.gen";

/** A function of the server's table: the keys of the answer, so a mistyped one fails to compile. */
export type FeatureKey = keyof components["schemas"]["FeatureStates"];

/**
 * What a destination, an action or a panel needs of the server: one function, or a list of them of which any one is enough (a
 * machine that only has Docker has no database engine of its own and does have the ones Noust runs in containers, so Databases is
 * offered for either). An empty list asks for nothing.
 */
export type FeatureNeed = FeatureKey | readonly FeatureKey[];

/**
 * What Databases needs: the system's own engines, or the ones Noust runs in containers beside them. A machine that only has Docker
 * has the second and not the first, and it lists, creates, copies and restores its instances all the same.
 */
export const DATABASES_NEED: FeatureNeed = ["databases", "database_instances"];

/** What the server says of one function: whether it works here, and why not. */
export interface FeatureAnswer {
  available: boolean;
  /** The sentence for the first thing in the way, as the server says it (a code the console translates, and its English). */
  reason: components["schemas"]["FeatureReason"] | null;
}

function keysOf(need: FeatureNeed): readonly FeatureKey[] {
  return typeof need === "string" ? [need] : need;
}

/**
 * Whether this Noust offers a function. Offered when there is nothing to ask (no key), when the session says nothing
 * of functions (a server that has no such table offers what its role always allowed), and when the function is
 * available; absent when the server says it is not. With a list, offered when any function of it is.
 *
 * @param session The signed-in session; `undefined` before it is read.
 * @param need The function a destination or an action needs, or a list of which any one does; `undefined` for one that needs none.
 */
export function offers(session: SessionInfo | undefined, need: FeatureNeed | undefined): boolean {
  if (need === undefined) return true;
  const keys = keysOf(need);
  if (keys.length === 0) return true;
  const states = session?.central?.features;
  // Before the session is read nothing is hidden, as for every other function.
  if (states === undefined || states === null) return true;
  // A key the server's table does not know (an older server) is not a reason to hide anything.
  const known = states as Record<string, { available: boolean } | undefined>;
  return keys.some((key) => {
    const state = known[key];
    return state === undefined || state.available;
  });
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
 * The server's answer for a function, with the sentence for why not; null when the session says nothing of it. For a list, the
 * answer of the first function that is available, or else the first one's: the sentence a page says when none of them works.
 */
export function featureAnswer(session: SessionInfo | undefined, need: FeatureNeed): FeatureAnswer | null {
  const states = session?.central?.features as Record<string, FeatureAnswer | undefined> | null | undefined;
  const answers = keysOf(need).flatMap((key) => {
    const answer = states?.[key];
    return answer === undefined ? [] : [answer];
  });
  return answers.find((answer) => answer.available) ?? answers[0] ?? null;
}
