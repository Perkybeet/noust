import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { installedRecipesQuery, recipesQuery } from "../../api/queries/recipes";
import { LinkTabs } from "../../app/LinkTabs";
import type { LinkTab } from "../../app/LinkTabs";
import { CommandHint } from "../../components/page/CommandHint";
import { ListPage } from "../../components/page/ListPage";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";

export interface CatalogFrameProps {
  /** The tab's own filters, when it has them. */
  filters?: ReactNode;
  /** The tab's own notice (a job in hand, a failure). */
  notice?: ReactNode;
  /** The CLI equivalent of the tab, once, at the foot. */
  command: string;
  children: ReactNode;
}

/**
 * The frame of the catalogue's two tabs: one header (what the catalogue is), the tabs as URLs
 * with their counts, then the tab. The header and the strip are the same on both, so switching
 * moves nothing but the content under them.
 */
export function CatalogFrame({ filters, notice, command, children }: CatalogFrameProps) {
  const t = useT();
  const { node } = useNode();
  const everything = useQuery(recipesQuery());
  const installed = useQuery(installedRecipesQuery());
  const tabs: LinkTab[] = [
    { label: "catalog.tabs.browse", to: "/catalog", exact: true, count: everything.data?.total ?? null },
    { label: "catalog.tabs.installed", to: "/catalog/installed", count: installed.data?.items.length ?? null },
  ];
  return (
    <ListPage
      header={{ title: t("catalog.page.title"), description: t("catalog.page.description"), server: node }}
      tabs={<LinkTabs label={t("catalog.tabs.label")} tabs={tabs} />}
      {...(notice !== undefined ? { notice } : {})}
      {...(filters !== undefined ? { filters } : {})}
      footer={<CommandHint command={command} label={t("newApp.source.terminal")} />}
    >
      {children}
    </ListPage>
  );
}
