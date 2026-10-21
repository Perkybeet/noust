import { createFileRoute } from "@tanstack/react-router";

import { SecurityPage } from "../../../features/account/SecurityPage";

/** Account > Sign-in security: the authenticator, the passkeys and where the person is signed in. */
export const Route = createFileRoute("/_console/account/security")({
  component: SecurityPage,
});
