
import { ErrorBlock } from "../../components/page/QueryState";
import { Card } from "../../components/ui/Card";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { ServerLink } from "../fleet/links";
import { ReachabilityPill } from "../fleet/ReachabilityPill";
import type { FleetSummaryRow, FleetSummaryState } from "./useFleetSummary";

const NAME = "min-w-0 truncate rounded-chip text-13 font-medium text-fg hover:underline hover:underline-offset-2";

/** The most servers listed here; the fleet page has them all. */
export const FLEET_ROWS = 5;

function columns(t: T): Column<FleetSummaryRow>[] {
  return [
    {
      id: "server",
      header: t("overview.fleet.server"),
      card: "title",
      cell: (row) => (
        <span className="flex min-w-0 items-baseline gap-2">
          <ServerLink node={row.node} path="/" className={NAME}>
            <Mono>{row.name}</Mono>
          </ServerLink>
          {row.node === null ? <span className="shrink-0 text-12 text-fg-faint">{t("overview.fleet.thisServer")}</span> : null}
        </span>
      ),
    },
    {
      id: "state",
      header: t("overview.fleet.state"),
      card: "status",
      width: "w-40",
      cell: (row) => <ReachabilityPill reachability={row.reachability} />,
    },
    {
      id: "version",
      header: t("overview.fleet.version"),
      hideBelow: "md",
      card: "meta",
      cell: (row) => (row.version !== null ? <Mono tone="muted">{row.version}</Mono> : <EmptyCell reason={t("overview.fleet.unknownState")} />),
    },
    {
      id: "apps",
      header: t("overview.fleet.apps"),
      card: "meta",
      cell: (row) =>
        row.apps !== null ? (
          <span className="text-13 text-fg-muted tabular-nums">{t("overview.fleet.appsValue", { running: row.apps.running, failed: row.apps.failed })}</span>
        ) : (
          <EmptyCell reason={row.older ? t("overview.fleet.olderVersion") : t("overview.fleet.unknownState")} />
        ),
    },
    {
      id: "attention",
      header: t("overview.fleet.attention"),
      card: "meta",
      cell: (row) => {
        if (row.error !== null) {
          return (
            <Mono tone="muted" truncate title={row.error}>
              {row.error}
            </Mono>
          );
        }
        if (row.older) return <span className="text-12 text-fg-faint">{t("overview.fleet.olderVersion")}</span>;
        if (row.attention === null) return <EmptyCell reason={t("overview.fleet.unknownState")} />;
        if (row.attention.count === 0) return <span className="text-13 text-fg-muted">{t("overview.fleet.noAttention")}</span>;
        const state = row.attention.worst === "fail" ? "failed" : "warning";
        return (
          <span className={`inline-flex items-center gap-1.5 text-13 ${stateTextClass(state)}`}>
            <StatusGlyph state={state} size={12} />
            {t("overview.fleet.attentionCount", { count: row.attention.count })}
          </span>
        );
      },
    },
  ];
}

/**
 * The fleet at a glance on a central's Overview: how many servers, how many answer, and one line
 * per server (this one first) with its state, version, applications and what needs attention.
 */
export function FleetSummary({ state }: { state: FleetSummaryState }) {
  const t = useT();
  const { summary } = state;
  const rows = summary?.rows.slice(0, FLEET_ROWS) ?? [];
  const line =
    summary === null
      ? undefined
      : [
          t("overview.fleet.servers", { count: summary.servers }),
          t("overview.fleet.reachable", { count: summary.reachable }),
          ...(summary.unreachable > 0 ? [t("overview.fleet.unreachable", { count: summary.unreachable })] : []),
        ].join(" · ");
  return (
    <Card
      as="section"
      level={2}
      padding="none"
      title={t("overview.fleet.title")}
      {...(line !== undefined ? { description: line } : {})}
      actions={
        <TextLink to="/fleet" size="ui">
          {t("overview.fleet.open")}
        </TextLink>
      }
    >
      {state.error !== null ? (
        <div className="p-4">
          <ErrorBlock compact error={state.error} title={t("overview.fleet.couldNotLoad")} onRetry={state.retry} />
        </div>
      ) : (
        <DataTable
          caption={t("overview.fleet.caption")}
          columns={columns(t)}
          rows={rows}
          getRowId={(row) => row.key}
          density="compact"
          mobile="cards"
          loading={state.loading}
          skeletonRows={2}
          className="rounded-none border-0 shadow-none"
        />
      )}
    </Card>
  );
}
