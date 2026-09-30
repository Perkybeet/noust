import { Outlet, createFileRoute, useMatch } from "@tanstack/react-router";

import { AppSettingsLayout } from "../../../../features/app/settings/AppSettingsLayout";

/** An application's settings: one subsection per URL, beside the list of them (T3). */
export const Route = createFileRoute("/_console/apps/$domain/settings")({
  component: AppSettingsRoute,
});

function AppSettingsRoute() {
  const { domain } = Route.useParams();
  // On a phone the index is the list of subsections; wider, it shows General beside it.
  const index = useMatch({ from: "/_console/apps/$domain/settings/", shouldThrow: false }) !== undefined;
  return (
    <AppSettingsLayout domain={domain} index={index}>
      <Outlet />
    </AppSettingsLayout>
  );
}
