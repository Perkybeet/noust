/**
 * The tags an app carries to be grouped: written as a list separated by commas or spaces, checked
 * here with the rules the server holds them to (`noust.core.app_tags`) so a refusal is rare and
 * quick; the server decides again.
 */

import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { translate } from "../../../i18n/translate";

/** A tag is 1 to 32 lowercase letters, digits, dots, underscores or dashes, starting and ending with a letter or digit. */
export const TAG_PATTERN = /^[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?$/;

/** How many tags an app carries. */
export const MAX_TAGS = 16;

/** The tags in a text, each once, in the order written. */
export function tagsOf(text: string): string[] {
  const tags: string[] = [];
  for (const word of text.split(/[\s,]+/)) {
    if (word !== "" && !tags.includes(word)) tags.push(word);
  }
  return tags;
}

/** The text an app's tags are edited as. */
export function tagsText(tags: readonly string[]): string {
  return tags.join(", ");
}

/** What is wrong with the tags in a text, or null when they can be saved. */
export function tagsProblem(text: string, locale: Locale = getLocale()): string | null {
  const tags = tagsOf(text);
  const invalid = tags.filter((tag) => !TAG_PATTERN.test(tag));
  if (invalid.length > 0) return translate(locale, "appTags.dialog.invalid", { count: invalid.length, list: invalid.join(", ") });
  if (tags.length > MAX_TAGS) return translate(locale, "appTags.dialog.tooMany", { max: MAX_TAGS });
  return null;
}
