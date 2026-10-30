import { Outlet, useRouterState } from "@tanstack/react-router";
import { useState } from "react";

import { LinkTabs } from "../../app/LinkTabs";
import { tabsOf } from "../../app/navCore";
import { DetailPage } from "../../components/page/DetailPage";
import { useT } from "../../i18n";
import { ALERTS_SCREENS } from "../nav/alerts";
import { PrimarySlot } from "../settings/SettingsPrimaryAction";

/** What each section says it is for, by the last segment of its address; the first section has none. */
const DESCRIPTIONS = {
  alerts: "alerts.page.alerts",
  checks: "alerts.page.checks",
  probes: "alerts.page.probes",
  status: "alerts.page.status",
  metrics: "alerts.page.metrics",
} as const;

/**
 * Alerts: what warns an operator before a person does. The rules on the metrics Noust records and
 * which of them are firing, the checks the monitor makes without a rule, the addresses asked from
 * outside, the public status page and the measurements. They were two sections of Settings in 4.0 and
 * are an area of the bar since 4.1.
 *
 * The title is the section's, not the area's, and what it is for is said once under it. A section that
 * has a primary action puts it in the header, at the right, through the slot Settings uses
 * (`SettingsPrimaryAction`), so the rule is the same on every page that has one.
 */
export function AlertsLayout() {
  const t = useT();
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const path = useRouterState({ select: (state) => state.location.pathname });
  const tabs = tabsOf(ALERTS_SCREENS);
  const current = tabs.find((tab) => tab.exact === true && tab.to === path) ?? tabs[0];
  const last = path.split("/").filter(Boolean).at(-1) ?? "alerts";
  const section = last in DESCRIPTIONS ? (last as keyof typeof DESCRIPTIONS) : "alerts";
  return (
    <DetailPage
      header={{
        title: t(current === undefined ? "nav.alerts.label" : current.label),
        description: t(DESCRIPTIONS[section]),
        // Empty (and boxless) unless the section on screen puts its primary action here.
        primaryAction: <span ref={setSlot} className="contents" />,
      }}
      tabs={<LinkTabs label={t("nav.landmarks.alertsSections")} tabs={tabs} />}
    >
      <PrimarySlot.Provider value={slot}>
        <Outlet />
      </PrimarySlot.Provider>
    </DetailPage>
  );
}
