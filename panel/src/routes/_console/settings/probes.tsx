import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * Settings > Probes moved in 4.1 with the alerts: the probes and the status page are the second tab of
 * Alerts. The old address keeps working and opens it (app/nav.ts, REDIRECTS).
 */
export const Route = createFileRoute("/_console/settings/probes")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/alerts/probes", replace: true });
  },
});
