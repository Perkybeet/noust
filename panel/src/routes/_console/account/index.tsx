import { createFileRoute } from "@tanstack/react-router";

import { ProfilePage } from "../../../features/account/ProfilePage";

/** Account > Profile and password: who is signed in, and the password. */
export const Route = createFileRoute("/_console/account/")({
  component: ProfilePage,
});
