import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { appQuery } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { Card } from "../../../components/ui/Card";
import { SettingsLayout } from "../../../components/page/SettingsLayout";
import type { SettingsNavItem } from "../../../components/page/SettingsLayout";
import { Skeleton } from "../../../components/ui/Skeleton";
import { KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { hasBuildBlock } from "../../build/model";
import { SettingsAppContext } from "./settingsApp";

/**
 * The subsections, in order: each its own URL under the app's settings, Delete last and apart.
 * `when` leaves one out for an application it does not concern: a container application's own
 * settings, and the build commands of a type that has a block (a Compose stack or a container
 * is built by its own configuration).
 */
const SUBSECTIONS: readonly { path: string; label: PlainKey; danger?: boolean; when?: (app: App) => boolean }[] = [
  { path: "", label: "appSettings.nav.general" },
  { path: "/source", label: "appSettings.nav.source" },
  { path: "/container", label: "appSettings.nav.container", when: (app) => app.app_type === "container" },
  { path: "/deploys", label: "appSettings.nav.deploys" },
  { path: "/deploy-on-push", label: "appSettings.nav.push" },
  { path: "/hooks", label: "appSettings.nav.hooks" },
  { path: "/build", label: "appSettings.nav.buildCommands", when: (app) => hasBuildBlock(app.app_type) },
  { path: "/builds", label: "appSettings.nav.builds" },
  { path: "/resources", label: "appSettings.nav.resources" },
  { path: "/processes", label: "runtime.nav.processes", when: (app) => hasUnit(app) },
  { path: "/security", label: "runtime.nav.security", when: (app) => hasUnit(app) },
  { path: "/mounts", label: "runtime.nav.mounts", when: (app) => hasUnit(app) && app.app_type !== "docker-compose" },
  { path: "/previews", label: "appSettings.nav.previews" },
  { path: "/export", label: "appSettings.nav.export" },
  { path: "/delete", label: "appSettings.nav.delete", danger: true },
];

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
  const items: SettingsNavItem[] = SUBSECTIONS.filter((section) => section.when === undefined || (app.data !== undefined && section.when(app.data))).map((section) => ({
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
