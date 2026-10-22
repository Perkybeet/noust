import { createFileRoute, redirect } from "@tanstack/react-router";

import { toLogsSearch, validateLegacySearch } from "../../../features/server/logs/data";

/**
 * The system's logs became a source of Logs in 4.0: the old address keeps working, with its
 * service, level, range, words and stretch (`/server/logs?unit=nginx.service` opens
 * `/logs?source=system&unit=nginx.service`).
 */
export const Route = createFileRoute("/_console/server/logs")({
  validateSearch: validateLegacySearch,
  beforeLoad: ({ search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/logs", search: toLogsSearch(search), replace: true });
  },
});
