/**
 * Resource monitor: what the machine uses over time (the processor, the memory, the disk and the network, over the
 * window in the address), and below it the watcher that writes down what stands out. The charts are the same
 * component Alerts > Metrics draws, so a window means one thing wherever it is chosen.
 */

import { useState } from "react";

import { NodeCapabilityGate } from "../../../nodes/capability";
import { MachineCharts } from "../../overview/MachineCharts";
import { DEFAULT_RANGE } from "../../overview/ranges";
import type { MetricRange } from "../../overview/ranges";
import { MonitorCard } from "../MonitorCard";
import { SERVER_CAPABILITY } from "../queries";

/** What a person asks of the machine: how busy it is, how much it holds in memory, how full the disk is, how much it talks. */
const FOUR = ["cpu", "memory", "disk", "network"] as const;

function Content() {
  const [range, setRange] = useState<MetricRange>(DEFAULT_RANGE);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <MachineCharts range={range} onRangeChange={setRange} only={FOUR} />
      <MonitorCard />
    </div>
  );
}

/** The Resource monitor section. */
export function MonitorPage() {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <Content />
    </NodeCapabilityGate>
  );
}
