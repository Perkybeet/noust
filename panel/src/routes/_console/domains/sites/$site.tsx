import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { SiteConfigPage } from "../../../../features/domains/SiteConfigPage";
import { validateSiteSearch } from "../../../../features/domains/siteViews";

/** One web server site and its configuration file: `/domains/sites/example.com?view=diagram`. */
export const Route = createFileRoute("/_console/domains/sites/$site")({
  validateSearch: validateSiteSearch,
  component: SiteRoute,
});

function SiteRoute() {
  const { site } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  // Keyed: another site is another editor, with none of this one's draft.
  return (
    <SiteConfigPage
      key={site}
      site={site}
      view={search.view ?? "text"}
      onViewChange={(view) => void navigate({ search: view === "text" ? {} : { view }, ...inPlace({ replace: true }) })}
    />
  );
}
