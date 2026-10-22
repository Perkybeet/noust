import { useQuery } from "@tanstack/react-query";

import { appsQuery } from "../../api/queries/apps";
import { installedRecipesQuery, recipesQuery } from "../../api/queries/recipes";
import { LinkTabs } from "../../app/LinkTabs";
import { tabsOf, withCounts } from "../../app/navCore";
import type { PageHeaderProps } from "../../app/PageHeader";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { APPS_TABS } from "../nav/appsTabs";
import { useOwnerScope } from "../teams/ownership";

/**
 * The three views of Applications as URLs (D-2): the applications, the recipes they can start from
 * and the recipes installed. Each has its count, kept in room while it is read so the strip does not
 * change width when it arrives. Read from the register, like every strip, and drawn by the list and
 * by both views of the catalogue, so switching moves nothing but what is under it.
 */
export function AppsTabs() {
  const t = useT();
  const apps = useQuery(appsQuery());
  const everything = useQuery(recipesQuery());
  const installed = useQuery(installedRecipesQuery());
  // The same count the list says: the applications of the team in view.
  const owner = useOwnerScope(apps.data?.apps ?? [], {});
  const tabs = withCounts(tabsOf(APPS_TABS), {
    "/apps": apps.data === undefined ? null : owner.inTeam.length,
    "/catalog": everything.data?.total ?? null,
    "/catalog/installed": installed.data?.items.length ?? null,
  });
  return <LinkTabs label={t("nav.landmarks.appsSections")} tabs={tabs} />;
}

/**
 * The header the three views share, so switching between them moves nothing but what is under the
 * strip: the same title and the same sentence (the strip says which view it is), the server named on
 * a fleet, and the view's own actions.
 */
export function appsHeader(t: T, node: string | null, view: Pick<PageHeaderProps, "primaryAction" | "secondaryActions"> = {}): PageHeaderProps {
  return { title: t("apps.page.title"), description: t("apps.page.description"), server: node, ...view };
}
