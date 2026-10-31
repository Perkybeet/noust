import { Fragment } from "react";
import type { ReactNode } from "react";

import { SERVER_TEXT } from "../../api/serverTextCodes.gen";
import type { ServerTextCode } from "../../api/serverTextCodes.gen";
import { translateRich, useT } from "../../i18n";
import type { Locale } from "../../i18n";
import { sentenceKey, serverSentence } from "../../lib/serverSentence";
import type { CodedText, SentencePart } from "../../lib/serverSentence";
import { Mono } from "./Mono";

export { serverSentence };
export type { CodedText, SentencePart };

const sayRich = translateRich as unknown as (locale: Locale, key: string, params: Record<string, ReactNode>) => ReactNode;

/** The words of a sentence as nodes: a command is set as it is, in mono. */
function sentenceRich(locale: Locale, coded: CodedText | null | undefined, english: string | null | undefined, part: SentencePart): ReactNode {
  const key = coded ? sentenceKey(coded, part) : null;
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
 * A sentence and its fix as one text, for a line that gives the reason for something ("MariaDB: ...").
 * A code this console has no entry for is the English the server sent, which already holds both.
 */
export function useServerReason(): (coded: CodedText | null | undefined, english: string | null | undefined) => string {
  const t = useT();
  return (coded, english) => [serverSentence(t.locale, coded, english, "text"), serverSentence(t.locale, coded, null, "hint")].filter((part) => part !== "").join(" ");
}

/**
 * The same words as plain text, for a title, an aria-label or a toast: `say(coded, english)`, and
 * `say(coded, english, "hint")` for the fix. Re-renders when the language changes or the catalog arrives.
 */
export function useServerSentence(): (coded: CodedText | null | undefined, english: string | null | undefined, part?: SentencePart) => string {
  const t = useT();
  return (coded, english, part = "text") => serverSentence(t.locale, coded, english, part);
}
