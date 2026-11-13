import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { recipesQuery } from "../../api/queries/recipes";
import { ListPage } from "../../components/page/ListPage";
import { ScopeLine } from "../../components/page/ScopeLine";
import { Button } from "../../components/ui/Button";
import { useT } from "../../i18n";
import { useHasFleet } from "../../nodes/servers";
import { CatalogPackageDialog } from "./CatalogPackage";
import { CatalogProblems } from "./CatalogProblems";
import { OwnRecipesDialog } from "./OwnRecipes";

export interface CatalogFrameProps {
  /**
   * The recipes are the same on every server, so on a fleet the page says so in the first line of its body and that the server is
   * chosen when one is used (`ScopeLine` library), and the catalog is the same page with a server chosen, with All servers and on a hub.
   */
  library?: boolean;
  /** The tab's own filters, when it has them. */
  filters?: ReactNode;
  /** The tab's own notice (a job in hand, a failure). */
  notice?: ReactNode;
  children: ReactNode;
}

/**
 * The frame of the catalog's two views, the recipes and the ones installed: the header and the strip,
 * then the view. The title and the strip are the same on both, so switching moves nothing but the
 * content under them.
 */
export function CatalogFrame({ library = false, filters, notice, children }: CatalogFrameProps) {
  const t = useT();
  const hasFleet = useHasFleet();
  const everything = useQuery(recipesQuery());
  const [adding, setAdding] = useState(false);
  const [packageOpen, setPackageOpen] = useState(false);
  const problems = everything.data?.problems ?? [];
  const said = library && hasFleet;
  const shownNotice =
    notice !== undefined || problems.length > 0 || said ? (
      <div className="flex flex-col gap-3">
        {said ? <ScopeLine scope={{ kind: "library" }} /> : null}
        {notice}
        <CatalogProblems problems={problems} />
      </div>
    ) : undefined;
  return (
    <>
      <ListPage
        header={{
          title: t("nav.catalog.label"),
          description: t("catalog.page.description"),
          secondaryActions: (
            <>
              <Button onClick={() => setPackageOpen(true)}>{t("catalog.package.open")}</Button>
              <Button onClick={() => setAdding(true)}>{t("catalog.own.open")}</Button>
            </>
          ),
        }}
        {...(shownNotice !== undefined ? { notice: shownNotice } : {})}
        {...(filters !== undefined ? { filters } : {})}
      >
        {children}
      </ListPage>
      <OwnRecipesDialog open={adding} onOpenChange={setAdding} />
      <CatalogPackageDialog open={packageOpen} onOpenChange={setPackageOpen} />
    </>
  );
}
