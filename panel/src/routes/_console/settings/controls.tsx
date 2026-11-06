import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * Settings > Controls joined the frameworks in 4.2: what a framework asks, its report and the controls it is made of are
 * one page, Compliance. The old address keeps working and opens it (app/nav.ts, REDIRECTS).
 */
export const Route = createFileRoute("/_console/settings/controls")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/settings/compliance", replace: true });
  },
});
