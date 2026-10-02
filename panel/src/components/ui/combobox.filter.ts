/**
 * The default search of `Combobox`: every word of the query must appear in one of the item's
 * text fields, and a query that reads as a UTC offset (`+2`, `utc+1`, `gmt-03:30`) matches the
 * offsets written in those fields instead. Kept apart from the component so the rules can be
 * tested on their own and reused by a caller that filters elsewhere.
 */

/** An offset typed by the operator, in minutes east of UTC. */
export interface OffsetQuery {
  minutes: number;
  /** False for `+2`, which means "two hours ahead, whatever the minutes". */
  exactMinutes: boolean;
}

// "+2", "utc+1", "UTC -03:30", "+0530". Hours are one or two digits, so "+12" is twelve hours,
// never one hour and something.
const OFFSET_QUERY = /^(?:utc|gmt)?([+\-−])(\d{1,2})(?::?(\d{2}))?$/;
// An offset inside an item's text: "UTC+02:00", "GMT-3", or a bare "+05:30".
const OFFSET_IN_TEXT = /(?:utc|gmt|^|\s)([+\-−])(\d{1,2})(?::?(\d{2}))?(?![\d])/g;

function fold(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

function signed(sign: string, hours: string, minutes: string | undefined): number {
  const total = Number(hours) * 60 + Number(minutes ?? "0");
  return sign === "+" ? total : -total;
}

/** The offset a query stands for, or null when it is not an offset. */
export function parseOffsetQuery(query: string): OffsetQuery | null {
  const compact = fold(query).replace(/\s+/g, "");
  const match = OFFSET_QUERY.exec(compact);
  if (!match) return null;
  const [, sign = "+", hours = "0", minutes] = match;
  return { minutes: signed(sign, hours, minutes), exactMinutes: minutes !== undefined };
}

/** Every string an item carries, in its own fields or in arrays of strings. */
function textsOf(item: object): string[] {
  const texts: string[] = [];
  for (const value of Object.values(item)) {
    if (typeof value === "string") texts.push(value);
    else if (Array.isArray(value)) for (const entry of value) if (typeof entry === "string") texts.push(entry);
  }
  return texts;
}

function offsetsIn(text: string): number[] {
  const offsets: number[] = [];
  for (const match of fold(text).matchAll(OFFSET_IN_TEXT)) {
    const [, sign = "+", hours = "0", minutes] = match;
    offsets.push(signed(sign, hours, minutes));
  }
  return offsets;
}

function offsetMatches(wanted: OffsetQuery, offset: number): boolean {
  if (wanted.exactMinutes) return offset === wanted.minutes;
  // "+2" is every offset whose hour part is two ahead: +02:00, and +02:30 too.
  const hours = offset < 0 ? -Math.floor(-offset / 60) : Math.floor(offset / 60);
  if (wanted.minutes === 0) return hours === 0;
  return hours * 60 === wanted.minutes && Math.sign(offset) === Math.sign(wanted.minutes);
}

/**
 * Whether an item answers a query. Words are matched anywhere in any text field, ignoring
 * case and accents, with `_` and `/` read as spaces (`buenos aires` finds
 * `America/Argentina/Buenos_Aires`).
 */
export function matchesComboboxQuery(item: object, query: string): boolean {
  if (query.trim() === "") return true;
  const texts = textsOf(item);
  const offset = parseOffsetQuery(query);
  if (offset !== null) return texts.some((text) => offsetsIn(text).some((found) => offsetMatches(offset, found)));
  const haystack = texts.map((text) => fold(text).replace(/[_/]+/g, " ")).join(" \u0000 ");
  return fold(query)
    .split(/\s+/)
    .filter((word) => word !== "")
    .every((word) => haystack.includes(word));
}
