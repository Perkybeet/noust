/**
 * The address that opens GitHub's form with what the console already knows written in it.
 *
 * The form is `.github/ISSUE_TEMPLATE/console_report.yml`; each of its fields has an id, and GitHub fills a
 * field in from a query parameter of that name. Noust sends nothing: this is a link the person
 * opens, and what is public is what they read in the form and in the file they attach.
 *
 * An address can only be so long (GitHub answers an error above about 8 kB), so a long text is
 * cut, and the console says so and puts the whole of it in the file as well (`bundle.ts`).
 */

import { REPOSITORY } from "../../app/help";
import type { AreaValue } from "./areas";

/** The form the console opens, by its file name under `.github/ISSUE_TEMPLATE`. */
export const REPORT_TEMPLATE = "console_report.yml";

/** What the form's title starts with, as the form itself starts it. */
export const TITLE_PREFIX = "[Console] ";

/** The ids of the form's fields the console fills in. */
export const FIELD_IDS = { version: "version", area: "area", whatHappened: "what-happened", reproduce: "reproduce" } as const;

/**
 * What GitHub's form calls the fields it is opened with, as it writes them whatever the console's language (they
 * are another system's labels, so the preview says them as the person will read them there). The test of the
 * form holds them to `console_report.yml`.
 */
export const FORM_LABELS = {
  title: "Title",
  version: "Noust version",
  area: "Where does it happen",
  whatHappened: "What happened",
  reproduce: "Steps to reproduce",
} as const;

/** How long the whole address may be: under what GitHub refuses, with room for the repository's own part. */
export const MAX_URL_LENGTH = 7000;

export interface ReportFields {
  title: string;
  version: string;
  area: AreaValue;
  whatHappened: string;
  reproduce: string;
}

export interface ReportLink {
  url: string;
  /** The fields as they will be written there, cut where they did not fit. */
  fields: ReportFields;
  /** Whether a text was cut to fit the address, so the whole of it travels in the file. */
  cut: boolean;
}

const encode = encodeURIComponent;

/** The longest start of a text whose encoded form is at most `room` characters, ending in an ellipsis when it was cut. */
function fit(text: string, room: number): { text: string; cut: boolean } {
  if (encode(text).length <= room) return { text, cut: false };
  const points = Array.from(text);
  let low = 0;
  let high = points.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (encode(`${points.slice(0, middle).join("")}…`).length <= room) low = middle;
    else high = middle - 1;
  }
  return { text: `${points.slice(0, low).join("").trimEnd()}…`, cut: true };
}

function address(fields: ReportFields): string {
  const query = [
    ["template", REPORT_TEMPLATE],
    ["title", `${TITLE_PREFIX}${fields.title}`],
    [FIELD_IDS.version, fields.version],
    [FIELD_IDS.area, fields.area],
    [FIELD_IDS.whatHappened, fields.whatHappened],
    [FIELD_IDS.reproduce, fields.reproduce],
  ]
    .filter(([, value]) => value !== "")
    .map(([name, value]) => `${encode(name ?? "")}=${encode(value ?? "")}`)
    .join("&");
  return `${REPOSITORY}/issues/new?${query}`;
}

/**
 * Builds the address of the form, filled in.
 *
 * @param fields - What the person wrote and what the console knows.
 * @returns The address, the fields as it carries them, and whether any was cut. The steps are cut first, since
 * what happened is what a maintainer reads first; the title is never cut.
 */
export function reportLink(fields: ReportFields): ReportLink {
  const whole = address(fields);
  if (whole.length <= MAX_URL_LENGTH) return { url: whole, fields, cut: false };
  const without = address({ ...fields, whatHappened: "", reproduce: "" });
  const room = Math.max(0, MAX_URL_LENGTH - without.length);
  // A third of the room for the steps, at most: the account of what happened is the part that matters most.
  const steps = fit(fields.reproduce, Math.floor(room / 3));
  const happened = fit(fields.whatHappened, room - encode(steps.text).length - 40);
  const cutFields = { ...fields, whatHappened: happened.text, reproduce: steps.text };
  return { url: address(cutFields), fields: cutFields, cut: happened.cut || steps.cut };
}
