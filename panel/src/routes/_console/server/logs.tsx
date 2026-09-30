import { createFileRoute } from "@tanstack/react-router";

import { LogsTab } from "../../../features/server/logs/LogsTab";
import { validateLogsSearch } from "../../../features/server/logs/data";

/** The journal of any unit: `/server/logs?unit=nginx.service&priority=err&since=-1h`. */
export const Route = createFileRoute("/_console/server/logs")({
  validateSearch: validateLogsSearch,
  component: LogsRoute,
});

function LogsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <LogsTab search={search} onSearchChange={(next) => void navigate({ search: next, replace: true })} />;
}
