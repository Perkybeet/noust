import { createFileRoute } from "@tanstack/react-router";

import { OverviewPage } from "../../features/overview/OverviewPage";
import { DEFAULT_RANGE, isRange } from "../../features/overview/ranges";
import type { MetricRange } from "../../features/overview/ranges";

interface OverviewSearch {
  /** The machine charts' time range; the last 24 hours when absent. */
  window?: MetricRange;
}

function validateSearch(search: Record<string, unknown>): OverviewSearch {
  const window = search["window"];
  return isRange(window) && window !== DEFAULT_RANGE ? { window } : {};
}

export const Route = createFileRoute("/_console/")({
  validateSearch,
  component: OverviewRoute,
});

function OverviewRoute() {
  const { window = DEFAULT_RANGE } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <OverviewPage
      range={window}
      onRangeChange={(next) => void navigate({ search: next === DEFAULT_RANGE ? {} : { window: next }, replace: true })}
    />
  );
}
