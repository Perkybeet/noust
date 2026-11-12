import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { sessionQuery } from "../../api/queries/auth";
import type { Dest, NavContext } from "../../app/navCore";
import type { SettingsNavItem } from "../../components/page/SettingsLayout";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { useHasFleet } from "../../nodes/servers";
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
  noust: "nav.settingsGroups.titles.noust",
};

/** An item of the list, with the register's id of the section. */
export interface SettingsSection extends SettingsNavItem {
  id: string;
  /** What is each server's own: on the central a table of servers, and with a server a page of that server. */
  table: boolean;
}

/** What the sections of Settings are about, for the page that frames them and for the second column. */
export interface SettingsView {
  items: SettingsSection[];
  hasFleet: boolean;
}

/**
 * The sections of Settings as one list, in the groups a person looks for a setting by (people and access, notices,
 * connections, security, API, Noust) and drawn from the register. One list for the page that frames them
 * (SettingsShell), for the hub that opens them (SettingsHome) and for the second column of the double bar, so the three
 * can never name different sections. The sections are the central's and the server chosen in the selector does not count
 * in them (item 166); the ones that are each server's own open the table of servers, so their link carries no server.
 */
export function useSettingsItems(): SettingsView {
  const t = useT();
  const hasFleet = useHasFleet();
  const central = useCentral();
  const { data: session } = useQuery(sessionQuery());
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
        // A table of servers is the section as the central has it: a link that carried a server would open that server's own page.
        ...(entry.table === true ? { search: { node: undefined } } : {}),
        group: t(GROUP_TITLES[group] ?? "nav.settingsGroups.titles.noust"),
        groupKey: group,
        id: entry.id,
        table: entry.table === true,
        ...(entry.hint !== undefined ? { hint: t(entry.hint) } : {}),
      };
    };
    return groupSettings(SETTINGS_SCREENS, ctx).flatMap((group) => group.sections.map((entry) => item(entry, group.id)));
  }, [t, hasFleet, sealed, permissions]);

  return { items, hasFleet };
}
