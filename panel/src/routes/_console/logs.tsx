import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../app/searchNavigation";
import { validateLogsSearch } from "../../features/logs/data";
import { LogSearchPage } from "../../features/logs/LogSearchPage";

/** One search over the logs of every application: `/logs?q=timeout&app=shop.example.com&since=24h&level=err`. */
export const Route = createFileRoute("/_console/logs")({
  validateSearch: validateLogsSearch,
  component: LogsRoute,
});

function LogsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <LogSearchPage search={search} onSearchChange={(next) => void navigate({ search: next, ...inPlace({ replace: true }) })} />;
}
