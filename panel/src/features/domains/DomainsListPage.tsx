import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import type { ReactNode } from "react";

import { certsQuery } from "../../api/queries/certs";
import { dnsAccountsQuery, streamsQuery, tunnelsQuery } from "../../api/queries/network";
import { sitesQuery } from "../../api/queries/sites";
import { LinkTabs } from "../../app/LinkTabs";
import type { LinkTab } from "../../app/LinkTabs";
import { tabsOf, withCounts } from "../../app/navCore";
import { ListPage } from "../../components/page/ListPage";
import { useT } from "../../i18n";
import { DOMAINS_SCREENS } from "../nav/domains";
import { useOrganization } from "../teams/useOrganization";
import { domainRows } from "./domainRows";

/** The sections of the Domains area: each is a tab, a page and a title of its own. */
export type DomainsSection = "domains" | "sites" | "certificates" | "dns" | "tunnels" | "ports";

export interface DomainsListPageProps {
  /** Which section this is: its title and its one sentence come from the catalog, so the title is never the area's name again. */
  section: DomainsSection;
  /** A page beneath the section (Noust DNS under DNS) names itself: its title and sentence replace the section's. */
  title?: string;
  description?: string;
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
 * The frame of the Domains sections: one header whose title is the open section's and whose primary
 * action is its own (add a domain, create a site, issue a certificate), the sections as URL tabs with
 * their counts, then the section. An application's own names are managed from its Domains section;
 * this is every name, site and certificate on the machine, and the server's own network: its DNS, the
 * Cloudflare Tunnels that reach it and the TCP and UDP ports it publishes. Those three are the
 * server's to manage, so a principal that cannot see the server is not shown them.
 */
export function DomainsListPage({ section, title, description, secondaryActions, primaryAction, overflow, notice, filters, footer, children }: DomainsListPageProps) {
  const t = useT();
  const org = useOrganization();
  const server = org.can("server.read");
  const certs = useQuery(certsQuery());
  const sites = useQuery(sitesQuery());
  const accounts = useQuery({ ...dnsAccountsQuery(), enabled: server });
  const tunnels = useQuery({ ...tunnelsQuery(), enabled: server });
  const streams = useQuery({ ...streamsQuery(), enabled: server });
  const names = useMemo(() => (sites.data === undefined || certs.data === undefined ? null : domainRows(sites.data.sites, certs.data.certificates).length), [sites.data, certs.data]);
  // The server's own network (DNS, tunnels, ports) is the server's to manage: the register leaves those tabs out for whoever cannot.
  const tabs: LinkTab[] = withCounts(tabsOf(DOMAINS_SCREENS, { can: org.can }), {
    "/domains": names,
    "/domains/sites": sites.data?.total ?? null,
    "/domains/certificates": certs.data?.total ?? null,
    "/domains/dns": accounts.data?.accounts.length ?? null,
    "/domains/tunnels": tunnels.data?.tunnels.length ?? null,
    "/domains/ports": streams.data?.exposures.length ?? null,
  });
  return (
    <ListPage
      header={{
        title: title ?? t(`domains.sections.${section}.title`),
        description: description ?? t(`domains.sections.${section}.description`),
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
