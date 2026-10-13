import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { CloudSettings } from "../../../features/cloud/CloudSettings";

interface CloudSearch {
  /** The "Create a server" wizard is open: Settings > Servers links straight to it. */
  create?: true;
}

function validateSearch(search: Record<string, unknown>): CloudSearch {
  return search["create"] === true || search["create"] === "true" || search["create"] === 1 ? { create: true } : {};
}

/** Settings > Cloud: the provider accounts, the servers made at them and the boot scripts. */
export const Route = createFileRoute("/_console/settings/cloud")({
  validateSearch,
  component: CloudRoute,
});

function CloudRoute() {
  const { create } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <CloudSettings
      creating={create === true}
      onCreatingChange={(creating) => void navigate({ search: creating ? { create: true } : {}, ...inPlace({ replace: true }) })}
    />
  );
}
