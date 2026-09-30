import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../../app/searchNavigation";
import { MetricsTab } from "../../../../../features/databases/metrics/MetricsTab";
import { DEFAULT_RANGE, isRange } from "../../../../../features/overview/ranges";
import type { MetricRange } from "../../../../../features/overview/ranges";

interface MetricsSearch {
  /** The charts' time range; the last 24 hours when absent. */
  range?: MetricRange;
}

function validateSearch(search: Record<string, unknown>): MetricsSearch {
  const range = search["range"];
  return isRange(range) && range !== DEFAULT_RANGE ? { range } : {};
}

export const Route = createFileRoute("/_console/databases/$engine/$name/metrics")({
  validateSearch,
  component: DatabaseMetricsRoute,
});

function DatabaseMetricsRoute() {
  const { engine, name } = Route.useParams();
  const { range = DEFAULT_RANGE } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <MetricsTab
      engine={engine}
      name={name}
      range={range}
      onRangeChange={(next) => void navigate({ search: next === DEFAULT_RANGE ? {} : { range: next }, ...inPlace({ replace: true }) })}
    />
  );
}
