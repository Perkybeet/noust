/**
 * The languages contributed as data besides English and Spanish (checklist M-63): one line each,
 * and a file `community/<code>.json` of the same name. `npm run i18n:new -- pt Português` adds both;
 * a test fails if a line has no file or a file has no line. A `draft` is previewed (by choosing the
 * language in the browser's storage, see docs/TRANSLATING.md) and is not offered in the language
 * switch or picked from the browser's languages; a maintainer makes it `released` once at least 95%
 * of English is translated and a speaker has read it.
 *
 * Empty until somebody brings a language: the mechanism is ready, and nothing ships half translated.
 */

import type { CommunityLocale } from "./community";

const REGISTERED = [
  // `npm run i18n:new -- <code> <Name>` adds a line here, and the file it names.
] as const satisfies readonly CommunityLocale[];

/** The codes, as literals: they are what makes `Locale` a closed union that includes them. */
export type CommunityCode = (typeof REGISTERED)[number]["code"];

/** The registry, for code that loops over it. */
export const COMMUNITY_LOCALES: readonly CommunityLocale[] = REGISTERED;
