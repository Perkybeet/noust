import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { sessionQuery } from "../../api/queries/auth";
import type { Dest, NavContext } from "../../app/navCore";
import type { SettingsNavItem } from "../../components/page/SettingsLayout";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { useHasFleet, useReturnServer, useServerList } from "../../nodes/servers";
import { useConsoleContext } from "../../nodes/useNode";
import { useCentral } from "../central/central";
import { iconOf } from "../nav/icons";
import { RENAMED, SETTINGS_SCREENS, groupSettings } from "../nav/settings";

/** What the register's group ids are called in the list: what its sections are for. */
export const GROUP_TITLES: Readonly<Record<string, PlainKey>> = {
  people: "nav.settingsGroups.titles.people",
  notices: "nav.settingsGroups.titles.notices",
  connections: "nav.settingsGroups.titles.connections",
  security: "nav.settingsGroups.titles.security",
  api: "nav.settingsGroups.titles.api",
  fleet: "nav.settingsGroups.titles.fleet",
  noust: "nav.settingsGroups.titles.noust",
};

/** An item of the list, with the register's id of the section and whether it is the central's whichever server is selected. */
export interface SettingsSection extends SettingsNavItem {
  id: string;
  central: boolean;
}

/** What the sections of Settings are about, for the page that frames them and for the second column. */
export interface SettingsView {
  items: SettingsSection[];
  /** The page is the central's own, not a server's. */
  onCentral: boolean;
  hasFleet: boolean;
  /** The server the server's sections are about: the one on screen, or the one to go back to. */
  serverNode: string | null;
  serverName: string;
  centralName: string;
}

/**
 * The sections of Settings as one list, in the groups a person looks for a setting by (people and
 * access, notifications, connections, security, API, the fleet, Noust) and drawn from the register.
 * One list for the page that frames them (SettingsShell), for the hub that opens them
 * (SettingsHome) and for the second column of the double bar, so the three can never name different
 * sections. Whose a section is (the selected server's, or the central's whichever server is
 * selected) is decided by where it leads, not by a heading over it: from the central's sections the
 * server's lead back to the server the operator came from.
 */
export function useSettingsItems(): SettingsView {
  const t = useT();
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const central = useCentral();
  const back = useReturnServer();
  const { hostname } = useServerList();
  const { data: session } = useQuery(sessionQuery());

  const onCentral = context.kind === "central";
  const serverNode = context.kind === "server" ? context.node : (back?.node ?? null);
  const serverName = serverNode ?? hostname ?? t("fleet.selector.thisServer");
  const centralName = hostname ?? t("fleet.selector.central");
  const permissions = session?.permissions;
  const sealed = central.sealed;

  const items = useMemo<SettingsSection[]>(() => {
    const ctx: NavContext = { can: (permission) => permissions?.includes(permission) ?? false, when: { fleet: hasFleet || sealed } };
    const item = (entry: Dest, group: string): SettingsSection => {
      const names = [entry.keywords !== undefined ? t(entry.keywords) : "", ...(RENAMED[entry.id] ?? []).map((key) => t(key))];
      return {
        to: entry.to,
        label: t(entry.label),
        icon: iconOf(entry),
        keywords: names.join(" ").trim(),
        // From the central's sections, the server's lead back to the server the operator was on.
        ...(entry.central !== true && onCentral ? { search: { node: serverNode ?? undefined } } : {}),
        group: t(GROUP_TITLES[group] ?? "nav.settingsGroups.titles.noust"),
        groupKey: group,
        id: entry.id,
        central: entry.central === true,
      };
    };
    return groupSettings(SETTINGS_SCREENS, ctx).flatMap((group) => group.sections.map((entry) => item(entry, group.id)));
  }, [t, hasFleet, sealed, permissions, onCentral, serverNode]);

  return { items, onCentral, hasFleet, serverNode, serverName, centralName };
}
