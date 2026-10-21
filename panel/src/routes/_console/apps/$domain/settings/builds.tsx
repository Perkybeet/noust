import { createFileRoute, redirect } from "@tanstack/react-router";

/** Builds became a card of Build in 4.0: the old address keeps working. */
export const Route = createFileRoute("/_console/apps/$domain/settings/builds")({
  beforeLoad: ({ params }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/apps/$domain/settings/build", params, replace: true });
  },
});
