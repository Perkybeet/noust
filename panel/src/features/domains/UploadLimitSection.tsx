import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { sitesQuery } from "../../api/queries/sites";
import { Section } from "../../components/page/Section";
import { buttonClassName } from "../../components/ui/Button";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { useScopeOffers } from "../../nodes/useNode";
import { hasStructure } from "./siteViews";

/**
 * Where the biggest file an application can receive is set (item 160): a line of its site's configuration, in the
 * Structure view, which nobody looks for under an application's Domains. The section is only a way there, because the
 * limit is the web server's and its one writer is the site's page (rule 3: nothing here writes the file). It is not on the
 * page where the server has no sites to offer (the filter of the register, not a condition of its own) or where the
 * application has none to link to.
 */
export function UploadLimitSection({ domain }: { domain: string }) {
  const t = useT();
  const offered = useScopeOffers();
  const sites = useQuery({ ...sitesQuery(), enabled: offered("sites"), retry: false });
  const site = offered("sites") ? sites.data?.sites.find((candidate) => candidate.app === domain) : undefined;
  if (site === undefined) return null;
  // The structure is read for the two web servers Noust administers; the other's file is the text.
  const view = hasStructure(sites.data?.webserver ?? site.webserver) ? ({ view: "structure" } as const) : {};
  return (
    <Section icon={OBJECT_ICONS.site} title={t("domains.appTab.upload.title")} description={t("domains.appTab.upload.description")}>
      <div>
        <Link to="/domains/sites/$site" params={{ site: site.name }} search={view} className={buttonClassName("secondary", "md")}>
          {t("domains.appTab.upload.open")}
        </Link>
      </div>
    </Section>
  );
}
