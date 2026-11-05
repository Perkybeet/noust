import { createFileRoute } from "@tanstack/react-router";

import { SignInImageSettings } from "../../../features/settings/signin/SignInImageSettings";

/** Settings > Sign-in image: the picture the screens before the console show in place of Noust's drawing. */
export const Route = createFileRoute("/_console/settings/sign-in-image")({
  component: SignInImageSettings,
});
