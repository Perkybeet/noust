import { useQuery } from "@tanstack/react-query";
import { Landmark } from "lucide-react";
import { useMemo } from "react";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import type { Dest, NavContext } from "../../app/navCore";
import type { SettingsNavItem } from "../../components/page/SettingsLayout";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { useHasFleet, useReturnServer, useServerList } from "../../nodes/servers";
import { useConsoleContext } from "../../nodes/useNode";
import { useCentral } from "../central/central";
import { iconOf } from "../nav/icons";
import { RENAMED, SETTINGS_SCREENS, groupSettings } from "../nav/settings";
import type { SettingsBlock } from "../nav/settings";

/** What the register's group ids are called in the list: whose the sections under them are. */
const GROUP_TITLES: Readonly<Record<string, PlainKey>> = {
  "this-server": "nav.settingsGroups.titles.thisServer",
  monitoring: "nav.settingsGroups.titles.monitoring",
  developers: "nav.settingsGroups.titles.developers",
  people: "nav.settingsGroups.titles.people",
  governance: "nav.settingsGroups.titles.governance",
  fleet: "nav.settingsGroups.titles.fleet",
};

const BLOCKS: readonly SettingsBlock[] = ["server", "instance"];

/** What the sections of Settings are about, for the page that frames them and for the second column. */
export interface SettingsView {
  items: SettingsNavItem[];
  /** The page is the central's own, not a server's. */
  onCentral: boolean;
  hasFleet: boolean;
  /** The server the server's sections are about: the one on screen, or the one to go back to. */
  serverNode: string | null;
  serverName: string;
  centralName: string;
}

/**
 * The sections of Settings as a list, grouped by whose they are (NV-7, NV-11) and drawn from the
 * register: the selected server's own follow the selector, the instance's are the central's whichever
 * server is selected. One list for the page that frames them (SettingsShell) and for the second
 * column of the double bar, so the two can never name different sections.
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

  const items = useMemo<SettingsNavItem[]>(() => {
    const ctx: NavContext = { can: (permission) => permissions?.includes(permission) ?? false, when: { fleet: hasFleet || sealed } };
    const scopes: Readonly<Record<SettingsBlock, ReactNode>> = {
      server: (
        <Mono tone="default" truncate className="min-w-0">
          {serverName}
        </Mono>
      ),
      instance: (
        <span className="inline-flex min-w-0 items-center gap-1.5">
          <Landmark aria-hidden="true" className="size-icon-xs shrink-0 text-fg-faint" />
          <span className="min-w-0 truncate">{t.rich("nav.settingsGroups.central", { name: <Mono key="central">{centralName}</Mono> })}</span>
        </span>
      ),
    };
    const item = (entry: Dest, block: SettingsBlock, group: string): SettingsNavItem => {
      const root = entry.id === "settings";
      const names = [entry.keywords !== undefined ? t(entry.keywords) : "", ...(RENAMED[entry.id] ?? []).map((key) => t(key))];
      return {
        to: entry.to,
        label: t(root ? (entry.rootLabel ?? entry.label) : entry.label),
        icon: iconOf(entry),
        ...(root ? { exact: true } : {}),
        keywords: names.join(" ").trim(),
        // From the central's sections, the server's lead back to the server the operator was on.
        ...(entry.central !== true && onCentral ? { search: { node: serverNode ?? undefined } } : {}),
        group: t(GROUP_TITLES[group] ?? "nav.settingsGroups.titles.thisServer"),
        groupKey: group,
        // Alone, a server's groups need no name above them; with a fleet each is headed by whose it is.
        ...(hasFleet ? { scope: scopes[block], scopeKey: block } : {}),
      };
    };
    return BLOCKS.flatMap((block) => {
      const groups = groupSettings(SETTINGS_SCREENS, block, ctx);
      if (!hasFleet) return groups.flatMap((group) => group.sections.map((entry) => item(entry, block, group.id)));
      // On a fleet the central's own sections that the register files with the server's (the API
      // tokens, a node refuses a central on them) go under the central's name: under a server's they
      // would say they are that server's.
      const centrals = (entry: Dest): boolean => entry.central === true;
      if (block === "server") {
        return groups.flatMap((group) => group.sections.filter((entry) => !centrals(entry)).map((entry) => item(entry, block, group.id)));
      }
      const lent = groupSettings(SETTINGS_SCREENS, "server", ctx).flatMap((group) => group.sections.filter(centrals).map((entry) => item(entry, block, group.id)));
      return [...lent, ...groups.flatMap((group) => group.sections.map((entry) => item(entry, block, group.id)))];
    });
  }, [t, hasFleet, sealed, permissions, onCentral, serverNode, serverName, centralName]);

  return { items, onCentral, hasFleet, serverNode, serverName, centralName };
}
