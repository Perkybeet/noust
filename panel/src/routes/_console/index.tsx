import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../app/searchNavigation";
import { HomePage } from "../../features/overview/HomePage";
import { DEFAULT_RANGE, isRange } from "../../features/overview/ranges";
import type { MetricRange } from "../../features/overview/ranges";

interface HomeSearch {
  /** The machine charts' time range; the last 24 hours when absent. */
  window?: MetricRange;
}

function validateSearch(search: Record<string, unknown>): HomeSearch {
  const window = search["window"];
  return isRange(window) && window !== DEFAULT_RANGE ? { window } : {};
}

/** Home: what needs attention, the fleet in courses, what happened and what to do next; below, the machine's history. */
export const Route = createFileRoute("/_console/")({
  validateSearch,
  component: HomeRoute,
});

function HomeRoute() {
  const { window = DEFAULT_RANGE } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <HomePage
      scope="server"
      range={window}
      onRangeChange={(next) => void navigate({ search: next === DEFAULT_RANGE ? {} : { window: next }, ...inPlace({ replace: true }) })}
    />
  );
}
