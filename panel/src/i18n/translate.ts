/**
 * Turning a key into text, outside React. `useT()` is the same function bound to the active
 * language; call this one directly where there is no component (toasts raised from a
 * mutation, validation helpers, document titles).
 */

import { Fragment, createElement } from "react";
import type { ReactNode } from "react";

import { catalogFor } from "./catalogs";
import type { Locale, MessageKey, ParamName, ParamValue, PluralForms, TranslateArgs } from "./types";

interface Tree {
  readonly [key: string]: Tree | string | undefined;
}

const PLACEHOLDER = /\{(\w+)\}/g;

const pluralRules = new Map<Locale, Intl.PluralRules>();

function pluralRulesFor(locale: Locale): Intl.PluralRules {
  let rules = pluralRules.get(locale);
  if (rules === undefined) {
    rules = new Intl.PluralRules(locale);
    pluralRules.set(locale, rules);
  }
  return rules;
}

function lookup(locale: Locale, key: string): Tree | string | undefined {
  // Walked as an untyped tree: the key was checked against the English catalog by its type.
  let node: Tree | string | undefined = catalogFor(locale);
  for (const part of key.split(".")) {
    if (node === undefined || typeof node === "string") return undefined;
    node = node[part];
  }
  return node;
}

const reported = new Set<string>();

/** Says once, in development, that a key fell back to English or was not found at all. */
function reportMissing(locale: Locale, key: string): void {
  if (!import.meta.env.DEV || reported.has(`${locale}:${key}`)) return;
  reported.add(`${locale}:${key}`);
  console.warn(`i18n: "${key}" has no ${locale} text; showing ${locale === "en" ? "the key" : "English"}.`);
}

function isPlural(value: Tree | string | undefined): value is Tree & PluralForms {
  return typeof value === "object" && typeof value["other"] === "string";
}

/** The template for a key in a language, its plural form picked by `count`. */
function template(locale: Locale, key: string, count: unknown): string {
  let value = lookup(locale, key);
  let language = locale;
  if (value === undefined && locale !== "en") {
    reportMissing(locale, key);
    value = lookup("en", key);
    language = "en";
  }
  if (typeof value === "string") return value;
  if (isPlural(value)) {
    const category = typeof count === "number" ? pluralRulesFor(language).select(count) : "other";
    const form = value[category];
    return typeof form === "string" ? form : value.other;
  }
  reportMissing("en", key);
  return key;
}

/**
 * The text of `key` in `locale`, with its `{placeholders}` filled from `params`. A plural key
 * takes `{ count }` and picks its form with the language's plural rules. A placeholder
 * without a value is left visible rather than silently dropped.
 */
export function translate<K extends MessageKey>(locale: Locale, key: K, ...args: TranslateArgs<K>): string {
  const params = (args[0] ?? {}) as Readonly<Record<string, ParamValue | undefined>>;
  return template(locale, key, params["count"]).replace(PLACEHOLDER, (match: string, name: string) => {
    const value = params[name];
    return value === undefined ? match : String(value);
  });
}

/** Rich parameters: any React node (a <Mono> path, a link) in place of a placeholder. */
export type RichParams<K extends MessageKey> = { readonly [P in ParamName<K>]: P extends "count" ? number : ReactNode };

/**
 * Like `translate`, but a placeholder may be an element, so a sentence with a code span or a
 * link inside is still one message whose word order the translation decides. Returns nodes
 * for React to render: nothing is ever turned into HTML.
 */
export function translateRich<K extends MessageKey>(locale: Locale, key: K, params: RichParams<K>): ReactNode {
  const values = params as Readonly<Record<string, ReactNode>>;
  const text = template(locale, key, values["count"]);
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(PLACEHOLDER)) {
    const [whole, name = ""] = match;
    if (match.index > last) nodes.push(text.slice(last, match.index));
    nodes.push(name in values ? values[name] : whole);
    last = match.index + whole.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  // Spread as children, not passed as an array: the parts are static and need no keys.
  return createElement(Fragment, null, ...nodes);
}
