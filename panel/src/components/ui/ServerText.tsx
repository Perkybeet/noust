import { Fragment } from "react";
import type { ReactNode } from "react";

import type { components } from "../../api/schema.gen";
import { SERVER_TEXT } from "../../api/serverTextCodes.gen";
import type { ServerTextCode } from "../../api/serverTextCodes.gen";
import { hasMessage, translate, translateRich, useT } from "../../i18n";
import type { Locale } from "../../i18n";
import { Mono } from "./Mono";

/** A sentence the server says as a code and the values to put in it (`coded` in the API). */
export type CodedText = components["schemas"]["CodedText"];

/** The sentence itself, or its fix (the server's code plus `.hint`). */
export type SentencePart = "text" | "hint";

type Values = Readonly<Record<string, string | number | boolean>>;

// The key is built from data, so it cannot be one of the typed keys the catalogs are checked by:
// `serverText.test.ts` is what holds the catalogs to the codes the server says.
const say = translate as unknown as (locale: Locale, key: string, params: Record<string, string>) => string;
const sayRich = translateRich as unknown as (locale: Locale, key: string, params: Record<string, ReactNode>) => ReactNode;

/** Whether this console has a text for the code that takes the values the server sent. */
function keyFor(coded: CodedText, part: SentencePart): string | null {
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

/** The words of a sentence as text: this console's, or the English the server sent beside the code. */
export function serverSentence(locale: Locale, coded: CodedText | null | undefined, english: string | null | undefined, part: SentencePart): string {
  const key = coded ? keyFor(coded, part) : null;
  return coded && key !== null ? say(locale, key, strings(coded.params ?? {})) : (english ?? "");
}


/** The words of a sentence as nodes: a command is set as it is, in mono. */
function sentenceRich(locale: Locale, coded: CodedText | null | undefined, english: string | null | undefined, part: SentencePart): ReactNode {
  const key = coded ? keyFor(coded, part) : null;
  if (!coded || key === null) return english ?? null;
  const commands = new Set(SERVER_TEXT[coded.code as ServerTextCode].commands as readonly string[]);
  const nodes = Object.fromEntries(
    Object.entries(coded.params ?? {}).map(([name, value]) => [name, commands.has(name) ? <Mono>{String(value)}</Mono> : String(value)]),
  );
  return sayRich(locale, key, nodes);
}

export interface ServerTextProps {
  /** The code and values the server sent; null for a sentence nobody gave a code. */
  coded: CodedText | null | undefined;
  /**
   * The English the server sent beside the code: shown as it is when this console has no entry
   * for the code, so a server newer than the console still says something true.
   */
  text: string | null | undefined;
  /** `hint` says the sentence's fix instead of the sentence. */
  part?: SentencePart;
}

/**
 * A sentence the server says, in the console's language: looks up `serverText.<code>` in the
 * English and Spanish catalogs, puts the values in, and sets a command as it is, in mono. A code
 * the console has no entry for, or one whose values did not all arrive, is the server's English.
 * It draws no element of its own, so it goes inside whatever paragraph the page has.
 */
export function ServerText({ coded, text, part = "text" }: ServerTextProps) {
  const t = useT();
  return <Fragment>{sentenceRich(t.locale, coded, text, part)}</Fragment>;
}

/**
 * The same words as plain text, for a title, an aria-label or a toast: `say(coded, english)`, and
 * `say(coded, english, "hint")` for the fix. Re-renders when the language changes or the catalog arrives.
 */
export function useServerSentence(): (coded: CodedText | null | undefined, english: string | null | undefined, part?: SentencePart) => string {
  const t = useT();
  return (coded, english, part = "text") => serverSentence(t.locale, coded, english, part);
}
