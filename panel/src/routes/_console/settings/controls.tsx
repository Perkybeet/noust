import { createFileRoute } from "@tanstack/react-router";

import { ControlsSettings } from "../../../features/settings/compliance/ControlsSettings";

/** Settings > Controls: the compliance frameworks, the controls they are made of and the security profile. */
export const Route = createFileRoute("/_console/settings/controls")({
  component: ControlsSettings,
});
