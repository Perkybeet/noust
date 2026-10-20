import type { App } from "../../../api/queries/apps";
import type { PlainKey } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { isAdopted } from "../adopted";

/**
 * Whose a group of subsections is, in the order the list draws them. General has no group: it is the
 * list's own first page. The ids are the register's (`features/nav/app.ts`); the titles are the
 * catalog's (`appSettings.groups`).
 */
export const SETTINGS_GROUPS = ["code", "deploys", "triggers", "runtime", "access", "danger"] as const;

export type SettingsGroupId = (typeof SETTINGS_GROUPS)[number];

/** One subsection of an application's settings. */
export interface SettingsSection {
  /** The path under `/apps/$domain/settings`, empty for the first. */
  path: string;
  label: PlainKey;
  /** The heading it is listed under; absent for General. */
  group?: SettingsGroupId;
  danger?: boolean;
  /** Leaves the section out for an application it does not concern. */
  when?: (app: App) => boolean;
}

/** An application Noust built: the sections about building, deploying and rewriting it concern only these. */
function built(app: App): boolean {
  return !isAdopted(app);
}

/**
 * The subsections of an application's settings, in order and in their groups: each its own URL,
 * Delete last and apart. `when` leaves one out for an application it does not concern: a container
 * application's own settings and a Compose stack's. Build is every built application's: the commands
 * of a type that has a block, and the sandbox its builds run in, which every type has. The settings'
 * navigation and the command palette read this one list, so a section cannot be on a page and missing
 * from the search (checklist M-04).
 */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { path: "", label: "appSettings.nav.general" },
  { path: "/source", label: "appSettings.nav.source", group: "code", when: built },
  { path: "/build", label: "appSettings.nav.build", group: "code", when: built },
  { path: "/container", label: "appSettings.nav.container", group: "code", when: (app) => app.app_type === "container" },
  { path: "/deploys", label: "appSettings.nav.deploys", group: "deploys", when: built },
  { path: "/automation", label: "appSettings.nav.automation", group: "deploys", when: built },
  { path: "/compose", label: "appSettings.nav.compose", group: "deploys", when: (app) => built(app) && app.app_type === "docker-compose" },
  { path: "/deploy-on-push", label: "appSettings.nav.push", group: "triggers", when: built },
  { path: "/hooks", label: "appSettings.nav.hooks", group: "triggers", when: built },
  { path: "/previews", label: "appSettings.nav.previews", group: "triggers", when: built },
  { path: "/resources", label: "appSettings.nav.resources", group: "runtime", when: built },
  { path: "/processes", label: "runtime.nav.processes", group: "runtime", when: (app) => built(app) && hasUnit(app) },
  { path: "/mounts", label: "runtime.nav.mounts", group: "runtime", when: (app) => built(app) && hasUnit(app) && app.app_type !== "docker-compose" },
  { path: "/disk", label: "runtime.nav.disk", group: "runtime", when: built },
  { path: "/security", label: "runtime.nav.security", group: "runtime", when: (app) => built(app) && hasUnit(app) },
  { path: "/access", label: "network.access.nav", group: "access", when: built },
  { path: "/protection", label: "network.protection.nav", group: "access", when: built },
  { path: "/export", label: "appSettings.nav.export", group: "danger", when: built },
  { path: "/delete", label: "appSettings.nav.delete", group: "danger", danger: true },
];
