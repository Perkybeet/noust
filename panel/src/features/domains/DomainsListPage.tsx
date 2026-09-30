import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { certsQuery } from "../../api/queries/certs";
import { sitesQuery } from "../../api/queries/sites";
import { LinkTabs } from "../../app/LinkTabs";
import type { LinkTab } from "../../app/LinkTabs";
import { ListPage } from "../../components/page/ListPage";
import { useT } from "../../i18n";

export interface DomainsListPageProps {
  secondaryActions?: ReactNode;
  primaryAction?: ReactNode;
  notice?: ReactNode;
  filters?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}

/**
 * The frame of the Domains and certificates tabs: one header whose primary action is the
 * open tab's (issue a certificate, create a site), the tabs as URLs with their counts, then
 * the tab. An application's own names are managed from its Domains tab; this is every
 * certificate and every web server site on the machine.
 */
export function DomainsListPage({ secondaryActions, primaryAction, notice, filters, footer, children }: DomainsListPageProps) {
  const t = useT();
  const certs = useQuery(certsQuery());
  const sites = useQuery(sitesQuery());
  const tabs: LinkTab[] = [
    { label: "domains.page.certificatesTab", to: "/domains", exact: true, count: certs.data?.total ?? null },
    { label: "domains.page.sitesTab", to: "/domains/sites", exact: true, count: sites.data?.total ?? null },
  ];
  return (
    <ListPage
      header={{
        title: t("nav.domains.label"),
        description: t("domains.page.description"),
        ...(secondaryActions !== undefined ? { secondaryActions } : {}),
        ...(primaryAction !== undefined ? { primaryAction } : {}),
      }}
      tabs={<LinkTabs label={t("domains.page.tabsLabel")} tabs={tabs} />}
      {...(notice !== undefined ? { notice } : {})}
      {...(filters !== undefined ? { filters } : {})}
      {...(footer !== undefined ? { footer } : {})}
    >
      {children}
    </ListPage>
  );
}
