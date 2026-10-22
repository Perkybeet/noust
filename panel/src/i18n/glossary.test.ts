/**
 * The glossary of the interface, held in both catalogs (README.md, "The words of the interface").
 *
 * Four things are counted, and each count is held to glossary-baseline.json, which may only go
 * down, with the same contract as the design ratchet (styles/design-structure.test.ts):
 *
 * - a retired value (README, the "Retired" columns) written in a catalog: the count per namespace;
 * - a glossary word whose Spanish is not the glossary's: "Delete" translated "Borrar";
 * - an English text of fewer than forty characters that appears four times or more and whose Spanish
 *   differs from key to key (the case of R5: "Failed" said five ways);
 * - style: a title that ends in a full stop or is written with a capital on every word, three dots
 *   where the ellipsis goes.
 *
 * A count above its baseline fails (fix the text, or add the key to a documented exception in
 * glossary.table.ts); a count below it fails too, asking for `npm run design:baseline`, so progress is
 * locked in. The baseline is today's: what violates the glossary and belongs to a lane that has not
 * landed yet is counted here, not fixed here, and the final sweep takes it to zero.
 */

import { describe, expect, it } from "vitest";

import * as en from "./en";
import * as es from "./es";
import baseline from "./glossary-baseline.json";
import { DIVERGENCE_EXCEPTIONS, GLOSSARY, OLD_NAMES_KEPT, RETIRED, agreementStem } from "./glossary.table";
import type { Retired } from "./glossary.table";

type Node = string | { readonly [key: string]: Node };
type Section = Record<string, Record<string, number>>;

interface Baseline {
  $comment?: string;
  rules: Section;
}

const PLURAL_CATEGORIES = new Set(["zero", "one", "two", "few", "many", "other"]);

function isPlural(node: Node): node is Readonly<Record<string, string>> {
  return typeof node === "object" && typeof node["other"] === "string" && Object.keys(node).every((key) => PLURAL_CATEGORIES.has(key));
}

/** Every message as [dotted key, every text it can say: one for a string, each form for a plural]. */
function flatten(node: Node, prefix = ""): [string, string[]][] {
  if (typeof node === "string") return [[prefix, [node]]];
  if (isPlural(node)) return [[prefix, Object.values(node)]];
  return Object.entries(node).flatMap(([key, child]) => flatten(child, prefix === "" ? key : `${prefix}.${key}`));
}

/** One text per key: a plural is its `other` form. */
function flattenMain(node: Node, prefix = ""): [string, string][] {
  if (typeof node === "string") return [[prefix, node]];
  if (isPlural(node)) return [[prefix, node["other"] ?? ""]];
  return Object.entries(node).flatMap(([key, child]) => flattenMain(child, prefix === "" ? key : `${prefix}.${key}`));
}

type Language = "en" | "es";

const ENTRIES: Record<Language, readonly [string, string[]][]> = { en: flatten({ ...en }), es: flatten({ ...es }) };

const TEXT: Record<Language, ReadonlyMap<string, string>> = {
  en: new Map(flattenMain({ ...en })),
  es: new Map(flattenMain({ ...es })),
};

const namespaceOf = (key: string): string => key.split(".")[0] ?? key;
const wordsOf = (text: string): number => text.split(/\s+/).filter((word) => word !== "").length;

function bump(section: Section, rule: string, bucket: string): void {
  const counts = (section[rule] ??= {});
  counts[bucket] = (counts[bucket] ?? 0) + 1;
}

/** Whether a retired rule counts this text: not a deliberate old name, not an excepted key, and the right size. */
function counts(rule: Retired, key: string, text: string): boolean {
  if (OLD_NAMES_KEPT.some((kept) => kept.key.test(key))) return false;
  if (rule.except?.some((kept) => kept.key.test(key)) === true) return false;
  if (rule.maxWords !== undefined && wordsOf(text) > rule.maxWords) return false;
  if (rule.notSentence === true && /[.!?]$/.test(text)) return false;
  return rule.pattern.test(text);
}

function retiredValues(): Section {
  const section: Section = {};
  for (const rule of RETIRED) {
    section[`${rule.language}:${rule.id}`] = {};
    for (const [key, texts] of ENTRIES[rule.language]) {
      for (const text of texts) if (counts(rule, key, text)) bump(section, `${rule.language}:${rule.id}`, namespaceOf(key));
    }
  }
  return section;
}

/** Where a glossary word is translated otherwise: "Delete|Borrar" counts the keys that say it. */
function divergentPairs(): Section {
  const section: Section = { pairs: {} };
  for (const row of GLOSSARY) {
    for (const [key, text] of TEXT.en) {
      if (text !== row.en) continue;
      const spanish = TEXT.es.get(key) ?? "";
      if (!row.es.includes(spanish)) bump(section, "pairs", `${row.en}|${spanish}`);
    }
  }
  return section;
}

/**
 * The English texts of fewer than forty characters, four times or more, that Spanish translates more than one way.
 * Two translations that differ only in gender or number are one (`agreementStem`), and a text with a documented
 * exception (glossary.table.ts) is left out unless `withExceptions` asks for it.
 */
function divergentTranslations(withExceptions = false): Section {
  const byText = new Map<string, Set<string>>();
  const copies = new Map<string, number>();
  for (const [key, text] of TEXT.en) {
    if (text.length >= 40) continue;
    copies.set(text, (copies.get(text) ?? 0) + 1);
    const set = byText.get(text) ?? new Set<string>();
    set.add(agreementStem(TEXT.es.get(key) ?? ""));
    byText.set(text, set);
  }
  const section: Section = { divergence: {} };
  for (const [text, spanish] of byText) {
    if (!withExceptions && text in DIVERGENCE_EXCEPTIONS) continue;
    if ((copies.get(text) ?? 0) >= 4 && spanish.size > 1) (section["divergence"] ??= {})[text] = spanish.size;
  }
  return section;
}

const isTitleKey = (key: string): boolean => /(?:^|\.)(?:title|\w+Title)$/.test(key);

/** Small words that a title keeps in lower case. */
const SMALL = new Set(["a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "from", "as", "is", "it", "its", "no", "not", "your", "this", "that", "per"]);

/** A title of three words or more in which nearly every word begins with a capital ("Add A New Server"). */
function isTitleCase(text: string): boolean {
  const words = text
    .replace(/\{[^}]+\}/g, "")
    .split(/\s+/)
    .filter((word) => /[A-Za-z]/.test(word) && !SMALL.has(word.toLowerCase()));
  if (words.length < 3) return false;
  const capitalised = words.filter((word, index) => index > 0 && /^[A-Z]/.test(word));
  return capitalised.length >= 2 && capitalised.length >= words.length - 2;
}

/** Style: what DESIGN 7 says of a title and of the ellipsis. */
function style(): Section {
  const section: Section = {};
  const proper = /^settings\.integrations\.others\.title$/;
  const tooltip = /^services\.unitFile\.checkedTitle$/; // a title attribute of two sentences, not a title
  const placeholder = /^nodeTools\.connection\.bastionKeyDescription$/;
  for (const language of ["en", "es"] as const) {
    for (const [key, text] of TEXT[language]) {
      if (isTitleKey(key) && !tooltip.test(key) && text.endsWith(".") && !text.endsWith("...")) bump(section, `${language}:title-stop`, namespaceOf(key));
      if (text.includes("...") && !placeholder.test(key)) bump(section, `${language}:three-dots`, namespaceOf(key));
      if (language === "en" && isTitleKey(key) && isTitleCase(text) && !proper.test(key)) bump(section, "en:title-case", namespaceOf(key));
    }
  }
  return section;
}

/** The ids the ratchet knows: a baseline entry for any other is stale. */
const KNOWN_RULES: ReadonlySet<string> = new Set([
  ...RETIRED.map((rule) => `${rule.language}:${rule.id}`),
  "pairs",
  "divergence",
  "en:title-stop",
  "es:title-stop",
  "en:three-dots",
  "es:three-dots",
  "en:title-case",
]);

const CURRENT: Section = { ...retiredValues(), ...divergentPairs(), ...divergentTranslations(), ...style() };
const BASE = baseline as Baseline;
const MODE = import.meta.env["UPDATE_DESIGN_BASELINE"] as string | undefined;

function regressions(): string[] {
  const out: string[] = [];
  for (const [rule, buckets] of Object.entries(CURRENT)) {
    for (const [bucket, count] of Object.entries(buckets)) {
      const allowed = BASE.rules[rule]?.[bucket] ?? 0;
      if (count > allowed) out.push(`${rule} in ${bucket}: ${String(count)} (baseline ${String(allowed)}). ${hint(rule)}`);
    }
  }
  return out;
}

function improvements(): string[] {
  const out: string[] = [];
  for (const [rule, buckets] of Object.entries(BASE.rules)) {
    for (const [bucket, allowed] of Object.entries(buckets)) {
      const count = CURRENT[rule]?.[bucket] ?? 0;
      if (count < allowed) out.push(`${rule} in ${bucket} is down to ${String(count)} from ${String(allowed)}`);
    }
  }
  return out;
}

function hint(rule: string): string {
  const retired = RETIRED.find((entry) => `${entry.language}:${entry.id}` === rule);
  if (retired !== undefined) return `Retired in the glossary: write ${retired.instead}.`;
  if (rule === "pairs") return "A word of the glossary has one translation (README, \"The words of the interface\").";
  if (rule === "divergence") return "One English text, one Spanish translation: unify the keys or make the English differ.";
  return "DESIGN 7: a title has no final full stop and is not written with a capital on every word; an ellipsis is one character.";
}

function sortedObject<T>(entries: [string, T][]): Record<string, T> {
  return Object.fromEntries(entries.sort(([a], [b]) => a.localeCompare(b)));
}

if (MODE !== undefined && MODE !== "") {
  describe("glossary baseline", () => {
    it("is rewritten from today's counts", async () => {
      if (MODE !== "init") expect(regressions(), "Counts went up: fix them before moving the baseline.").toEqual([]);
      const next: Baseline = {
        $comment:
          "Written by `npm run design:baseline` (i18n/glossary.test.ts); may only go down. rules: the count of each retired value, glossary word translated otherwise, divergent translation and style breach, per catalog namespace (see glossary.table.ts).",
        rules: sortedObject(
          Object.entries(CURRENT)
            .filter(([, buckets]) => Object.keys(buckets).length > 0)
            .map(([rule, buckets]) => [rule, sortedObject(Object.entries(buckets))] as [string, Record<string, number>]),
        ),
      };
      const [fsModule, processModule] = ["node:fs", "node:process"];
      const fs = (await import(/* @vite-ignore */ fsModule)) as { writeFileSync: (path: string, data: string) => void };
      const { cwd } = (await import(/* @vite-ignore */ processModule)) as { cwd: () => string };
      fs.writeFileSync(`${cwd()}/src/i18n/glossary-baseline.json`, `${JSON.stringify(next, null, 2)}\n`);
    });
  });
} else {
  describe("the glossary in the catalogs", () => {
    it("reads both languages whole", () => {
      expect(ENTRIES.en.length).toBeGreaterThan(5000);
      expect(ENTRIES.es.length).toBe(ENTRIES.en.length);
    });

    it("lets no retired value, no glossary word translated otherwise and no style breach in beyond the baseline", () => {
      expect(regressions(), "New breaches of the glossary (panel/src/i18n/README.md, \"The words of the interface\")").toEqual([]);
    });

    it("locks in every improvement: run `npm run design:baseline` when a count goes down", () => {
      expect(improvements(), "Counts went down: run `npm run design:baseline` and commit i18n/glossary-baseline.json").toEqual([]);
    });

    it("keeps a documented exception only for an English text that still diverges", () => {
      const diverging = new Set(Object.keys(divergentTranslations(true)["divergence"] ?? {}));
      expect(Object.keys(DIVERGENCE_EXCEPTIONS).filter((text) => !diverging.has(text))).toEqual([]);
    });

    it("has a baseline entry for no rule that does not exist", () => {
      expect(Object.keys(BASE.rules).filter((rule) => !KNOWN_RULES.has(rule))).toEqual([]);
    });
  });
}

describe("what the retired values catch", () => {
  const EXAMPLES: Readonly<Record<string, { yes: readonly string[]; no: readonly string[] }>> = {
    "en:redeploy": { yes: ["Redeploy", "Redeploy all"], no: ["Pushes to this branch can redeploy it later."] },
    "en:deploy-again": { yes: ["Deploy again"], no: ["Fix the error, then deploy again."] },
    "en:update-now": { yes: ["Update now"], no: ["{count} can update now."] },
    "en:retry-variants": { yes: ["Retry now", "Retry the failed", "Retry certificate"], no: ["Retry failed", "Retry"] },
    "en:reading": { yes: ["Reading the processes", "Reading {range}…"], no: ["Reading them needs this server's GitHub App on the repository."] },
    "en:make-verb": { yes: ["Make a file", "Make it public"], no: ["Make the token from a terminal and paste it into the field below here."] },
    "en:confirm-or-ok": { yes: ["Confirm", "OK", "Confirm and continue"], no: ["Confirm it's you", "OK, understood"] },
    "en:health-check": { yes: ["Change health check", "Health checks"], no: ["Startup check"] },
    "en:unit-label": { yes: ["Unit", "Systemd unit", "Unit security"], no: ["The systemd unit the site is in front of.", "Unit of the time a preview lives and ends"] },
    "en:emergency-access": { yes: ["Emergency access"], no: ["Access token"] },
    "en:deliveries": { yes: ["Deliveries", "Recent Deliveries"], no: ["Pushes received"] },
    "en:open-capitalised": { yes: ["Open Approvals", "Open Activity"], no: ["Open approvals", "Open Terminal"] },
    "en:learn-more": { yes: ["Learn more", "Learn more about it"], no: ["Open alerts"] },
    "en:old-names": { yes: ["Domains and certificates", "Log shipping"], no: ["Domains", "Logs and metrics export"] },
    "es:borrar": { yes: ["Borrar", "Borrar fila", "Borrarla de aquí", "para borrarse"], no: ["Borrador", "Eliminar"] },
    "es:leyendo": { yes: ["Leyendo los procesos", "Leyendo: {range}…"], no: ["Leyendo esto cuesta una petición."] },
    "es:parar-arrancar": { yes: ["Parar", "Arrancar", "Parar tras"], no: ["Lo lee el proceso al arrancar", "Detener"] },
    "es:fallo-verbo": { yes: ["Falló", "falló"], no: ["Fallido", "Falló un gancho que se ejecuta después"] },
    "es:en-vivo": { yes: ["En vivo", "Seguir el registro en vivo"], no: ["En directo", "En producción"] },
    "es:ganchos": { yes: ["Ganchos", "Ganchos de despliegue", "un gancho"], no: ["Hooks de despliegue"] },
    "es:dueno": { yes: ["Dueño", "su dueño", "Tokens sin dueño"], no: ["Propietario"] },
    "es:vuestro": { yes: ["vuestras direcciones", "podéis hacerlo con vosotros"], no: ["tus direcciones"] },
    "es:trabajo-job": { yes: ["Trabajos en {name}", "El log del trabajo"], no: ["Directorio de trabajo", "espacio de trabajo", "el mismo trabajo"] },
    "es:log-word": { yes: ["Envío de logs", "El log del trabajo"], no: ["registros", "Catálogo"] },
    "es:ok-or-bien": { yes: ["OK", "Bien"], no: ["Correcto", "Bien hecho, ya está listo"] },
    "es:volver-back": { yes: ["Volver"], no: ["Volver atrás", "Atrás"] },
    "es:reconstruir": { yes: ["Reconstruir", "Reconstruir la imagen"], no: ["Recompilar"] },
    "es:abrir-capitalised": { yes: ["Abrir Aprobaciones", "Abrir Cuentas"], no: ["Abrir aprobaciones", "Abrir Terminal"] },
    "es:mas-informacion": { yes: ["Más información", "Saber más"], no: ["Abrir alertas"] },
    "es:old-names": { yes: ["Dominios y certificados", "Envío de logs"], no: ["Dominios", "Exportación de registros y métricas"] },
  };

  it("has an example of what each retired value catches and what it leaves alone", () => {
    expect(Object.keys(EXAMPLES).sort()).toEqual(RETIRED.map((rule) => `${rule.language}:${rule.id}`).sort());
  });

  it.each(Object.entries(EXAMPLES))("%s", (id, example) => {
    const rule = RETIRED.find((entry) => `${entry.language}:${entry.id}` === id);
    expect(rule, id).toBeDefined();
    if (rule === undefined) return;
    for (const text of example.yes) expect(counts(rule, "x.y", text), `${text} should be caught`).toBe(true);
    for (const text of example.no) expect(counts(rule, "x.y", text), `${text} should be left alone`).toBe(false);
  });

  it("leaves a palette keyword and the map of renamed screens their old name, and an excepted key its word", () => {
    const rule = RETIRED.find((entry) => entry.id === "old-names" && entry.language === "en");
    const emergency = RETIRED.find((entry) => entry.id === "emergency-access");
    if (rule === undefined || emergency === undefined) throw new Error("rule missing");
    expect(counts(rule, "nav.screens.logs.keywords", "Log shipping")).toBe(false);
    expect(counts(rule, "nav.renamed.logShipping", "Log shipping")).toBe(false);
    expect(counts(rule, "settings.title", "Log shipping")).toBe(true);
    expect(counts(emergency, "auditEvents.action.auth_break_glass", "Emergency access used")).toBe(false);
    expect(counts(emergency, "auth.emergency.title", "Emergency access")).toBe(true);
  });
});

describe("what counts as one translation", () => {
  it("merges the genders and the numbers of one word, and keeps two words apart", () => {
    expect(agreementStem("Fallido")).toBe(agreementStem("Fallida"));
    expect(agreementStem("Fallidos")).toBe(agreementStem("Fallidas"));
    expect(agreementStem("Pendiente")).toBe(agreementStem("Pendientes"));
    expect(agreementStem("Con fallos")).toBe(agreementStem("con fallo"));
    expect(agreementStem("Desactivado")).not.toBe(agreementStem("Apagado"));
    expect(agreementStem("Quitar filtros")).not.toBe(agreementStem("Borrar filtros"));
    expect(agreementStem("Se quitó a {name}")).not.toBe(agreementStem("Se quitó {name}"));
  });
});

describe("what the style checks catch", () => {
  it("tells a title written with a capital on every word from a sentence and from proper names in a sentence", () => {
    expect(isTitleCase("Add A New Server")).toBe(true);
    expect(isTitleCase("Turn On Instant Rollback For {domain}")).toBe(true);
    expect(isTitleCase("Add a new server")).toBe(false);
    expect(isTitleCase("Turn on instant rollback for GitHub")).toBe(false);
    expect(isTitleCase("Backup schedule")).toBe(false);
  });
});
