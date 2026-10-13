import { createFileRoute } from "@tanstack/react-router";

import { DnsAccountsPage } from "../../../features/domains/DnsAccountsPage";

/** The DNS accounts Noust holds, and the records it manages in them. */
export const Route = createFileRoute("/_console/domains/dns")({
  component: DnsAccountsPage,
});
