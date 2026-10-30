import { createFileRoute } from "@tanstack/react-router";

import { SettingsHome } from "../../../features/settings/SettingsHome";

/** Settings: every section as a door, in the groups a person looks for a setting by. */
export const Route = createFileRoute("/_console/settings/")({
  component: SettingsHome,
});
