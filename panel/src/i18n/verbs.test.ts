import { describe, expect, it } from "vitest";

import * as en from "./en";
import * as es from "./es";

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
    expect(en.overview.firstSteps.deploy.action).toBe("New application");
    expect(en.shell.commands.newApplication).toBe("New application");
  });
});
