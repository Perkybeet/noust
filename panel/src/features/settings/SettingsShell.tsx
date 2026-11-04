import { useRouterState } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import { CommandHint } from "../../components/page/CommandHint";
import { DetailPage } from "../../components/page/DetailPage";
import { SettingsLayout } from "../../components/page/SettingsLayout";
import { Badge } from "../../components/ui/Badge";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { useReturnServer } from "../../nodes/servers";
import { SETTINGS_SCREENS } from "../nav/settings";
import { MasterSlot, PrimarySlot } from "./SettingsPrimaryAction";
import { useSettingsItems } from "./useSettingsItems";

export { SettingsPrimaryAction } from "./SettingsPrimaryAction";

/** The hub's own route: where Settings opens and lists every section, drawn by SettingsHome. */
const HUB_ROUTES: readonly string[] = ["/settings", "/settings/"];

/**
 * Settings, one section per URL, in the groups a person looks for a setting by (item 107) and drawn
 * from the register (`useSettingsItems`). The title (`h1`) is the section on screen, never the area
 * repeated (NV-15); `/settings` itself is the hub, which draws every section as a door with a line
 * about what is inside (SettingsHome), and is the page a phone's "All sections" leads back to.
 *
 * Whose a section is (the selected server's or the central's) is not said above each group any more:
 * the scope selector says which server is selected (6.11) and the central's sections open the central's
 * page whichever server it is. From the central's sections a note says how a node's own sign-in,
 * two-factor and tokens are managed there: a node refuses a central on those on purpose, so a
 * compromised central cannot lock anyone out.
 *
 * The list is the second column (app/frame/SettingsColumn.tsx), which reads the same `useSettingsItems`,
 * and on a phone the sheet behind the section selector: the page never repeats it.
 */
export function SettingsShell({ children }: { children: ReactNode }) {
  const t = useT();
  const back = useReturnServer();
  const leaf = useRouterState({ select: (state) => state.matches.at(-1)?.fullPath });
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [master, setMaster] = useState<HTMLElement | null>(null);
  const { items, onCentral, hasFleet } = useSettingsItems();
  const hub = leaf === undefined || HUB_ROUTES.includes(leaf);
  const current = items.find((item) => item.to === leaf);
  // A section the list does not offer here (fleet settings on a server that is not a central, reached by its address)
  // is still called what it is, not "Settings".
  const named = SETTINGS_SCREENS.find((screen) => screen.to === leaf);

  return (
    // T3 under the title of the section (the T2 header, with no tabs): the header is the layout's, the
    // same on every section, and the section is its title.
    <DetailPage
      // A section is as wide as its header: the title and its actions end where the content does.
      measure={hub ? "page" : "settings"}
      header={{
        title: hub ? t("settings.page.title") : (current?.label ?? (named !== undefined ? t(named.label) : t("settings.page.title"))),
        // A section of the whole instance says so where it is read, as the column does: opening it never moves the selector.
        ...(hasFleet && !hub && current?.central === true ? { meta: <Badge>{t("nav.column.fleetWide")}</Badge> } : {}),
        // Empty (and boxless) unless the section on screen puts its primary action here.
        primaryAction: <span ref={setSlot} className="contents" />,
        // Empty too: the switch that governs the whole section, beside its title (rule 106).
        master: <span ref={setMaster} className="contents" />,
      }}
    >
      <div data-scope={onCentral ? "central" : "server"} className="min-w-0">
        {hub ? (
          <PrimarySlot.Provider value={slot}>
            <MasterSlot.Provider value={master}>{children}</MasterSlot.Provider>
          </PrimarySlot.Provider>
        ) : (
          <SettingsLayout label={t("nav.landmarks.settingsSections")} items={items} index={false} backTo="/settings" carried>
            {onCentral && hasFleet && back !== null && back.node !== null && current?.central === true ? <NodeAccessNote node={back.node} /> : null}
            <PrimarySlot.Provider value={slot}>
              <MasterSlot.Provider value={master}>{children}</MasterSlot.Provider>
            </PrimarySlot.Provider>
          </SettingsLayout>
        )}
      </div>
    </DetailPage>
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
