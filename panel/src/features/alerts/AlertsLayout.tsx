import { Outlet } from "@tanstack/react-router";
import { useState } from "react";

import { LinkTabs } from "../../app/LinkTabs";
import { tabsOf } from "../../app/navCore";
import { DetailPage } from "../../components/page/DetailPage";
import { useT } from "../../i18n";
import { ALERTS_SCREENS } from "../nav/alerts";
import { PrimarySlot } from "../settings/SettingsPrimaryAction";

/**
 * Alerts: what warns an operator before a person does. The rules on the metrics Noust records and
 * which of them are firing, and the probes asked from outside with the public status page. They were two
 * sections of Settings in 4.0 and are an area of the bar since 4.1; the pages are the same.
 *
 * The page of a section that has a primary action puts it in the header, at the right, through the slot
 * Settings uses (`SettingsPrimaryAction`), so the rule is the same on every page that has one.
 */
export function AlertsLayout() {
  const t = useT();
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  return (
    <DetailPage
      header={{
        title: t("nav.alerts.label"),
        description: t("alerts.page.description"),
        // Empty (and boxless) unless the section on screen puts its primary action here.
        primaryAction: <span ref={setSlot} className="contents" />,
      }}
      tabs={<LinkTabs label={t("nav.landmarks.alertsSections")} tabs={tabsOf(ALERTS_SCREENS)} />}
    >
      <PrimarySlot.Provider value={slot}>
        <Outlet />
      </PrimarySlot.Provider>
    </DetailPage>
  );
}
