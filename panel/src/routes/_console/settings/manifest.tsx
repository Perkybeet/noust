import { createFileRoute } from "@tanstack/react-router";

import { ManifestPreview } from "../../../features/settings/manifest/ManifestPreview";

/** Settings > Manifest: what applying a manifest would change on this server, before anything does. */
export const Route = createFileRoute("/_console/settings/manifest")({
  component: ManifestPreview,
});
