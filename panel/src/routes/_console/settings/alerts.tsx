import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * Settings > Alerts moved in 4.1: what warns an operator is an area of the bar, Alerts. The old
 * address keeps working and opens it (app/nav.ts, REDIRECTS).
 */
export const Route = createFileRoute("/_console/settings/alerts")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/alerts", replace: true });
  },
});
