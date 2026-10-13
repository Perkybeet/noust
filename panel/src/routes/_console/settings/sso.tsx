import { createFileRoute } from "@tanstack/react-router";

import { SsoSettings } from "../../../features/settings/sso/SsoSettings";

/** Settings > Single sign-on: identity providers, the people linked to them, and the password sign-in switch. */
export const Route = createFileRoute("/_console/settings/sso")({
  component: SsoSettings,
});
