import { createFileRoute, redirect } from "@tanstack/react-router";

/** 4.2: the settings of an application are the pages of its column; this subsection is now in access. */
export const Route = createFileRoute("/_console/apps/$domain/settings/access")({
  beforeLoad: ({ params }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/apps/$domain/access", params, replace: true });
  },
});
