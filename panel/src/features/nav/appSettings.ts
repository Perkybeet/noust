import { section } from "../../app/navCore";
import type { Dest, Strip } from "../../app/navCore";
import { appSettings } from "./appRoot";

/**
 * The subsections of an application's settings (features/app/settings/sections.ts decides, per
 * application, which of them apply). The groups are the ones of 2.5 of the navigation design.
 */
const setting = (name: string, label: Dest["label"], group: string): Dest => section(appSettings, name, label, { scope: "server", owner: "resource", group });
export const APP_SETTINGS_SCREENS: Strip = [
  setting("source", "appSettings.nav.source", "code"),
  setting("build", "appSettings.nav.buildCommands", "code"),
  setting("builds", "appSettings.nav.builds", "code"),
  setting("container", "appSettings.nav.container", "code"),
  setting("deploys", "appSettings.nav.deploys", "deploys"),
  setting("deploy-on-push", "appSettings.nav.push", "triggers"),
  setting("hooks", "appSettings.nav.hooks", "triggers"),
  setting("previews", "appSettings.nav.previews", "triggers"),
  setting("resources", "appSettings.nav.resources", "runtime"),
  setting("disk", "runtime.nav.disk", "runtime"),
  setting("processes", "runtime.nav.processes", "runtime"),
  setting("mounts", "runtime.nav.mounts", "runtime"),
  setting("security", "runtime.nav.security", "runtime"),
  setting("access", "network.access.nav", "access"),
  setting("export", "appSettings.nav.export", "danger"),
  setting("delete", "appSettings.nav.delete", "danger"),
];

/**
 * The register's entry for a subsection of an application's settings, by its path under
 * `/apps/$domain/settings` ("" is General): what the list on the side takes its group from.
 */
export function appSettingsEntry(path: string): Dest | undefined {
  return path === "" ? appSettings : APP_SETTINGS_SCREENS.find((entry) => entry.to === `/apps/$domain/settings${path}`);
}
