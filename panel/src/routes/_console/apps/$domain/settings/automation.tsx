import { createFileRoute } from "@tanstack/react-router";

import { AutomationSettings } from "../../../../../features/app/settings/AutomationSettings";

/** An application's settings, Automation: what deploys it by itself, and what holds, plans and protects its updates. */
export const Route = createFileRoute("/_console/apps/$domain/settings/automation")({
  component: AutomationSettings,
});
