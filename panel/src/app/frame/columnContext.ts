/**
 * Which resource's sections the second column is holding, for the page under it: a page that draws
 * its own list of the same sections (the tabs under an application's header, the side list of
 * Settings) does not draw it again when the column has it, and draws it in the page when it has not
 * (one bar, or a window with no room). The shell provides it; nothing else sets it.
 */

import { createContext, useContext } from "react";

import type { ColumnKind } from "../columns";

export const NavColumnContext = createContext<ColumnKind | null>(null);

/** Whether the second column holds this resource's sections, so the page need not. */
export function useColumnCarries(kind: ColumnKind): boolean {
  return useContext(NavColumnContext) === kind;
}
