import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { recipesQuery } from "../../api/queries/recipes";
import { CommandHint } from "../../components/page/CommandHint";
import { ListPage } from "../../components/page/ListPage";
import { Button } from "../../components/ui/Button";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { AppsTabs, appsHeader } from "../apps/AppsTabs";
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
 * The frame of the catalogue's two views, which are tabs of Applications (D-2): the header and the
 * strip Applications draws, then the view. The title and the strip are the same on all three, so
 * switching moves nothing but the content under them.
 */
export function CatalogFrame({ filters, notice, command, children }: CatalogFrameProps) {
  const t = useT();
  const { node } = useNode();
  const everything = useQuery(recipesQuery());
  const [adding, setAdding] = useState(false);
  const [packageOpen, setPackageOpen] = useState(false);
  const problems = everything.data?.problems ?? [];
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
        header={appsHeader(t, node, {
          description: t("catalog.page.description"),
          secondaryActions: (
            <>
              <Button onClick={() => setPackageOpen(true)}>{t("catalog.package.open")}</Button>
              <Button onClick={() => setAdding(true)}>{t("catalog.own.open")}</Button>
            </>
          ),
        })}
        tabs={<AppsTabs />}
        {...(shownNotice !== undefined ? { notice: shownNotice } : {})}
        {...(filters !== undefined ? { filters } : {})}
        footer={<CommandHint command={command} label={t("newApp.source.terminal")} />}
      >
        {children}
      </ListPage>
      <OwnRecipesDialog open={adding} onOpenChange={setAdding} />
      <CatalogPackageDialog open={packageOpen} onOpenChange={setPackageOpen} />
    </>
  );
}
