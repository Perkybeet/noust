/**
 * The page the panel reads, from where the person is: a deployment or the diagnosis of an application is read by Noust (the model is not
 * asked to go and find it), and anywhere else the panel reads no page in particular. The person can take it away.
 */

import type { T } from "../../i18n";
import type { PageRef } from "./model";

const DEPLOYMENT = /^\/apps\/([^/]+)\/deployments\/(\d+)\/?$/;
const DIAGNOSIS = /^\/apps\/([^/]+)\/diagnose\/?$/;

/**
 * @param pathname - The console's current path, without a server prefix.
 * @returns The page that path is, or null when it is not one the panel reads.
 */
export function pageForPath(pathname: string): PageRef | null {
  const deployment = DEPLOYMENT.exec(pathname);
  if (deployment?.[1] !== undefined && deployment[2] !== undefined) return { kind: "deployment", id: Number(deployment[2]), domain: decodeURIComponent(deployment[1]) };
  const diagnosis = DIAGNOSIS.exec(pathname);
  if (diagnosis?.[1] !== undefined) return { kind: "diagnosis", domain: decodeURIComponent(diagnosis[1]) };
  return null;
}

/** How a page is named to the person, in their language. */
export function pageLabel(t: T, page: PageRef): string {
  switch (page.kind) {
    case "deployment":
      return page.domain !== undefined ? t("assistant.panel.page.deployment", { id: page.id, domain: page.domain }) : t("assistant.panel.page.deploymentBare", { id: page.id });
    case "diagnosis":
      return t("assistant.panel.page.diagnosis", { domain: page.domain });
    case "alert":
      return t("assistant.panel.page.alert", { rule: page.rule });
    case "page":
      return page.route;
  }
}
