import type { ReactNode } from "react";

import { ListPage } from "../../components/page/ListPage";
import { useT } from "../../i18n";

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
 * The frame of the sections of Domains and of the network's published ports and tunnels: one header whose
 * title is the open section's and whose primary action is its own (add a domain, create a site, issue a
 * certificate, publish a port), then the section the column has open. An application's own names are
 * managed from its Domains section; this is every name, site and certificate on the machine.
 */
export function DomainsListPage({ section, title, description, secondaryActions, primaryAction, overflow, notice, filters, footer, children }: DomainsListPageProps) {
  const t = useT();
  return (
    <ListPage
      header={{
        title: title ?? t(`domains.sections.${section}.title`),
        description: description ?? t(`domains.sections.${section}.description`),
        ...(secondaryActions !== undefined ? { secondaryActions } : {}),
        ...(primaryAction !== undefined ? { primaryAction } : {}),
        ...(overflow !== undefined ? { overflow } : {}),
      }}
      {...(notice !== undefined ? { notice } : {})}
      {...(filters !== undefined ? { filters } : {})}
      {...(footer !== undefined ? { footer } : {})}
    >
      {children}
    </ListPage>
  );
}
