import type { App } from "../../../api/queries/apps";
import type { PlainKey } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { hasBuildBlock } from "../../build/model";
import { isAdopted } from "../adopted";

/** One subsection of an application's settings. */
export interface SettingsSection {
  /** The path under `/apps/$domain/settings`, empty for the first. */
  path: string;
  label: PlainKey;
  danger?: boolean;
  /** Leaves the section out for an application it does not concern. */
  when?: (app: App) => boolean;
}

/** An application Noust built: the sections about building, deploying and rewriting it concern only these. */
function built(app: App): boolean {
  return !isAdopted(app);
}

/**
 * The subsections of an application's settings, in order: each its own URL, Delete last and
 * apart. `when` leaves one out for an application it does not concern: a container application's
 * own settings, and the build commands of a type that has a block (a Compose stack or a container
 * is built by its own configuration). The settings' navigation and the command palette read this
 * one list, so a section cannot be on a page and missing from the search (checklist M-04).
 */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { path: "", label: "appSettings.nav.general" },
  { path: "/source", label: "appSettings.nav.source", when: built },
  { path: "/container", label: "appSettings.nav.container", when: (app) => app.app_type === "container" },
  { path: "/deploys", label: "appSettings.nav.deploys", when: built },
  { path: "/deploy-on-push", label: "appSettings.nav.push", when: built },
  { path: "/hooks", label: "appSettings.nav.hooks", when: built },
  { path: "/access", label: "network.access.nav", when: built },
  { path: "/build", label: "appSettings.nav.buildCommands", when: (app) => built(app) && hasBuildBlock(app.app_type) },
  { path: "/builds", label: "appSettings.nav.builds", when: built },
  { path: "/resources", label: "appSettings.nav.resources", when: built },
  { path: "/disk", label: "runtime.nav.disk", when: built },
  { path: "/processes", label: "runtime.nav.processes", when: (app) => built(app) && hasUnit(app) },
  { path: "/security", label: "runtime.nav.security", when: (app) => built(app) && hasUnit(app) },
  { path: "/mounts", label: "runtime.nav.mounts", when: (app) => built(app) && hasUnit(app) && app.app_type !== "docker-compose" },
  { path: "/previews", label: "appSettings.nav.previews", when: built },
  { path: "/export", label: "appSettings.nav.export", when: built },
  { path: "/delete", label: "appSettings.nav.delete", danger: true },
];
