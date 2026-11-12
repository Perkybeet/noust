import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * The manifest is a machine written down in a file, so it is a page of the machine since 4.3 (item 166): Server > Manifest.
 * The old address keeps working and opens it, on the server the address named (app/redirects.ts).
 */
export const Route = createFileRoute("/_console/settings/manifest")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/server/manifest", replace: true });
  },
});
