import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { backupDestinationsQuery } from "../../api/queries/backupDestinations";
import { backupSchedulesQuery, backupsQuery } from "../../api/queries/backups";
import { LinkTabs } from "../../app/LinkTabs";
import type { LinkTab } from "../../app/LinkTabs";
import { ListPage } from "../../components/page/ListPage";
import { useT } from "../../i18n";
import { StorageSummary } from "./StorageSummary";

export type BackupsTab = "backups" | "schedules" | "destinations";

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
 * The frame of the three Backups tabs: one header (what backups are, the storage they take,
 * and the tab's own primary action), the tabs as URLs with their counts, then the tab. The
 * header and the tabs are the same on every tab, so switching moves nothing but the content.
 */
export function BackupsListPage({ secondaryActions, primaryAction, notice, filters, footer, children }: BackupsListPageProps) {
  const t = useT();
  const backups = useQuery(backupsQuery(null));
  const schedules = useQuery(backupSchedulesQuery());
  const destinations = useQuery(backupDestinationsQuery());
  const tabs: LinkTab[] = [
    { label: "backups.tabs.backups", to: "/backups", exact: true, count: backups.data?.total ?? null },
    { label: "backups.tabs.schedules", to: "/backups/schedules", count: schedules.data?.total ?? null },
    { label: "backups.tabs.destinations", to: "/backups/destinations", count: destinations.data?.total ?? null },
  ];
  return (
    <ListPage
      header={{
        title: t("backups.page.title"),
        description: t("backups.page.description"),
        meta: <StorageSummary />,
        ...(secondaryActions !== undefined ? { secondaryActions } : {}),
        ...(primaryAction !== undefined ? { primaryAction } : {}),
      }}
      tabs={<LinkTabs label={t("backups.tabs.label")} tabs={tabs} />}
      {...(notice !== undefined ? { notice } : {})}
      {...(filters !== undefined ? { filters } : {})}
      {...(footer !== undefined ? { footer } : {})}
    >
      {children}
    </ListPage>
  );
}
