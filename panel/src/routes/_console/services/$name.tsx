import { createFileRoute, redirect } from "@tanstack/react-router";

/** A service's old address (3.0, and the unit alerts sent before 3.1): its page under Server. */
export const Route = createFileRoute("/_console/services/$name")({
  beforeLoad: ({ params }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/server/services/$name", params: { name: params.name }, replace: true });
  },
});
