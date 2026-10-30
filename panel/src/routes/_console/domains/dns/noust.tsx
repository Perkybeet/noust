import { createFileRoute } from "@tanstack/react-router";

import { NoustDnsPage } from "../../../../features/domains/noustdns/NoustDnsPage";

/** Noust DNS: the name server Noust runs on the fleet's servers, its state and its zones. */
export const Route = createFileRoute("/_console/domains/dns/noust")({
  component: NoustDnsPage,
});
