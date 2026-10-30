import { createFileRoute, redirect } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { SecurityTab } from "../../../features/server/security/SecurityTab";
import { validateSecuritySearch } from "../../../features/server/security/data";

/** Security and SSH: the hardening checks, SSH and the bans as `?view=`. The firewall is Network and ports'. */
export const Route = createFileRoute("/_console/server/security")({
  validateSearch: validateSecuritySearch,
  beforeLoad: ({ search }) => {
    // The firewall was a view of the Security tab in 4.0: its old address opens it where it is now.
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (search.view === "firewall") throw redirect({ to: "/server/network", search: { view: "firewall" }, replace: true });
  },
  component: SecurityRoute,
});

function SecurityRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const view = search.view === undefined || search.view === "firewall" ? "checks" : search.view;
  return <SecurityTab view={view} onViewChange={(next) => void navigate({ search: next === "checks" ? {} : { view: next }, ...inPlace({ replace: true }) })} />;
}
