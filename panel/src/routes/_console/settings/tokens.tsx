import { createFileRoute } from "@tanstack/react-router";

import { TokensSettings } from "../../../features/settings/TokensSettings";

/** Settings > API tokens: named tokens for CI and scripts, each owned, capped and expiring. */
export const Route = createFileRoute("/_console/settings/tokens")({
  component: TokensSettings,
});
