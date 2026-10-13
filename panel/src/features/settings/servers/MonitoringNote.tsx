import { RelativeTime } from "../../../components/page/RelativeTime";
import { useT } from "../../../i18n";
import type { NodeMonitoring } from "../../fleet/nodes";

/**
 * Under a server's status: that its monitoring is paused (and why), or how long it has been
 * silent and whether the central has declared it down. A server that answers and is watched as
 * usual says nothing, so a line here is always news.
 */
export function MonitoringNote({ monitoring }: { monitoring: NodeMonitoring | null }) {
  const t = useT();
  if (monitoring === null) return null;
  if (monitoring.paused) {
    return (
      <span className="max-w-full truncate text-12 text-fg-muted" title={monitoring.paused_reason ?? undefined}>
        {monitoring.paused_reason ? t("servers.settings.monitoring.pausedWhy", { reason: monitoring.paused_reason }) : t("servers.settings.monitoring.paused")}
      </span>
    );
  }
  if (monitoring.since == null) return null;
  const when = <RelativeTime value={monitoring.since} />;
  return (
    <span className="text-12 text-fg-muted">
      {monitoring.announced ? t.rich("servers.settings.monitoring.downSince", { when }) : t.rich("servers.settings.monitoring.silentSince", { when })}
    </span>
  );
}
