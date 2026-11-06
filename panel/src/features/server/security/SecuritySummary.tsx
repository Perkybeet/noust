/**
 * The checks' state in one line, under the title of Security, and the button that runs them again, at its right.
 * Nobody has to run them first: reading the summary starts them in the background when there is no report or it is
 * due, and while they run the line says so, with the last counts when there are some. When a run ends, the checks'
 * list is read again.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { request } from "../../../api/client";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Button } from "../../../components/ui/Button";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { useNodeCapability } from "../../../nodes/capability";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { explainServerError } from "../errors";
import { HeaderActions } from "../headerSlot";
import { SERVER_CAPABILITY, securityOverviewQuery, serverKeys } from "../queries";
import { useServerJob } from "../serverJob";

/** The checks' counts and when they were made, as the sentence under the title. */
export function SecuritySummary() {
  const t = useT();
  const queryClient = useQueryClient();
  const capability = useNodeCapability(SERVER_CAPABILITY);
  const available = capability.status === "available" || capability.status === "unknown";
  const overview = useQuery({ ...securityOverviewQuery(), enabled: available });
  const checking = overview.data?.checking === true;
  const wasChecking = useRef(false);
  useEffect(() => {
    if (wasChecking.current && !checking) void queryClient.invalidateQueries({ queryKey: serverKeys.checks, exact: true });
    wasChecking.current = checking;
  }, [checking, queryClient]);
  if (!available) return null;
  if (overview.data === undefined) {
    return overview.isError ? (
      <span>{t("server.security.summaryFailed")}</span>
    ) : (
      <LoadingRegion label={t("server.security.summaryLoading")}>
        <Skeleton className="h-5 w-72" />
      </LoadingRegion>
    );
  }
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
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
      <span>{t("server.security.summary", { critical: counts.critical, warning: counts.warning, passed: counts.passed })}</span>
      <span>{t.rich("server.security.checked", { when: <RelativeTime value={checkedAt} /> })}</span>
      {checking ? (
        <span role="status">
          <StatusPill state="deploying" label={t("server.security.checkingAgain")} appearance="inline" size="sm" />
        </span>
      ) : null}
    </span>
  );
}

/** "Check now", in the header of the page: the checks run for whoever may read the server, as they always have. */
export function RunChecks() {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const run = useMutation({
    mutationFn: () => request("post", "/api/server/security/checks/refresh"),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "checks"),
    onError: (error) => reportActionError(t("server.job.checks.failed"), explainServerError(t, error, node)),
  });
  return (
    <HeaderActions>
      {/* design-exception: write-without-permission running the checks takes server.read, which is what draws this page */}
      <Button loading={run.isPending} onClick={() => run.mutate()}>
        {t("server.security.runChecks")}
      </Button>
    </HeaderActions>
  );
}
