import type { components } from "../api/schema.gen";
import { SERVER_TEXT } from "../api/serverTextCodes.gen";
import type { ServerTextCode } from "../api/serverTextCodes.gen";
import { hasMessage, translate } from "../i18n";
import type { Locale } from "../i18n";

/** A sentence the server says as a code and the values to put in it (`coded` in the API). */
export type CodedText = components["schemas"]["CodedText"];

/** The sentence itself, or its fix (the server's code plus `.hint`). */
export type SentencePart = "text" | "hint";

type Values = Readonly<Record<string, string | number | boolean>>;

// The key is built from data, so it cannot be one of the typed keys the catalogs are checked by:
// `serverText.test.ts` is what holds the catalogs to the codes the server says.
const say = translate as unknown as (locale: Locale, key: string, params: Record<string, string>) => string;

/**
 * The catalog key of a sentence or of its fix, when this console has a text for the code that takes the
 * values the server sent; null otherwise.
 */
export function sentenceKey(coded: CodedText, part: SentencePart): string | null {
  if (!Object.hasOwn(SERVER_TEXT, coded.code)) return null;
  const wanted = SERVER_TEXT[coded.code as ServerTextCode][part];
  if (wanted === null) return null;
  const sent = coded.params ?? {};
  // A server older than this console's table may not send a value the text puts in.
  if (wanted.some((name) => !Object.hasOwn(sent, name))) return null;
  const key = `serverText.${coded.code}.${part}`;
  // Asked of English, which has every text: it also starts fetching the namespace if it is not here yet.
  return hasMessage(key) ? key : null;
}

function strings(values: Values): Record<string, string> {
  return Object.fromEntries(Object.entries(values).map(([name, value]) => [name, String(value)]));
}

/**
 * The words of a sentence as text: this console's, or the English the server sent beside the code. Where a
 * page draws it, `ServerText` is the component; this is for what is said outside a render (a toast) or inside
 * a string (a title).
 */
export function serverSentence(locale: Locale, coded: CodedText | null | undefined, english: string | null | undefined, part: SentencePart): string {
  const key = coded ? sentenceKey(coded, part) : null;
  return coded && key !== null ? say(locale, key, strings(coded.params ?? {})) : (english ?? "");
}

/**
 * A message whose first line is a sentence of Noust's and whose rest is what a program printed (a failed
 * deployment: the probes, the journal, the package manager's output): the first line in this console's
 * language, the rest as it came. The code stands for the first line only.
 */
export function leadingSentence(locale: Locale, coded: CodedText | null | undefined, english: string): string {
  if (!coded) return english;
  const lines = english.split("\n");
  const at = lines.findIndex((line) => line.trim() !== "");
  if (at < 0) return english;
  lines[at] = serverSentence(locale, coded, lines[at] ?? "", "text");
  return lines.join("\n");
}
