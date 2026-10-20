import { useQuery } from "@tanstack/react-query";
import { useRouterState } from "@tanstack/react-router";
import { Landmark } from "lucide-react";
import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

import { sessionQuery } from "../../api/queries/auth";
import type { Dest, NavContext } from "../../app/navCore";
import { CommandHint } from "../../components/page/CommandHint";
import { DetailPage } from "../../components/page/DetailPage";
import { SettingsLayout } from "../../components/page/SettingsLayout";
import type { SettingsNavItem } from "../../components/page/SettingsLayout";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { useHasFleet, useReturnServer, useServerList } from "../../nodes/servers";
import { useConsoleContext } from "../../nodes/useNode";
import { useCentral } from "../central/central";
import { RENAMED, SETTINGS_SCREENS, groupSettings } from "../nav/settings";
import type { SettingsBlock } from "../nav/settings";

/** Where a section's primary action goes: the settings' page header (see SettingsPrimaryAction). */
const PrimarySlot = createContext<HTMLElement | null>(null);

/**
 * A settings section's one primary action ("Invite a person", "Create token"), put in the page's
 * header where every view has its primary (docs/DESIGN.md 4, "One primary action per view"),
 * although the header belongs to the layout the sections share. Renders nothing outside it.
 */
export function SettingsPrimaryAction({ children }: { children: ReactNode }) {
  const slot = useContext(PrimarySlot);
  return slot === null ? null : createPortal(children, slot);
}

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

/**
 * Settings, grouped by whose they are (NV-7, NV-11) and drawn from the register: the selected
 * server's own - This server, Monitoring, Developers - follow the selector (on web-2 they are
 * `/n/web-2/settings/...` and read and write web-2); the instance's - People and access,
 * Governance, Fleet - are the central's whichever server is selected, and say so in their
 * heading, in their group of the navigation and under the title.
 *
 * On a lone server there is nothing to tell apart: one list, each group under its title. On a
 * fleet every group is headed by whose it is, the server's name in mono or the central's; from the
 * central's sections the server's groups still lead back to the server the operator came from, and
 * a note says how that server's own sign-in, two-factor and tokens are managed there - a node
 * refuses a central on those on purpose, so a compromised central cannot lock anyone out. A list
 * of twelve sections or more has a box that narrows it, by name and by the names it had before.
 */
export function SettingsShell({ children }: { children: ReactNode }) {
  const t = useT();
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const central = useCentral();
  const back = useReturnServer();
  const { hostname } = useServerList();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const { data: session } = useQuery(sessionQuery());
  const [slot, setSlot] = useState<HTMLElement | null>(null);

  const onCentral = context.kind === "central";
  // The server the server's sections are about: the one on screen, or the one to go back to.
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
      const central = (entry: Dest): boolean => entry.central === true;
      if (block === "server") {
        return groups.flatMap((group) => group.sections.filter((entry) => !central(entry)).map((entry) => item(entry, block, group.id)));
      }
      const lent = groupSettings(SETTINGS_SCREENS, "server", ctx).flatMap((group) => group.sections.filter(central).map((entry) => item(entry, block, group.id)));
      return [...lent, ...groups.flatMap((group) => group.sections.map((entry) => item(entry, block, group.id)))];
    });
  }, [t, hasFleet, sealed, permissions, onCentral, serverNode, serverName, centralName]);

  const description = !hasFleet
    ? t("settings.page.description")
    : onCentral
      ? t.rich("nav.settingsGroups.centralDescription", { name: <Mono key="central">{centralName}</Mono> })
      : t.rich("nav.settingsGroups.serverDescription", { name: <Mono key="server">{serverName}</Mono> });

  return (
    // T3 under the title "Settings" (the T2 header, with no tabs): the header is the layout's,
    // the same on every section, and says whose sections these are.
    <DetailPage
      header={{
        title: t("settings.page.title"),
        description,
        // The central's sections are no one server's: the header names the central instead.
        ...(onCentral && hasFleet ? { meta: <CentralLabel name={centralName} /> } : {}),
        // Empty (and boxless) unless the section on screen puts its primary action here.
        primaryAction: <span ref={setSlot} className="contents" />,
      }}
    >
      <div data-scope={onCentral ? "central" : "server"} className="min-w-0">
        <SettingsLayout label={t("nav.landmarks.settingsSections")} items={items} index={pathname === "/settings"} backTo="/settings">
          {onCentral && hasFleet && back !== null && back.node !== null ? <NodeAccessNote node={back.node} /> : null}
          <PrimarySlot.Provider value={slot}>{children}</PrimarySlot.Provider>
        </SettingsLayout>
      </div>
    </DetailPage>
  );
}

/** "Central · nas": which machine the central's settings are, beside the title. */
function CentralLabel({ name }: { name: string }) {
  const t = useT();
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-13 text-fg-muted">
      <Landmark aria-hidden="true" className="size-icon-sm shrink-0" />
      {t.rich("nav.settingsGroups.central", { name: <Mono key="central">{name}</Mono> })}
    </span>
  );
}

/**
 * Why a node's own sign-in, two-factor and tokens are not here, and where they are: the node
 * refuses a central on them on purpose (`FLEET_REFUSED_PREFIXES`), so they are managed on it.
 */
function NodeAccessNote({ node }: { node: string }) {
  const t = useT();
  return (
    <Notice title={t("nav.settingsGroups.nodeNoteTitle", { node })}>
      <div className="flex flex-col gap-2">
        <p className="max-w-measure text-pretty">{t("nav.settingsGroups.nodeNote", { node })}</p>
        <CommandHint label={t("nav.settingsGroups.nodeNoteCommands", { node })} command="noust user list && noust token list" />
      </div>
    </Notice>
  );
}
