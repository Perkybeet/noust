import { createFileRoute } from "@tanstack/react-router";

import { FilesPage, validateFilesSearch } from "../../../../features/app/files/FilesPage";

/** An application's files, a folder at a time. The folder is a search param: `/apps/shop.example.com/files?path=config`. */
export const Route = createFileRoute("/_console/apps/$domain/files")({
  validateSearch: validateFilesSearch,
  component: AppFiles,
});

function AppFiles() {
  const { domain } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  // Keyed: another application's files are another listing, from its own top.
  return (
    <FilesPage
      key={domain}
      domain={domain}
      folder={search.path ?? ""}
      onFolder={(path) => void navigate({ search: path === "" ? {} : { path } })}
    />
  );
}
