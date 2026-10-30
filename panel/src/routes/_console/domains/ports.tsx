import { createFileRoute, redirect } from "@tanstack/react-router";

/** The ports a server publishes are the server's: Network and ports. The old address of the Domains tab goes there. */
export const Route = createFileRoute("/_console/domains/ports")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/server/network", replace: true });
  },
});
