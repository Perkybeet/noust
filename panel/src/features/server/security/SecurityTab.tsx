/**
 * The Security tab: how hardened the server is, as counted findings (there is no score), and
 * four views behind them, each a URL (`?view=`): the checks, SSH, the firewall against what
 * listens, and the bans. The views load only when shown.
 */

import { useMutation, useQuery } from "@tanstack/react-query";

import { request } from "../../../api/client";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { explainServerError } from "../errors";
import { SERVER_CAPABILITY, securityOverviewQuery } from "../queries";
import { useServerJob } from "../serverJob";
import { TabToolbar } from "../TabToolbar";
import { BansView } from "./BansView";
import { ChecksView } from "./ChecksView";
import { FirewallView } from "./FirewallView";
import { SshView } from "./SshView";
import type { SecurityView } from "./data";

export interface SecurityTabProps {
  view: SecurityView;
  onViewChange: (view: SecurityView) => void;
}

function Summary() {
  const t = useT();
  const overview = useQuery(securityOverviewQuery());
  if (overview.data === undefined) return overview.isError ? <span>{t("server.security.summaryFailed")}</span> : <Skeleton className="h-5 w-72" />;
  const counts = overview.data.counts;
  if (counts === null || counts === undefined || overview.data.checked_at == null) return <span>{t("server.security.notChecked")}</span>;
  return (
    <span>
      {t("server.security.summary", { critical: counts.critical, warning: counts.warning, passed: counts.passed })} ·{" "}
      {t.rich("server.security.checked", { when: <RelativeTime value={overview.data.checked_at} /> })}
    </span>
  );
}

function Security({ view, onViewChange }: SecurityTabProps) {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const run = useMutation({
    mutationFn: () => request("post", "/api/server/security/checks/refresh"),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "checks"),
    onError: (error) => reportActionError(t("server.job.checks.failed"), explainServerError(t, error, node)),
  });
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <TabToolbar
        summary={<Summary />}
        actions={
          <Button loading={run.isPending} onClick={() => run.mutate()}>
            {t("server.security.runChecks")}
          </Button>
        }
      />
      <SegmentedControl<SecurityView>
        label={t("server.security.viewsLabel")}
        value={view}
        onValueChange={onViewChange}
        options={[
          { value: "checks", label: t("server.security.views.checks") },
          { value: "ssh", label: t("server.security.views.ssh") },
          { value: "firewall", label: t("server.security.views.firewall") },
          { value: "bans", label: t("server.security.views.bans") },
        ]}
        className="self-start"
      />
      {view === "ssh" ? <SshView /> : view === "firewall" ? <FirewallView /> : view === "bans" ? <BansView /> : <ChecksView />}
    </div>
  );
}

/** The Security tab. */
export function SecurityTab(props: SecurityTabProps) {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <Security {...props} />
    </NodeCapabilityGate>
  );
}
