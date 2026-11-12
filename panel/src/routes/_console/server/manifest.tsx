import { createFileRoute } from "@tanstack/react-router";

import { ManifestPreview } from "../../../features/settings/manifest/ManifestPreview";

/** The manifest of this machine: what applying it would change here, before anything does. It was Settings > Manifest. */
export const Route = createFileRoute("/_console/server/manifest")({
  component: ManifestPreview,
});
