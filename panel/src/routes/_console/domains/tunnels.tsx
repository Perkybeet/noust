import { createFileRoute, redirect } from "@tanstack/react-router";

/** Cloudflare Tunnels are a section of Network since 4.2: the old address goes there. */
export const Route = createFileRoute("/_console/domains/tunnels")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/network/tunnels", replace: true });
  },
});
