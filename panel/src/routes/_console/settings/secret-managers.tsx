import { createFileRoute } from "@tanstack/react-router";

import { SecretManagers } from "../../../features/variables/SecretManagers";

export const Route = createFileRoute("/_console/settings/secret-managers")({
  component: SecretManagers,
});
