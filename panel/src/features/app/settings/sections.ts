import type { App } from "../../../api/queries/apps";
import type { PlainKey } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { hasBuildBlock } from "../../build/model";

/** One subsection of an application's settings. */
export interface SettingsSection {
  /** The path under `/apps/$domain/settings`, empty for the first. */
  path: string;
  label: PlainKey;
  danger?: boolean;
  /** Leaves the section out for an application it does not concern. */
  when?: (app: App) => boolean;
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
  { path: "/source", label: "appSettings.nav.source" },
  { path: "/container", label: "appSettings.nav.container", when: (app) => app.app_type === "container" },
  { path: "/deploys", label: "appSettings.nav.deploys" },
  { path: "/deploy-on-push", label: "appSettings.nav.push" },
  { path: "/hooks", label: "appSettings.nav.hooks" },
  { path: "/build", label: "appSettings.nav.buildCommands", when: (app) => hasBuildBlock(app.app_type) },
  { path: "/builds", label: "appSettings.nav.builds" },
  { path: "/resources", label: "appSettings.nav.resources" },
  { path: "/processes", label: "runtime.nav.processes", when: (app) => hasUnit(app) },
  { path: "/security", label: "runtime.nav.security", when: (app) => hasUnit(app) },
  { path: "/mounts", label: "runtime.nav.mounts", when: (app) => hasUnit(app) && app.app_type !== "docker-compose" },
  { path: "/previews", label: "appSettings.nav.previews" },
  { path: "/export", label: "appSettings.nav.export" },
  { path: "/delete", label: "appSettings.nav.delete", danger: true },
];
