import type { App } from "../../../api/queries/apps";
import type { PlainKey } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { isAdopted } from "../adopted";

/**
 * The four pages that gather what were eighteen subsections of an application's settings (4.2, item 116): the
 * column of the application lists these four as sections of their own, and each draws the subsections it
 * gathers one under another, as surfaces with a title. The ids are the last segment of the page's path under
 * `/apps/$domain`; the register (`features/nav/app.ts`) owns its name, its group and its icon.
 */
export const CONFIG_PAGES = ["code", "triggers", "resources", "access"] as const;

export type ConfigPageId = (typeof CONFIG_PAGES)[number];

/** One subsection a page gathers: the form or the group of cards it was, still one piece with its own tests. */
export interface PagePart {
  /** What it was called as a subsection of the settings: its path segment, the name an old address carries. */
  id: string;
  /** The heading it has when a page folds it, or names it: the old subsection's. */
  label: PlainKey;
  /** Leaves the part out for an application it does not concern. */
  when?: (app: App) => boolean;
}

/** An application Noust built: the parts about building, deploying and rewriting it concern only these. */
function built(app: App): boolean {
  return !isAdopted(app);
}

/**
 * Which applications each part concerns. A container application has its own settings, a Compose stack its own
 * options, and what needs a service of its own needs an application that has a unit.
 */
export const PAGE_PARTS: Readonly<Record<ConfigPageId, readonly PagePart[]>> = {
  code: [
    { id: "source", label: "appSettings.nav.source", when: built },
    { id: "build", label: "appSettings.nav.build", when: built },
    { id: "container", label: "appSettings.nav.container", when: (app) => app.app_type === "container" },
  ],
  triggers: [
    { id: "deploys", label: "appSettings.nav.deploys", when: built },
    { id: "compose", label: "appSettings.nav.compose", when: (app) => built(app) && app.app_type === "docker-compose" },
    { id: "automation", label: "appSettings.nav.automation", when: built },
    { id: "deploy-on-push", label: "appSettings.nav.push", when: built },
    { id: "hooks", label: "appSettings.nav.hooks", when: built },
    { id: "previews", label: "appSettings.nav.previews", when: built },
  ],
  resources: [
    { id: "general", label: "appSettings.nav.general" },
    { id: "resources", label: "appSettings.nav.resources", when: built },
    { id: "processes", label: "runtime.nav.processes", when: (app) => built(app) && hasUnit(app) },
    { id: "mounts", label: "runtime.nav.mounts", when: (app) => built(app) && hasUnit(app) && app.app_type !== "docker-compose" },
    { id: "disk", label: "runtime.nav.disk", when: built },
    { id: "security", label: "runtime.nav.security", when: (app) => built(app) && hasUnit(app) },
  ],
  access: [
    { id: "access", label: "network.access.nav", when: built },
    { id: "protection", label: "network.protection.nav", when: built },
  ],
};

/** The parts of a page that concern an application, in order. */
export function partsOf(page: ConfigPageId, app: App): readonly PagePart[] {
  return PAGE_PARTS[page].filter((part) => part.when === undefined || part.when(app));
}

/**
 * Where an old subsection's address goes now: the page that gathers it. `General`, which listed what the
 * application is and how it runs, is the first part of Resources and running.
 */
export const SUBSECTION_PAGE: Readonly<Record<string, ConfigPageId | "export" | "delete">> = Object.fromEntries([
  ...CONFIG_PAGES.flatMap((page) => PAGE_PARTS[page].map((part) => [part.id, page] as const)),
  ["export", "export"],
  ["delete", "delete"],
]);
