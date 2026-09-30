/**
 * The Security tab: how hardened the server is, as counted findings (there is no score), and
 * four views behind them, each a URL (`?view=`): the checks, SSH, the firewall against what
 * listens, and the bans. The views load only when shown.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { request } from "../../../api/client";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { explainServerError } from "../errors";
import { SERVER_CAPABILITY, securityOverviewQuery, serverKeys } from "../queries";
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

/**
 * The checks' state in one line. Nobody has to run them first: reading the summary starts them in
 * the background when there is no report or it is due, and while they run the line says so, with
 * the last counts when there are some. When a run ends, the checks view is read again.
 */
function Summary() {
  const t = useT();
  const queryClient = useQueryClient();
  const overview = useQuery(securityOverviewQuery());
  const checking = overview.data?.checking === true;
  const wasChecking = useRef(false);
  useEffect(() => {
    if (wasChecking.current && !checking) void queryClient.invalidateQueries({ queryKey: serverKeys.checks, exact: true });
    wasChecking.current = checking;
  }, [checking, queryClient]);
  if (overview.data === undefined) return overview.isError ? <span>{t("server.security.summaryFailed")}</span> : <Skeleton className="h-5 w-72" />;
  const counts = overview.data.counts;
  const checkedAt = overview.data.checked_at;
  if (counts === null || counts === undefined || checkedAt == null) {
    if (!checking) return <span>{t("server.security.notChecked")}</span>;
    // Work in progress, in the state language: the amber arc of anything the server is doing.
    return (
      <span role="status">
        <StatusPill state="deploying" label={t("server.security.checking")} appearance="inline" />
      </span>
    );
  }
  return (
    <span>
      {t("server.security.summary", { critical: counts.critical, warning: counts.warning, passed: counts.passed })} ·{" "}
      {t.rich("server.security.checked", { when: <RelativeTime value={checkedAt} /> })}
      {checking ? (
        <span role="status" className="ms-2 align-middle">
          <StatusPill state="deploying" label={t("server.security.checkingAgain")} appearance="inline" size="sm" />
        </span>
      ) : null}
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
