import { useRouterState } from "@tanstack/react-router";
import { Landmark } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { useColumnCarries } from "../../app/frame/columnContext";
import { CommandHint } from "../../components/page/CommandHint";
import { DetailPage } from "../../components/page/DetailPage";
import { SettingsLayout } from "../../components/page/SettingsLayout";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { useReturnServer } from "../../nodes/servers";
import { PrimarySlot } from "./SettingsPrimaryAction";
import { useSettingsItems } from "./useSettingsItems";

export { SettingsPrimaryAction } from "./SettingsPrimaryAction";

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
 *
 * The list is in the page with one bar and is the second column of the double bar (app/frame/NavColumn.tsx),
 * which reads the same `useSettingsItems`: where the column has it, the page does not repeat it.
 */
export function SettingsShell({ children }: { children: ReactNode }) {
  const t = useT();
  const back = useReturnServer();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const { items, onCentral, hasFleet, serverName, centralName } = useSettingsItems();
  const carried = useColumnCarries("settings");

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
        <SettingsLayout label={t("nav.landmarks.settingsSections")} items={items} index={pathname === "/settings"} backTo="/settings" carried={carried}>
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
