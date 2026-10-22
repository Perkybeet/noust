import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { validateAppLogsSearch } from "../../../../features/app/logs/data";
import { LogsTab } from "../../../../features/app/logs/LogsTab";

/**
 * An application's logs: `/apps/shop.example.com/logs?source=access`, `?since=24h&q=timeout`, or
 * `?since=deploy` for everything since the last deployment started. Live is the default.
 */
export const Route = createFileRoute("/_console/apps/$domain/logs")({
  validateSearch: validateAppLogsSearch,
  component: AppLogsTab,
});

function AppLogsTab() {
  const { domain } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  // Keyed: another app's journal is another stream, from its first line.
  return <LogsTab key={domain} domain={domain} search={search} onSearchChange={(next) => void navigate({ search: next, ...inPlace({ replace: true }) })} />;
}
