import { useRouterState } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import { DetailPage } from "../../components/page/DetailPage";
import { SettingsLayout } from "../../components/page/SettingsLayout";
import { useT } from "../../i18n";
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
 * The sections are the central's, and the server chosen in the selector does not count in them (item 166): what is
 * written here holds for the central and, setting by setting, for the servers it manages, and each page says how many
 * follow it. Nothing here says "All the fleet" any more: a section is the central's, and its page says for whom.
 *
 * The list is the second column (app/frame/SettingsColumn.tsx), which reads the same `useSettingsItems`,
 * and on a phone the sheet behind the section selector: the page never repeats it.
 */
export function SettingsShell({ children }: { children: ReactNode }) {
  const t = useT();
  const leaf = useRouterState({ select: (state) => state.matches.at(-1)?.fullPath });
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [master, setMaster] = useState<HTMLElement | null>(null);
  const { items } = useSettingsItems();
  const hub = leaf === undefined || HUB_ROUTES.includes(leaf);
  const current = items.find((item) => item.to === leaf);
  // A section the list does not offer here (one this person may not read, reached by its address) is still called what it
  // is, not "Settings".
  const named = SETTINGS_SCREENS.find((screen) => screen.to === leaf);

  return (
    // T3 under the title of the section (the T2 header, with no tabs): the header is the layout's, the
    // same on every section, and the section is its title.
    <DetailPage
      // A section is as wide as its header: the title and its actions end where the content does.
      measure={hub ? "page" : "settings"}
      header={{
        title: hub ? t("settings.page.title") : (current?.label ?? (named !== undefined ? t(named.label) : t("settings.page.title"))),
        // Where Settings opens it says what it is for, under its title, as every page does.
        ...(hub ? { description: t("settingsHome.lead") } : {}),
        // Empty (and boxless) unless the section on screen puts its primary action here.
        primaryAction: <span ref={setSlot} className="contents" />,
        // Empty too: the switch that governs the whole section, beside its title (rule 106).
        master: <span ref={setMaster} className="contents" />,
      }}
    >
      <div data-scope="central" className="min-w-0">
        {hub ? (
          <PrimarySlot.Provider value={slot}>
            <MasterSlot.Provider value={master}>{children}</MasterSlot.Provider>
          </PrimarySlot.Provider>
        ) : (
          <SettingsLayout label={t("nav.landmarks.settingsSections")} items={items} index={false} backTo="/settings" carried>
            <PrimarySlot.Provider value={slot}>
              <MasterSlot.Provider value={master}>{children}</MasterSlot.Provider>
            </PrimarySlot.Provider>
          </SettingsLayout>
        )}
      </div>
    </DetailPage>
  );
}
