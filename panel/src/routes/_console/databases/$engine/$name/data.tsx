import { createFileRoute } from "@tanstack/react-router";

import { DataTab } from "../../../../../features/databases/data/DataTab";
import { validateDataSearch } from "../../../../../features/databases/data/search";

/** The data browser: `?schema=public&table=orders&sort=id:desc&filter=status:eq:paid`. */
export const Route = createFileRoute("/_console/databases/$engine/$name/data")({
  validateSearch: validateDataSearch,
  component: DatabaseDataRoute,
});

function DatabaseDataRoute() {
  const { engine, name } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <DataTab
      engine={engine}
      name={name}
      search={search}
      onSearchChange={(next, options) => void navigate({ search: next, replace: options?.replace ?? false })}
    />
  );
}
