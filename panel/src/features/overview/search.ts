/**
 * The address of Home: which panel is open and whether it is being customised. Read by the route (to validate
 * it), by the page and by the column, which are drawn by different parts of the frame and must agree without
 * being given anything: they read the same address.
 *
 * `/` is the panels of one server and `/fleet` those of the whole fleet; `?panel=` names one of the person's own
 * (none: the one that comes with the console for that address), and `?customise` is the mode in which the column is the
 * gallery. `?panel=new` is the page that makes one.
 */

import { useRouterState } from "@tanstack/react-router";

import type { PanelScope } from "../../components/widgets/types";

export interface HomeSearch {
  panel?: string;
  customise?: true;
}

/** What a panel's id may be (`noust.validators.dashboards`): `new` is the page that makes one, and is of this form too. */
const ID = /^[a-z0-9][a-z0-9-]{0,39}$/;

/** Reads the address' own keys and drops anything else, so a link that was typed wrong opens the panel that comes with the console. */
export function validateHomeSearch(search: Record<string, unknown>): HomeSearch {
  const panel = search["panel"];
  const customise = search["customise"];
  return {
    ...(typeof panel === "string" && ID.test(panel) ? { panel } : {}),
    ...(customise === true || customise === "true" || customise === "1" || customise === 1 ? { customise: true as const } : {}),
  };
}

export interface HomeAddress {
  scope: PanelScope;
  /** The panel the address names, or null for the one that comes with the console. */
  panel: string | null;
  customising: boolean;
}

/** Where Home is, read from the address bar: the same answer wherever in the frame it is asked. */
export function homeAddressOf(pathname: string, search: unknown): HomeAddress {
  const read = validateHomeSearch(typeof search === "object" && search !== null ? (search as Record<string, unknown>) : {});
  return { scope: pathname === "/fleet" || pathname === "/fleet/" ? "fleet" : "server", panel: read.panel ?? null, customising: read.customise === true };
}

export function useHomeAddress(): HomeAddress {
  const { pathname, search } = useRouterState({ select: (state) => state.location });
  return homeAddressOf(pathname, search);
}
