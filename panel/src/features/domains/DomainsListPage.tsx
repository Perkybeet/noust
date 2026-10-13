import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { certsQuery } from "../../api/queries/certs";
import { dnsAccountsQuery, streamsQuery, tunnelsQuery } from "../../api/queries/network";
import { sitesQuery } from "../../api/queries/sites";
import { LinkTabs } from "../../app/LinkTabs";
import type { LinkTab } from "../../app/LinkTabs";
import { ListPage } from "../../components/page/ListPage";
import { useT } from "../../i18n";
import { useOrganization } from "../teams/useOrganization";

export interface DomainsListPageProps {
  secondaryActions?: ReactNode;
  primaryAction?: ReactNode;
  /** MenuItems behind "More actions": what is rare on the open tab. */
  overflow?: ReactNode;
  notice?: ReactNode;
  filters?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}

/**
 * The frame of the Domains and certificates tabs: one header whose primary action is the
 * open tab's (issue a certificate, create a site), the tabs as URLs with their counts, then
 * the tab. An application's own names are managed from its Domains tab; this is every
 * certificate and every web server site on the machine, and the server's own network: its
 * DNS accounts, the Cloudflare Tunnels that reach it and the TCP and UDP ports it publishes.
 * Those three are the server's to manage, so a principal that cannot see the server is not
 * shown them.
 */
export function DomainsListPage({ secondaryActions, primaryAction, overflow, notice, filters, footer, children }: DomainsListPageProps) {
  const t = useT();
  const org = useOrganization();
  const server = org.can("server.read");
  const certs = useQuery(certsQuery());
  const sites = useQuery(sitesQuery());
  const accounts = useQuery({ ...dnsAccountsQuery(), enabled: server });
  const tunnels = useQuery({ ...tunnelsQuery(), enabled: server });
  const streams = useQuery({ ...streamsQuery(), enabled: server });
  const tabs: LinkTab[] = [
    { label: "domains.page.certificatesTab", to: "/domains", exact: true, count: certs.data?.total ?? null },
    { label: "domains.page.sitesTab", to: "/domains/sites", exact: true, count: sites.data?.total ?? null },
    ...(server
      ? ([
          { label: "network.tabs.dns", to: "/domains/dns", exact: true, count: accounts.data?.accounts.length ?? null },
          { label: "network.tabs.tunnels", to: "/domains/tunnels", exact: true, count: tunnels.data?.tunnels.length ?? null },
          { label: "network.tabs.ports", to: "/domains/ports", exact: true, count: streams.data?.exposures.length ?? null },
        ] satisfies LinkTab[])
      : []),
  ];
  return (
    <ListPage
      header={{
        title: t("nav.domains.label"),
        description: t("domains.page.description"),
        ...(secondaryActions !== undefined ? { secondaryActions } : {}),
        ...(primaryAction !== undefined ? { primaryAction } : {}),
        ...(overflow !== undefined ? { overflow } : {}),
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
