import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { appsQuery } from "../../api/queries/apps";
import { overviewQuery } from "../../api/queries/overview";
import { CommandHint } from "../../components/page/CommandHint";
import { DashboardPage } from "../../components/page/DashboardPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { buttonClassName } from "../../components/ui/Button";
import { MenuItem } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { FirstSteps } from "./FirstSteps";
import { FleetSummary } from "./FleetSummary";
import { useFleetSummary } from "./useFleetSummary";
import { KeyFigures } from "./KeyFigures";
import { MACHINE_METRICS, MachineCharts } from "./MachineCharts";
import { useMetricsRead } from "./metricsData";
import { NeedsAttention } from "./NeedsAttention";
import { attentionItems, attentionTotal, isEmptyServer } from "./overviewData";
import type { MetricRange } from "./ranges";
import { RecentActivity } from "./RecentActivity";

function NewAppLink() {
  const t = useT();
  return (
    <Link to="/apps/new" className={buttonClassName("primary")}>
      <Plus aria-hidden="true" />
      {t("overview.newApplication")}
    </Link>
  );
}

/**
 * The quick actions, in the header's More actions: each opens the place where it is done, with
 * that place's own confirmation, rather than a second way of doing it from here.
 */
function QuickActions({ central }: { central: boolean }) {
  const t = useT();
  const navigate = useNavigate();
  return (
    <>
      <MenuItem onClick={() => void navigate({ to: "/domains" })}>{t("overview.quickActions.renewCertificates")}</MenuItem>
      <MenuItem onClick={() => void navigate({ to: "/backups" })}>{t("overview.quickActions.backUp")}</MenuItem>
      <MenuItem onClick={() => void navigate({ to: "/server" })}>{t("overview.quickActions.checkUpdates")}</MenuItem>
      <MenuItem onClick={() => void navigate({ to: "/activity" })}>{t("overview.quickActions.activity")}</MenuItem>
      {central ? <MenuItem onClick={() => void navigate({ to: "/fleet" })}>{t("overview.quickActions.addServer")}</MenuItem> : null}
    </>
  );
}

/** The longest what sits below the attention list waits for it before showing regardless. */
const HOLD_BELOW_MS = 1_500;

/**
 * Whether what sits below the attention list may show yet. That list's height is only known once
 * the Overview has answered; until then the charts and the page's foot are not drawn at all, so
 * nothing jumps down under the operator's pointer as it fills: the page only grows. Their data is
 * read meanwhile. Never held for longer than a moment.
 */
function useHoldBelow(settled: boolean): boolean {
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    if (settled) return;
    const timer = setTimeout(() => {
      setExpired(true);
    }, HOLD_BELOW_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [settled]);
  return !settled && !expired;
}

export interface OverviewPageProps {
  range: MetricRange;
  onRangeChange: (range: MetricRange) => void;
}

/**
 * The first page, a dashboard: six key figures, what needs attention beside what happened
 * lately, on a central the fleet, and the machine's charts. An empty server gets its first steps
 * instead of empty charts.
 */
export function OverviewPage({ range, onRangeChange }: OverviewPageProps) {
  const t = useT();
  const overview = useQuery(overviewQuery());
  const apps = useQuery(appsQuery());
  const fleet = useFleetSummary();
  const items = useMemo(() => (overview.data === undefined ? undefined : attentionItems(overview.data, apps.data?.apps)), [overview.data, apps.data]);
  const total = overview.data === undefined || items === undefined ? 0 : attentionTotal(overview.data, items);
  const server = overview.data?.server;
  const empty = overview.data !== undefined && isEmptyServer(overview.data);
  const held = useHoldBelow(overview.data !== undefined || overview.isError);
  // The charts' read starts with the page, not once they are drawn.
  useMetricsRead(MACHINE_METRICS, range);

  const header = {
    title: t("overview.title"),
    description: t("overview.description"),
    // The line keeps its height before the answer arrives, so nothing below it moves.
    meta:
      server !== undefined ? (
        <>
          <Mono>{server.name}</Mono>
          <span>{t("overview.version", { version: server.version })}</span>
        </>
      ) : (
        <span aria-hidden="true" className="flex h-5 items-center">
          <Skeleton className="h-3 w-40" />
        </span>
      ),
    primaryAction: <NewAppLink />,
    overflow: <QuickActions central={fleet !== null} />,
  };
  const footer = (
    <CommandHint
      label={t("overview.command")}
      command={empty ? "noust create -d example.com -s https://github.com/you/app" : "noust health"}
    />
  );

  if (overview.isError && overview.data === undefined) {
    return (
      <DashboardPage
        header={header}
        firstSteps={
          <ErrorBlock error={overview.error} title={t("overview.couldNotLoad")} onRetry={() => void overview.refetch()} retrying={overview.isRefetching} />
        }
        footer={footer}
      />
    );
  }

  if (empty) {
    return (
      <DashboardPage
        header={header}
        firstSteps={
          <Sections>
            {fleet !== null ? <FleetSummary state={fleet} /> : null}
            <FirstSteps overview={overview.data} />
          </Sections>
        }
        footer={footer}
      />
    );
  }

  const charts = (
    <Sections className="col-span-full">
      {fleet !== null ? <FleetSummary state={fleet} /> : null}
      <MachineCharts range={range} onRangeChange={onRangeChange} />
    </Sections>
  );
  return (
    <DashboardPage
      header={header}
      figures={
        <section aria-label={t("overview.figures.label")} className="contents">
          <KeyFigures overview={overview.data} />
        </section>
      }
      attention={<NeedsAttention items={items} total={total} />}
      activity={<RecentActivity entries={overview.data?.activity} />}
      {...(held ? {} : { charts, footer })}
    />
  );
}
