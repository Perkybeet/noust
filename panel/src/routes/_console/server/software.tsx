import { createFileRoute } from "@tanstack/react-router";

import { SoftwarePage } from "../../../features/server/software/SoftwarePage";

/** What the server is made of as far as Noust is concerned: each piece, who put it there, what uses it, and removing or installing it. */
export const Route = createFileRoute("/_console/server/software")({
  component: SoftwarePage,
});
