/**
 * Where a problem happens, in the words of the issue form.
 *
 * The form's dropdown (`.github/ISSUE_TEMPLATE/console_report.yml`, the field `area`) takes one of its
 * own options, in English, exactly: a value that is not one of them is ignored when the form is
 * opened with its fields filled in. So the console keeps the options as values and says them in
 * the person's language only on screen (`report.areas.*`); `areas.test.ts` holds this list to the
 * form, option by option.
 */

import { columnKindOf } from "../../app/columns";
import type { ColumnKind } from "../../app/columns";

/** The options of the form's `area` dropdown, in its order. */
export const AREAS = [
  { value: "Applications and deploys", key: "report.areas.apps" },
  { value: "Databases", key: "report.areas.databases" },
  { value: "Domains and certificates", key: "report.areas.domains" },
  { value: "Network, DNS and the web server", key: "report.areas.network" },
  { value: "The server (services, monitoring, software, updates)", key: "report.areas.server" },
  { value: "Security, accounts and access", key: "report.areas.security" },
  { value: "Backups and restore", key: "report.areas.backups" },
  { value: "Console (the browser)", key: "report.areas.console" },
  { value: "Fleet (a central and its servers)", key: "report.areas.fleet" },
  { value: "Command line", key: "report.areas.cli" },
  { value: "Documentation", key: "report.areas.docs" },
  { value: "Installing or updating Noust", key: "report.areas.install" },
  { value: "Recipes (the catalogue)", key: "report.areas.recipes" },
  { value: "Other", key: "report.areas.other" },
] as const;

export type AreaValue = (typeof AREAS)[number]["value"];

/** Which option each area of the console is, for a person who opens the report from a page of it. */
const OF_COLUMN: Record<ColumnKind, AreaValue> = {
  overview: "Console (the browser)",
  apps: "Applications and deploys",
  app: "Applications and deploys",
  catalog: "Recipes (the catalogue)",
  databases: "Databases",
  database: "Databases",
  cron: "The server (services, monitoring, software, updates)",
  domains: "Domains and certificates",
  network: "Network, DNS and the web server",
  logs: "The server (services, monitoring, software, updates)",
  alerts: "The server (services, monitoring, software, updates)",
  activity: "The server (services, monitoring, software, updates)",
  backups: "Backups and restore",
  security: "Security, accounts and access",
  server: "The server (services, monitoring, software, updates)",
  service: "The server (services, monitoring, software, updates)",
  settings: "Console (the browser)",
  account: "Security, accounts and access",
  team: "Security, accounts and access",
};

/**
 * The option the page a person is on belongs to.
 *
 * @param pathname - The path of the console on screen, such as `/databases/postgresql/shop`.
 * @returns The form's own option; "Other" for a page that is under no area.
 */
export function areaForPath(pathname: string): AreaValue {
  if (pathname === "/fleet" || pathname.startsWith("/fleet/")) return "Fleet (a central and its servers)";
  const kind = columnKindOf(pathname);
  return kind === null ? "Other" : OF_COLUMN[kind];
}
