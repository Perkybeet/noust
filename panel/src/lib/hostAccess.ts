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
  /**
   * Which lines these are, as the server fingerprints them. Accepting them is accepting exactly these: the request that accepts sends
   * it back, and the server answers 409 with the lines as they are then if the file no longer says what was listed.
   */
  digest: string;
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
  const digest: unknown = error.extra["host_access_digest"];
  return { lines, digest: typeof digest === "string" ? digest : "", coded: codedOf(error), detail: describeError(error).detail };
}

/**
 * What the request says of the lines it accepts: the digest of the lines the person was shown, once they have ticked the box. A request
 * that accepts nothing says none: it asks to be shown the lines, which is how it learns the digest.
 *
 * @param access - The lines on screen, or null when none were listed.
 * @param accepted - Whether the person ticked the box beside them.
 * @returns The field of the request.
 */
export function acceptedLines(access: HostAccess | null, accepted: boolean): { host_access_digest: string } {
  return { host_access_digest: access !== null && accepted ? access.digest : "" };
}
