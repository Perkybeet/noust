import { createFileRoute } from "@tanstack/react-router";

import { SourceSettings } from "../../../../../features/app/settings/SourceSettings";

/** An application's settings, Source: the deploy key and the credential its repository is reached with. */
export const Route = createFileRoute("/_console/apps/$domain/settings/source")({
  component: SourceSettings,
});
