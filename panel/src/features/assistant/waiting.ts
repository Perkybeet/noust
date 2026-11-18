import { isApiError } from "../../api/errors";
import { codedOf } from "../../lib/serverSentence";
import type { CodedText } from "../../lib/serverSentence";

/**
 * The sentences with which the server does not take a question because of how many are being answered: the person's own share
 * (`account_busy`) or the whole server's (`busy`, which the API answers as `queue_full`). Neither is a failure; both are a wait.
 */
const WAITS: ReadonlySet<string> = new Set(["assistant.turn.account_busy", "assistant.turn.busy"]);

/**
 * The sentence of an error that is only a wait (a `429` that says one of the two above), read by its code and never by its text; null
 * for any other error, which is drawn as one.
 */
export function waitOf(error: unknown): CodedText | null {
  if (!isApiError(error) || error.status !== 429) return null;
  const coded = codedOf(error);
  return coded !== null && WAITS.has(coded.code) ? coded : null;
}
