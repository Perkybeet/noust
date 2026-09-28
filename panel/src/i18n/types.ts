/**
 * The shape of a catalog and of the keys and parameters derived from it. English is the
 * source of truth: every type here is computed from `typeof import("./en")`, so a key that
 * does not exist, a missing parameter or a Spanish catalog with a key too many or too few is
 * a compile error rather than a string shown to an operator.
 */

import type * as english from "./en";

/** The languages the console speaks. */
export type Locale = "en" | "es";

export const LOCALES: readonly Locale[] = ["en", "es"];

/** Every catalog, by namespace: `Messages["nav"]["apps"]["label"]`. */
export type Messages = typeof english;

/**
 * A message that depends on a number, chosen with `Intl.PluralRules`. English only has `one`
 * and `other`; Spanish may add `many` (CLDR uses it for "1 millón de ..."). A category a
 * catalog does not define falls back to `other`.
 */
export interface PluralForms {
  readonly one: string;
  readonly other: string;
}

// Records rather than an interface: a catalog must stay assignable to a plain string tree.
export type TranslatedPlural = Readonly<Record<"one" | "other", string> & Partial<Record<"zero" | "two" | "few" | "many", string>>>;

/**
 * The same tree as an English catalog with every text widened to `string`. Annotating a
 * translation with it makes tsc reject a missing key and, through the excess property check
 * on the literal, an extra one.
 */
export type Catalog<T> = {
  readonly [K in keyof T]: T[K] extends string ? string : T[K] extends PluralForms ? TranslatedPlural : Catalog<T[K]>;
};

type Join<Prefix extends string, Key extends string> = Prefix extends "" ? Key : `${Prefix}.${Key}`;

type KeysOf<T, Prefix extends string = ""> = {
  [K in keyof T & string]: T[K] extends string | PluralForms ? Join<Prefix, K> : KeysOf<T[K], Join<Prefix, K>>;
}[keyof T & string];

/** Every message, as a dotted path: "nav.apps.label", "time.justNow". */
export type MessageKey = KeysOf<Messages>;

type ValueAt<T, Key extends string> = Key extends `${infer Head}.${infer Rest}`
  ? Head extends keyof T
    ? ValueAt<T[Head], Rest>
    : never
  : Key extends keyof T
    ? T[Key]
    : never;

/** The English text (or plural forms) of a key, as a literal type. */
export type MessageOf<K extends MessageKey> = ValueAt<Messages, K>;

type Placeholders<S> = S extends `${string}{${infer Name}}${infer Rest}` ? Name | Placeholders<Rest> : never;

/** The `{name}` placeholders a key's English text uses; `count` for every plural. */
export type ParamName<K extends MessageKey> =
  MessageOf<K> extends infer V
    ? V extends PluralForms
      ? Placeholders<V["one"] | V["other"]> | "count"
      : Placeholders<V>
    : never;

/** A value for a placeholder. Numbers are printed as they are: format them first. */
export type ParamValue = string | number;

type IsPlural<K extends MessageKey> = MessageOf<K> extends PluralForms ? true : false;

/** The parameters a key takes: its placeholders, plus a numeric `count` for plurals. */
export type Params<K extends MessageKey, V = ParamValue> =
  IsPlural<K> extends true
    ? { readonly count: number } & Readonly<Record<Exclude<ParamName<K>, "count">, V>>
    : Readonly<Record<ParamName<K>, V>>;

/**
 * The arguments after the key: none for a plain message, the parameters otherwise. It
 * distributes over a union of keys, so a key held in a variable (`item.label`) type-checks
 * when every candidate takes no parameters.
 */
export type TranslateArgs<K extends MessageKey> = K extends MessageKey
  ? [ParamName<K>] extends [never]
    ? []
    : [params: Params<K>]
  : never;

/** A key that takes no parameters: what a data structure such as the navigation may hold. */
export type PlainKey = { [K in MessageKey]: [ParamName<K>] extends [never] ? K : never }[MessageKey];
