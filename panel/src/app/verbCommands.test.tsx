import { describe, expect, it, vi } from "vitest";

import type { AppInfo } from "../features/apps/data";
import { bindT } from "../i18n";
import { appVerbCommands } from "./verbCommands";
import type { AppVerb } from "./verbCommands";

const t = bindT("en");

function app(overrides: Partial<AppInfo> & { domain: string }): AppInfo {
  return { name: overrides.domain.replaceAll(".", "-"), status: "running", app_type: "nextjs", layout: "releases", ...overrides } as AppInfo;
}

function build(apps: readonly AppInfo[], options: { can?: (permission: string, team: string | null) => boolean; current?: string | null; run?: (verb: AppVerb, app: AppInfo) => void } = {}) {
  return appVerbCommands(apps, { t, can: options.can ?? (() => true), current: options.current ?? null, run: options.run ?? (() => undefined) });
}

const labels = (commands: ReturnType<typeof build>) => commands.map((command) => command.label);

describe("the palette's verbs on an application", () => {
  it("offers a running application the daily verbs, in the header's order, named with the application", () => {
    const commands = build([app({ domain: "shop.example.com" })]);
    expect(labels(commands)).toEqual(["Restart shop.example.com", "Stop shop.example.com", "Roll back shop.example.com", "Update shop.example.com"]);
    expect(commands.every((command) => command.group === "Operations")).toBe(true);
  });

  it("offers Start, not Stop, to an application that is stopped", () => {
    expect(labels(build([app({ domain: "a.example.com", status: "stopped" })]))).toEqual([
      "Restart a.example.com",
      "Start a.example.com",
      "Roll back a.example.com",
      "Update a.example.com",
    ]);
  });

  it("offers neither Start nor Stop to a stack that runs outside its unit: the banner's hand-back is the way", () => {
    const names = labels(build([app({ domain: "stack.example.com", status: "running_unmanaged", app_type: "docker-compose" })]));
    expect(names).not.toContain("Start stack.example.com");
    expect(names).not.toContain("Stop stack.example.com");
    expect(names).toContain("Restart stack.example.com");
  });

  it("offers a static site only what a site without a unit can take: going back and an update", () => {
    expect(labels(build([app({ domain: "docs.example.com", status: "static", app_type: "static" })]))).toEqual(["Roll back docs.example.com", "Update docs.example.com"]);
  });

  it("leaves out what the principal may not do in the application's team, as the buttons' permissions say", () => {
    const asked: [string, string | null][] = [];
    const can = (permission: string, team: string | null) => {
      asked.push([permission, team]);
      return permission === "apps.operate";
    };
    const names = labels(build([app({ domain: "shop.example.com", team: "agency" })], { can }));
    expect(names).toEqual(["Restart shop.example.com", "Stop shop.example.com"]);
    expect(asked).toContainEqual(["apps.operate", "agency"]);
    expect(asked).toContainEqual(["apps.deploy", "agency"]);
  });

  it("offers an application being deployed nothing that would collide with the deploy, except the update, which folds into it", () => {
    expect(labels(build([app({ domain: "busy.example.com", status: "deploying" })]))).toEqual(["Roll back busy.example.com", "Update busy.example.com"]);
  });

  it("is found by the words a person says for it, in both languages, and by the application", () => {
    const [restart] = build([app({ domain: "shop.example.com" })]);
    expect(restart?.keywords).toContain("reboot");
    expect(restart?.keywords).toContain("shop.example.com");
  });

  it("shows only the verbs of the application on screen while nothing is typed", () => {
    const commands = build([app({ domain: "a.example.com" }), app({ domain: "b.example.com" })], { current: "b.example.com" });
    const browsing = commands.filter((command) => command.browse === true).map((command) => command.id);
    expect(browsing).toEqual(["verb:restart:b.example.com", "verb:stop:b.example.com", "verb:rollback:b.example.com", "verb:update:b.example.com"]);
  });

  it("runs the verb on the application it names", () => {
    const run = vi.fn();
    const shop = app({ domain: "shop.example.com" });
    const [restart, , rollback] = build([shop], { run });
    restart?.run();
    rollback?.run();
    expect(run).toHaveBeenNthCalledWith(1, "restart", shop);
    expect(run).toHaveBeenNthCalledWith(2, "rollback", shop);
  });

  it("asks for confirmation or a dialog before the verbs that need one, by leaving focus to the dialog that opens", () => {
    const commands = build([app({ domain: "shop.example.com" })]);
    const kinds = Object.fromEntries(commands.map((command) => [command.id, command.kind]));
    expect(kinds).toMatchObject({
      "verb:restart:shop.example.com": "action",
      "verb:update:shop.example.com": "action",
      "verb:stop:shop.example.com": "navigate",
      "verb:rollback:shop.example.com": "navigate",
    });
  });
});
