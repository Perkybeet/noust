/**
 * The sections of This machine that were four views of one page in 4.1: what is waiting to be updated (with Noust's
 * own update), the machine itself, what runs on it and what it uses over time. Each is a section of its own, one
 * route each, in the column; this is the one place that says which page a route draws.
 */

import { NodeCapabilityGate } from "../../../nodes/capability";
import { MonitorPage } from "./MonitorPage";
import { MachineSections } from "./MachineSections";
import { ProcessesPage } from "./ProcessesPage";
import { UpdatesTab } from "../updates/UpdatesTab";
import { SERVER_CAPABILITY } from "../queries";
import type { SystemView } from "./data";

export interface SystemTabProps {
  view: SystemView;
}

/** The section of the machine a route draws. */
export function SystemTab({ view }: SystemTabProps) {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      {view === "updates" ? <UpdatesTab /> : view === "machine" ? <MachineSections /> : view === "monitor" ? <MonitorPage /> : <ProcessesPage />}
    </NodeCapabilityGate>
  );
}
