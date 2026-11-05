import type { Me, Task } from "./model";
import { FleetPanel, ServerPanel } from "./TaskPanel";
import type { TaskState } from "./store";

export interface TaskPanelBodyProps {
  fleet: boolean;
  state: TaskState;
  /** The tasks of the server on screen: what a server's panel is made of. */
  here: readonly Task[];
  me: Me;
  /** This server's version, which a server still on an older one is asked to reach. */
  target: string | null;
  onNavigate: () => void;
}

/**
 * What the task centre's panel holds, one module so the frame of every page can load it only when it is asked for: the
 * icon and the pinned chips are in the first load, the rows, their logs and the fleet's groups are not.
 */
export default function TaskPanelBody({ fleet, state, here, me, target, onNavigate }: TaskPanelBodyProps) {
  return fleet ? <FleetPanel state={state} target={target} onNavigate={onNavigate} /> : <ServerPanel tasks={here} me={me} onNavigate={onNavigate} />;
}
