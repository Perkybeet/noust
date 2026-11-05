import { createFileRoute } from "@tanstack/react-router";

import { EnginesPage } from "../../../features/databases/EnginesPage";
import { validateEnginesSearch } from "../../../features/databases/search";

/** The engines: where the databases run, and the version each is on. `?engine=mariadb` keeps the engines of one flavour. */
export const Route = createFileRoute("/_console/databases/engines")({
  validateSearch: validateEnginesSearch,
  component: EnginesRoute,
});

function EnginesRoute() {
  const { engine } = Route.useSearch();
  return <EnginesPage engine={engine} />;
}
