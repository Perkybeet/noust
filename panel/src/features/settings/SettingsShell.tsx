import { useRouterState } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import { useColumnCarries } from "../../app/frame/columnContext";
import { CommandHint } from "../../components/page/CommandHint";
import { DetailPage } from "../../components/page/DetailPage";
import { SettingsLayout } from "../../components/page/SettingsLayout";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { useReturnServer } from "../../nodes/servers";
import { PrimarySlot } from "./SettingsPrimaryAction";
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
 * The list is in the page with one bar and is the second column of the double bar
 * (app/frame/NavColumn.tsx), which reads the same `useSettingsItems`: where the column has it, the
 * page does not repeat it.
 */
export function SettingsShell({ children }: { children: ReactNode }) {
  const t = useT();
  const back = useReturnServer();
  const leaf = useRouterState({ select: (state) => state.matches.at(-1)?.fullPath });
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const { items, onCentral, hasFleet } = useSettingsItems();
  const carried = useColumnCarries("settings");
  const hub = leaf === undefined || HUB_ROUTES.includes(leaf);
  const current = items.find((item) => item.to === leaf);

  return (
    // T3 under the title of the section (the T2 header, with no tabs): the header is the layout's, the
    // same on every section, and the section is its title.
    <DetailPage
      header={{
        title: hub ? t("settings.page.title") : (current?.label ?? t("settings.page.title")),
        // Empty (and boxless) unless the section on screen puts its primary action here.
        primaryAction: <span ref={setSlot} className="contents" />,
      }}
    >
      <div data-scope={onCentral ? "central" : "server"} className="min-w-0">
        {hub ? (
          <PrimarySlot.Provider value={slot}>{children}</PrimarySlot.Provider>
        ) : (
          <SettingsLayout label={t("nav.landmarks.settingsSections")} items={items} index={false} backTo="/settings" carried={carried}>
            {onCentral && hasFleet && back !== null && back.node !== null && current?.central === true ? <NodeAccessNote node={back.node} /> : null}
            <PrimarySlot.Provider value={slot}>{children}</PrimarySlot.Provider>
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
