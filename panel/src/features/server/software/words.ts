/**
 * The words of the Software section that are built from what the server sent: a component's name, what depends on
 * it ("18 sites"), and the sentence of its purpose or of why it stays. Pure, given the translator, so a test reads
 * English and a component passes the one `useT` gives it.
 */

import type { Locale, T } from "../../../i18n";
import { bindT } from "../../../i18n/useT";
import { asCoded, serverSentence } from "../../../lib/serverSentence";
import type { CodedText } from "../../../lib/serverSentence";
import { SOFTWARE_JOB_TITLES } from "./jobTitles";
import { nameKey } from "./registry";
import type { Dependent, SoftwareItem } from "./queries";

/** A component's name: the console's own for the ones it knows, the id the server gave for any other. */
export function componentName(t: T, id: string): string {
  const key = nameKey(id);
  return key === null ? id : t(key);
}

function countOf(dependent: Dependent): number {
  return dependent.names?.length ?? 0;
}

/** What depends on a component, by kind: "18 sites", "2 tunnels". One literal key per kind, so `tsc` checks each. */
export function dependentWords(t: T, dependent: Dependent): string {
  const count = countOf(dependent);
  switch (dependent.kind) {
    case "application":
      return t("software.dependents.application", { count });
    case "site":
      return t("software.dependents.site", { count });
    case "database":
      return t("software.dependents.database", { count });
    case "tunnel":
      return t("software.dependents.tunnel", { count });
    case "published_port":
      return t("software.dependents.published_port", { count });
    case "certificate":
      return t("software.dependents.certificate", { count });
    case "zone":
      return t("software.dependents.zone", { count });
    case "destination":
      return t("software.dependents.destination", { count });
    case "instance":
      return t("software.dependents.instance", { count });
    case "console":
      return t("software.dependents.console", { count });
    case "unreadable":
      return t("software.dependents.unreadable");
    default:
      return t("software.dependents.other", { kind: dependent.kind, count });
  }
}

/** Everything that depends on a component as one list: "4 sites and 2 certificates". */
export function dependentsSentence(t: T, dependents: readonly Dependent[]): string {
  return new Intl.ListFormat(t.locale, { style: "long", type: "conjunction" }).format(dependents.map((dependent) => dependentWords(t, dependent)));
}

/** The first `limit` names of what depends on something, and how many more there are. */
export function namesOf(names: readonly string[], limit: number): { shown: string[]; more: number } {
  return { shown: names.slice(0, limit), more: Math.max(0, names.length - limit) };
}

/** What the server says a component is for, as the code of a sentence the console has words for. */
export function purposeOf(item: Pick<SoftwareItem, "purpose" | "purpose_code">): { coded: CodedText; text: string } {
  return { coded: { code: item.purpose_code, params: {} }, text: item.purpose };
}

/**
 * The sentence that says why a component is never removed. The server names it by the component (`software.<id>.never`),
 * a sentence with no value to put in; null for a server whose console has no words for it.
 */
export function neverReason(locale: T["locale"], id: string): string | null {
  const said = serverSentence(locale, asCoded({ code: `software.${id}.never`, params: {} }), null, "text");
  return said === "" ? null : said;
}

/**
 * A package's version as a person says it: without the epoch (`1:9.6p1`) and the distribution's revision
 * (`-3ubuntu13.5`, `+dfsg`), which say which build of it this is and not which program. The whole one is the cell's title.
 */
export function shortVersion(version: string): string {
  const upstream = version.replace(/^\d+:/, "").split("-")[0] ?? version;
  const trimmed = upstream.replace(/\+(?:dfsg|ds|git|nmu|really|deb|ubuntu)\S*$/i, "");
  return trimmed === "" ? version : trimmed;
}

/**
 * The title of a job, with the component it names said as the Software page says it ("nginx stream module"), for a job of
 * the Software page; any other title is as it came. The id is the registry's, and is shown only for a component this console
 * has never heard of.
 */
export function withComponentName(title: CodedText | null, locale: Locale): CodedText | null {
  if (title === null || !SOFTWARE_JOB_TITLES.has(title.code)) return title;
  const id = title.params?.["component"];
  if (typeof id !== "string") return title;
  return { ...title, params: { ...title.params, component: componentName(bindT(locale), id) } };
}
