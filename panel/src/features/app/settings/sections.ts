import type { LucideIcon } from "lucide-react";

import type { App } from "../../../api/queries/apps";
import type { PlainKey } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { APP_SCREENS, APP_SETTINGS_SCREENS } from "../../nav/app";
import { iconOf } from "../../nav/icons";
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
  /** Drawn beside the name, when the register gives the destination one. */
  icon?: LucideIcon;
  /** The words the command palette also finds it by: the register's. */
  keywords?: PlainKey;
  danger?: boolean;
  /** Leaves the section out for an application it does not concern. */
  when?: (app: App) => boolean;
}

/** An application Noust built: the sections about building, deploying and rewriting it concern only these. */
function built(app: App): boolean {
  return !isAdopted(app);
}

/**
 * Which applications each subsection concerns, by its name in the register (`features/nav/app.ts`,
 * which owns the path, the label, the group and the icon: this file never spells them). A name that
 * is not here concerns every application. Build is every built application's: the commands of a
 * type that has a block, and the sandbox its builds run in, which every type has. A container
 * application has its own settings, and a Compose stack its own page.
 */
const CONCERNS: Readonly<Record<string, (app: App) => boolean>> = {
  source: built,
  build: built,
  container: (app) => app.app_type === "container",
  deploys: built,
  automation: built,
  compose: (app) => built(app) && app.app_type === "docker-compose",
  "deploy-on-push": built,
  hooks: built,
  previews: built,
  resources: built,
  processes: (app) => built(app) && hasUnit(app),
  mounts: (app) => built(app) && hasUnit(app) && app.app_type !== "docker-compose",
  disk: built,
  security: (app) => built(app) && hasUnit(app),
  access: built,
  protection: built,
  export: built,
};

/** The subsections that are set apart, last. */
const APART: ReadonlySet<string> = new Set(["delete"]);

function groupOf(id: string, group: string | undefined): SettingsGroupId {
  const known = SETTINGS_GROUPS.find((candidate) => candidate === group);
  if (known === undefined) throw new Error(`The register puts ${id} in the group ${group ?? "(none)"}, which the settings do not have`);
  return known;
}

const SETTINGS_ROOT = "/apps/$domain/settings";

/**
 * The subsections of an application's settings, in order and in their groups: each its own URL,
 * Delete last and apart. They are the register's (`APP_SETTINGS_SCREENS`), so what the side list
 * draws, what the command palette finds and the icon of each are one list; this file only says
 * which applications each concerns (`CONCERNS`). The settings' navigation and the command palette
 * read it, so a section cannot be on a page and missing from the search (checklist M-04).
 */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { path: "", label: APP_SCREENS.find((screen) => screen.id === "app.settings")?.rootLabel ?? "appSettings.nav.general", icon: iconOf({ id: "app.settings" }) },
  ...APP_SETTINGS_SCREENS.map((screen): SettingsSection => {
    const name = screen.to.slice(SETTINGS_ROOT.length + 1);
    const concerns = CONCERNS[name];
    return {
      path: `/${name}`,
      label: screen.label,
      group: groupOf(screen.id, screen.group),
      icon: iconOf(screen),
      ...(screen.keywords !== undefined ? { keywords: screen.keywords } : {}),
      ...(APART.has(name) ? { danger: true } : {}),
      ...(concerns !== undefined ? { when: concerns } : {}),
    };
  }),
];
