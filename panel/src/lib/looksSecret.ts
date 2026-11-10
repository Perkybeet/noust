/**
 * Whether a variable the person typed looks like a secret, by its name and by its value, so the field can hide it.
 *
 * It is `classify()` of `noust.core.secret_detection`, read in the browser: the shapes of a credential, the words and
 * prefixes of a name, and the thresholds of a value random enough to be one all come from `api/secretShapes.gen.ts`,
 * which `scripts/generate_secret_shapes.py` writes from the server's own lists. What a UUID, a hash, a path or an
 * address is, which are random-looking and are not secrets, is spelled here, and the table of cases both sides are
 * held to (`SECRET_VARIABLE_CASES`) fails the build of whichever side reads one differently.
 *
 * It judges a variable with no operator mark on it, which is what a variable being added by hand is. A false positive
 * only hides a value behind a button; a false negative shows a credential on a screen, so it errs to hiding.
 */

import { SECRET_SHAPES, SECRET_VARIABLE_RULES } from "../api/secretShapes.gen";

const RULES = SECRET_VARIABLE_RULES;

/** The shapes that are a credential wherever they stand, compiled once. */
const VALUE_SHAPES = SECRET_SHAPES.filter((shape) => shape.ofValue).map((shape) => new RegExp(shape.source, shape.flags));
const PRIVATE_KEY = (() => {
  const shape = SECRET_SHAPES.find((candidate) => candidate.name === "private key");
  if (shape === undefined) throw new Error("secretShapes.gen.ts has no private key shape");
  return new RegExp(shape.source, shape.flags);
})();

/** `^`...`$` of Python's `\A`...`\Z`: no flag makes either match inside a line. */
const UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const HEX_SHA = /^[0-9a-fA-F]{40}$/;
const FS_PATH = /^(?:[A-Za-z]:[\\/]|\.{1,2}\/|~\/|\/)\S*$/;
const URL_SCHEME = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//;
const PUBLIC_PEM = /-----BEGIN (?:(?:RSA |EC |DSA |OPENSSH )?PUBLIC KEY|CERTIFICATE)-----/;

function matches(pattern: RegExp, text: string): boolean {
  // The shapes are global: a stateful test would skip over a match the second time it is asked.
  pattern.lastIndex = 0;
  return pattern.test(text);
}

/** How many of lower case, upper case, digit and "everything else" a value mixes. */
function characterClasses(value: string): number {
  return [/\p{Ll}/u, /\p{Lu}/u, /\p{Nd}/u, /[^\p{L}\p{N}]/u].filter((pattern) => pattern.test(value)).length;
}

/** Shannon entropy in bits per character, over code points as Python reads a string. */
function entropyOf(value: string): number {
  const counts = new Map<string, number>();
  let length = 0;
  for (const character of value) {
    counts.set(character, (counts.get(character) ?? 0) + 1);
    length += 1;
  }
  let bits = 0;
  for (const count of counts.values()) bits -= (count / length) * Math.log2(count / length);
  return bits;
}

/** Whether a value is random enough, and nothing else explains it, to be taken for a secret on its shape alone. */
function looksRandom(value: string): boolean {
  const { minLength, minBits, minClasses } = RULES.entropy;
  // Characters as Python counts them (code points), not the UTF-16 units `length` counts.
  if (Array.from(value).length < minLength) return false;
  if (UUID.test(value) || HEX_SHA.test(value) || FS_PATH.test(value) || URL_SCHEME.test(value) || PUBLIC_PEM.test(value)) return false;
  if (characterClasses(value) < minClasses) return false;
  return entropyOf(value) >= minBits;
}

/** Whether a name alone says its value is a secret: by a substring (`ADMIN_PASS`) or a whole word (`apiKey`). */
export function nameLooksSecret(name: string): boolean {
  const typed = name.trim();
  if (typed === "") return false;
  const upper = typed.toUpperCase();
  if (RULES.nameSubstrings.some((part) => upper.includes(part))) return true;
  const normalized = typed.replace(/(?<=[a-z0-9])(?=[A-Z])/g, "_").toLowerCase();
  if (RULES.nonSecretNames.includes(normalized)) return false;
  return normalized.split(/[^a-z0-9]+/).some((word) => RULES.nameWords.includes(word));
}

/** Whether a value is a credential on its own: a vendor's token, a key, a JWT, a password in an address, or a random string. */
export function valueLooksSecret(value: string): boolean {
  if (value === "") return false;
  return VALUE_SHAPES.some((shape) => matches(shape, value)) || looksRandom(value);
}

/** A name a framework bakes into the client bundle is public, unless the rest of it says a password or a private key. */
function looksPublic(name: string, value: string): boolean {
  const upper = name.trim().toUpperCase();
  if (RULES.neverPublicWords.some((word) => upper.includes(word))) return false;
  if (RULES.publicPrefixes.some((prefix) => upper.startsWith(prefix))) return true;
  if (upper.endsWith("PUBLIC_KEY")) return !matches(PRIVATE_KEY, value);
  return false;
}

/**
 * Whether a variable looks like a secret, in the order the server decides it: what the value is first, whatever the
 * name; then what the name says, unless it says it is public.
 */
export function looksSecret(name: string, value: string): boolean {
  if (valueLooksSecret(value)) return true;
  return nameLooksSecret(name) && !looksPublic(name, value);
}
