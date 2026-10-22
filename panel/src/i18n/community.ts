/**
 * Languages contributed as data (checklist M-63): a JSON file of translations, with no TypeScript
 * to write. English and Spanish are typed catalogs the compiler holds complete; a community
 * language is a flat file `community/<code>.json` that the console reads at run time, so a person
 * who can translate and cannot program can still bring Noust to their language.
 *
 * ```json
 * {
 *   "nav.apps.label": "Aplicações",
 *   "nav.failed": { "one": "{count} falhou", "other": "{count} falharam" }
 * }
 * ```
 *
 * A key is the dotted path English uses; a message with plural forms is an object of forms. What a
 * file does not translate is shown in English, key by key, so a language can grow a page at a time.
 * Everything the compiler cannot check about such a file is checked here, by the same functions
 * the tests and `npm run i18n:check` use: no key English does not have, the same placeholders,
 * the plural forms the language itself uses, no empty text.
 *
 * Pure: it knows nothing of the console, so every rule is tested without rendering.
 */

/** A contributed file as it is read: dotted keys to a text or to plural forms. */
export type CommunityFile = Readonly<Record<string, unknown>>;

/** A message in the shape the catalogs use: text, or forms chosen by a count. */
export type Message = string | Readonly<Record<string, string>>;

/** What English says, flattened: the standard every file is held to. */
export type EnglishMessages = ReadonlyMap<string, Message>;

/** Where a language stands: draft is previewed and not offered, released is offered in the switch. */
export type CommunityStatus = "draft" | "released";

/** A language the console is built with besides English and Spanish. */
export interface CommunityLocale {
  /** The BCP 47 code the browser and Intl know it by: `pt`, `pt-BR`, `fr`. */
  readonly code: string;
  /** What the language calls itself, never translated: it is how its speakers find it. */
  readonly autonym: string;
  readonly status: CommunityStatus;
}

/** How much of English a file must translate before it may be released. The rest falls back to English. */
export const MIN_RELEASED_COMPLETENESS = 0.95;

/** The plural categories CLDR defines. */
const CATEGORIES = new Set(["zero", "one", "two", "few", "many", "other"]);

interface Tree {
  [key: string]: Tree | string;
}

function isPluralForms(value: unknown): value is Record<string, string> {
  return typeof value === "object" && value !== null && !Array.isArray(value) && typeof (value as Record<string, unknown>)["other"] === "string";
}

/**
 * English as the messages a file may translate.
 *
 * @param tree - The English catalogs: namespaces of nested text and plural forms.
 * @returns Every message by its dotted key.
 */
export function flattenEnglish(tree: unknown, prefix = ""): [string, Message][] {
  if (typeof tree === "string") return [[prefix, tree]];
  if (typeof tree !== "object" || tree === null) return [];
  if (isPluralForms(tree) && Object.keys(tree).every((key) => CATEGORIES.has(key))) return [[prefix, tree]];
  return Object.entries(tree).flatMap(([key, child]) => flattenEnglish(child, prefix === "" ? key : `${prefix}.${key}`));
}

/** The `{name}` placeholders of a message, over all its forms. */
export function placeholdersOf(message: Message): string[] {
  const texts = typeof message === "string" ? [message] : Object.values(message);
  return [...new Set(texts.flatMap((text) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1] ?? "")))].sort();
}

/** Something wrong with one key of a file, in words a translator can act on. */
export interface Problem {
  key: string;
  message: string;
}

/**
 * The plural categories a language uses, which a message in that language may have forms for.
 *
 * @param code - The language's code.
 * @returns The categories, or `one` and `other` where the runtime does not know the language.
 */
export function pluralCategories(code: string): string[] {
  try {
    return new Intl.PluralRules(code).resolvedOptions().pluralCategories;
  } catch {
    // An unknown tag is the registry's problem, reported by its own check; judge the forms by English's.
    return ["one", "other"];
  }
}

function problemsOfMessage(key: string, value: unknown, english: Message, categories: readonly string[]): Problem[] {
  if (typeof english === "string") {
    if (typeof value !== "string") return [{ key, message: "English has plain text here, so this must be text, not plural forms" }];
    if (value.trim() === "") return [{ key, message: "Is empty: leave the key out to show English" }];
    return same(key, english, value);
  }
  if (!isPluralForms(value)) return [{ key, message: 'English has plural forms here ({ "one": "...", "other": "..." }), so this must have them' }];
  const found: Problem[] = [];
  // "one" is required only of a language that selects it: Chinese and Japanese say every count with "other".
  for (const form of ["one", "other"].filter((required) => categories.includes(required))) {
    if (typeof value[form] !== "string" || value[form].trim() === "") found.push({ key, message: `Plural form "${form}" is missing or empty` });
  }
  for (const [form, text] of Object.entries(value)) {
    if (typeof text !== "string") found.push({ key, message: `Plural form "${form}" must be text` });
    else if (!categories.includes(form)) found.push({ key, message: `Plural form "${form}" is not one this language uses (it uses ${categories.join(", ")})` });
  }
  if (found.length > 0) return found;
  return same(key, english, value);
}

function same(key: string, english: Message, translated: Message): Problem[] {
  const want = placeholdersOf(english);
  const got = placeholdersOf(translated);
  return want.join() === got.join()
    ? []
    : [{ key, message: `Uses {${got.join("}, {")}} where English uses {${want.join("}, {")}}: a placeholder is filled in by Noust and must be kept exactly` }];
}

/**
 * Everything wrong with a contributed file.
 *
 * @param file - The file as read.
 * @param english - What English says.
 * @param code - The language's code, for its plural forms.
 * @returns The problems, key by key; empty when the file may be offered.
 */
export function check(file: CommunityFile, english: EnglishMessages, code: string): Problem[] {
  const categories = pluralCategories(code);
  return Object.entries(file).flatMap(([key, value]) => {
    const source = english.get(key);
    if (source === undefined) return [{ key, message: "English has no such key: it was renamed or removed, or there is a typo" }];
    return problemsOfMessage(key, value, source, categories);
  });
}

/**
 * How much of English a file translates.
 *
 * @param file - The file.
 * @param english - What English says.
 * @returns A number from 0 to 1: the share of English's keys the file has.
 */
export function completeness(file: CommunityFile, english: EnglishMessages): number {
  if (english.size === 0) return 1;
  let translated = 0;
  for (const key of Object.keys(file)) if (english.has(key)) translated += 1;
  return translated / english.size;
}

/** The keys English has and the file does not, with what English says: what is left to translate. */
export function missing(file: CommunityFile, english: EnglishMessages): [string, Message][] {
  return [...english].filter(([key]) => !(key in file));
}

/**
 * A file as the nested catalog the translator reads.
 *
 * @param file - The file as read.
 * @returns The same messages as a tree, `{ nav: { apps: { label: "..." } } }`.
 */
export function expand(file: CommunityFile): Tree {
  const root: Tree = {};
  for (const [key, value] of Object.entries(file)) {
    if (typeof value !== "string" && !isPluralForms(value)) continue;
    const parts = key.split(".");
    const last = parts.pop();
    if (last === undefined) continue;
    let node = root;
    for (const part of parts) {
      const next = node[part];
      if (typeof next === "object") {
        node = next;
      } else {
        const created: Tree = {};
        node[part] = created;
        node = created;
      }
    }
    // A plural's forms are strings under the key, exactly the shape a typed catalog has.
    node[last] = typeof value === "string" ? value : { ...value };
  }
  return root;
}

/**
 * Whether a language may be released.
 *
 * @param file - The file.
 * @param english - What English says.
 * @returns Why not, in a sentence; null when it may.
 */
export function releaseBlocker(file: CommunityFile, english: EnglishMessages): string | null {
  const share = completeness(file, english);
  if (share < MIN_RELEASED_COMPLETENESS) {
    return `Only ${String(Math.floor(share * 100))}% of English is translated; a released language has at least ${String(MIN_RELEASED_COMPLETENESS * 100)}%`;
  }
  return null;
}

/** The registry's own rule for a code: a lowercase language, optionally with a region, as BCP 47 writes it. */
export function validCode(code: string): boolean {
  return /^[a-z]{2,3}(-[A-Z]{2})?$/.test(code) && code !== "en" && code !== "es";
}

/**
 * The registry and the files held to each other and to English: what `npm test` and `npm run
 * i18n:check` run, so a contribution that breaks Noust's strings never reaches a release.
 *
 * @param entries - The registered languages.
 * @param files - The contributed files by the path they are found at (`./community/pt.json`).
 * @param english - What English says.
 * @returns Every problem, each key prefixed with its language; empty when all is well.
 */
export function auditRegistry(entries: readonly CommunityLocale[], files: Readonly<Record<string, CommunityFile>>, english: EnglishMessages): Problem[] {
  const found: Problem[] = [];
  const seen = new Set<string>();
  for (const entry of entries) {
    const at = `./community/${entry.code}.json`;
    if (!validCode(entry.code)) found.push({ key: entry.code, message: "Is not a language code: write it as pt or pt-BR (not en or es, which are not community languages)" });
    if (seen.has(entry.code)) found.push({ key: entry.code, message: "Is registered twice" });
    seen.add(entry.code);
    if (entry.autonym.trim() === "") found.push({ key: entry.code, message: "Has no autonym: write the language's name in itself, such as Português" });
    const file = files[at];
    if (file === undefined) {
      found.push({ key: entry.code, message: `Is registered but ${at} does not exist` });
      continue;
    }
    for (const problem of check(file, english, entry.code)) found.push({ key: `${entry.code}: ${problem.key}`, message: problem.message });
    const blocker = entry.status === "released" ? releaseBlocker(file, english) : null;
    if (blocker !== null) found.push({ key: entry.code, message: `Is released but ${blocker}` });
  }
  for (const path of Object.keys(files)) {
    if (!entries.some((entry) => `./community/${entry.code}.json` === path)) {
      found.push({ key: path, message: "Is a language file nobody registered: add it to communityLocales.ts (npm run i18n:new does)" });
    }
  }
  return found;
}
