import { createFileRoute } from "@tanstack/react-router";

import { ResetPasswordPage } from "../features/auth/ResetPasswordPage";

/** Choosing a new password with the link in a mail: public, the code in the link is the credential. */
export const Route = createFileRoute("/reset-password")({
  component: ResetPasswordPage,
});
