/**
 * The `.env` grammar, exactly as Noust reads the file on disk.
 *
 * `EnvManager.read_env_file` (src/noust/deployers/helpers/env_manager.py) is the one reader
 * of an app's environment: `noust env show`, the API and the deploy all go through it. What
 * the operator pastes here is parsed the same way, character for character, so the preview
 * they approve is what Noust will read back. `tests/fixtures/env/*.env` pins it: the Python
 * suite parses each fixture with EnvManager, this module's test parses the same files, and
 * both compare with the `.json` beside them.
 *
 * The grammar, in Python's terms: the text is read with universal newlines (`\r\n` and a
 * lone `\r` become `\n`), split with `str.splitlines()`, and each line `str.strip()`ped.
 * Blank lines and lines starting with `#` are skipped, so is a line without `=`. The line is
 * split at its first `=`; name and value are stripped again, a leading `export` keyword (the
 * lowercase word and the spaces or tabs after it) is dropped from the name the way a shell
 * sourcing the file would, and one pair of matching quotes (`"..."` or `'...'`) around the value
 * is removed. A value that opens a quote and does not close it on its line goes on in the next
 * lines up to the first one that ends with the same quote (a private key, a certificate); when
 * no later line closes it, it is the line as written. Nothing else: no escapes, no inline
 * comments. A later line for the same name replaces the value and keeps the name's first
 * position. A run of `#` lines directly above an assignment, with no blank line between, is
 * that variable's comment.
 *
 * The writer quotes a value only when writing it bare would change how it reads back
 * (surrounding spaces, or a value that is itself wrapped in a pair of quotes), so every value
 * the API accepts is read back exactly as it was saved.
 *
 * `nameProblem` and `valueProblem` take a trailing `locale`, defaulting to the active one: a
 * test calls them directly and reads English, a component passes `t.locale`.
 */

import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { translate } from "../../../i18n/translate";
import type { MessageKey } from "../../../i18n/types";

/** Characters Python's `str.isspace()` is true for: what `str.strip()` removes. */
const PYTHON_SPACE = new Set([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003,
  0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);

/** What `str.splitlines()` splits on, once universal newlines have turned `\r` into `\n`. */
// eslint-disable-next-line no-control-regex -- Python splits lines on these control characters too
const PYTHON_LINE_BREAK = /[\n\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;

/** `str.strip()`: JavaScript's trim() strips a different set (it takes U+FEFF, leaves U+001C). */
export function pythonStrip(text: string): string {
  let start = 0;
  let end = text.length;
  while (start < end && PYTHON_SPACE.has(text.charCodeAt(start))) start += 1;
  while (end > start && PYTHON_SPACE.has(text.charCodeAt(end - 1))) end -= 1;
  return text.slice(start, end);
}

/** `str.rstrip()`. */
function pythonRstrip(text: string): string {
  let end = text.length;
  while (end > 0 && PYTHON_SPACE.has(text.charCodeAt(end - 1))) end -= 1;
  return text.slice(0, end);
}

/** `str.lstrip()`. */
function pythonLstrip(text: string): string {
  let start = 0;
  while (start < text.length && PYTHON_SPACE.has(text.charCodeAt(start))) start += 1;
  return text.slice(start);
}

function unquote(value: string): string {
  if (value.length < 2) return value;
  const first = value[0];
  const last = value[value.length - 1];
  return (first === '"' && last === '"') || (first === "'" && last === "'") ? value.slice(1, -1) : value;
}

/** `EnvManager._EXPORT_PREFIX`: `export` and at least one space or tab, consumed whole. */
const EXPORT_PREFIX = /^export[ \t]+(.*)$/s;

function withoutExport(name: string): string {
  return EXPORT_PREFIX.exec(name)?.[1] ?? name;
}

export interface ParsedLine {
  /** 1-based line number, counted the way the parser splits lines. */
  line: number;
  name: string;
  value: string;
  /** The `#` lines directly above it, joined by a line feed; empty when none. */
  comment: string;
}

export interface ParsedDotenv {
  /** Every variable, in the order its name first appeared, with its last value. */
  variables: Map<string, string>;
  /** The comment above each variable that has one (the last assignment's, for a repeated name). */
  comments: Map<string, string>;
  /** Every assignment line, in order, duplicates included. */
  assignments: ParsedLine[];
  /** Lines that were neither blank, a comment nor an assignment: skipped, as Noust skips them. */
  skipped: ParsedLine[];
}

/** Splits text into lines the way Python reads and splits a file. */
export function pythonLines(text: string): string[] {
  const lines = text.replace(/\r\n?/g, "\n").split(PYTHON_LINE_BREAK);
  // splitlines() yields no empty last element after a final line break.
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/** `EnvManager._closing_line`: the first line from `start` that ends with the quote, and it without the quote. */
function closingLine(lines: readonly string[], start: number, quote: string): { index: number; tail: string } | null {
  for (let index = start; index < lines.length; index += 1) {
    const closing = pythonRstrip(lines[index] ?? "");
    if (closing.endsWith(quote)) return { index, tail: closing.slice(0, -1) };
  }
  return null;
}

/** Parses a `.env` file exactly as `EnvManager.parse_env_entries` does. */
export function parseDotenv(text: string): ParsedDotenv {
  const variables = new Map<string, string>();
  const comments = new Map<string, string>();
  const assignments: ParsedLine[] = [];
  const skipped: ParsedLine[] = [];
  const lines = pythonLines(text);
  let comment: string[] = [];
  let index = 0;
  while (index < lines.length) {
    const number = index + 1;
    const raw = lines[index] ?? "";
    const line = pythonStrip(raw);
    index += 1;
    if (line === "") {
      comment = [];
      continue;
    }
    if (line.startsWith("#")) {
      comment.push(pythonStrip(line.slice(1)));
      continue;
    }
    const at = line.indexOf("=");
    if (at === -1) {
      skipped.push({ line: number, name: line, value: "", comment: "" });
      comment = [];
      continue;
    }
    const name = withoutExport(pythonStrip(line.slice(0, at)));
    let value = pythonStrip(line.slice(at + 1));
    const opener = value.slice(0, 1);
    if ((opener === '"' || opener === "'") && (value.length === 1 || !value.endsWith(opener))) {
      const closed = closingLine(lines, index, opener);
      if (closed !== null) {
        // The first line as it is after the quote: its trailing spaces are the value's.
        const first = pythonLstrip(raw.slice(raw.indexOf("=") + 1)).slice(1);
        value = [first, ...lines.slice(index, closed.index), closed.tail].join("\n");
        index = closed.index + 1;
      }
    } else {
      value = unquote(value);
    }
    const note = comment.join("\n");
    comment = [];
    variables.set(name, value);
    if (note === "") comments.delete(name);
    else comments.set(name, note);
    assignments.push({ line: number, name, value, comment: note });
  }
  return { variables, comments, assignments, skipped };
}

// ---------------------------------------------------------------------------------------
// What the API accepts (noust/validators/environment.py), checked before sending.

/** ENV_NAME_PATTERN: a POSIX environment identifier. */
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** `_FORBIDDEN_VALUE_CHARS`: control characters, the C1 ones and the Unicode line separators. */
// eslint-disable-next-line no-control-regex -- the control characters the API refuses are the point
const FORBIDDEN = /[\x00-\x1f\x7f-\x9f\u2028\u2029]/;

/** `_FORBIDDEN_MULTILINE_CHARS`: the same, but the line feed that separates the lines of a value. */
// eslint-disable-next-line no-control-regex -- the control characters the API refuses are the point
const FORBIDDEN_IN_LINES = /[\x00-\x09\x0b-\x1f\x7f-\x9f\u2028\u2029]/;

const CONTROL_KEYS: Readonly<Record<string, MessageKey>> = {
  "\n": "environment.dotenv.controlNewline",
  "\r": "environment.dotenv.controlCr",
  "\t": "environment.dotenv.controlTab",
  "\x00": "environment.dotenv.controlNul",
};

export function isValidName(name: string): boolean {
  return NAME.test(name);
}

/** Why the API would refuse this name, or null when it accepts it. */
export function nameProblem(name: string, locale: Locale = getLocale()): string | null {
  if (isValidName(name)) return null;
  if (name === "") return translate(locale, "environment.dotenv.noName");
  return translate(locale, "environment.dotenv.invalidName", { name });
}

/**
 * Why the API would refuse this value, or null when it accepts it. A value may have several lines
 * (a private key, a certificate) as long as it holds no single quote and no other control character.
 */
export function valueProblem(value: string, locale: Locale = getLocale()): string | null {
  const lines = value.includes("\n");
  const match = (lines ? FORBIDDEN_IN_LINES : FORBIDDEN).exec(value);
  if (match === null) return lines && value.includes("'") ? translate(locale, "environment.dotenv.linesAndQuote") : null;
  const char = match[0];
  const code = char.charCodeAt(0).toString(16).toUpperCase().padStart(4, "0");
  const key = CONTROL_KEYS[char];
  const name = key ? translate(locale, key) : translate(locale, "environment.dotenv.controlGeneric", { code });
  return translate(locale, "environment.dotenv.forbiddenChar", { char: name });
}

/** `validate_env_comment`: plain short text, trimmed; null when the API accepts it. */
export const MAX_COMMENT_CHARS = 500;
export const MAX_COMMENT_LINES = 8;

export function commentProblem(comment: string, locale: Locale = getLocale()): string | null {
  const lines = comment.trim().split("\n");
  const match = FORBIDDEN_IN_LINES.exec(comment.trim());
  if (match !== null) return translate(locale, "environment.dotenv.commentChar");
  if (comment.trim().length > MAX_COMMENT_CHARS || lines.length > MAX_COMMENT_LINES) {
    return translate(locale, "environment.dotenv.commentTooLong", { chars: MAX_COMMENT_CHARS, lines: MAX_COMMENT_LINES });
  }
  return null;
}

/** A value on one line for a table or a list: its first line, and how many lines it has. */
export function oneLine(value: string): { text: string; lines: number } {
  const lines = value.split("\n");
  return { text: lines[0] ?? "", lines: lines.length };
}
