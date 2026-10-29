import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { noustServerQuery } from "../../../api/queries/noustDns";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { buttonClassName } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { noustState, NOUST_PILL, stateLabel } from "./state";

/**
 * Noust DNS on the DNS page, beside the providers: the other way to have DNS, a name server of your
 * own. It says where this server stands (not installed, not set up, primary, secondary or stopped)
 * and leads to the page that does the rest. A server whose Noust has no Noust DNS answers 404 and the
 * card is not drawn: an option that does not exist here is absent, not disabled.
 */
export function NoustDnsCard() {
  const t = useT();
  const status = useQuery({ ...noustServerQuery(), retry: false });
  if (status.isError) return null;
  if (status.data === undefined) {
    return (
      <LoadingRegion label={t("common.queryState.loading", { label: t("noustDns.loadingLabel") })}>
        <Skeleton className="h-28" />
      </LoadingRegion>
    );
  }
  const server = status.data;
  const state = noustState(server);
  const started = server.configured;
  return (
    <Card
      level={2}
      title={t("noustDns.title")}
      icon={OBJECT_ICONS.dns}
      description={t("noustDns.card.description")}
      status={<StatusPill state={NOUST_PILL[state]} label={stateLabel(t, state)} size="sm" />}
      actions={
        <Link to="/domains/dns/noust" className={buttonClassName("secondary", "sm")}>
          {started ? t("noustDns.card.open") : t("noustDns.card.setUp")}
        </Link>
      }
      {...(started ? { children: <p className="text-13 text-fg-muted">{t("noustDns.card.zones", { count: server.zones })}</p> } : {})}
    />
  );
}
