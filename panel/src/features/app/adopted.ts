import type { App } from "../../api/queries/apps";

/**
 * Whether an application is a site Noust adopted rather than built (checklist B-85): its unit,
 * its site and its files are the operator's, so Noust shows it, restarts its unit and reads its
 * logs, and offers nothing that would build, rewrite or remove it. The one question every part of
 * the console asks, so the answer cannot differ between the header, the settings and the list.
 */
export function isAdopted(app: Pick<App, "app_type"> | undefined): boolean {
  return app?.app_type === "adopted";
}
