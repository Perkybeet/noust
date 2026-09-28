import { useMemo } from "react";
import type { ReactNode } from "react";

import { useLocale } from "../app/locale";
import { translate, translateRich } from "./translate";
import type { RichParams } from "./translate";
import type { Locale, MessageKey, TranslateArgs } from "./types";

/** `t("nav.apps.label")`, `t("time.duration.seconds", { value })`, `t.rich(...)`. */
export interface T {
  <K extends MessageKey>(key: K, ...args: TranslateArgs<K>): string;
  /** A message whose placeholders are elements: `t.rich("key", { path: <Mono>{path}</Mono> })`. */
  rich: <K extends MessageKey>(key: K, params: RichParams<K>) => ReactNode;
  /** The language it translates to, for Intl formatters and `lang` attributes. */
  locale: Locale;
}

/** Binds the translation functions to a language. */
export function bindT(locale: Locale): T {
  const t = <K extends MessageKey>(key: K, ...args: TranslateArgs<K>): string => translate(locale, key, ...args);
  return Object.assign(t, {
    rich: <K extends MessageKey>(key: K, params: RichParams<K>): ReactNode => translateRich(locale, key, params),
    locale,
  });
}

/**
 * The translation function for the active language. The component re-renders when the
 * language changes; `t` is stable for a language, so it is safe in dependency arrays.
 */
export function useT(): T {
  const [locale] = useLocale();
  return useMemo(() => bindT(locale), [locale]);
}
