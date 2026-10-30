import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { MetricsPage } from "../../../features/alerts/MetricsPage";
import { validateMetricsSearch } from "../../../features/alerts/metricsSearch";

/** Alerts > Metrics: what the servers measure (CPU, memory, disk, network), over the window in the address. */
export const Route = createFileRoute("/_console/alerts/metrics")({
  validateSearch: validateMetricsSearch,
  component: MetricsRoute,
});

function MetricsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <MetricsPage search={search} onSearchChange={(next) => void navigate({ search: next, ...inPlace({ replace: true }) })} />;
}
