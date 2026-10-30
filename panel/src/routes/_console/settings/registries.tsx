import { createFileRoute } from "@tanstack/react-router";

import { RegistriesSettings } from "../../../features/settings/registries/RegistriesSettings";

/** Settings > Image registries: the accounts the server pulls private images with. */
export const Route = createFileRoute("/_console/settings/registries")({
  component: RegistriesSettings,
});
