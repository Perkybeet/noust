import { describe, expect, it } from "vitest";

import * as en from "./en";
import * as es from "./es";
import { GLOSSARY } from "./glossary.table";
import readme from "./README.md?raw";

/**
 * The verbs of docs/DESIGN.md section 7, held in both catalogs (checklist M-32). Coolify's users
 * could not tell "Redeploy all" from "Restart", nor an application's update from the system's:
 * here each word means one thing, and the three kinds of update are named by what they update.
 *
 * - Update: an application pulls its source and builds a new release.
 * - Restart: the same release, started again.
 * - Stop and Start: the service, with no build.
 * - Roll back: an earlier release.
 * - System updates: the operating system's packages. Update Noust: Noust itself.
 */
describe("the verbs of the console", () => {
  it("name an application's daily verbs once each, in English", () => {
    const actions = en.appPages.actions;
    expect({ update: actions.update, restart: actions.restart, stop: actions.stop, start: actions.start, rollBack: actions.rollBack }).toEqual({
      update: "Update",
      restart: "Restart",
      stop: "Stop",
      start: "Start",
      rollBack: "Roll back",
    });
  });

  it("name them in Spanish with the infinitives of the glossary", () => {
    const actions = es.appPages.actions;
    expect({ update: actions.update, restart: actions.restart, stop: actions.stop, start: actions.start, rollBack: actions.rollBack }).toEqual({
      update: "Actualizar",
      restart: "Reiniciar",
      stop: "Detener",
      start: "Iniciar",
      rollBack: "Volver atrás",
    });
  });

  it("never give two verbs of the same row the same word", () => {
    const actions = en.appPages.actions;
    const words = [actions.update, actions.restart, actions.stop, actions.start, actions.rollBack];
    expect(new Set(words).size).toBe(words.length);
  });

  it("call the operating system's updates system updates wherever they are a destination or a figure", () => {
    expect(en.server.tabs.updates).toMatch(/system/i);
    expect(en.overview.figures.updates.label).toMatch(/system/i);
    expect(es.server.tabs.updates).toMatch(/sistema/i);
    expect(es.overview.figures.updates.label).toMatch(/sistema/i);
  });

  it("call the fleet's updates view what it holds: Noust and the system", () => {
    expect(en.fleet.tabs.updates).toMatch(/Noust/);
    expect(en.fleet.tabs.updates).toMatch(/system/i);
    expect(es.fleet.tabs.updates).toMatch(/Noust/);
    expect(es.fleet.tabs.updates).toMatch(/sistema/i);
  });

  it("keep 'Update Noust' for Noust itself, never for an application", () => {
    expect(en.fleet.updates.updateNoust).toBe("Update Noust");
    expect(en.fleet.actions.apps_update.label).not.toMatch(/Noust/);
    expect(en.fleet.actions.noust_update.label).toBe("Update Noust");
  });

  it("have no button called Deploy that only opens a form: the entry points say New application", () => {
    expect(en.apps.newApplication).toBe("New application");
    // The Overview of an empty server names what each button does instead: a demo, a repository.
    expect(en.overview.firstSteps.demo.action).toBe("Try a demo");
    expect(en.overview.firstSteps.repository.action).not.toMatch(/deploy/i);
    expect(en.shell.commands.newApplication).toBe("New application");
  });
});

type Node = string | { readonly [key: string]: Node };

const PLURAL = new Set(["zero", "one", "two", "few", "many", "other"]);

/** Every message as [dotted key, text]; a plural is its `other` form. */
function flatten(node: Node, prefix = ""): [string, string][] {
  if (typeof node === "string") return [[prefix, node]];
  if (typeof node["other"] === "string" && Object.keys(node).every((key) => PLURAL.has(key))) return [[prefix, node["other"]]];
  return Object.entries(node).flatMap(([key, child]) => flatten(child, prefix === "" ? key : `${prefix}.${key}`));
}

const english = new Map(flatten({ ...en }));
const spanish = new Map(flatten({ ...es }));

/**
 * The glossary's verbs and states (README, "Actions" and "States"), one row at a time, in both
 * languages. Where a key says exactly the English word, Spanish must be the glossary's; the keys
 * that still say it another way are counted by glossary.test.ts and go down to zero there.
 */
describe("the glossary's verbs and states", () => {
  it("are all written in the README, in English and in Spanish", () => {
    for (const row of GLOSSARY) {
      expect(readme.includes(`**${row.en}**`), `README does not carry **${row.en}**`).toBe(true);
      expect(readme.includes(row.es[0] ?? ""), `README does not carry ${row.es[0] ?? ""}`).toBe(true);
    }
  });

  it.each(GLOSSARY.map((row) => [row.en, row] as const))("%s has its Spanish word on at least one key", (_, row) => {
    const keys = [...english].filter(([, text]) => text === row.en).map(([key]) => key);
    const conforming = keys.filter((key) => row.es.includes(spanish.get(key) ?? ""));
    expect(conforming.length, `no key says ${row.en} as ${row.es.join(" / ")}`).toBeGreaterThan(0);
  });

  it("keeps the daily verbs of an application on the same words as the glossary", () => {
    const byEnglish = new Map(GLOSSARY.map((row) => [row.en, row.es]));
    const actions = es.appPages.actions;
    expect(byEnglish.get("Update")).toContain(actions.update);
    expect(byEnglish.get("Restart")).toContain(actions.restart);
    expect(byEnglish.get("Stop")).toContain(actions.stop);
    expect(byEnglish.get("Start")).toContain(actions.start);
    expect(byEnglish.get("Roll back")).toContain(actions.rollBack);
  });

  it("say Remove and Delete once each way: Quitar unlinks, Eliminar destroys", () => {
    const byEnglish = new Map(GLOSSARY.map((row) => [row.en, row.es]));
    expect(byEnglish.get("Remove")).toEqual(["Quitar"]);
    expect(byEnglish.get("Delete")).toEqual(["Eliminar"]);
  });

  it("never give two English words the same canonical Spanish", () => {
    const owners = new Map<string, string[]>();
    for (const row of GLOSSARY) owners.set(row.es[0] ?? "", [...(owners.get(row.es[0] ?? "") ?? []), row.en]);
    // Close is the button and Dismiss the notice's: Spanish says Cerrar for both, and the glossary says so.
    const shared = [...owners].filter(([, words]) => words.length > 1).map(([word, words]) => `${word}: ${words.join(", ")}`);
    expect(shared).toEqual(["Cerrar: Dismiss, Close"]);
  });
});
