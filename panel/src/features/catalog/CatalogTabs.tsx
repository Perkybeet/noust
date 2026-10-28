import { useQuery } from "@tanstack/react-query";

import { installedRecipesQuery, recipesQuery } from "../../api/queries/recipes";
import { LinkTabs } from "../../app/LinkTabs";
import { tabsOf, withCounts } from "../../app/navCore";
import { useT } from "../../i18n";
import { CATALOG_TABS } from "../nav/catalog";

/**
 * The two views of the catalog as URLs: the recipes an application can start from and the ones installed.
 * Each has its count, kept in room while it is read so the strip does not change width when it arrives.
 * Read from the register, like every strip, and drawn by both views, so switching moves nothing but what
 * is under it.
 */
export function CatalogTabs() {
  const t = useT();
  const everything = useQuery(recipesQuery());
  const installed = useQuery(installedRecipesQuery());
  const tabs = withCounts(tabsOf(CATALOG_TABS), {
    "/catalog": everything.data?.total ?? null,
    "/catalog/installed": installed.data?.items.length ?? null,
  });
  return <LinkTabs label={t("nav.landmarks.catalogSections")} tabs={tabs} />;
}
