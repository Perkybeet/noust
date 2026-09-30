import { Outlet, createFileRoute } from "@tanstack/react-router";

import { SettingsShell } from "../../features/settings/SettingsShell";

/**
 * Settings, one section per URL: the selected server's own (`/n/web-2/settings/...`) and the
 * central's (`/settings/servers`, `/settings/security`...), told apart by SettingsShell.
 */
export const Route = createFileRoute("/_console/settings")({
  component: SettingsRoute,
});

function SettingsRoute() {
  return (
    <SettingsShell>
      <Outlet />
    </SettingsShell>
  );
}
