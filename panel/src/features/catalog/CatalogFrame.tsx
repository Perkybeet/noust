import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { installedRecipesQuery, recipesQuery } from "../../api/queries/recipes";
import { LinkTabs } from "../../app/LinkTabs";
import type { LinkTab } from "../../app/LinkTabs";
import { tabsOf, withCounts } from "../../app/navCore";
import { CommandHint } from "../../components/page/CommandHint";
import { ListPage } from "../../components/page/ListPage";
import { Button } from "../../components/ui/Button";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { CATALOG_SCREENS } from "../nav/catalog";
import { CatalogPackageDialog } from "./CatalogPackage";
import { CatalogProblems } from "./CatalogProblems";
import { OwnRecipesDialog } from "./OwnRecipes";

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
  const [adding, setAdding] = useState(false);
  const [packageOpen, setPackageOpen] = useState(false);
  const problems = everything.data?.problems ?? [];
  const tabs: LinkTab[] = withCounts(tabsOf(CATALOG_SCREENS),{
    "/catalog": everything.data?.total ?? null,
    "/catalog/installed": installed.data?.items.length ?? null,
  });
  const shownNotice =
    notice !== undefined || problems.length > 0 ? (
      <div className="flex flex-col gap-3">
        {notice}
        <CatalogProblems problems={problems} />
      </div>
    ) : undefined;
  return (
    <>
      <ListPage
        header={{
          title: t("catalog.page.title"),
          description: t("catalog.page.description"),
          server: node,
          secondaryActions: (
            <>
              <Button onClick={() => setPackageOpen(true)}>{t("catalog.package.open")}</Button>
              <Button onClick={() => setAdding(true)}>{t("catalog.own.open")}</Button>
            </>
          ),
        }}
        tabs={<LinkTabs label={t("catalog.tabs.label")} tabs={tabs} />}
        {...(shownNotice !== undefined ? { notice: shownNotice } : {})}
        {...(filters !== undefined ? { filters } : {})}
        footer={<CommandHint command={command} label={t("common.words.fromTerminal")} />}
      >
        {children}
      </ListPage>
      <OwnRecipesDialog open={adding} onOpenChange={setAdding} />
      <CatalogPackageDialog open={packageOpen} onOpenChange={setPackageOpen} />
    </>
  );
}
