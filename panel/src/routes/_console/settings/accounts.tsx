import { createFileRoute } from "@tanstack/react-router";

import { AccountsSettings } from "../../../features/settings/accounts/AccountsSettings";
import { validateAccountsSearch } from "../../../features/settings/accounts/data";

/** Settings > Accounts: who may sign in, with which role. Filters are search params. */
export const Route = createFileRoute("/_console/settings/accounts")({
  validateSearch: validateAccountsSearch,
  component: AccountsRoute,
});

function AccountsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <AccountsSettings search={search} onSearchChange={(next, options) => void navigate({ search: next, replace: options?.replace ?? false })} />;
}
