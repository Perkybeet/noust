/**
 * Turning a key into text, outside React. `useT()` is the same function bound to the active
 * language; call this one directly where there is no component (toasts raised from a
 * mutation, validation helpers, document titles).
 */

import { Fragment, createElement } from "react";
import type { ReactNode } from "react";

import { catalogFor, hasNamespace, isNamespace, missNamespace } from "./catalogs";
import { namespaceOf } from "./catalogStore";
import type { Locale, MessageKey, Namespace, ParamName, ParamValue, PluralForms, TranslateArgs } from "./types";

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
    // Own keys only: a key made from data ("constructor", "__proto__") is not a message.
    if (node === undefined || typeof node === "string" || !Object.hasOwn(node, part)) return undefined;
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

/** Says once, in development, that a page drew a namespace nobody told the router it needed. */
function reportUnloaded(locale: Locale, namespace: string, key: string): void {
  if (!import.meta.env.DEV || reported.has(`${locale}:${namespace}:unloaded`)) return;
  reported.add(`${locale}:${namespace}:unloaded`);
  console.warn(
    `i18n: "${key}" was asked for before the ${locale} texts of "${namespace}" were loaded, so they are being fetched now and the page may show English, or the key, for a moment. ` +
      "A page declares the namespaces it draws in src/i18n/routeNamespaces.gen.ts: run `npm run i18n:routes`.",
  );
}

function isPlural(value: Tree | string | undefined): value is Tree & PluralForms {
  return typeof value === "object" && typeof value["other"] === "string";
}

/**
 * Starts fetching a namespace whose texts a `t()` needed and did not find, and says so once in
 * development. Not an error and never a throw: `t()` runs in event handlers, toasts and code with
 * no component, where there is nothing to suspend. A component that called it is drawn again
 * when the namespace arrives (`useT`), and everyone else gets the best text there is right now.
 */
function askFor(locale: Locale, namespace: Namespace, key: string): void {
  reportUnloaded(locale, namespace, key);
  missNamespace(locale, namespace);
}

/**
 * The template for a key in a language, its plural form picked by `count`.
 *
 * A namespace can still be on its way when a text is asked for (an event handler that runs before
 * the idle preload reached it, a page whose namespaces nobody declared). What this returns then,
 * in order: the language's own text if it is here; English's, if the language is not English and
 * English's namespace is here, so the operator reads something rather than a key; otherwise the
 * key itself, which is what an unknown key has always been. Either way the namespace is
 * requested, so the next call, and the next render of a component, have the real text.
 */
function template(locale: Locale, key: string, count: unknown): string {
  let value = lookup(locale, key);
  let language = locale;
  let arriving = false;
  if (value === undefined) {
    const namespace = namespaceOf(key);
    const known = isNamespace(namespace);
    if (known && !hasNamespace(locale, namespace)) {
      askFor(locale, namespace, key);
      arriving = true;
    } else if (locale !== "en") {
      reportMissing(locale, key);
    }
    if (locale !== "en") {
      if (known && !hasNamespace("en", namespace)) {
        askFor("en", namespace, key);
        arriving = true;
      }
      value = lookup("en", key);
      language = "en";
    }
  }
  if (typeof value === "string") return value;
  if (isPlural(value)) {
    const category = typeof count === "number" ? pluralRulesFor(language).select(count) : "other";
    const form = value[category];
    return typeof form === "string" ? form : value.other;
  }
  if (!arriving) reportMissing("en", key);
  return key;
}

/**
 * Whether English has a message under a key, which is how a label built from data (the name of an
 * audit event, a compliance control) knows it has been translated at all, and falls back to the
 * server's own word when it has not. English is the standard: Spanish has exactly its keys.
 *
 * The namespace may be on its way; then the answer is no for now, the namespace is requested, and
 * the component that asked is drawn again when it arrives.
 */
export function hasMessage(key: string): boolean {
  const namespace = namespaceOf(key);
  if (isNamespace(namespace) && !hasNamespace("en", namespace)) {
    askFor("en", namespace, key);
    return false;
  }
  const value = lookup("en", key);
  return typeof value === "string" || isPlural(value);
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
