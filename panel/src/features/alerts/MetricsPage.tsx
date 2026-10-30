import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { Sections } from "../../components/page/Section";
import { useT } from "../../i18n";
import { useHasFleet } from "../../nodes/servers";
import { useNode } from "../../nodes/useNode";
import { FleetMetricsCard } from "../fleet/FleetMetricsCard";
import { MachineCharts } from "../overview/MachineCharts";
import { DEFAULT_RANGE } from "../overview/ranges";
import type { MetricRange } from "../overview/ranges";
import type { MetricsSearch } from "./metricsSearch";

export interface MetricsPageProps {
  search: MetricsSearch;
  onSearchChange: (search: MetricsSearch) => void;
}

/**
 * What the server measures, over the window in the address: CPU, memory, swap, load, network and
 * disk, with one range for all of them. On a central the servers are put side by side below it, one
 * measurement at a time, so which one is the busiest is a glance. The history is what the alert rules
 * judge, so it lives with them.
 */
export function MetricsPage({ search, onSearchChange }: MetricsPageProps) {
  const t = useT();
  useDocumentTitle(t("alerts.metricsPage.documentTitle"), 1);
  const hasFleet = useHasFleet();
  const { node } = useNode();
  const range = search.window ?? DEFAULT_RANGE;
  const change = (next: MetricRange): void => {
    onSearchChange(next === DEFAULT_RANGE ? {} : { window: next });
  };
  return (
    <Sections>
      <MachineCharts range={range} onRangeChange={change} />
      {hasFleet && node === null ? <FleetMetricsCard /> : null}
      <CommandHint command="noust health" label={t("common.words.fromTerminal")} />
    </Sections>
  );
}
