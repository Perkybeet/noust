import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../../../api/queries/auth";
import { useHasFleet } from "../../../nodes/servers";
import { fleetSettingsQuery, fleetStatusQuery } from "./data";
import type { FleetSetting, FleetSettingKind, StatusServer, StatusSetting } from "./data";

/** One setting of the fleet: what the central holds of it and where each server stands. */
export interface FleetRow {
  name: string;
  /** The value of the fleet's row (a secret shown as `***`); null where only servers' own values exist. */
  fleet: FleetSetting | null;
  /** The values the central set for one server. */
  exceptions: FleetSetting[];
  /** Each server's cell, and how many follow. */
  status: StatusSetting;
}

export interface FleetKind {
  /** The fleet is there to be asked: a central with servers, and a person who may read it. */
  active: boolean;
  /** The first answers are still on their way. */
  loading: boolean;
  error: unknown;
  /** What this kind is, as the server describes it (its permission, whether it is available). */
  kind: FleetSettingKind | undefined;
  /** Every kind there is: the dialog that makes a setting is told them. */
  kinds: FleetSettingKind[];
  rows: FleetRow[];
  /** How each server answered, over every kind. */
  servers: StatusServer[];
  /** The person may change what the fleet holds. */
  canManage: boolean;
  refetch: () => void;
}

/** The servers the central manages and how each stands, for the strip a page of the settings draws once under its title. */
export function useFleetServers(): { active: boolean; servers: StatusServer[] } {
  const hasFleet = useHasFleet();
  const { data: session } = useQuery(sessionQuery());
  const active = hasFleet && (session?.permissions?.includes("fleet.read") ?? false);
  const status = useQuery({ ...fleetStatusQuery(), enabled: active });
  return { active, servers: status.data?.servers ?? [] };
}

/**
 * The settings of one kind that the central carries to its servers, with where each server stands on them: what a
 * page of a section reads to say "N of M servers follow it". Quiet where there is no fleet (a lone server asks
 * nothing, and draws nothing of it) and where the person may not read it.
 */
export function useFleetKind(resource: string): FleetKind {
  const hasFleet = useHasFleet();
  const { data: session } = useQuery(sessionQuery());
  const permissions = session?.permissions;
  const active = hasFleet && (permissions?.includes("fleet.read") ?? false);
  const list = useQuery({ ...fleetSettingsQuery(), enabled: active });
  const status = useQuery({ ...fleetStatusQuery(), enabled: active });

  const rows: FleetRow[] = (status.data?.settings ?? [])
    .filter((row) => row.resource === resource)
    .map((row) => {
      const mine = (list.data?.items ?? []).filter((item) => item.resource === resource && item.name === row.name);
      return { name: row.name, status: row, fleet: mine.find((item) => item.scope === "fleet") ?? null, exceptions: mine.filter((item) => item.scope !== "fleet") };
    });

  return {
    active,
    loading: active && (list.isPending || status.isPending),
    error: list.error ?? status.error,
    kind: list.data?.kinds.find((entry) => entry.key === resource),
    kinds: list.data?.kinds ?? [],
    rows,
    servers: status.data?.servers ?? [],
    canManage: permissions?.includes("fleet.manage") ?? false,
    refetch: () => {
      void list.refetch();
      void status.refetch();
    },
  };
}
