import { createContext, useContext } from "react";
import type { ReactNode } from "react";

/**
 * Whether a `variant="primary"` button here is drawn as `secondary`. A view has one primary
 * (DESIGN 4): while the save bar holds changes, the primary is Save and the header's steps aside
 * (`PageHeader` wraps its actions in this). It changes the colour and nothing else, so the
 * header keeps its labels and its sizes and nothing shifts.
 */
const Demoted = createContext(false);

/** Inside, primary buttons are drawn as secondary while `active`; their `data-variant` says so. */
export function DemotePrimary({ active, children }: { active: boolean; children: ReactNode }) {
  return <Demoted value={active}>{children}</Demoted>;
}

/**
 * A dialog, a drawer or a confirmation has its own primary, whatever is behind it: it opens from
 * the header's action, and React carries context through a portal.
 */
export function ResetPrimary({ children }: { children: ReactNode }) {
  return <Demoted value={false}>{children}</Demoted>;
}

/** Whether the primary buttons around this component are being drawn as secondary. */
export function useDemoted(): boolean {
  return useContext(Demoted);
}
