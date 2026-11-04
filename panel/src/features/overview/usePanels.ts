/**
 * The panels of Home a person has, and how they are kept.
 *
 * An account's are kept by the server (`/api/auth/dashboards`, one set across the whole fleet, which a central never
 * forwards to a node), and this browser keeps a copy so Home draws at once and its changes cannot be lost to a slow
 * request. The access token is no account: the server answers it 400, and the copy in this browser is all there is.
 * Either way the panel on screen is the store's (`panelStore`), which is changed the moment a person arranges
 * something; the server hears of it a moment after they stop (the writes of a run of changes are one request), and a
 * change the server has not yet heard of is sent again the next time Home opens.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import type { QueryClient } from "@tanstack/react-query";

import { dashboardKeys, dashboardsQuery, deleteDashboard, saveDashboard } from "../../api/queries/dashboards";
import type { DashboardOut, Dashboards } from "../../api/queries/dashboards";
import { isApiError } from "../../api/errors";
import { toDocument } from "../../components/widgets/model";
import type { Placed } from "../../components/widgets/model";
import type { PanelScope } from "../../components/widgets/types";
import { useCan } from "../../components/widgets/useCan";
import { useT } from "../../i18n";
import { useHasFleet } from "../../nodes/servers";
import { reportActionError } from "../apps/useAppActions";
import { dropPanel, keepPanel, markClean, readPanelCache, settleHere, takeServerPanels, usePanelCache } from "./panelStore";
import { defaultLayout, panelsOf, slugOf, startingLayout } from "./panels";
import type { Panel, SavedPanel } from "./panels";

/** What an account may hold, until the server says: the limits of the server's own validator. */
const LIMITS = { dashboards: 20, widgets: 40 } as const;

/** How long a run of changes is waited out before the server is told: dragging a widget changes the panel at every move. */
export const SEND_DELAY_MS = 600;

const timers = new Map<string, number>();

export interface Panels {
  panels: Panel[];
  /** Whether Home knows what to draw: this browser has the panels, or the server has said. False only on a first visit that is still waiting. */
  ready: boolean;
  /** Whether the server keeps them (an account) or only this browser does (the access token). */
  persistence: "account" | "browser";
  limits: { dashboards: number; widgets: number };
  /** Puts a layout in a panel. Over one that comes with the console it is saved as the person's own. */
  save: (panel: Panel, layout: readonly Placed[]) => void;
  /** Makes a panel; null when the account has as many as it may. */
  create: (input: { name: string; scope: PanelScope; start: "copy" | "empty" }) => Panel | null;
  rename: (panel: Panel, name: string) => void;
  /** Takes a panel of the person's away. The two that come with the console are put back as they came instead. */
  remove: (panel: Panel) => void;
  /** Puts a panel back as it was made: what came with the console, or, for the person's own, the console's panel for its scope. */
  reset: (panel: Panel) => void;
}

function asSaved(out: DashboardOut): Omit<SavedPanel, "dirty"> {
  return { id: out.id, name: out.name, scope: out.scope === "fleet" ? "fleet" : "server", layout: out.layout as unknown as SavedPanel["layout"] };
}

/** The server's list as the cache keeps it up to date with what was just saved or deleted, so a read of it later says the same. */
function inCache(queryClient: QueryClient, change: (list: Dashboards["dashboards"]) => Dashboards["dashboards"]): void {
  queryClient.setQueryData<Dashboards>(dashboardKeys.all, (old) => (old === undefined ? old : { ...old, dashboards: change(old.dashboards) }));
}

export function usePanels(): Panels {
  const t = useT();
  const can = useCan();
  const hasFleet = useHasFleet();
  const queryClient = useQueryClient();
  const remote = useQuery(dashboardsQuery());
  const cache = usePanelCache();
  const persistence = isApiError(remote.error) && remote.error.status === 400 ? "browser" : "account";
  const offerFleet = hasFleet && can("fleet.read");

  // The server's list, once it is read: taken in, except what was changed here and has not got there yet.
  const fromServer = remote.data?.dashboards;
  useEffect(() => {
    if (fromServer !== undefined) takeServerPanels(fromServer.map(asSaved));
  }, [fromServer]);
  // The access token has no account: what this browser holds is all there is.
  useEffect(() => {
    if (persistence === "browser") settleHere();
  }, [persistence]);

  const send = (id: string): void => {
    const saved = readPanelCache().panels.find((panel) => panel.id === id);
    if (saved === undefined) return;
    const layout = saved.layout;
    saveDashboard(id, { name: saved.name, scope: saved.scope, layout: layout as unknown as Record<string, unknown> }).then(
      (out) => {
        markClean(id, layout);
        inCache(queryClient, (list) => (list.some((panel) => panel.id === id) ? list.map((panel) => (panel.id === id ? out : panel)) : [...list, out]));
      },
      (error: unknown) => {
        reportActionError(t("overview.panels.saveFailed", { name: saved.name }), error);
      },
    );
  };

  // What was changed here and the server has not heard of (a page closed within the delay) goes again once it can be told.
  useEffect(() => {
    if (persistence !== "account" || fromServer === undefined) return;
    for (const panel of readPanelCache().panels) if (panel.dirty && !timers.has(panel.id)) send(panel.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once the server's list is in; `send` reads what it needs when it runs
  }, [fromServer, persistence]);

  const later = (id: string, delay: number): void => {
    if (persistence !== "account") return;
    const waiting = timers.get(id);
    if (waiting !== undefined) window.clearTimeout(waiting);
    timers.set(
      id,
      window.setTimeout(() => {
        timers.delete(id);
        send(id);
      }, delay),
    );
  };

  const panels = useMemo(() => panelsOf(cache.panels, can, t, offerFleet), [cache.panels, can, t, offerFleet]);
  const limits = remote.data?.limits ?? LIMITS;

  const forget = (id: string): void => {
    const waiting = timers.get(id);
    if (waiting !== undefined) window.clearTimeout(waiting);
    timers.delete(id);
    dropPanel(id);
    if (persistence !== "account") return;
    deleteDashboard(id).then(
      () => {
        inCache(queryClient, (list) => list.filter((panel) => panel.id !== id));
      },
      (error: unknown) => {
        // A panel the account never saved is not there to delete: it is what the person wanted.
        if (isApiError(error) && error.status === 404) return;
        reportActionError(t("overview.panels.removeFailed"), error);
      },
    );
  };

  const save = (panel: Panel, layout: readonly Placed[]): void => {
    keepPanel({ id: panel.id, name: panel.name, scope: panel.scope, layout: toDocument(layout) });
    later(panel.id, SEND_DELAY_MS);
  };

  return {
    panels,
    ready: cache.settled || remote.isError,
    persistence,
    limits,
    save,
    create: ({ name, scope, start }) => {
      if (cache.panels.length >= limits.dashboards) return null;
      const id = slugOf(name, cache.panels.map((panel) => panel.id));
      const layout = startingLayout(scope, start, can);
      keepPanel({ id, name, scope, layout: toDocument(layout) });
      later(id, 0);
      return { id, name, scope, layout, builtin: false, customised: false };
    },
    rename: (panel, name) => {
      keepPanel({ id: panel.id, name, scope: panel.scope, layout: toDocument(panel.layout) });
      later(panel.id, SEND_DELAY_MS);
    },
    remove: (panel) => {
      forget(panel.id);
    },
    reset: (panel) => {
      if (panel.builtin) forget(panel.id);
      else save(panel, defaultLayout(panel.scope, can));
    },
  };
}
