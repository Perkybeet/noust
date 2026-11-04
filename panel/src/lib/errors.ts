import { queueFullHint } from "../api/errors";
import { getLocale } from "../app/locale";
import { codedOf, serverSentence } from "./serverSentence";

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
  const coded = codedOf(error);
  if (coded === null) return { hint: englishHint, detail: english, output: field(error, "output") };
  const locale = getLocale();
  const fix = serverSentence(locale, coded, englishHint, "hint");
  return {
    hint: fix === "" ? null : fix,
    detail: serverSentence(locale, coded, english, "text"),
    output: field(error, "output"),
  };
}
