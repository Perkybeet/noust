/**
 * The Server area (T2): the machine's header, what is pending on it and the job in hand, over the
 * section the second column has open. The header belongs to this layout, so it keeps its place and
 * height whichever section is open.
 *
 * On a central with a node selected every read and action goes to that node through the
 * proxy, and the node is named above the title. A node whose Noust predates this area answers
 * none of it: its sections say so, except Services, which every version has.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { Outlet, useNavigate } from "@tanstack/react-router";
import { ListChecks, Power, RotateCw } from "lucide-react";

import { request } from "../../api/client";
import { sessionQuery } from "../../api/queries/auth";
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
import { useHasPermission } from "../terminal/queries";
import { attentionItems, verdictOf } from "./attention";
import type { Verdict } from "./attention";
import { explainServerError } from "./errors";
import { PendingChanges } from "./PendingChanges";
import { PowerProvider, ScheduledPowerBanner, usePowerDialog } from "./PowerDialog";
import { SERVER_CAPABILITY, changesQuery, securityOverviewQuery, summaryQuery } from "./queries";
import type { ServerSummary } from "./queries";
import { ServerJobProvider, ServerJobSlot, useHasServerJob, useServerJob } from "./serverJob";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

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

/** One line of facts: the system, the kernel, how long it has been up. The name is the page's title. */
function Facts({ summary, t }: { summary: ServerSummary; t: T }) {
  return (
    <>
      <span>{`${summary.os.name}${summary.os.version !== "" && !summary.os.name.includes(summary.os.version) ? ` ${summary.os.version}` : ""}`}</span>
      <span>{t.rich("server.header.kernel", { kernel: <Mono>{summary.kernel}</Mono> })}</span>
      {summary.uptime_seconds != null ? <span>{t("server.header.uptime", { duration: formatDuration(summary.uptime_seconds, t.locale) })}</span> : null}
    </>
  );
}

function Layout() {
  const t = useT();
  const { node } = useNode();
  const { data: sessionHostname } = useQuery({ ...sessionQuery(), select: (session) => session.hostname });
  const power = usePowerDialog();
  const jobs = useServerJob();
  const hasJob = useHasServerJob();
  const navigate = useNavigate();
  // A root shell is offered to whoever holds its permission; whether it is on is the page's to say.
  const canOpenTerminal = useHasPermission("server.terminal") === true;
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
        // The server's own name: this page is the one place a header names the machine (item 92).
        title: node ?? sessionHostname ?? t("server.title"),
        mono: true,
        status: !available ? undefined : verdict !== null ? <StatusPill state={verdict.state} label={verdictLabel(t, verdict)} /> : <Skeleton className="h-6 w-28 rounded-pill" />,
        meta: !available ? undefined : summary.data !== undefined ? <Facts summary={summary.data} t={t} /> : (
          <span className="flex h-5 w-80 max-w-full items-center">
            <Skeleton className="h-3.5 w-full" />
          </span>
        ),
        ...(available
          ? {
              secondaryActions: (
                <Button icon={<RotateCw aria-hidden="true" />} onClick={() => power.open("reboot")}>
                  {t("server.header.reboot")}
                </Button>
              ),
              overflow: (
                <>
                  <MenuItem icon={<OBJECT_ICONS.update />} disabled={jobs.busy || refresh.isPending} onClick={() => refresh.mutate()}>
                    {t("server.header.checkUpdates")}
                  </MenuItem>
                  <MenuItem icon={<ListChecks />} disabled={runChecks.isPending} onClick={() => runChecks.mutate()}>
                    {t("server.header.runChecks")}
                  </MenuItem>
                  {canOpenTerminal ? (
                    <MenuItem icon={<OBJECT_ICONS.terminal />} onClick={() => void navigate({ to: "/server/terminal" })}>
                      {t("server.header.terminal")}
                    </MenuItem>
                  ) : null}
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
