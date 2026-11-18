import { isApiError } from "../api/errors";
import { describeError } from "./errors";
import { codedOf } from "./serverSentence";
import type { CodedText } from "./serverSentence";

/**
 * What a stack's Compose file gives this server to a container, as the refusal of adopting or bringing it says it: one line of the
 * file for each thing, and the sentence the server says of it. The lines are the server's own words and are shown as they came.
 */
export interface HostAccess {
  /** The lines of the Compose file that give a container something of this server. Never empty. */
  lines: readonly string[];
  /** The sentence the refusal says, with its code, for the console to say it in its language. */
  coded: CodedText | null;
  /** The same sentence in the server's English, which is what is shown for a code this console has no text for. */
  detail: string;
}

/**
 * The lines a 409 of adopting or bringing a stack lists when the stack runs with what the Compose guard refuses a new one
 * (`host_access` in the body of the error). Null for any other failure, and for a 409 that lists none: it is read by the field
 * and never from the text of the error.
 */
export function hostAccessOf(error: unknown): HostAccess | null {
  if (!isApiError(error) || error.status !== 409) return null;
  const listed: unknown = error.extra["host_access"];
  if (!Array.isArray(listed)) return null;
  const lines = listed.filter((line): line is string => typeof line === "string" && line.trim() !== "");
  if (lines.length === 0) return null;
  return { lines, coded: codedOf(error), detail: describeError(error).detail };
}
