import { createFileRoute, redirect } from "@tanstack/react-router";

import { validateServicesSearch } from "../../../features/services/data";

/** Services moved under Server in 3.1: the old address keeps working, filters and all. */
export const Route = createFileRoute("/_console/services/")({
  validateSearch: validateServicesSearch,
  beforeLoad: ({ search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/server/services", search, replace: true });
  },
});
