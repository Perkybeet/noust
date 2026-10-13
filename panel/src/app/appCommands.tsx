/**
 * What the command palette finds inside the application on screen (checklist M-04): each of its
 * pages and each section of its settings, named with the application, so "build" finds
 * "Build commands: shop.example.com" and a section moved to another page is still found by its name.
 *
 * Read from the definitions that draw the pages, never copied from them: the tabs are `APP_TABS`
 * and the settings sections are `SETTINGS_SECTIONS`, the lists their navigations render.
 */

import { Box, Settings as SettingsIcon } from "lucide-react";

import type { App } from "../api/queries/apps";
import { SETTINGS_SECTIONS } from "../features/app/settings/sections";
import type { T } from "../i18n";
import type { Command } from "./CommandPalette";
import { APP_TABS } from "./nav";

/**
 * @param domain - The application on screen.
 * @param app - Its record, when it is read: a section it does not concern (the container's own
 * settings on a Node application) is left out; unknown, every section is offered.
 * @param t - The translator.
 * @param go - Opens a route of the console with its path parameters.
 * @returns One command per page of the application and per section of its settings.
 */
export function appSectionCommands(domain: string, app: App | undefined, t: T, go: (to: string, params?: Record<string, string>) => () => void): Command[] {
  const params = { domain };
  const pages = APP_TABS.filter((tab) => tab.exact !== true).map(
    (tab): Command => ({
      id: `app-section:${tab.to}`,
      group: "Pages",
      label: t("shell.commands.appSection", { section: t(tab.label), domain }),
      icon: <Box />,
      keywords: [tab.keywords !== undefined ? t(tab.keywords) : "", domain].join(" "),
      kind: "navigate",
      run: go(tab.to, params),
    }),
  );
  const settings = SETTINGS_SECTIONS.filter((section) => section.path !== "" && (section.when === undefined || app === undefined || section.when(app))).map(
    (section): Command => ({
      id: `app-setting:${section.path}`,
      group: "Pages",
      label: t("shell.commands.appSetting", { section: t(section.label), domain }),
      icon: <SettingsIcon />,
      keywords: [t("nav.appTabs.settings.label"), domain].join(" "),
      kind: "navigate",
      run: go(`/apps/$domain/settings${section.path}`, params),
    }),
  );
  return [...pages, ...settings];
}
