import { createFileRoute } from "@tanstack/react-router";

import { EngineSettingsPage } from "../../../../../features/databases/EngineSettingsPage";

/** An engine's settings on this server: what it runs with, what Noust sets, and the advice. */
export const Route = createFileRoute("/_console/databases/engines_/$engine/settings")({
  component: EngineSettingsRoute,
});

function EngineSettingsRoute() {
  const { engine } = Route.useParams();
  // Keyed: another engine is another form, with none of this one's draft.
  return <EngineSettingsPage key={engine} engine={engine} />;
}
