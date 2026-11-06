/**
 * What the command palette finds inside the application on screen (checklist M-04): each of its
 * sections, named with the application, so "build" finds "Code and build: shop.example.com". What
 * were subsections of its settings in 4.1 are gathered into four pages, and each page carries the old
 * names as search words (the register's `keywords`), so "Previews" still finds the page that holds them.
 *
 * Read from the register, never copied from it: the sections are `APP_TABS`, the list the column draws.
 */

import { Box } from "lucide-react";

import type { App } from "../api/queries/apps";
import type { T } from "../i18n";
import type { Command } from "./CommandPalette";
import { APP_TABS } from "./nav";
import { isAdopted } from "../features/app/adopted";
import { ADOPTED_TABS } from "../features/app/tabs";

/**
 * @param domain - The application on screen.
 * @param app - Its record, when it is read: a section it does not concern (the building and deploying
 * of an adopted site) is left out; unknown, every section is offered.
 * @param t - The translator.
 * @param go - Opens a route of the console with its path parameters.
 * @returns One command per section of the application.
 */
export function appSectionCommands(domain: string, app: App | undefined, t: T, go: (to: string, params?: Record<string, string>) => () => void): Command[] {
  const params = { domain };
  return APP_TABS.filter((tab) => tab.exact !== true && (app === undefined || !isAdopted(app) || ADOPTED_TABS.has(tab.to))).map(
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
}
