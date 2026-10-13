import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { appQuery } from "../../../api/queries/apps";
import { Card } from "../../../components/ui/Card";
import { SettingsLayout } from "../../../components/page/SettingsLayout";
import type { SettingsNavItem } from "../../../components/page/SettingsLayout";
import { Skeleton } from "../../../components/ui/Skeleton";
import { KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { useT } from "../../../i18n";
import { SETTINGS_SECTIONS } from "./sections";
import { SettingsAppContext } from "./settingsApp";

/** A subsection's shape while the app is read: its title, a line, and a card of facts. */
function SubsectionSkeleton() {
  const t = useT();
  return (
    <div aria-busy="true" className="flex flex-col gap-4">
      <span className="sr-only">{t("appSettings.loading")}</span>
      <div aria-hidden="true" className="flex flex-col gap-2">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-4 w-72 max-w-full" />
      </div>
      <Card padding="none">
        <KeyValueListSkeleton rows={6} className="px-5 py-1" />
      </Card>
    </div>
  );
}

/**
 * An application's settings (T3): the list of subsections beside the one open, each its own
 * URL, at most one and a half screens long, with its own save bar when it has fields. The app
 * is read once here and handed to the subsection; its load failure and its not-found page are
 * the app's layout's, above.
 */
export function AppSettingsLayout({ domain, index, children }: { domain: string; index: boolean; children: ReactNode }) {
  const t = useT();
  const app = useQuery(appQuery(domain));
  const params = { domain };
  const items: SettingsNavItem[] = SETTINGS_SECTIONS.filter((section) => section.when === undefined || (app.data !== undefined && section.when(app.data))).map((section) => ({
    to: `/apps/$domain/settings${section.path}`,
    params,
    label: t(section.label),
    ...(section.path === "" ? { exact: true } : {}),
    ...(section.danger === true ? { danger: true } : {}),
  }));
  return (
    <SettingsLayout label={t("appSettings.nav.label")} items={items} index={index} backTo="/apps/$domain/settings" backParams={params}>
      {app.data !== undefined ? (
        <SettingsAppContext.Provider value={app.data}>{children}</SettingsAppContext.Provider>
      ) : app.isError ? null : (
        <SubsectionSkeleton />
      )}
    </SettingsLayout>
  );
}
