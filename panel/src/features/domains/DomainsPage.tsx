import { useQuery } from "@tanstack/react-query";

import { certsQuery } from "../../api/queries/certs";
import { sitesQuery } from "../../api/queries/sites";
import { PageHeader } from "../../app/PageHeader";
import { Tab, TabList, TabPanel, Tabs } from "../../components/ui/Tabs";
import { useT } from "../../i18n";
import { CertificatesTab } from "./CertificatesTab";
import type { DomainsTab } from "./search";
import { SitesTab } from "./SitesTab";

export interface DomainsPageProps {
  tab: DomainsTab;
  /** The certificates' filter as the page opens with it. */
  filter?: string;
  onTabChange: (tab: DomainsTab) => void;
}

/**
 * Certificates and web server sites, machine-wide, as two tabs whose choice is the URL.
 * An application's own names are managed from its Domains tab; this is every name on the box.
 */
export function DomainsPage({ tab, filter, onTabChange }: DomainsPageProps) {
  const t = useT();
  const certs = useQuery(certsQuery());
  const sites = useQuery(sitesQuery());
  return (
    <>
      <PageHeader title={t("nav.domains.label")} description={t("domains.page.description")} />
      <Tabs<DomainsTab> value={tab} onValueChange={onTabChange}>
        <TabList aria-label={t("nav.domains.label")}>
          <Tab value="certificates" {...(certs.data ? { count: certs.data.total } : {})}>
            {t("domains.page.certificatesTab")}
          </Tab>
          <Tab value="sites" {...(sites.data ? { count: sites.data.total } : {})}>
            {t("domains.page.sitesTab")}
          </Tab>
        </TabList>
        {/* The tab names the panel for sight; the heading gives it a place in the outline. */}
        <TabPanel value="certificates">
          <h2 className="sr-only">{t("domains.page.certificatesTab")}</h2>
          {/* Keyed by the filter, so following another link to this page starts from that one. */}
          <CertificatesTab key={filter ?? ""} {...(filter !== undefined ? { initialFilter: filter } : {})} />
        </TabPanel>
        <TabPanel value="sites">
          <h2 className="sr-only">{t("domains.page.sitesTab")}</h2>
          <SitesTab />
        </TabPanel>
      </Tabs>
    </>
  );
}
