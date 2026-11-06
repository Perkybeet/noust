import { createFileRoute, redirect } from "@tanstack/react-router";

/** The TCP and UDP ports nginx publishes are the first section of Network since 4.2: the old address goes there. */
export const Route = createFileRoute("/_console/domains/ports")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/network", replace: true });
  },
});
