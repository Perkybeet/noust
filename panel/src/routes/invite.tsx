import { createFileRoute } from "@tanstack/react-router";

import { InvitePage } from "../features/auth/InvitePage";

/** Accepting an invitation: public, the invitation code is the credential. */
export const Route = createFileRoute("/invite")({
  component: InvitePage,
});
