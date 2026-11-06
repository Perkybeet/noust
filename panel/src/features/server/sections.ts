/**
 * What the Server layout says above a section: the section's own name as the title (Status is the one page named
 * after the machine), whether the machine's facts follow it on one line, and the sentence some sections carry. The
 * names are the register's (`features/nav/server.ts`), so the column, the title and the palette say the same word.
 */

import type { PlainKey } from "../../i18n";
import { SERVER_SCREENS } from "../nav/server";

export interface ServerSection {
  /** The register's label for the section; null on Status, whose title is the machine's own name. */
  title: PlainKey | null;
  /** The sentence under the title, when the section has one that is the same on every server. */
  description?: PlainKey;
  /** The machine's facts on one line under the title: the name, the system and the Noust it runs. */
  facts: boolean;
}

/** A sentence the layout puts under the title of a section, by the section's path. */
const DESCRIPTIONS: Readonly<Record<string, PlainKey>> = {
  "/server/services": "server.sections.services",
};

/** Sections that carry their own line under the title (a page of its own sentence), so the layout adds none. */
const OWN_LINE: ReadonlySet<string> = new Set(["/server/services"]);

/**
 * The section a path of the Server area is.
 *
 * @param pathname - The path on screen (`/server/updates`).
 * @returns Its title, sentence and whether the machine's facts follow the title.
 */
export function serverSection(pathname: string): ServerSection {
  if (pathname === "/server" || pathname === "/server/") return { title: null, facts: false };
  const entry = SERVER_SCREENS.find((candidate) => candidate.to !== "/server" && (pathname === candidate.to || pathname.startsWith(`${candidate.to}/`)));
  const description = entry === undefined ? undefined : DESCRIPTIONS[entry.to];
  return {
    title: entry?.label ?? null,
    ...(description !== undefined ? { description } : {}),
    facts: entry !== undefined && !OWN_LINE.has(entry.to),
  };
}
