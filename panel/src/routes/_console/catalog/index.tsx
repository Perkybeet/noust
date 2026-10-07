import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { CatalogPage } from "../../../features/catalog/CatalogPage";
import { validateCatalogSearch } from "../../../features/catalog/model";

/**
 * The recipes this release ships. The filters are search params: `/catalog?category=media`,
 * `/catalog?q=passwords&kind=compose`.
 */
export const Route = createFileRoute("/_console/catalog/")({
  validateSearch: validateCatalogSearch,
  component: CatalogRoute,
});

function CatalogRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <CatalogPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
