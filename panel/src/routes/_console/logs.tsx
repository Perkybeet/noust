import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../app/searchNavigation";
import { validateLogsSearch } from "../../features/logs/data";
import { LogSearchPage } from "../../features/logs/LogSearchPage";

/**
 * One search over the logs: `/logs?q=timeout&app=shop.example.com&since=24h` over the
 * applications, and `/logs?source=system&unit=nginx.service&since=live` over the system's journal.
 */
export const Route = createFileRoute("/_console/logs")({
  validateSearch: validateLogsSearch,
  component: LogsRoute,
});

function LogsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <LogSearchPage
      search={search}
      // A keystroke or a range replaces the history entry; opening a line is a place Back leaves.
      onSearchChange={(next, options) => void navigate({ search: next, ...inPlace({ replace: options?.push !== true }) })}
    />
  );
}
