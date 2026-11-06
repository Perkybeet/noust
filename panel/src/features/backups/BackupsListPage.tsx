import type { ReactNode } from "react";

import { ListPage } from "../../components/page/ListPage";
import { useT } from "../../i18n";
import { StorageSummary } from "./StorageSummary";

export type BackupsTab = "backups" | "schedules" | "volumes" | "noust" | "destinations";

export interface BackupsListPageProps {
  /** The tab's own actions: its primary last, as the header orders them. */
  secondaryActions?: ReactNode;
  primaryAction?: ReactNode;
  notice?: ReactNode;
  filters?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}

/**
 * The frame of the Backups sections: one header (what backups are, the storage they take, and the
 * section's own primary action), then the section the column has open, with its count. The header is the
 * same on every section, so switching moves nothing but the content.
 */
export function BackupsListPage({ secondaryActions, primaryAction, notice, filters, footer, children }: BackupsListPageProps) {
  const t = useT();
  return (
    <ListPage
      header={{
        title: t("backups.page.title"),
        description: t("backups.page.description"),
        meta: <StorageSummary />,
        ...(secondaryActions !== undefined ? { secondaryActions } : {}),
        ...(primaryAction !== undefined ? { primaryAction } : {}),
      }}
      {...(notice !== undefined ? { notice } : {})}
      {...(filters !== undefined ? { filters } : {})}
      {...(footer !== undefined ? { footer } : {})}
    >
      {children}
    </ListPage>
  );
}
