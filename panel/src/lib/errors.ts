import { queueFullHint } from "../api/errors";
import { getLocale } from "../app/locale";
import { asCoded, codedOf, sentenceKey, serverSentence } from "./serverSentence";
import type { CodedText } from "./serverSentence";

/**
 * The parts of an error as the console shows it: the suggested fix above, the system's own
 * words below, never paraphrased, and - when the failing tool printed something of its own
 * (psql's, git's or nginx's own text) - that output verbatim, separate from the one-line
 * `detail`.
 */
export interface DescribedError {
  hint: string | null;
  detail: string;
  /** A failing tool's own output, verbatim, when the error carries one. Never paraphrase it. */
  output: string | null;
  /**
   * `detail` is one of Noust's own sentences (the server gave it a code this console has a text for), said
   * in this console's language: words of the product, not of a program, so they are not set as system output.
   */
  own: boolean;
}

/** A job that failed, as the API gives it: the English error, the sentence behind it and what the failing program printed. */
export interface JobFailureSource {
  error?: string | null | undefined;
  error_coded?: CodedText | null | undefined;
  error_output?: string | null | undefined;
}

/**
 * The failure of a job as `describeError` reads it, so a job that failed says its error in this
 * console's language, from its code, and shows what the failing program printed as it came. The one
 * way a page draws a job's error: never `job.error` by itself, which is the English of whoever ran it.
 */
export function jobFailure(job: JobFailureSource, fallback = ""): { detail: string; coded: CodedText | null; output: string | null } {
  const detail = job.error !== undefined && job.error !== null && job.error !== "" ? job.error : fallback;
  return { detail, coded: job.error_coded ?? null, output: job.error_output ?? null };
}

function field(value: unknown, key: string): string | null {
  if (typeof value !== "object" || value === null || !(key in value)) return null;
  const found: unknown = (value as Record<string, unknown>)[key];
  return typeof found === "string" && found !== "" ? found : null;
}

/**
 * Extracts hint, verbatim detail and any tool output from anything thrown. API errors carry
 * `detail`, `hint` and `output` (the backend error contract); plain errors carry a message. When
 * the error carries the sentence of the server's catalog that its `detail` says (`coded`), the
 * detail and the fix are said in this console's language, and the English the server sent is what
 * is shown when this console has no text for the code.
 */
export function describeError(error: unknown): DescribedError {
  const english = field(error, "detail") ?? field(error, "message") ?? String(error);
  const englishHint = queueFullHint(error) ?? field(error, "hint");
  // The sentence travels with an API error (in `extra`) or beside a job's failure (`jobFailure`).
  const coded = codedOf(error) ?? (typeof error === "object" && error !== null ? asCoded((error as { coded?: unknown }).coded) : null);
  if (coded === null) return { hint: englishHint, detail: english, output: field(error, "output"), own: false };
  const locale = getLocale();
  const fix = serverSentence(locale, coded, englishHint, "hint");
  return {
    hint: fix === "" ? null : fix,
    detail: serverSentence(locale, coded, english, "text"),
    output: field(error, "output"),
    // Said from the catalog; for a code this console lacks the English is all there is, and it may carry a program's lines.
    own: sentenceKey(coded, "text") !== null,
  };
}
