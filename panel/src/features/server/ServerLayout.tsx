/**
 * The Server area (T2): the machine's header, what is pending on it, the job in hand, and the
 * seven tabs, each a URL. The header and the tabs belong to this layout, so they keep their
 * place and height whichever tab is open.
 *
 * On a central with a node selected every read and action goes to that node through the
 * proxy, and the node is named above the title. A node whose Noust predates this area answers
 * none of it: its tabs say so, except Services, which every version has.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { Outlet } from "@tanstack/react-router";
import { ListChecks, Power, RefreshCw, RotateCw } from "lucide-react";

import { request } from "../../api/client";
import { LinkTabs } from "../../app/LinkTabs";
import type { LinkTab } from "../../app/LinkTabs";
import { DetailPage } from "../../components/page/DetailPage";
import { Button } from "../../components/ui/Button";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatDuration } from "../../lib/format";
import { useNodeCapability } from "../../nodes/capability";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { attentionItems, verdictOf } from "./attention";
import type { Verdict } from "./attention";
import { explainServerError } from "./errors";
import { PendingChanges } from "./PendingChanges";
import { PowerProvider, ScheduledPowerBanner, usePowerDialog } from "./PowerDialog";
import { SERVER_CAPABILITY, changesQuery, securityOverviewQuery, summaryQuery } from "./queries";
import type { ServerSummary } from "./queries";
import { ServerJobProvider, ServerJobSlot, useHasServerJob, useServerJob } from "./serverJob";

function verdictLabel(t: T, verdict: Verdict): string {
  switch (verdict.label) {
    case "healthy":
      return t("server.verdict.healthy");
    case "attention":
      return t("server.verdict.attention");
    case "critical":
      return t("server.verdict.critical");
  }
}

/** One line of facts: the host name, the system, the kernel, how long it has been up. */
function Facts({ summary, t }: { summary: ServerSummary; t: T }) {
  return (
    <>
      <Mono tone="default">{summary.hostname}</Mono>
      <span>{`${summary.os.name}${summary.os.version !== "" && !summary.os.name.includes(summary.os.version) ? ` ${summary.os.version}` : ""}`}</span>
      <span>{t.rich("server.header.kernel", { kernel: <Mono>{summary.kernel}</Mono> })}</span>
      {summary.uptime_seconds != null ? <span>{t("server.header.uptime", { duration: formatDuration(summary.uptime_seconds, t.locale) })}</span> : null}
    </>
  );
}

function Layout() {
  const t = useT();
  const { node } = useNode();
  const power = usePowerDialog();
  const jobs = useServerJob();
  const hasJob = useHasServerJob();
  const capability = useNodeCapability(SERVER_CAPABILITY);
  const available = capability.status === "available" || capability.status === "unknown";
  const summary = useQuery({ ...summaryQuery(), enabled: available });
  const security = useQuery({ ...securityOverviewQuery(), enabled: available });
  const changes = useQuery({ ...changesQuery(), enabled: available });

  const refresh = useMutation({
    mutationFn: () => request("post", "/api/server/updates/refresh"),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "refresh"),
    onError: (error) => reportActionError(t("server.job.refresh.failed"), explainServerError(t, error, node)),
  });
  const runChecks = useMutation({
    mutationFn: () => request("post", "/api/server/security/checks/refresh"),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "checks"),
    onError: (error) => reportActionError(t("server.job.checks.failed"), explainServerError(t, error, node)),
  });

  const loaded = summary.data !== undefined;
  const verdict = loaded ? verdictOf(attentionItems(summary.data, security.data)) : null;
  const pending = (changes.data ?? []).filter((change) => change.status === "pending");
  const scheduled = summary.data?.power.scheduled ?? null;
  const counts = security.data?.counts;
  const openFindings = counts ? counts.critical + counts.warning : null;

  const tabs: readonly LinkTab[] = [
    { label: "server.tabs.overview", to: "/server", exact: true },
    { label: "server.tabs.updates", to: "/server/updates", ...(available ? { count: summary.data?.updates.pending ?? null } : {}) },
    { label: "server.tabs.security", to: "/server/security", ...(available ? { count: openFindings } : {}) },
    { label: "server.tabs.storage", to: "/server/storage" },
    { label: "server.tabs.services", to: "/server/services" },
    { label: "server.tabs.logs", to: "/server/logs" },
    { label: "server.tabs.system", to: "/server/system" },
  ];

  const banner =
    pending.length > 0 || scheduled !== null ? (
      <div className="flex min-w-0 flex-col gap-2">
        {pending.length > 0 ? <PendingChanges changes={pending} /> : null}
        {scheduled !== null ? <ScheduledPowerBanner scheduled={scheduled} /> : null}
      </div>
    ) : undefined;

  return (
    <DetailPage
      header={{
        title: t("server.title"),
        server: node,
        status: !available ? undefined : verdict !== null ? <StatusPill state={verdict.state} label={verdictLabel(t, verdict)} /> : <Skeleton className="h-6 w-28 rounded-pill" />,
        meta: !available ? undefined : summary.data !== undefined ? <Facts summary={summary.data} t={t} /> : <Skeleton className="h-4 w-80 max-w-full" />,
        ...(available
          ? {
              secondaryActions: (
                <Button icon={<RotateCw aria-hidden="true" />} onClick={() => power.open("reboot")}>
                  {t("server.header.reboot")}
                </Button>
              ),
              overflow: (
                <>
                  <MenuItem icon={<RefreshCw />} disabled={jobs.busy || refresh.isPending} onClick={() => refresh.mutate()}>
                    {t("server.header.checkUpdates")}
                  </MenuItem>
                  <MenuItem icon={<ListChecks />} disabled={runChecks.isPending} onClick={() => runChecks.mutate()}>
                    {t("server.header.runChecks")}
                  </MenuItem>
                  <MenuSeparator />
                  <MenuItem icon={<Power />} destructive onClick={() => power.open("shutdown")}>
                    {t("server.header.shutdown")}
                  </MenuItem>
                </>
              ),
            }
          : {}),
      }}
      banner={banner}
      job={hasJob ? <ServerJobSlot /> : undefined}
      tabs={<LinkTabs label={t("server.tabs.label")} tabs={tabs} />}
    >
      <Outlet />
    </DetailPage>
  );
}

/** The Server area's layout route. */
export function ServerLayout() {
  return (
    <ServerJobProvider>
      <PowerProvider>
        <Layout />
      </PowerProvider>
    </ServerJobProvider>
  );
}
