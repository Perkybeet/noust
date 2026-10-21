import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import type { ReactNode } from "react";

import { appQuery } from "../../../api/queries/apps";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { SettingsLayout } from "../../../components/page/SettingsLayout";
import type { SettingsNavItem } from "../../../components/page/SettingsLayout";
import { SkeletonSection } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { SETTINGS_SECTIONS } from "./sections";
import { SettingsAppContext, SettingsIconsContext } from "./settingsApp";

/**
 * An application's settings (T3): the list of subsections beside the one open, each its own
 * URL, at most one and a half screens long, with its own save bar when it has fields. The app
 * is read once here and handed to the subsection; its load failure and its not-found page are
 * the app's layout's, above. The list is titled by whose the sections are (the code, the
 * deploys, what triggers them, how it runs, who reaches it) and General opens it, untitled.
 */
export function AppSettingsLayout({ domain, index, children }: { domain: string; index: boolean; children: ReactNode }) {
  const t = useT();
  const app = useQuery(appQuery(domain));
  const params = { domain };
  const icons = useMemo(() => new Map(SETTINGS_SECTIONS.flatMap((section) => (section.icon === undefined ? [] : [[section.label, section.icon] as const]))), []);
  const items: SettingsNavItem[] = SETTINGS_SECTIONS.filter((section) => section.when === undefined || (app.data !== undefined && section.when(app.data))).map((section) => ({
    to: `/apps/$domain/settings${section.path}`,
    params,
    label: t(section.label),
    ...(section.path === "" ? { exact: true } : {}),
    ...(section.danger === true ? { danger: true } : {}),
    ...(section.icon !== undefined ? { icon: section.icon } : {}),
    ...(section.group !== undefined ? { group: t(`appSettings.groups.${section.group}`), groupKey: section.group } : {}),
  }));
  return (
    <SettingsLayout label={t("appSettings.nav.label")} items={items} index={index} backTo="/apps/$domain/settings" backParams={params}>
      {app.data !== undefined ? (
        <SettingsAppContext.Provider value={app.data}>
          <SettingsIconsContext.Provider value={icons}>{children}</SettingsIconsContext.Provider>
        </SettingsAppContext.Provider>
      ) : app.isError ? null : (
        <LoadingRegion label={t("appSettings.loading")}>
          <SkeletonSection rows={5} />
        </LoadingRegion>
      )}
    </SettingsLayout>
  );
}
