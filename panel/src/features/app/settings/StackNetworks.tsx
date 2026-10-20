import { useQuery } from "@tanstack/react-query";

import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Badge } from "../../../components/ui/Badge";
import { Card } from "../../../components/ui/Card";
import { EmptyState } from "../../../components/ui/EmptyState";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { stackNetworksQuery } from "./queries";

/**
 * The Docker networks a Compose stack is on, and who else is on them, as Docker reports it.
 * Read only: Compose gives a stack a network of its own and that isolation stops being true
 * quietly when a service is attached to a network another stack uses, so this says so.
 */
export function StackNetworksCard({ domain }: { domain: string }) {
  const t = useT();
  const view = useQuery(stackNetworksQuery(domain));
  const title = t("composeSettings.networks.title");
  const description = t("composeSettings.networks.description");

  if (view.isPending) {
    return (
      <LoadingRegion label={t("composeSettings.networks.loading")}>
        <SkeletonCard lines={2} />
      </LoadingRegion>
    );
  }
  if (view.isError) {
    return (
      <Card title={title} description={description}>
        <ErrorBlock compact error={view.error} title={t("composeSettings.networks.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }
  const { networks, isolated, applicable, reason } = view.data;
  return (
    <Card title={title} description={description}>
      <div className="flex flex-col gap-4">
        {!applicable ? <p className="text-13 text-fg-muted">{reason}</p> : null}
        {applicable && networks.length === 0 ? <EmptyState variant="inline" title={t("composeSettings.networks.none")} /> : null}
        {applicable && networks.length > 0 ? (
          <p className="text-14" role="status">
            {isolated ? t("composeSettings.networks.isolated") : t("composeSettings.networks.shared")}
          </p>
        ) : null}
        {networks.length > 0 ? (
          <ul aria-label={t("composeSettings.networks.list")} className="flex flex-col gap-3">
            {networks.map((network) => (
              <li key={network.name} className="flex flex-col gap-2 rounded-card border border-border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Mono>{network.name}</Mono>
                  <Badge>{network.driver}</Badge>
                  {network.internal ? <Badge>{t("composeSettings.networks.internal")}</Badge> : null}
                  {network.shared ? <StatusPill state="warning" size="sm" label={t("composeSettings.networks.sharedBadge")} /> : null}
                </div>
                {network.subnets.length > 0 ? <p className="text-13 text-fg-muted">{t("composeSettings.networks.ranges", { ranges: network.subnets.join(", ") })}</p> : null}
                <ul aria-label={t("composeSettings.networks.members", { name: network.name })} className="flex flex-wrap gap-2">
                  {network.members.map((member) => (
                    <li key={member.name} className="flex items-center gap-1 text-13">
                      <Mono>{member.name}</Mono>
                      {member.address !== "" ? <span className="text-fg-muted">{member.address}</span> : null}
                      {member.own ? null : <StatusPill state="warning" size="sm" label={t("composeSettings.networks.other")} />}
                    </li>
                  ))}
                </ul>
                {network.findings.map((finding) => (
                  <InlineMessage key={finding}>{finding}</InlineMessage>
                ))}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Card>
  );
}
