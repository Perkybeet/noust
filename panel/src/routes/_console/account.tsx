import { Outlet, createFileRoute } from "@tanstack/react-router";

import { AccountShell } from "../../features/account/AccountShell";

/**
 * The signed-in person's own page, opened from the account menu: profile and password, and how
 * they sign in. The central's whichever server is selected (app/nodeRoute.ts), so it never carries one.
 */
export const Route = createFileRoute("/_console/account")({
  component: AccountRoute,
});

function AccountRoute() {
  return (
    <AccountShell>
      <Outlet />
    </AccountShell>
  );
}
