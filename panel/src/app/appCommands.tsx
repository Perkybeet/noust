/**
 * What the command palette finds inside the application on screen (checklist M-04): each of its
 * sections, named with the application, so "build" finds "Code and build: shop.example.com". What
 * were subsections of its settings in 4.1 are gathered into four pages, and each page carries the old
 * names as search words (the register's `keywords`), so "Previews" still finds the page that holds them.
 * The sections off the strip, the ones the column groups under Fix (Diagnose, Terminal, Files), are found
 * the same way: a person who wants to run a command once has no reason to look for "Fix" (item 160).
 *
 * Read from the register, never copied from it: the sections are `APP_TABS` and `appColumn`, the lists
 * the strip and the column draw.
 */

import { Box } from "lucide-react";

import type { App } from "../api/queries/apps";
import type { T } from "../i18n";
import type { Command } from "./CommandPalette";
import { appColumn } from "./columns";
import { APP_TABS, dest } from "./nav";
import type { NavContext } from "./nav";
import { isAdopted } from "../features/app/adopted";
import { ADOPTED_TABS } from "../features/app/tabs";

/**
 * @param domain - The application on screen.
 * @param app - Its record, when it is read: a section it does not concern (the building and deploying
 * of an adopted site) is left out; unknown, every section is offered.
 * @param t - The translator.
 * @param go - Opens a route of the console with its path parameters.
 * @param ctx - What the principal may open and what the server can do: a section off the strip that needs a permission
 * (the shell) is left out without it. Absent, none is left out.
 * @returns One command per section of the application.
 */
export function appSectionCommands(domain: string, app: App | undefined, t: T, go: (to: string, params?: Record<string, string>) => () => void, ctx: NavContext = {}): Command[] {
  const params = { domain };
  const command = (to: string, label: string, keywords: string | undefined): Command => ({
    id: `app-section:${to}`,
    group: "Pages",
    label: t("shell.commands.appSection", { section: label, domain }),
    icon: <Box />,
    keywords: [keywords ?? "", domain].join(" "),
    kind: "navigate",
    run: go(to, params),
  });
  // What the column draws is what the palette finds: a section whose function this machine does not have is in neither.
  const features = ctx.features;
  const offered = new Set(appColumn(features !== undefined ? { features } : {}, false).flatMap((group) => group.items.map((item) => item.to as string)));
  const onStrip = APP_TABS.filter((tab) => tab.exact !== true && (features === undefined || offered.has(tab.to)) && (app === undefined || !isAdopted(app) || ADOPTED_TABS.has(tab.to)));
  const listed = new Set<string>(APP_TABS.map((tab) => tab.to));
  // What the column lists and the strip does not: the register decides which are offered to this principal and to an adopted site.
  const offStrip = appColumn(ctx, app !== undefined && isAdopted(app))
    .flatMap((group) => group.items)
    .filter((item) => item.id !== "app" && !listed.has(item.to));
  return [
    ...onStrip.map((tab) => command(tab.to, t(tab.label), tab.keywords !== undefined ? t(tab.keywords) : undefined)),
    ...offStrip.map((item) => {
      const keywords = dest(item.id).keywords;
      return command(item.to, t(item.label), keywords !== undefined ? t(keywords) : undefined);
    }),
  ];
}
